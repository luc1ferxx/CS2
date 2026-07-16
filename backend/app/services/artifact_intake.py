from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO, Callable, Protocol

from app.services.storage import (
    ArtifactBindingError,
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactReferenceError,
    ArtifactStoreError,
    ArtifactTooLargeError,
)


ARTIFACT_INTAKE_POLICY_VERSION = "artifact_intake_v1"
DEFAULT_MAX_SOURCE_BYTES = 1024 * 1024 * 1024
DEFAULT_MIN_SOURCE_BYTES = 16
DEFAULT_STREAM_CHUNK_BYTES = 1024 * 1024
DEFAULT_QUARANTINE_TTL_SECONDS = 60 * 60

_SAFE_MESSAGES = {
    "INTAKE_TYPE_REJECTED": "Only .dem demo uploads are supported",
    "INTAKE_EMPTY": "Uploaded demo is empty",
    "INTAKE_TRUNCATED": "Uploaded demo is incomplete",
    "INTAKE_TOO_LARGE": "Uploaded demo exceeds the configured size limit",
    "INTAKE_CONTENT_MISMATCH": "Uploaded demo content does not match .dem",
    "INTAKE_INTEGRITY_FAILED": "Uploaded demo failed integrity validation",
    "INTAKE_STORAGE_UNAVAILABLE": "Artifact storage is temporarily unavailable",
    "INTAKE_REJECTED": "Uploaded demo was rejected",
}

_PROHIBITED_CONTENT_TYPES = frozenset(
    {
        "application/gzip",
        "application/java-archive",
        "application/vnd.microsoft.portable-executable",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/x-bzip",
        "application/x-bzip2",
        "application/x-dosexec",
        "application/x-executable",
        "application/x-gzip",
        "application/x-msdownload",
        "application/x-rar-compressed",
        "application/x-tar",
        "application/x-zip-compressed",
        "application/zip",
    }
)
_CONTENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class _ArtifactStore(Protocol):
    def new_reference(
        self,
        *,
        owner_id: str,
        demo_id: str,
        kind: str,
        state: str,
        artifact_id: str | None = None,
    ) -> str: ...

    def parse_reference(self, reference: str) -> Any: ...

    def require_binding(
        self,
        reference: str,
        *,
        owner_id: str,
        demo_id: str,
        kind: str | None = None,
        state: str | None = None,
    ) -> Any: ...

    def write_stream(
        self,
        reference: str,
        stream: BinaryIO,
        *,
        max_bytes: int,
        chunk_size: int,
        content_type: str | None,
        policy_version: str | None,
        now: datetime,
    ) -> ArtifactMetadata: ...

    def promote(
        self,
        quarantine_reference: str,
        *,
        expected_generation: str,
        expected_size: int,
        expected_sha256: str,
        now: datetime,
    ) -> ArtifactMetadata: ...

    def head(self, reference: str) -> ArtifactMetadata | None: ...

    def delete(
        self,
        reference: str,
        *,
        expected_generation: str | None = None,
    ) -> bool: ...

    def cleanup_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> list[str]: ...


class ArtifactIntakeError(ValueError):
    """A stable, user-safe artifact-intake rejection."""

    def __init__(self, code: str):
        if code not in _SAFE_MESSAGES:
            code = "INTAKE_REJECTED"
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class ArtifactIntakePolicy:
    version: str = ARTIFACT_INTAKE_POLICY_VERSION
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    min_source_bytes: int = DEFAULT_MIN_SOURCE_BYTES
    stream_chunk_bytes: int = DEFAULT_STREAM_CHUNK_BYTES
    quarantine_ttl_seconds: int = DEFAULT_QUARANTINE_TTL_SECONDS

    def __post_init__(self) -> None:
        if self.version != ARTIFACT_INTAKE_POLICY_VERSION:
            raise ValueError("Unsupported artifact intake policy")
        if self.min_source_bytes < DEFAULT_MIN_SOURCE_BYTES:
            raise ValueError("Minimum source size cannot weaken the V1 policy")
        if self.max_source_bytes < self.min_source_bytes:
            raise ValueError("Maximum source size is below minimum source size")
        if self.max_source_bytes > DEFAULT_MAX_SOURCE_BYTES:
            raise ValueError("Maximum source size cannot exceed the V1 policy")
        if self.stream_chunk_bytes != DEFAULT_STREAM_CHUNK_BYTES:
            raise ValueError("Artifact intake chunks must be 1 MiB")
        if self.quarantine_ttl_seconds != DEFAULT_QUARANTINE_TTL_SECONDS:
            raise ValueError("Quarantine TTL must be 3600 seconds")


@dataclass(frozen=True)
class AcceptedArtifact:
    policy_version: str
    state: str
    reference: str
    owner_id: str
    demo_id: str
    kind: str
    display_filename: str
    size_bytes: int
    sha256: str
    accepted_at: datetime
    generation: str

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "state": self.state,
            "reference": self.reference,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "acceptedAt": _isoformat_utc(self.accepted_at),
            "generation": self.generation,
        }


class _PrefixCaptureStream:
    """Capture only a bounded prefix while the store owns streamed byte counting."""

    def __init__(self, source: BinaryIO, *, prefix_limit: int = 512):
        self._source = source
        self._prefix_limit = prefix_limit
        self._prefix = bytearray()

    @property
    def prefix(self) -> bytes:
        return bytes(self._prefix)

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        if chunk is None:
            raise ArtifactIntegrityError("Artifact stream ended incompletely")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ArtifactIntegrityError("Artifact stream returned invalid bytes")
        normalized = bytes(chunk)
        remaining = self._prefix_limit - len(self._prefix)
        if remaining > 0 and normalized:
            self._prefix.extend(normalized[:remaining])
        return normalized

    def tell(self) -> int:
        return self._source.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._source.seek(offset, whence)


class ArtifactIntakeService:
    """Byte-policy intake only; this service never parses, persists, or dispatches."""

    def __init__(
        self,
        store: _ArtifactStore,
        *,
        policy: ArtifactIntakePolicy | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.policy = policy or ArtifactIntakePolicy()
        self._now = now or (lambda: datetime.now(timezone.utc))

    def intake_demo(
        self,
        *,
        owner_id: str,
        demo_id: str,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        release_stream_before_promotion: bool = False,
    ) -> AcceptedArtifact:
        display_filename = _normalize_display_filename(filename)
        normalized_content_type = _normalize_content_type(content_type)
        if normalized_content_type in _PROHIBITED_CONTENT_TYPES:
            raise ArtifactIntakeError("INTAKE_CONTENT_MISMATCH")

        operation_now = self._operation_now()
        quarantine_reference: str | None = None
        accepted_reference: str | None = None
        promotion_attempted = False
        completed = False
        written: ArtifactMetadata | None = None
        captured_stream = _PrefixCaptureStream(stream)

        try:
            quarantine_reference = self.store.new_reference(
                owner_id=owner_id,
                demo_id=demo_id,
                kind="source",
                state="quarantine",
            )
            self.store.require_binding(
                quarantine_reference,
                owner_id=owner_id,
                demo_id=demo_id,
                kind="source",
                state="quarantine",
            )
            written = self.store.write_stream(
                quarantine_reference,
                captured_stream,
                max_bytes=self.policy.max_source_bytes,
                chunk_size=self.policy.stream_chunk_bytes,
                content_type=normalized_content_type,
                policy_version=self.policy.version,
                now=operation_now,
            )
            self._verify_quarantine_metadata(
                written,
                reference=quarantine_reference,
                owner_id=owner_id,
                demo_id=demo_id,
            )
            if written.size_bytes == 0:
                raise ArtifactIntakeError("INTAKE_EMPTY")
            if written.size_bytes < self.policy.min_source_bytes:
                raise ArtifactIntakeError("INTAKE_TRUNCATED")
            if _has_incompatible_content_prefix(captured_stream.prefix):
                raise ArtifactIntakeError("INTAKE_CONTENT_MISMATCH")
            if release_stream_before_promotion:
                try:
                    stream.close()
                except Exception:
                    raise ArtifactIntakeError("INTAKE_STORAGE_UNAVAILABLE") from None

            promotion_attempted = True
            promoted = self.store.promote(
                quarantine_reference,
                expected_generation=written.generation,
                expected_size=written.size_bytes,
                expected_sha256=written.sha256,
                now=operation_now,
            )
            accepted_reference = promoted.reference
            found = self.store.head(accepted_reference)
            self._verify_accepted_metadata(
                promoted,
                found=found,
                quarantine_reference=quarantine_reference,
                owner_id=owner_id,
                demo_id=demo_id,
                written=written,
                accepted_at=operation_now,
            )

            result = AcceptedArtifact(
                policy_version=self.policy.version,
                state="accepted",
                reference=promoted.reference,
                owner_id=owner_id,
                demo_id=demo_id,
                kind="source",
                display_filename=display_filename,
                size_bytes=promoted.size_bytes,
                sha256=promoted.sha256,
                accepted_at=promoted.created_at,
                generation=promoted.generation,
            )
            completed = True
            return result
        except ArtifactIntakeError:
            raise
        except ArtifactTooLargeError:
            raise ArtifactIntakeError("INTAKE_TOO_LARGE") from None
        except (
            ArtifactBindingError,
            ArtifactConflictError,
            ArtifactIntegrityError,
            ArtifactNotFoundError,
            ArtifactReferenceError,
        ):
            raise ArtifactIntakeError("INTAKE_INTEGRITY_FAILED") from None
        except ArtifactStoreError:
            raise ArtifactIntakeError("INTAKE_STORAGE_UNAVAILABLE") from None
        except Exception:
            raise ArtifactIntakeError("INTAKE_STORAGE_UNAVAILABLE") from None
        finally:
            if not completed:
                cleanup_references: list[str] = []
                if accepted_reference is not None:
                    cleanup_references.append(accepted_reference)
                if promotion_attempted and quarantine_reference is not None:
                    candidate = self._accepted_candidate(quarantine_reference)
                    if candidate is not None and candidate not in cleanup_references:
                        cleanup_references.append(candidate)
                if quarantine_reference is not None:
                    cleanup_references.append(quarantine_reference)
                for reference in cleanup_references:
                    self._delete_safely(reference)

    def cleanup_abandoned(
        self,
        *,
        now: datetime | None = None,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> int:
        try:
            cleanup_now = _normalize_datetime(now) if now is not None else self._operation_now()
            cutoff = cleanup_now - timedelta(seconds=self.policy.quarantine_ttl_seconds)
            cleaned = self.store.cleanup_quarantine_before(
                cutoff,
                owner_id=owner_id,
                demo_id=demo_id,
            )
            return len(cleaned)
        except ArtifactIntakeError:
            raise
        except Exception:
            raise ArtifactIntakeError("INTAKE_STORAGE_UNAVAILABLE") from None

    def _operation_now(self) -> datetime:
        try:
            return _normalize_datetime(self._now())
        except Exception:
            raise ArtifactIntakeError("INTAKE_REJECTED") from None

    def _verify_quarantine_metadata(
        self,
        metadata: ArtifactMetadata,
        *,
        reference: str,
        owner_id: str,
        demo_id: str,
    ) -> None:
        if (
            metadata.reference != reference
            or metadata.owner_id != owner_id
            or metadata.demo_id != demo_id
            or metadata.kind != "source"
            or metadata.state != "quarantine"
            or metadata.policy_version != self.policy.version
            or metadata.size_bytes < 0
            or metadata.size_bytes > self.policy.max_source_bytes
        ):
            raise ArtifactIntegrityError("Quarantine metadata is invalid")

    def _verify_accepted_metadata(
        self,
        promoted: ArtifactMetadata,
        *,
        found: ArtifactMetadata | None,
        quarantine_reference: str,
        owner_id: str,
        demo_id: str,
        written: ArtifactMetadata,
        accepted_at: datetime,
    ) -> None:
        if found is None:
            raise ArtifactIntegrityError("Accepted artifact was not found")
        quarantine_binding = self.store.require_binding(
            quarantine_reference,
            owner_id=owner_id,
            demo_id=demo_id,
            kind="source",
            state="quarantine",
        )
        accepted_binding = self.store.require_binding(
            promoted.reference,
            owner_id=owner_id,
            demo_id=demo_id,
            kind="source",
            state="accepted",
        )
        if (
            accepted_binding.artifact_id != quarantine_binding.artifact_id
            or promoted != found
            or promoted.owner_id != owner_id
            or promoted.demo_id != demo_id
            or promoted.kind != "source"
            or promoted.state != "accepted"
            or promoted.policy_version != self.policy.version
            or promoted.size_bytes != written.size_bytes
            or not hmac.compare_digest(promoted.sha256, written.sha256)
            or promoted.created_at != accepted_at
            or not promoted.generation
        ):
            raise ArtifactIntegrityError("Accepted artifact metadata is invalid")

    def _accepted_candidate(self, quarantine_reference: str) -> str | None:
        try:
            parsed = self.store.parse_reference(quarantine_reference)
            return self.store.new_reference(
                owner_id=parsed.owner_id,
                demo_id=parsed.demo_id,
                kind=parsed.kind,
                state="accepted",
                artifact_id=parsed.artifact_id,
            )
        except Exception:
            return None

    def _delete_safely(self, reference: str) -> None:
        try:
            self.store.delete(reference)
        except Exception:
            # Failed quarantine deletion remains TTL cleanup work. Storage promotion
            # itself must make a partial accepted target non-consumable.
            pass


def _normalize_display_filename(filename: str) -> str:
    if not isinstance(filename, str):
        raise ArtifactIntakeError("INTAKE_TYPE_REJECTED")
    stripped = filename.strip()
    if (
        not stripped
        or stripped in {".", ".."}
        or "/" in stripped
        or "\\" in stripped
        or _WINDOWS_DRIVE_PATTERN.match(stripped)
        or any(ord(character) < 32 for character in stripped)
        or not stripped.lower().endswith(".dem")
    ):
        raise ArtifactIntakeError("INTAKE_TYPE_REJECTED")

    stem = stripped[:-4]
    if not stem or stem.endswith("."):
        raise ArtifactIntakeError("INTAKE_TYPE_REJECTED")
    normalized_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    if not normalized_stem:
        normalized_stem = "demo"
    normalized_stem = normalized_stem[:250]
    return f"{normalized_stem}{stripped[-4:]}"


def _normalize_content_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    if not isinstance(content_type, str) or any(ord(char) < 32 for char in content_type):
        raise ArtifactIntakeError("INTAKE_CONTENT_MISMATCH")
    base_type = content_type.split(";", 1)[0].strip().lower()
    if not base_type:
        return None
    if not _CONTENT_TYPE_PATTERN.fullmatch(base_type):
        raise ArtifactIntakeError("INTAKE_CONTENT_MISMATCH")
    return base_type


def _has_incompatible_content_prefix(prefix: bytes) -> bool:
    signatures = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
        b"Rar!\x1a\x07",
        b"7z\xbc\xaf\x27\x1c",
        b"\x1f\x8b",
        b"MZ",
        b"\x7fELF",
    )
    if any(prefix.startswith(signature) for signature in signatures):
        return True
    return len(prefix) >= 262 and prefix[257:262] == b"ustar"


def _normalize_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Artifact intake timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    return _normalize_datetime(value).isoformat().replace("+00:00", "Z")
