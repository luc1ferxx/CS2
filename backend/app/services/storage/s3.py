"""The S3-compatible artifact store for production: private objects only, conditional reads, and generation-checked promotion and delete."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, BinaryIO

from app.services.storage._boundary import _ArtifactReferenceBoundary
from app.services.storage.contract import (
    ArtifactMetadata,
    ArtifactRead,
    ArtifactReference,
    _decode_reference_identity,
    _encode_reference_identity,
)
from app.services.storage.errors import (
    ArtifactBindingError,
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRangeError,
    ArtifactReferenceError,
    ArtifactStoreError,
    ArtifactTooLargeError,
)
from app.services.storage.local import LocalArtifactStore


class _SeekableStreamWindow:
    """Expose one immutable seekable stream segment as a zero-based upload body."""

    def __init__(self, source: BinaryIO, *, start: int, size_bytes: int):
        self.source = source
        self.start = start
        self.size_bytes = size_bytes
        self.position = 0
        self.source.seek(self.start)

    def read(self, size: int = -1) -> bytes:
        remaining = self.size_bytes - self.position
        if remaining <= 0:
            return b""
        requested = remaining if size is None or size < 0 else min(size, remaining)
        chunk = self.source.read(requested)
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise OSError("Artifact upload stream returned invalid bytes")
        normalized = bytes(chunk)
        self.position += len(normalized)
        return normalized

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self.position + offset
        elif whence == os.SEEK_END:
            target = self.size_bytes + offset
        else:
            raise OSError("Artifact upload seek is invalid")
        if target < 0 or target > self.size_bytes:
            raise OSError("Artifact upload seek is out of range")
        self.source.seek(self.start + target)
        self.position = target
        return target

    def tell(self) -> int:
        return self.position

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


class S3ArtifactStore(_ArtifactReferenceBoundary):
    """Private S3-compatible adapter with conditional immutable reads."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "cs2-artifacts-v1",
        region: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: Any | None = None,
    ):
        self.bucket = self._validate_bucket(bucket)
        self.prefix = self._validate_prefix(prefix)
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ArtifactStoreError("S3-compatible storage client is unavailable") from exc
            client_options: dict[str, Any] = {}
            if region:
                client_options["region_name"] = region
            if endpoint_url:
                client_options["endpoint_url"] = endpoint_url
            if access_key_id:
                client_options["aws_access_key_id"] = access_key_id
            if secret_access_key:
                client_options["aws_secret_access_key"] = secret_access_key
            client = boto3.client("s3", **client_options)
            self._validate_client_capabilities(client)
        self.client = client

    def write_stream(
        self,
        reference: str,
        stream: BinaryIO,
        *,
        max_bytes: int,
        chunk_size: int = 1024 * 1024,
        expected_size: int | None = None,
        content_type: str | None = None,
        policy_version: str | None = None,
        now: datetime | None = None,
    ) -> ArtifactMetadata:
        parsed = self.parse_reference(reference)
        LocalArtifactStore._validate_write_limits(
            max_bytes=max_bytes,
            chunk_size=chunk_size,
        )
        if expected_size is not None and (expected_size < 0 or expected_size > max_bytes):
            raise ArtifactIntegrityError("Artifact expected size is invalid")
        created_at = LocalArtifactStore._normalize_now(now)
        operation_token = uuid.uuid4().hex
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            stream_start = stream.tell()
            stream.seek(stream_start)
        except (AttributeError, OSError):
            raise ArtifactStoreError(
                "S3-compatible artifact uploads require a seekable stream"
            ) from None
        try:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ArtifactIntegrityError("Artifact stream returned invalid bytes")
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise ArtifactTooLargeError("Artifact exceeds configured size limit")
                digest.update(chunk)
        except ArtifactStoreError:
            raise
        except Exception as exc:
            raise ArtifactStoreError("Artifact write failed safely") from exc
        if expected_size is not None and size_bytes != expected_size:
            raise ArtifactIntegrityError("Artifact size does not match")
        body = _SeekableStreamWindow(
            stream,
            start=stream_start,
            size_bytes=size_bytes,
        )
        object_key = self._object_key(parsed)
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": object_key,
            "Body": body,
            "ContentLength": size_bytes,
            "IfNoneMatch": "*",
            "Metadata": self._object_metadata(
                reference=reference,
                sha256=digest.hexdigest(),
                size_bytes=size_bytes,
                created_at=created_at,
                policy_version=policy_version,
                write_token=operation_token,
            ),
        }
        if content_type:
            request["ContentType"] = content_type
        try:
            self.client.put_object(**request)
        except Exception as exc:
            if self._error_code(exc) in {"PreconditionFailed", "412"}:
                raise ArtifactConflictError("Artifact already exists") from exc
            self._delete_operation_owned(parsed, operation_token)
            raise ArtifactStoreError("Artifact write failed safely") from exc

        try:
            verified = self.head(reference)
        except ArtifactStoreError:
            self._delete_operation_owned(parsed, operation_token)
            raise
        if (
            verified is None
            or verified.size_bytes != size_bytes
            or not hmac.compare_digest(verified.sha256, digest.hexdigest())
        ):
            self._delete_operation_owned(parsed, operation_token)
            raise ArtifactIntegrityError("Artifact write verification failed")
        return verified

    def head(self, reference: str) -> ArtifactMetadata | None:
        parsed = self.parse_reference(reference)
        try:
            response = self.client.head_object(
                Bucket=self.bucket,
                Key=self._object_key(parsed),
            )
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            raise ArtifactStoreError("Artifact metadata lookup failed safely") from exc
        return self._metadata_from_head(reference, response)

    def read_range(
        self,
        reference: str,
        *,
        start: int = 0,
        end_inclusive: int | None = None,
        expected_generation: str | None = None,
    ) -> ArtifactRead:
        metadata = self.head(reference)
        if metadata is None:
            raise ArtifactNotFoundError("Artifact was not found")
        if expected_generation is not None and metadata.generation != expected_generation:
            raise ArtifactIntegrityError("Artifact generation changed")
        normalized_end = LocalArtifactStore._normalize_range(
            size_bytes=metadata.size_bytes,
            start=start,
            end_inclusive=end_inclusive,
        )
        parsed = self.parse_reference(reference)
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(parsed),
        }
        request.update(self._conditional_read_args(metadata.generation))
        if metadata.size_bytes > 0:
            request["Range"] = f"bytes={start}-{normalized_end}"
        try:
            response = self.client.get_object(**request)
        except Exception as exc:
            if self._is_not_found(exc):
                raise ArtifactNotFoundError("Artifact was not found") from exc
            if self._error_code(exc) in {"PreconditionFailed", "412"}:
                raise ArtifactIntegrityError("Artifact generation changed") from exc
            if self._error_code(exc) in {"InvalidRange", "416"}:
                raise ArtifactRangeError("Artifact range is invalid") from exc
            raise ArtifactStoreError("Artifact read failed safely") from exc
        body = response.get("Body")
        expected_length = max(0, normalized_end - start + 1)
        try:
            response_generation = self._generation_from_response(response)
            content_length = int(response.get("ContentLength", -1))
        except (ArtifactStoreError, TypeError, ValueError) as exc:
            if hasattr(body, "close"):
                body.close()
            raise ArtifactIntegrityError("Artifact read metadata is invalid") from exc
        if (
            response_generation != metadata.generation
            or content_length != expected_length
            or not hasattr(body, "read")
            or not hasattr(body, "close")
        ):
            if hasattr(body, "close"):
                body.close()
            raise ArtifactIntegrityError("Artifact generation changed")
        return ArtifactRead(
            metadata=metadata,
            handle=body,
            start=start,
            end_inclusive=normalized_end,
        )

    def promote(
        self,
        quarantine_reference: str,
        *,
        expected_generation: str,
        expected_size: int,
        expected_sha256: str,
        now: datetime | None = None,
    ) -> ArtifactMetadata:
        source_reference = self.parse_reference(quarantine_reference)
        if source_reference.state != "quarantine":
            raise ArtifactBindingError("Only quarantine artifacts can be promoted")
        source_metadata = self.head(quarantine_reference)
        if source_metadata is None:
            raise ArtifactNotFoundError("Artifact was not found")
        if (
            source_metadata.generation != expected_generation
            or source_metadata.size_bytes != expected_size
            or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
            or not hmac.compare_digest(source_metadata.sha256, expected_sha256)
        ):
            raise ArtifactIntegrityError("Artifact promotion integrity check failed")
        accepted_reference = ArtifactReference(
            owner_id=source_reference.owner_id,
            demo_id=source_reference.demo_id,
            kind=source_reference.kind,
            state="accepted",
            artifact_id=source_reference.artifact_id,
        )
        accepted_uri = accepted_reference.to_uri()
        existing = self.head(accepted_uri)
        if existing is not None:
            raise ArtifactConflictError("Accepted artifact already exists")
        operation_token = uuid.uuid4().hex
        copy_source: dict[str, str] = {
            "Bucket": self.bucket,
            "Key": self._object_key(source_reference),
        }
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(accepted_reference),
            "CopySource": copy_source,
            "IfNoneMatch": "*",
            "MetadataDirective": "REPLACE",
            "Metadata": self._object_metadata(
                reference=accepted_uri,
                sha256=source_metadata.sha256,
                size_bytes=source_metadata.size_bytes,
                created_at=LocalArtifactStore._normalize_now(now),
                policy_version=source_metadata.policy_version,
                write_token=operation_token,
            ),
        }
        if expected_generation.startswith("version:"):
            copy_source["VersionId"] = expected_generation[len("version:") :]
        elif expected_generation.startswith("etag:"):
            request["CopySourceIfMatch"] = (
                f'"{expected_generation[len("etag:") :]}"'
            )
        else:
            raise ArtifactIntegrityError("Artifact generation is invalid")
        if source_metadata.content_type:
            request["ContentType"] = source_metadata.content_type
        try:
            self.client.copy_object(**request)
        except Exception as exc:
            if self._error_code(exc) in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }:
                raise ArtifactConflictError("Accepted artifact already exists") from exc
            # A transport failure may happen after the server persisted the
            # unique accepted target. Remove only this operation's generation.
            self._delete_operation_owned(accepted_reference, operation_token)
            raise ArtifactStoreError("Artifact promotion failed safely") from exc

        try:
            accepted_metadata = self.head(accepted_uri)
            if (
                accepted_metadata is None
                or accepted_metadata.size_bytes != expected_size
                or not hmac.compare_digest(accepted_metadata.sha256, expected_sha256)
            ):
                raise ArtifactIntegrityError("Accepted artifact verification failed")
        except ArtifactStoreError:
            self._delete_operation_owned(accepted_reference, operation_token)
            raise
        try:
            self.delete(
                quarantine_reference,
                expected_generation=expected_generation,
            )
        except ArtifactStoreError:
            # A valid accepted copy remains authoritative; cleanup retries quarantine.
            pass
        return accepted_metadata

    def delete(
        self,
        reference: str,
        *,
        expected_generation: str | None = None,
    ) -> bool:
        parsed = self.parse_reference(reference)
        metadata = self.head(reference)
        if metadata is None:
            return False
        if expected_generation is not None and metadata.generation != expected_generation:
            raise ArtifactIntegrityError("Artifact generation changed")
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(parsed),
        }
        generation = expected_generation or metadata.generation
        if generation.startswith("version:"):
            request["VersionId"] = generation[len("version:") :]
        elif generation.startswith("etag:"):
            request["IfMatch"] = f'"{generation[len("etag:") :]}"'
        else:
            raise ArtifactIntegrityError("Artifact generation is invalid")
        try:
            self.client.delete_object(**request)
        except Exception as exc:
            if self._error_code(exc) in {"PreconditionFailed", "412"}:
                raise ArtifactIntegrityError("Artifact generation changed") from exc
            if self._is_not_found(exc):
                return False
            raise ArtifactStoreError("Artifact delete failed safely") from exc
        return True

    def iter_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> Iterator[ArtifactMetadata]:
        normalized_cutoff = LocalArtifactStore._normalize_now(cutoff)
        prefix = f"{self.prefix}/v1/quarantine/" if self.prefix else "v1/quarantine/"
        continuation_token: str | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            try:
                response = self.client.list_objects_v2(**request)
            except Exception as exc:
                raise ArtifactStoreError("Artifact cleanup listing failed safely") from exc
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise ArtifactIntegrityError("Artifact cleanup listing is invalid")
            for item in contents:
                if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
                    continue
                try:
                    parsed = self._reference_from_object_key(item["Key"])
                except ArtifactReferenceError:
                    continue
                if parsed.state != "quarantine":
                    continue
                if owner_id is not None and parsed.owner_id != owner_id:
                    continue
                if demo_id is not None and parsed.demo_id != demo_id:
                    continue
                try:
                    metadata = self.head(parsed.to_uri())
                except ArtifactStoreError:
                    continue
                if metadata is not None and metadata.created_at < normalized_cutoff:
                    yield metadata
            if not response.get("IsTruncated"):
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise ArtifactIntegrityError("Artifact cleanup listing is invalid")
            continuation_token = next_token

    def _metadata_from_head(
        self,
        reference: str,
        response: Mapping[str, Any],
    ) -> ArtifactMetadata:
        try:
            object_metadata = response["Metadata"]
            if not isinstance(object_metadata, Mapping):
                raise TypeError
            stored_reference = str(object_metadata["artifact-reference"])
            if stored_reference != reference:
                raise ArtifactIntegrityError("Artifact metadata binding is invalid")
            parsed = self.parse_reference(stored_reference)
            size_bytes = int(object_metadata["size-bytes"])
            if size_bytes != int(response["ContentLength"]):
                raise ArtifactIntegrityError("Artifact metadata size is invalid")
            created_at = datetime.fromisoformat(
                str(object_metadata["created-at"]).replace("Z", "+00:00")
            )
            generation = self._generation_from_response(response)
            return ArtifactMetadata(
                reference=stored_reference,
                owner_id=parsed.owner_id,
                demo_id=parsed.demo_id,
                kind=parsed.kind,
                state=parsed.state,
                size_bytes=size_bytes,
                sha256=str(object_metadata["sha256"]),
                generation=generation,
                created_at=created_at,
                content_type=(
                    str(response["ContentType"])
                    if response.get("ContentType") is not None
                    else None
                ),
                policy_version=(
                    str(object_metadata["policy-version"])
                    if object_metadata.get("policy-version") is not None
                    else None
                ),
            )
        except ArtifactStoreError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("Artifact metadata is invalid") from exc

    def _object_key(self, reference: ArtifactReference) -> str:
        relative = "/".join(
            (
                "v1",
                reference.state,
                reference.kind,
                _encode_reference_identity(reference.owner_id),
                _encode_reference_identity(reference.demo_id),
                reference.artifact_id,
            )
        )
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def _reference_from_object_key(self, object_key: str) -> ArtifactReference:
        prefix_segments = self.prefix.split("/") if self.prefix else []
        segments = object_key.split("/")
        expected_length = len(prefix_segments) + 6
        if len(segments) != expected_length or segments[: len(prefix_segments)] != prefix_segments:
            raise ArtifactReferenceError("Artifact object key is invalid")
        offset = len(prefix_segments)
        version, state, kind, owner_token, demo_token, artifact_id = segments[offset:]
        if version != "v1":
            raise ArtifactReferenceError("Artifact object key is invalid")
        parsed = ArtifactReference(
            owner_id=_decode_reference_identity(owner_token),
            demo_id=_decode_reference_identity(demo_token),
            kind=kind,
            state=state,
            artifact_id=artifact_id,
        )
        if self._object_key(parsed) != object_key:
            raise ArtifactReferenceError("Artifact object key is invalid")
        return parsed

    @staticmethod
    def _object_metadata(
        *,
        reference: str,
        sha256: str,
        size_bytes: int,
        created_at: datetime,
        policy_version: str | None = None,
        write_token: str | None = None,
    ) -> dict[str, str]:
        metadata = {
            "artifact-reference": reference,
            "sha256": sha256,
            "size-bytes": str(size_bytes),
            "created-at": created_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if policy_version is not None:
            metadata["policy-version"] = policy_version
        if write_token is not None:
            metadata["write-token"] = write_token
        return metadata

    @staticmethod
    def _generation_from_response(response: Mapping[str, Any]) -> str:
        version_id = response.get("VersionId")
        if version_id not in {None, "", "null"}:
            generation = f"version:{version_id}"
        else:
            etag = str(response.get("ETag", "")).strip().strip('"')
            generation = f"etag:{etag}" if etag else ""
        if (
            not generation
            or len(generation) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in generation)
        ):
            raise ArtifactIntegrityError("Artifact generation is missing")
        return generation

    @staticmethod
    def _conditional_read_args(generation: str) -> dict[str, str]:
        if generation.startswith("version:"):
            version_id = generation[len("version:") :]
            if not version_id:
                raise ArtifactIntegrityError("Artifact generation is invalid")
            return {"VersionId": version_id}
        if generation.startswith("etag:"):
            etag = generation[len("etag:") :]
            if not etag:
                raise ArtifactIntegrityError("Artifact generation is invalid")
            return {"IfMatch": f'"{etag}"'}
        raise ArtifactIntegrityError("Artifact generation is invalid")

    @staticmethod
    def _validate_bucket(bucket: str) -> str:
        value = str(bucket).strip()
        if (
            not 3 <= len(value) <= 63
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]", value)
            is None
        ):
            raise ArtifactStoreError("Object storage bucket is invalid")
        return value

    @staticmethod
    def _validate_prefix(prefix: str) -> str:
        value = str(prefix).strip().strip("/")
        if not value or len(value) > 128:
            raise ArtifactStoreError("Object storage prefix is invalid")
        segments = value.split("/")
        if any(
            segment in {"", ".", ".."}
            or re.fullmatch(r"[A-Za-z0-9._-]+", segment) is None
            for segment in segments
        ):
            raise ArtifactStoreError("Object storage prefix is invalid")
        return value

    @staticmethod
    def _validate_client_capabilities(client: Any) -> None:
        try:
            service_model = client.meta.service_model
            copy_members = service_model.operation_model(
                "CopyObject"
            ).input_shape.members
            put_members = service_model.operation_model("PutObject").input_shape.members
        except Exception as exc:
            raise ArtifactStoreError(
                "S3-compatible storage client capability model is unavailable"
            ) from exc
        if not {"IfNoneMatch", "CopySourceIfMatch"}.issubset(copy_members) or (
            "IfNoneMatch" not in put_members
        ):
            raise ArtifactStoreError(
                "S3-compatible storage client lacks required conditional writes"
            )

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        return S3ArtifactStore._error_code(exc) in {
            "404",
            "NoSuchKey",
            "NotFound",
            "NoSuchVersion",
        }

    @staticmethod
    def _error_code(exc: Exception) -> str | None:
        response = getattr(exc, "response", None)
        if not isinstance(response, Mapping):
            return None
        error = response.get("Error")
        if not isinstance(error, Mapping):
            return None
        code = error.get("Code")
        return str(code) if code is not None else None

    def _delete_operation_owned(
        self,
        reference: ArtifactReference,
        write_token: str,
    ) -> None:
        try:
            response = self.client.head_object(
                Bucket=self.bucket,
                Key=self._object_key(reference),
            )
            metadata = response.get("Metadata")
            if (
                not isinstance(metadata, Mapping)
                or not hmac.compare_digest(
                    str(metadata.get("write-token", "")),
                    write_token,
                )
            ):
                return
            generation = self._generation_from_response(response)
            self._delete_generation(reference, generation)
        except Exception:
            pass

    def _delete_generation(
        self,
        reference: ArtifactReference,
        generation: str,
    ) -> None:
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(reference),
        }
        if generation.startswith("version:"):
            request["VersionId"] = generation[len("version:") :]
        elif generation.startswith("etag:"):
            request["IfMatch"] = f'"{generation[len("etag:") :]}"'
        else:
            return
        try:
            self.client.delete_object(**request)
        except Exception:
            pass
