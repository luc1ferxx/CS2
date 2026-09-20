"""The local filesystem artifact store: content-addressed leaves under a private root, quarantine/accepted promotion, and deterministic quarantine cleanup."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

from app.services.storage._boundary import _ArtifactReferenceBoundary
from app.services.storage._directory import _DEFAULT_DIRECTORY_BACKEND, _LEAF_CREATE_FLAGS, _ArtifactDirectory
from app.services.storage.contract import (
    _ARTIFACT_ID_PATTERN,
    _ARTIFACT_KINDS,
    ARTIFACT_CLEANUP_BATCH_SIZE,
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


class LocalArtifactStore(_ArtifactReferenceBoundary):
    """Provider-neutral artifact contract backed by a private local root."""

    directory_backend: ClassVar[type[_ArtifactDirectory]] = _DEFAULT_DIRECTORY_BACKEND

    def __init__(self, artifact_root: Path):
        self.artifact_root = Path(artifact_root)

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
        self._validate_write_limits(max_bytes=max_bytes, chunk_size=chunk_size)
        if expected_size is not None and (expected_size < 0 or expected_size > max_bytes):
            raise ArtifactIntegrityError("Artifact expected size is invalid")
        created_at = self._normalize_now(now)
        generation = uuid.uuid4().hex
        data_name, metadata_name = self._leaf_names(parsed)
        temp_name = f".{parsed.artifact_id}.{uuid.uuid4().hex}.tmp"
        directory = self._open_artifact_directory(parsed, create=True)
        published_data = False
        try:
            if self._leaf_exists(directory, data_name) or self._leaf_exists(
                directory, metadata_name
            ):
                raise ArtifactConflictError("Artifact already exists")
            file_fd = self._create_temp_file(directory, temp_name)
            digest = hashlib.sha256()
            size_bytes = 0
            try:
                with os.fdopen(file_fd, "wb", closefd=True) as handle:
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
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                self._unlink_leaf(directory, temp_name)
                raise
            if expected_size is not None and size_bytes != expected_size:
                self._unlink_leaf(directory, temp_name)
                raise ArtifactIntegrityError("Artifact size does not match")

            self._publish_new_leaf(directory, temp_name, data_name)
            published_data = True
            data_stat = self._stat_regular_leaf(directory, data_name)
            metadata = ArtifactMetadata(
                reference=reference,
                owner_id=parsed.owner_id,
                demo_id=parsed.demo_id,
                kind=parsed.kind,
                state=parsed.state,
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
                generation=generation,
                created_at=created_at,
                content_type=content_type,
                policy_version=policy_version,
            )
            self._write_metadata_leaf(
                directory,
                metadata_name,
                metadata,
                data_stat=data_stat,
            )
            return metadata
        except ArtifactStoreError:
            if published_data:
                self._unlink_leaf(directory, data_name)
            raise
        except Exception as exc:
            if published_data:
                self._unlink_leaf(directory, data_name)
            raise ArtifactStoreError("Artifact write failed safely") from exc
        finally:
            self._unlink_leaf(directory, temp_name)
            directory.close()

    def head(self, reference: str) -> ArtifactMetadata | None:
        parsed = self.parse_reference(reference)
        data_name, metadata_name = self._leaf_names(parsed)
        try:
            directory = self._open_artifact_directory(parsed, create=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ArtifactIntegrityError("Artifact directory is unsafe") from exc
        try:
            try:
                raw_metadata = self._read_small_leaf(directory, metadata_name)
                metadata_payload = json.loads(raw_metadata.decode("utf-8"))
                metadata = self._metadata_from_payload(metadata_payload)
                if metadata.reference != reference:
                    raise ArtifactIntegrityError("Artifact metadata binding is invalid")
                data_stat = self._stat_regular_leaf(directory, data_name)
                identity = metadata_payload.get("fileIdentity")
                if not isinstance(identity, dict) or (
                    identity.get("device") != data_stat.st_dev
                    or identity.get("inode") != data_stat.st_ino
                    or identity.get("mtimeNs") != data_stat.st_mtime_ns
                    or metadata.size_bytes != data_stat.st_size
                ):
                    raise ArtifactIntegrityError("Artifact generation changed")
                return metadata
            except FileNotFoundError:
                return None
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ArtifactIntegrityError("Artifact metadata is invalid") from exc
            except OSError as exc:
                raise ArtifactIntegrityError("Artifact object is unsafe") from exc
        finally:
            directory.close()

    def read_range(
        self,
        reference: str,
        *,
        start: int = 0,
        end_inclusive: int | None = None,
        expected_generation: str | None = None,
    ) -> ArtifactRead:
        parsed = self.parse_reference(reference)
        data_name, metadata_name = self._leaf_names(parsed)
        try:
            directory = self._open_artifact_directory(parsed, create=False)
        except (FileNotFoundError, OSError) as exc:
            raise ArtifactNotFoundError("Artifact was not found") from exc
        data_fd: int | None = None
        try:
            try:
                payload = json.loads(
                    self._read_small_leaf(directory, metadata_name).decode("utf-8")
                )
                metadata = self._metadata_from_payload(payload)
            except FileNotFoundError as exc:
                raise ArtifactNotFoundError("Artifact was not found") from exc
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ArtifactIntegrityError("Artifact metadata is invalid") from exc
            if metadata.reference != reference:
                raise ArtifactIntegrityError("Artifact metadata binding is invalid")
            if expected_generation is not None and metadata.generation != expected_generation:
                raise ArtifactIntegrityError("Artifact generation changed")

            data_fd = directory.open_leaf(data_name)
            data_stat = os.fstat(data_fd)
            identity = payload.get("fileIdentity") if isinstance(payload, dict) else None
            if (
                not stat.S_ISREG(data_stat.st_mode)
                or not isinstance(identity, dict)
                or identity.get("device") != data_stat.st_dev
                or identity.get("inode") != data_stat.st_ino
                or identity.get("mtimeNs") != data_stat.st_mtime_ns
                or metadata.size_bytes != data_stat.st_size
            ):
                raise ArtifactIntegrityError("Artifact generation changed")

            normalized_end = self._normalize_range(
                size_bytes=metadata.size_bytes,
                start=start,
                end_inclusive=end_inclusive,
            )
            os.lseek(data_fd, start, os.SEEK_SET)
            handle = os.fdopen(data_fd, "rb", closefd=True)
            data_fd = None
            return ArtifactRead(
                metadata=metadata,
                handle=handle,
                start=start,
                end_inclusive=normalized_end,
            )
        except ArtifactStoreError:
            raise
        except OSError as exc:
            raise ArtifactIntegrityError("Artifact read failed safely") from exc
        finally:
            if data_fd is not None:
                os.close(data_fd)
            directory.close()

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
        source_data_name, _ = self._leaf_names(source_reference)
        target_data_name, target_metadata_name = self._leaf_names(accepted_reference)
        source_directory = self._open_artifact_directory(source_reference, create=False)
        target_directory = self._open_artifact_directory(accepted_reference, create=True)
        target_published = False
        try:
            if self._leaf_exists(target_directory, target_data_name) or self._leaf_exists(
                target_directory, target_metadata_name
            ):
                raise ArtifactConflictError("Accepted artifact already exists")
            source_stat = self._stat_regular_leaf(source_directory, source_data_name)
            try:
                source_directory.link_leaf(
                    source_data_name,
                    target_data_name,
                    target=target_directory,
                )
            except FileExistsError as exc:
                raise ArtifactConflictError("Accepted artifact already exists") from exc
            target_published = True
            target_stat = self._stat_regular_leaf(target_directory, target_data_name)
            if (
                target_stat.st_dev != source_stat.st_dev
                or target_stat.st_ino != source_stat.st_ino
                or target_stat.st_mtime_ns != source_stat.st_mtime_ns
                or target_stat.st_size != expected_size
            ):
                raise ArtifactIntegrityError("Artifact generation changed during promotion")
            target_digest = self._sha256_regular_leaf(
                target_directory,
                target_data_name,
                expected_size=expected_size,
            )
            if not hmac.compare_digest(target_digest, expected_sha256):
                raise ArtifactIntegrityError("Artifact promotion integrity check failed")
            accepted_metadata = ArtifactMetadata(
                reference=accepted_uri,
                owner_id=source_metadata.owner_id,
                demo_id=source_metadata.demo_id,
                kind=source_metadata.kind,
                state="accepted",
                size_bytes=source_metadata.size_bytes,
                sha256=source_metadata.sha256,
                generation=uuid.uuid4().hex,
                created_at=self._normalize_now(now),
                content_type=source_metadata.content_type,
                policy_version=source_metadata.policy_version,
            )
            self._write_metadata_leaf(
                target_directory,
                target_metadata_name,
                accepted_metadata,
                data_stat=target_stat,
            )
        except Exception:
            if target_published:
                self._unlink_leaf(target_directory, target_metadata_name)
                self._unlink_leaf(target_directory, target_data_name)
            raise
        finally:
            target_directory.close()
            source_directory.close()

        verified = self.head(accepted_uri)
        if verified is None or verified != accepted_metadata:
            self.delete(
                accepted_uri,
                expected_generation=accepted_metadata.generation,
            )
            raise ArtifactIntegrityError("Accepted artifact verification failed")
        try:
            self.delete(quarantine_reference, expected_generation=expected_generation)
        except ArtifactStoreError:
            # The accepted copy is valid; TTL cleanup owns a failed quarantine delete.
            pass
        return verified

    def delete(
        self,
        reference: str,
        *,
        expected_generation: str | None = None,
    ) -> bool:
        parsed = self.parse_reference(reference)
        data_name, metadata_name = self._leaf_names(parsed)
        try:
            directory = self._open_artifact_directory(parsed, create=False)
        except FileNotFoundError:
            return False
        claim_token = uuid.uuid4().hex
        claimed_data_name = f".{data_name}.{claim_token}.delete"
        claimed_metadata_name = f".{metadata_name}.{claim_token}.delete"
        data_claimed = False
        metadata_claimed = False
        try:
            try:
                directory.rename_leaf(metadata_name, claimed_metadata_name)
                metadata_claimed = True
            except FileNotFoundError:
                return False
            try:
                directory.rename_leaf(data_name, claimed_data_name)
                data_claimed = True
            except FileNotFoundError as exc:
                raise ArtifactIntegrityError("Artifact object is incomplete") from exc

            try:
                payload = json.loads(
                    self._read_small_leaf(
                        directory,
                        claimed_metadata_name,
                    ).decode("utf-8")
                )
                metadata = self._metadata_from_payload(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ArtifactIntegrityError("Artifact metadata is invalid") from exc
            if metadata.reference != reference:
                raise ArtifactIntegrityError("Artifact metadata binding is invalid")
            if expected_generation is not None and metadata.generation != expected_generation:
                raise ArtifactIntegrityError("Artifact generation changed")
            data_stat = self._stat_regular_leaf(directory, claimed_data_name)
            identity = payload.get("fileIdentity") if isinstance(payload, dict) else None
            if (
                not isinstance(identity, dict)
                or identity.get("device") != data_stat.st_dev
                or identity.get("inode") != data_stat.st_ino
                or identity.get("mtimeNs") != data_stat.st_mtime_ns
                or metadata.size_bytes != data_stat.st_size
            ):
                raise ArtifactIntegrityError("Artifact generation changed")

            self._unlink_leaf(directory, claimed_metadata_name)
            metadata_claimed = False
            self._unlink_leaf(directory, claimed_data_name)
            data_claimed = False
            return True
        except ArtifactStoreError:
            raise
        except OSError as exc:
            raise ArtifactStoreError("Artifact delete failed safely") from exc
        finally:
            if data_claimed:
                self._restore_claimed_leaf(
                    directory,
                    claimed_data_name,
                    data_name,
                )
            if metadata_claimed:
                self._restore_claimed_leaf(
                    directory,
                    claimed_metadata_name,
                    metadata_name,
                )
            directory.close()

    @contextmanager
    def materialize(
        self,
        reference: str,
        *,
        expected_generation: str,
        expected_size: int,
        expected_sha256: str,
        max_bytes: int,
        suffix: str = ".dem",
    ) -> Iterator[Path]:
        parsed = self.parse_reference(reference)
        if parsed.state != "accepted":
            raise ArtifactBindingError("Only accepted artifacts can be materialized")
        if (
            expected_size < 0
            or expected_size > max_bytes
            or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
            or not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix)
        ):
            raise ArtifactIntegrityError("Artifact materialization metadata is invalid")
        metadata = self.head(reference)
        if metadata is None:
            raise ArtifactNotFoundError("Artifact was not found")
        if (
            metadata.generation != expected_generation
            or metadata.size_bytes != expected_size
            or not hmac.compare_digest(metadata.sha256, expected_sha256)
        ):
            raise ArtifactIntegrityError("Artifact materialization integrity check failed")

        with tempfile.TemporaryDirectory(prefix="cs2-artifact-") as directory:
            directory_path = Path(directory)
            os.chmod(directory_path, 0o700)
            materialized_path = directory_path / f"artifact{suffix}"
            file_fd = os.open(materialized_path, _LEAF_CREATE_FLAGS, 0o600)
            digest = hashlib.sha256()
            size_bytes = 0
            try:
                with os.fdopen(file_fd, "wb", closefd=True) as output:
                    with self.read_range(
                        reference,
                        expected_generation=expected_generation,
                    ) as opened:
                        for chunk in opened.iter_chunks():
                            size_bytes += len(chunk)
                            if size_bytes > max_bytes:
                                raise ArtifactTooLargeError(
                                    "Artifact exceeds materialization size limit"
                                )
                            digest.update(chunk)
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                materialized_path.unlink(missing_ok=True)
                raise
            if (
                size_bytes != expected_size
                or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
            ):
                materialized_path.unlink(missing_ok=True)
                raise ArtifactIntegrityError("Artifact materialization integrity check failed")
            try:
                yield materialized_path
            finally:
                materialized_path.unlink(missing_ok=True)

    def iter_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> Iterator[ArtifactMetadata]:
        normalized_cutoff = self._normalize_now(cutoff)
        try:
            root = self._open_root(create=False)
        except FileNotFoundError:
            return
        try:
            artifact_directory = self._open_child_directory(root, "artifact-v1")
            if artifact_directory is None:
                return
            try:
                quarantine = self._open_child_directory(artifact_directory, "quarantine")
                if quarantine is None:
                    return
                try:
                    for kind in self._iter_directory_names(quarantine):
                        if kind not in _ARTIFACT_KINDS:
                            continue
                        kind_directory = self._open_child_directory(quarantine, kind)
                        if kind_directory is None:
                            continue
                        try:
                            for owner_token in self._iter_directory_names(kind_directory):
                                try:
                                    parsed_owner = _decode_reference_identity(owner_token)
                                except ArtifactReferenceError:
                                    continue
                                if owner_id is not None and parsed_owner != owner_id:
                                    continue
                                owner_directory = self._open_child_directory(
                                    kind_directory,
                                    owner_token,
                                )
                                if owner_directory is None:
                                    continue
                                try:
                                    for demo_token in self._iter_directory_names(owner_directory):
                                        try:
                                            parsed_demo = _decode_reference_identity(demo_token)
                                        except ArtifactReferenceError:
                                            continue
                                        if demo_id is not None and parsed_demo != demo_id:
                                            continue
                                        demo_directory = self._open_child_directory(
                                            owner_directory,
                                            demo_token,
                                        )
                                        if demo_directory is None:
                                            continue
                                        try:
                                            for filename in self._iter_directory_names(
                                                demo_directory
                                            ):
                                                suffix = ".metadata.json"
                                                if not filename.endswith(suffix):
                                                    continue
                                                artifact_id = filename[: -len(suffix)]
                                                if not _ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
                                                    continue
                                                reference = ArtifactReference(
                                                    owner_id=parsed_owner,
                                                    demo_id=parsed_demo,
                                                    kind=kind,
                                                    state="quarantine",
                                                    artifact_id=artifact_id,
                                                ).to_uri()
                                                try:
                                                    metadata = self.head(reference)
                                                except ArtifactStoreError:
                                                    continue
                                                if (
                                                    metadata is not None
                                                    and metadata.created_at < normalized_cutoff
                                                ):
                                                    yield metadata
                                        finally:
                                            demo_directory.close()
                                finally:
                                    owner_directory.close()
                        finally:
                            kind_directory.close()
                finally:
                    quarantine.close()
            finally:
                artifact_directory.close()
        finally:
            root.close()

    def cleanup_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> list[str]:
        removed: list[str] = []
        attempted = 0
        for metadata in self.iter_quarantine_before(
            cutoff,
            owner_id=owner_id,
            demo_id=demo_id,
        ):
            if attempted >= ARTIFACT_CLEANUP_BATCH_SIZE:
                break
            attempted += 1
            try:
                if self.delete(
                    metadata.reference,
                    expected_generation=metadata.generation,
                ):
                    removed.append(metadata.reference)
            except ArtifactStoreError:
                # A replacement or failed delete remains non-accepted for a later pass.
                continue
        return removed

    @staticmethod
    def _normalize_range(
        *,
        size_bytes: int,
        start: int,
        end_inclusive: int | None,
    ) -> int:
        if size_bytes == 0 and start == 0 and end_inclusive is None:
            return -1
        normalized_end = size_bytes - 1 if end_inclusive is None else end_inclusive
        if (
            start < 0
            or start >= size_bytes
            or normalized_end < start
            or normalized_end >= size_bytes
        ):
            raise ArtifactRangeError("Artifact range is invalid")
        return normalized_end

    @staticmethod
    def _validate_write_limits(*, max_bytes: int, chunk_size: int) -> None:
        if max_bytes < 0 or chunk_size <= 0:
            raise ArtifactStoreError("Artifact write limits are invalid")

    @staticmethod
    def _normalize_now(now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        if value.tzinfo is None:
            raise ArtifactStoreError("Artifact timestamp is invalid")
        return value.astimezone(UTC)

    @staticmethod
    def _leaf_names(reference: ArtifactReference) -> tuple[str, str]:
        return f"{reference.artifact_id}.blob", f"{reference.artifact_id}.metadata.json"

    def _relative_directory_parts(self, reference: ArtifactReference) -> tuple[str, ...]:
        return (
            "artifact-v1",
            reference.state,
            reference.kind,
            _encode_reference_identity(reference.owner_id),
            _encode_reference_identity(reference.demo_id),
        )

    def _open_root(self, *, create: bool) -> _ArtifactDirectory:
        return self.directory_backend.open_root(self.artifact_root, create=create)

    def _open_artifact_directory(
        self,
        reference: ArtifactReference,
        *,
        create: bool,
    ) -> _ArtifactDirectory:
        current = self._open_root(create=create)
        try:
            for segment in self._relative_directory_parts(reference):
                child = current.open_child(segment, create=create)
                current.close()
                current = child
            return current
        except Exception:
            current.close()
            raise

    @staticmethod
    def _open_child_directory(
        parent: _ArtifactDirectory,
        name: str,
    ) -> _ArtifactDirectory | None:
        return parent.open_optional_child(name)

    @staticmethod
    def _iter_directory_names(directory: _ArtifactDirectory) -> Iterator[str]:
        return directory.iter_names()

    @staticmethod
    def _leaf_exists(directory: _ArtifactDirectory, name: str) -> bool:
        return directory.leaf_exists(name)

    @staticmethod
    def _create_temp_file(directory: _ArtifactDirectory, name: str) -> int:
        return directory.create_leaf(name)

    @staticmethod
    def _publish_new_leaf(
        directory: _ArtifactDirectory,
        temporary_name: str,
        final_name: str,
    ) -> None:
        try:
            directory.link_leaf(temporary_name, final_name)
        except FileExistsError as exc:
            raise ArtifactConflictError("Artifact already exists") from exc
        finally:
            directory.unlink_leaf(temporary_name)

    @staticmethod
    def _unlink_leaf(directory: _ArtifactDirectory, name: str) -> None:
        directory.unlink_leaf(name)

    @staticmethod
    def _restore_claimed_leaf(
        directory: _ArtifactDirectory,
        claimed_name: str,
        canonical_name: str,
    ) -> None:
        try:
            directory.link_leaf(claimed_name, canonical_name)
        except FileExistsError:
            pass
        except OSError:
            return
        directory.unlink_leaf(claimed_name)

    @staticmethod
    def _stat_regular_leaf(directory: _ArtifactDirectory, name: str) -> os.stat_result:
        file_fd = directory.open_leaf(name)
        try:
            result = os.fstat(file_fd)
            if not stat.S_ISREG(result.st_mode):
                raise ArtifactIntegrityError("Artifact object is unsafe")
            return result
        finally:
            os.close(file_fd)

    @staticmethod
    def _sha256_regular_leaf(
        directory: _ArtifactDirectory,
        name: str,
        *,
        expected_size: int,
    ) -> str:
        file_fd = directory.open_leaf(name)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise ArtifactIntegrityError("Artifact object is unsafe")
            digest = hashlib.sha256()
            size_bytes = 0
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > expected_size:
                        raise ArtifactIntegrityError("Artifact size changed")
                    digest.update(chunk)
            after = os.fstat(file_fd)
            if (
                size_bytes != expected_size
                or after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_mtime_ns != before.st_mtime_ns
                or after.st_size != before.st_size
            ):
                raise ArtifactIntegrityError("Artifact generation changed")
            return digest.hexdigest()
        finally:
            os.close(file_fd)

    def _write_metadata_leaf(
        self,
        directory: _ArtifactDirectory,
        metadata_name: str,
        metadata: ArtifactMetadata,
        *,
        data_stat: os.stat_result,
    ) -> None:
        payload = metadata.as_snapshot()
        payload["fileIdentity"] = {
            "device": data_stat.st_dev,
            "inode": data_stat.st_ino,
            "mtimeNs": data_stat.st_mtime_ns,
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        temp_name = f".{metadata_name}.{uuid.uuid4().hex}.tmp"
        file_fd = self._create_temp_file(directory, temp_name)
        try:
            with os.fdopen(file_fd, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._publish_new_leaf(directory, temp_name, metadata_name)
        finally:
            self._unlink_leaf(directory, temp_name)

    @staticmethod
    def _read_small_leaf(directory: _ArtifactDirectory, name: str) -> bytes:
        file_fd = directory.open_leaf(name)
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 64 * 1024:
                raise ArtifactIntegrityError("Artifact metadata is invalid")
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                return handle.read(64 * 1024 + 1)
        finally:
            os.close(file_fd)

    @staticmethod
    def _metadata_from_payload(payload: Any) -> ArtifactMetadata:
        if not isinstance(payload, dict):
            raise ArtifactIntegrityError("Artifact metadata is invalid")
        created_at = datetime.fromisoformat(str(payload["createdAt"]).replace("Z", "+00:00"))
        return ArtifactMetadata(
            reference=str(payload["reference"]),
            owner_id=str(payload["ownerId"]),
            demo_id=str(payload["demoId"]),
            kind=str(payload["kind"]),
            state=str(payload["state"]),
            size_bytes=int(payload["sizeBytes"]),
            sha256=str(payload["sha256"]),
            generation=str(payload["generation"]),
            created_at=created_at,
            content_type=(
                str(payload["contentType"])
                if payload.get("contentType") is not None
                else None
            ),
            policy_version=(
                str(payload["policyVersion"])
                if payload.get("policyVersion") is not None
                else None
            ),
        )
