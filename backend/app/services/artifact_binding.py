from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.services.storage import ArtifactMetadata, ArtifactReference

ACCEPTED_ARTIFACT_POLICY_VERSION = "artifact_intake_v1"
_SNAPSHOT_FIELDS = frozenset(
    {
        "policyVersion",
        "state",
        "reference",
        "sizeBytes",
        "sha256",
        "acceptedAt",
        "generation",
    }
)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

_SAFE_MESSAGES = {
    "ARTIFACT_SNAPSHOT_INVALID": "Accepted artifact metadata is invalid",
    "ARTIFACT_BINDING_INVALID": "Accepted artifact binding is invalid",
    "ARTIFACT_NOT_AVAILABLE": "Accepted artifact is not available",
    "ARTIFACT_INTEGRITY_FAILED": "Accepted artifact integrity verification failed",
    "ARTIFACT_TOO_LARGE": "Accepted artifact exceeds the configured size limit",
    "ARTIFACT_CONTENT_INVALID": "Accepted artifact content is invalid",
    "ARTIFACT_RANGE_INVALID": "Accepted artifact range is invalid",
}


class AcceptedArtifactError(RuntimeError):
    """Stable, provider-neutral failure safe for API/service boundary mapping."""

    def __init__(self, code: str):
        if code not in _SAFE_MESSAGES:
            code = "ARTIFACT_NOT_AVAILABLE"
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


class ArtifactReadProtocol(Protocol):
    metadata: ArtifactMetadata
    start: int
    end_inclusive: int
    total_size: int

    @property
    def length(self) -> int: ...

    def iter_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...

    def close(self) -> None: ...

    def __enter__(self) -> ArtifactReadProtocol: ...

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any: ...


class ArtifactStoreProtocol(Protocol):
    def require_binding(
        self,
        reference: str,
        *,
        owner_id: str,
        demo_id: str,
        kind: str | None = None,
        state: str | None = None,
    ) -> ArtifactReference: ...

    def head(self, reference: str) -> ArtifactMetadata | None: ...

    def read_range(
        self,
        reference: str,
        *,
        start: int = 0,
        end_inclusive: int | None = None,
        expected_generation: str | None = None,
    ) -> ArtifactReadProtocol: ...

    def materialize(
        self,
        reference: str,
        *,
        expected_generation: str,
        expected_size: int,
        expected_sha256: str,
        max_bytes: int,
        suffix: str = ".dem",
    ) -> AbstractContextManager[Path]: ...


def _canonical_utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reject_json_constant(_: str) -> Any:
    raise ValueError("non-finite JSON value")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID") from None
    if parsed.tzinfo is None:
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
    return parsed.astimezone(UTC)


def _validate_generation(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
    return value


@dataclass(frozen=True)
class AcceptedArtifactSnapshot:
    reference: str
    size_bytes: int
    sha256: str
    accepted_at: datetime
    generation: str
    policy_version: str = ACCEPTED_ARTIFACT_POLICY_VERSION
    state: str = "accepted"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | Any) -> AcceptedArtifactSnapshot:
        if not isinstance(payload, Mapping):
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
        try:
            if frozenset(payload.keys()) != _SNAPSHOT_FIELDS:
                raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
            policy_version = payload["policyVersion"]
            state = payload["state"]
            reference = payload["reference"]
            size_bytes = payload["sizeBytes"]
            sha256 = payload["sha256"]
            accepted_at = payload["acceptedAt"]
            generation = payload["generation"]
        except AcceptedArtifactError:
            raise
        except Exception:
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID") from None

        if policy_version != ACCEPTED_ARTIFACT_POLICY_VERSION or state != "accepted":
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
        if not isinstance(reference, str) or len(reference) > 4096:
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
        try:
            parsed_reference = ArtifactReference.parse(reference)
        except Exception:
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID") from None
        if parsed_reference.state != "accepted":
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
        if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")

        return cls(
            reference=reference,
            size_bytes=size_bytes,
            sha256=sha256,
            accepted_at=_parse_timestamp(accepted_at),
            generation=_validate_generation(generation),
        )

    @classmethod
    def from_metadata(cls, metadata: ArtifactMetadata | Any) -> AcceptedArtifactSnapshot:
        try:
            return cls.from_mapping(
                {
                    "policyVersion": ACCEPTED_ARTIFACT_POLICY_VERSION,
                    "state": "accepted",
                    "reference": metadata.reference,
                    "sizeBytes": metadata.size_bytes,
                    "sha256": metadata.sha256,
                    "acceptedAt": _canonical_utc(metadata.created_at),
                    "generation": metadata.generation,
                }
            )
        except AcceptedArtifactError:
            raise
        except Exception:
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED") from None

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "state": self.state,
            "reference": self.reference,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "acceptedAt": _canonical_utc(self.accepted_at),
            "generation": self.generation,
        }


@dataclass(frozen=True)
class VerifiedAcceptedArtifact:
    metadata: ArtifactMetadata
    snapshot: AcceptedArtifactSnapshot


def parse_source_artifact_snapshot(
    payload: Mapping[str, Any] | Any,
) -> AcceptedArtifactSnapshot:
    snapshot = AcceptedArtifactSnapshot.from_mapping(payload)
    try:
        parsed = ArtifactReference.parse(snapshot.reference)
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID") from None
    if parsed.kind != "source" or parsed.state != "accepted":
        raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
    return snapshot


def _validate_limit(max_bytes: int | None) -> None:
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0
    ):
        raise AcceptedArtifactError("ARTIFACT_BINDING_INVALID")


def _metadata_snapshot(metadata: ArtifactMetadata | Any) -> AcceptedArtifactSnapshot:
    try:
        return AcceptedArtifactSnapshot.from_metadata(metadata)
    except AcceptedArtifactError as exc:
        if exc.code == "ARTIFACT_SNAPSHOT_INVALID":
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED") from None
        raise


def _metadata_matches_binding(
    metadata: ArtifactMetadata | Any,
    *,
    reference: str,
    owner_id: str,
    demo_id: str,
    kind: str,
) -> bool:
    try:
        return (
            metadata.reference == reference
            and metadata.owner_id == owner_id
            and metadata.demo_id == demo_id
            and metadata.kind == kind
            and metadata.state == "accepted"
        )
    except Exception:
        return False


def verify_accepted_artifact(
    store: ArtifactStoreProtocol,
    reference: str,
    *,
    owner_id: str,
    demo_id: str,
    kind: str,
    snapshot: AcceptedArtifactSnapshot | Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
) -> VerifiedAcceptedArtifact:
    """Bind an accepted reference to immutable metadata before any byte read."""

    _validate_limit(max_bytes)
    if not all(isinstance(value, str) and value for value in (reference, owner_id, demo_id, kind)):
        raise AcceptedArtifactError("ARTIFACT_BINDING_INVALID")
    try:
        parsed = store.require_binding(
            reference,
            owner_id=owner_id,
            demo_id=demo_id,
            kind=kind,
            state="accepted",
        )
        if (
            parsed.owner_id != owner_id
            or parsed.demo_id != demo_id
            or parsed.kind != kind
            or parsed.state != "accepted"
            or parsed.to_uri() != reference
        ):
            raise ValueError
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_BINDING_INVALID") from None

    supplied_snapshot: AcceptedArtifactSnapshot | None = None
    if snapshot is not None:
        if isinstance(snapshot, AcceptedArtifactSnapshot):
            supplied_snapshot = AcceptedArtifactSnapshot.from_mapping(snapshot.as_dict())
        else:
            supplied_snapshot = AcceptedArtifactSnapshot.from_mapping(snapshot)

    try:
        metadata = store.head(reference)
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None
    if metadata is None:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE")
    if not _metadata_matches_binding(
        metadata,
        reference=reference,
        owner_id=owner_id,
        demo_id=demo_id,
        kind=kind,
    ):
        raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")

    current_snapshot = _metadata_snapshot(metadata)
    if max_bytes is not None and current_snapshot.size_bytes > max_bytes:
        raise AcceptedArtifactError("ARTIFACT_TOO_LARGE")
    if supplied_snapshot is not None:
        if (
            supplied_snapshot.reference != reference
            or supplied_snapshot.size_bytes != current_snapshot.size_bytes
            or not hmac.compare_digest(supplied_snapshot.sha256, current_snapshot.sha256)
            or supplied_snapshot.generation != current_snapshot.generation
            or supplied_snapshot.accepted_at != current_snapshot.accepted_at
        ):
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")

    return VerifiedAcceptedArtifact(metadata=metadata, snapshot=current_snapshot)


def _read_exact_artifact(
    opened: ArtifactReadProtocol,
    *,
    expected: VerifiedAcceptedArtifact,
    chunk_size: int,
) -> bytes:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise AcceptedArtifactError("ARTIFACT_BINDING_INVALID")
    if not _metadata_matches_binding(
        opened.metadata,
        reference=expected.snapshot.reference,
        owner_id=expected.metadata.owner_id,
        demo_id=expected.metadata.demo_id,
        kind=expected.metadata.kind,
    ):
        raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
    opened_snapshot = _metadata_snapshot(opened.metadata)
    if opened_snapshot != expected.snapshot:
        raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")

    digest = hashlib.sha256()
    result = bytearray()
    try:
        for chunk in opened.iter_chunks(chunk_size):
            if not isinstance(chunk, bytes) or not chunk:
                raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
            if len(result) + len(chunk) > expected.snapshot.size_bytes:
                raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
            digest.update(chunk)
            result.extend(chunk)
    except AcceptedArtifactError:
        raise
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None
    if len(result) != expected.snapshot.size_bytes or not hmac.compare_digest(
        digest.hexdigest(), expected.snapshot.sha256
    ):
        raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
    return bytes(result)


def read_accepted_replay_json(
    store: ArtifactStoreProtocol,
    reference: str,
    *,
    owner_id: str,
    demo_id: str,
    snapshot: AcceptedArtifactSnapshot | Mapping[str, Any] | None = None,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> dict[str, Any]:
    verified = verify_accepted_artifact(
        store,
        reference,
        owner_id=owner_id,
        demo_id=demo_id,
        kind="replay",
        snapshot=snapshot,
        max_bytes=max_bytes,
    )
    if verified.snapshot.size_bytes == 0:
        raise AcceptedArtifactError("ARTIFACT_CONTENT_INVALID")
    try:
        opened = store.read_range(
            reference,
            start=0,
            end_inclusive=verified.snapshot.size_bytes - 1,
            expected_generation=verified.snapshot.generation,
        )
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None
    try:
        with opened:
            encoded = _read_exact_artifact(
                opened,
                expected=verified,
                chunk_size=chunk_size,
            )
    except AcceptedArtifactError:
        raise
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise AcceptedArtifactError("ARTIFACT_CONTENT_INVALID") from None
    if not isinstance(payload, dict) or payload.get("demoId") != demo_id:
        raise AcceptedArtifactError("ARTIFACT_CONTENT_INVALID")
    return payload


def head_accepted_video(
    store: ArtifactStoreProtocol,
    reference: str,
    *,
    owner_id: str,
    demo_id: str,
    snapshot: AcceptedArtifactSnapshot | Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
) -> VerifiedAcceptedArtifact:
    return verify_accepted_artifact(
        store,
        reference,
        owner_id=owner_id,
        demo_id=demo_id,
        kind="video",
        snapshot=snapshot,
        max_bytes=max_bytes,
    )


def _verified_video_matches(
    verified: VerifiedAcceptedArtifact,
    *,
    reference: str,
    owner_id: str,
    demo_id: str,
    max_bytes: int | None,
) -> bool:
    if not isinstance(verified, VerifiedAcceptedArtifact):
        return False
    return (
        _metadata_matches_binding(
            verified.metadata,
            reference=reference,
            owner_id=owner_id,
            demo_id=demo_id,
            kind="video",
        )
        and verified.snapshot == _metadata_snapshot(verified.metadata)
        and (max_bytes is None or verified.snapshot.size_bytes <= max_bytes)
    )


def open_accepted_video_range(
    store: ArtifactStoreProtocol,
    reference: str,
    *,
    owner_id: str,
    demo_id: str,
    start: int = 0,
    end_inclusive: int | None = None,
    snapshot: AcceptedArtifactSnapshot | Mapping[str, Any] | None = None,
    verified: VerifiedAcceptedArtifact | None = None,
    max_bytes: int | None = None,
) -> ArtifactReadProtocol:
    _validate_limit(max_bytes)
    pinned = verified or head_accepted_video(
        store,
        reference,
        owner_id=owner_id,
        demo_id=demo_id,
        snapshot=snapshot,
        max_bytes=max_bytes,
    )
    try:
        if not _verified_video_matches(
            pinned,
            reference=reference,
            owner_id=owner_id,
            demo_id=demo_id,
            max_bytes=max_bytes,
        ):
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
    except AcceptedArtifactError:
        raise
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED") from None

    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or start < 0
        or start >= pinned.snapshot.size_bytes
        or (
            end_inclusive is not None
            and (
                isinstance(end_inclusive, bool)
                or not isinstance(end_inclusive, int)
                or end_inclusive < start
                or end_inclusive >= pinned.snapshot.size_bytes
            )
        )
    ):
        raise AcceptedArtifactError("ARTIFACT_RANGE_INVALID")
    normalized_end = (
        pinned.snapshot.size_bytes - 1 if end_inclusive is None else end_inclusive
    )
    try:
        opened = store.read_range(
            reference,
            start=start,
            end_inclusive=normalized_end,
            expected_generation=pinned.snapshot.generation,
        )
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None
    try:
        if _metadata_snapshot(opened.metadata) != pinned.snapshot:
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
        if (
            opened.start != start
            or opened.end_inclusive != normalized_end
            or opened.total_size != pinned.snapshot.size_bytes
            or opened.length != normalized_end - start + 1
        ):
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
    except Exception as exc:
        try:
            opened.close()
        except Exception:
            pass
        if isinstance(exc, AcceptedArtifactError):
            raise
        raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED") from None
    return opened


@contextmanager
def materialize_accepted_demo(
    store: ArtifactStoreProtocol,
    reference: str,
    *,
    owner_id: str,
    demo_id: str,
    snapshot: AcceptedArtifactSnapshot | Mapping[str, Any],
    max_bytes: int,
) -> Iterator[Path]:
    verified = verify_accepted_artifact(
        store,
        reference,
        owner_id=owner_id,
        demo_id=demo_id,
        kind="source",
        snapshot=snapshot,
        max_bytes=max_bytes,
    )
    try:
        materialized = store.materialize(
            reference,
            expected_generation=verified.snapshot.generation,
            expected_size=verified.snapshot.size_bytes,
            expected_sha256=verified.snapshot.sha256,
            max_bytes=max_bytes,
            suffix=".dem",
        )
    except Exception:
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None

    consumer_failure: BaseException | None = None
    try:
        with materialized as raw_path:
            try:
                path = Path(raw_path)
                file_stat = path.lstat()
                if (
                    path.suffix != ".dem"
                    or path.is_symlink()
                    or not path.is_file()
                    or file_stat.st_size != verified.snapshot.size_bytes
                    or file_stat.st_size > max_bytes
                ):
                    raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
            except AcceptedArtifactError:
                raise
            except Exception:
                raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED") from None
            try:
                yield path
            except BaseException as exc:
                consumer_failure = exc
                raise
    except BaseException as exc:
        if exc is consumer_failure or isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, AcceptedArtifactError):
            raise
        raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE") from None
