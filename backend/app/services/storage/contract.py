"""The provider-neutral artifact contract: logical references, immutable metadata, bounded reads, and the `ArtifactStore` protocol every backend implements."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable
from urllib.parse import urlparse

from app.services.storage.errors import ArtifactIntegrityError, ArtifactReferenceError, ArtifactStoreError

_ARTIFACT_KINDS = frozenset({"source", "replay", "video", "summary"})
_ARTIFACT_STATES = frozenset(
    {"quarantine", "accepted", "rejected", "abandoned", "cleaned"}
)
_ARTIFACT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
ARTIFACT_CLEANUP_BATCH_SIZE = 1000
# Objects one purge call may delete before it reports itself incomplete.
ARTIFACT_PURGE_BATCH_SIZE = 5000


def _encode_reference_identity(value: str) -> str:
    raw = str(value)
    if not raw or len(raw.encode("utf-8")) > 512 or any(ord(char) < 32 for char in raw):
        raise ArtifactReferenceError("Artifact identity is invalid")
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_reference_identity(value: str) -> str:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ArtifactReferenceError("Artifact reference is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactReferenceError("Artifact reference is invalid") from exc
    if _encode_reference_identity(decoded) != value:
        raise ArtifactReferenceError("Artifact reference is invalid")
    return decoded


@dataclass(frozen=True)
class ArtifactReference:
    owner_id: str
    demo_id: str
    kind: str
    state: str
    artifact_id: str

    def __post_init__(self) -> None:
        _encode_reference_identity(self.owner_id)
        _encode_reference_identity(self.demo_id)
        if self.kind not in _ARTIFACT_KINDS:
            raise ArtifactReferenceError("Artifact kind is invalid")
        if self.state not in _ARTIFACT_STATES:
            raise ArtifactReferenceError("Artifact state is invalid")
        if not _ARTIFACT_ID_PATTERN.fullmatch(self.artifact_id):
            raise ArtifactReferenceError("Artifact identifier is invalid")

    def to_uri(self) -> str:
        return "/".join(
            (
                "artifact://v1",
                self.state,
                self.kind,
                _encode_reference_identity(self.owner_id),
                _encode_reference_identity(self.demo_id),
                self.artifact_id,
            )
        )

    @classmethod
    def parse(cls, reference: str) -> ArtifactReference:
        parsed = urlparse(reference)
        if (
            parsed.scheme != "artifact"
            or parsed.netloc != "v1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ArtifactReferenceError("Artifact reference is invalid")
        segments = parsed.path.split("/")
        if len(segments) != 6 or segments[0] != "":
            raise ArtifactReferenceError("Artifact reference is invalid")
        _, state, kind, owner_token, demo_token, artifact_id = segments
        result = cls(
            owner_id=_decode_reference_identity(owner_token),
            demo_id=_decode_reference_identity(demo_token),
            kind=kind,
            state=state,
            artifact_id=artifact_id,
        )
        if result.to_uri() != reference:
            raise ArtifactReferenceError("Artifact reference is invalid")
        return result


@dataclass(frozen=True)
class ArtifactMetadata:
    reference: str
    owner_id: str
    demo_id: str
    kind: str
    state: str
    size_bytes: int
    sha256: str
    generation: str
    created_at: datetime
    content_type: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        parsed = ArtifactReference.parse(self.reference)
        if (
            parsed.owner_id != self.owner_id
            or parsed.demo_id != self.demo_id
            or parsed.kind != self.kind
            or parsed.state != self.state
        ):
            raise ArtifactIntegrityError("Artifact metadata binding is invalid")
        if self.size_bytes < 0:
            raise ArtifactIntegrityError("Artifact metadata size is invalid")
        if not re.fullmatch(r"[a-f0-9]{64}", self.sha256):
            raise ArtifactIntegrityError("Artifact metadata digest is invalid")
        if (
            not self.generation
            or len(self.generation) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in self.generation)
        ):
            raise ArtifactIntegrityError("Artifact metadata generation is invalid")
        if self.created_at.tzinfo is None:
            raise ArtifactIntegrityError("Artifact metadata timestamp is invalid")
        if self.content_type is not None and (
            not self.content_type
            or len(self.content_type) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in self.content_type)
        ):
            raise ArtifactIntegrityError("Artifact metadata content type is invalid")
        if self.policy_version is not None and not re.fullmatch(
            r"[A-Za-z0-9._-]{1,64}", self.policy_version
        ):
            raise ArtifactIntegrityError("Artifact metadata policy version is invalid")

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "ownerId": self.owner_id,
            "demoId": self.demo_id,
            "kind": self.kind,
            "state": self.state,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "generation": self.generation,
            "createdAt": self.created_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "contentType": self.content_type,
            "policyVersion": self.policy_version,
        }


@dataclass(frozen=True)
class ArtifactPurgeResult:
    """What one `purge_prefix` pass saw.

    `found` counts the objects the listing returned inside the cutoff (each of
    them was then deleted); `complete` is False when the batch limit stopped the
    pass before the listing ended, so a caller must run another pass before
    trusting `found == 0`.
    """

    found: int
    deleted: int
    complete: bool = True


def purge_scope_tokens(owner_id: str, demo_id: str | None) -> tuple[str, ...]:
    """The encoded path tokens a purge is confined to: owner, then optionally demo.

    Callers join each token with a trailing separator, so an owner or demo whose
    encoded token merely starts with another one's never matches.
    """
    owner_token = _encode_reference_identity(owner_id)
    if demo_id is None:
        return (owner_token,)
    return (owner_token, _encode_reference_identity(demo_id))


class ArtifactRead:
    """A closeable, length-bounded view of one immutable artifact generation."""

    def __init__(
        self,
        *,
        metadata: ArtifactMetadata,
        handle: BinaryIO,
        start: int,
        end_inclusive: int,
    ):
        self.metadata = metadata
        self._handle = handle
        self.start = start
        self.end_inclusive = end_inclusive
        self.total_size = metadata.size_bytes
        self._remaining = max(0, end_inclusive - start + 1)
        self._closed = False

    @property
    def length(self) -> int:
        return max(0, self.end_inclusive - self.start + 1)

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise ArtifactStoreError("Artifact read is closed")
        if self._remaining == 0:
            return b""
        amount = self._remaining if size is None or size < 0 else min(size, self._remaining)
        data = self._handle.read(amount)
        if not isinstance(data, bytes) or len(data) != amount:
            self.close()
            raise ArtifactIntegrityError("Artifact read was incomplete")
        self._remaining -= len(data)
        return data

    def iter_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ArtifactStoreError("Artifact read chunk size is invalid")
        while self._remaining:
            yield self.read(min(chunk_size, self._remaining))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._handle.close()

    def __enter__(self) -> ArtifactRead:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


@runtime_checkable
class ArtifactStore(Protocol):
    def new_reference(
        self,
        *,
        owner_id: str,
        demo_id: str,
        kind: str,
        state: str,
        artifact_id: str | None = None,
    ) -> str: ...

    def parse_reference(self, reference: str) -> ArtifactReference: ...

    def require_binding(
        self,
        reference: str,
        *,
        owner_id: str,
        demo_id: str,
        kind: str | None = None,
        state: str | None = None,
    ) -> ArtifactReference: ...

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
    ) -> ArtifactMetadata: ...

    def head(self, reference: str) -> ArtifactMetadata | None: ...

    def read_range(
        self,
        reference: str,
        *,
        start: int = 0,
        end_inclusive: int | None = None,
        expected_generation: str | None = None,
    ) -> ArtifactRead: ...

    def promote(
        self,
        quarantine_reference: str,
        *,
        expected_generation: str,
        expected_size: int,
        expected_sha256: str,
        now: datetime | None = None,
    ) -> ArtifactMetadata: ...

    def delete(
        self,
        reference: str,
        *,
        expected_generation: str | None = None,
    ) -> bool: ...

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

    def iter_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> Iterator[ArtifactMetadata]: ...

    def cleanup_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> list[str]: ...

    # Hard deletion. Unlike `delete`, these never compare generations or read
    # metadata first: a purge must also remove objects whose metadata is
    # corrupt, and a store that ignores conditional deletes must not keep them.

    def purge_references(self, references: Iterable[str]) -> int:
        """Delete these exact artifacts unconditionally; returns how many were attempted."""
        ...

    def purge_prefix(
        self,
        *,
        owner_id: str,
        demo_id: str | None = None,
        created_before: datetime | None = None,
        max_objects: int = ARTIFACT_PURGE_BATCH_SIZE,
    ) -> ArtifactPurgeResult:
        """Delete every object of one demo (or, with no demo, one owner) in every state and kind.

        `created_before` keeps objects created at or after it (an owner-wide
        account sweep must not touch uploads of a newer account under the same
        owner id).
        """
        ...
