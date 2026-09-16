from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import (
    Any,
    BinaryIO,
    ClassVar,
    Protocol,
    runtime_checkable,
)
from urllib.parse import urlparse

from app.core.config import settings


class StorageKeyError(ValueError):
    pass


class StorageWriteError(RuntimeError):
    pass


class ArtifactStoreError(RuntimeError):
    """Safe storage-boundary failure without backend details in its message."""


class ArtifactReferenceError(ArtifactStoreError, ValueError):
    pass


class ArtifactBindingError(ArtifactStoreError):
    pass


class ArtifactNotFoundError(ArtifactStoreError):
    pass


class ArtifactConflictError(ArtifactStoreError):
    pass


class ArtifactIntegrityError(ArtifactStoreError):
    pass


class ArtifactTooLargeError(ArtifactStoreError):
    pass


class ArtifactRangeError(ArtifactStoreError):
    pass


_ARTIFACT_KINDS = frozenset({"source", "replay", "video", "summary"})
_ARTIFACT_STATES = frozenset(
    {"quarantine", "accepted", "rejected", "abandoned", "cleaned"}
)
_ARTIFACT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
ARTIFACT_CLEANUP_BATCH_SIZE = 1000


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


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_O_BINARY = getattr(os, "O_BINARY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NOINHERIT = getattr(os, "O_NOINHERIT", 0)

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC | _O_NOFOLLOW
_LEAF_READ_FLAGS = os.O_RDONLY | _O_BINARY | _O_CLOEXEC | _O_NOFOLLOW | _O_NOINHERIT
_LEAF_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | _O_BINARY
    | _O_CLOEXEC
    | _O_NOFOLLOW
    | _O_NOINHERIT
)

_DIRECTORY_FD_OPERATIONS = (os.link, os.mkdir, os.open, os.rename, os.stat, os.unlink)
SUPPORTS_DIRECTORY_FD = (
    all(operation in os.supports_dir_fd for operation in _DIRECTORY_FD_OPERATIONS)
    and os.scandir in os.supports_fd
    and bool(_O_DIRECTORY)
    and bool(_O_NOFOLLOW)
)


def _is_reparse_point(status: os.stat_result) -> bool:
    """Report Windows reparse points, which ``S_ISLNK`` does not see.

    A directory junction is a reparse point that ``stat.S_ISLNK`` and
    ``Path.is_symlink`` both report as a plain directory, so the mode bits
    alone cannot decide whether a component redirects elsewhere.
    """
    return bool(getattr(status, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _reject_linked_component(status: os.stat_result, path: Path) -> None:
    """Raise the same ``OSError`` POSIX ``O_NOFOLLOW`` raises for a link."""
    if stat.S_ISLNK(status.st_mode) or _is_reparse_point(status):
        raise OSError(errno.ELOOP, "Artifact path component is a link", str(path))


class _ArtifactDirectory:
    """One opened artifact directory; every leaf operation is relative to it.

    Subclasses differ only in how a name is resolved to an object. Callers get
    the same contract either way: links are never followed, directories are
    never silently substituted for files, and a missing name raises
    ``FileNotFoundError``.
    """

    def close(self) -> None:
        raise NotImplementedError

    @classmethod
    def open_root(cls, artifact_root: Path, *, create: bool) -> _ArtifactDirectory:
        raise NotImplementedError

    def open_child(self, name: str, *, create: bool) -> _ArtifactDirectory:
        raise NotImplementedError

    def iter_names(self) -> Iterator[str]:
        raise NotImplementedError

    def leaf_exists(self, name: str) -> bool:
        raise NotImplementedError

    def create_leaf(self, name: str) -> int:
        raise NotImplementedError

    def open_leaf(self, name: str) -> int:
        raise NotImplementedError

    def link_leaf(
        self,
        source_name: str,
        target_name: str,
        *,
        target: _ArtifactDirectory | None = None,
    ) -> None:
        raise NotImplementedError

    def rename_leaf(self, source_name: str, target_name: str) -> None:
        raise NotImplementedError

    def unlink_leaf(self, name: str) -> None:
        raise NotImplementedError

    def open_optional_child(self, name: str) -> _ArtifactDirectory | None:
        try:
            return self.open_child(name, create=False)
        except (OSError, ArtifactIntegrityError):
            return None

    def _require_same_backend(self, other: _ArtifactDirectory) -> None:
        if type(other) is not type(self):
            raise ArtifactStoreError("Artifact directory handles are mismatched")


class _PosixArtifactDirectory(_ArtifactDirectory):
    """Directory pinned by a descriptor, so no later operation can be redirected.

    ``dir_fd`` resolves each name against the inode this handle holds open.
    Swapping a path component after the handle exists cannot retarget a
    subsequent read, link or unlink, which is what closes the TOCTOU window.
    """

    __slots__ = ("_fd",)

    def __init__(self, directory_fd: int):
        self._fd = directory_fd

    @classmethod
    def open_root(cls, artifact_root: Path, *, create: bool) -> _PosixArtifactDirectory:
        if create:
            artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_status = os.lstat(artifact_root)
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise ArtifactIntegrityError("Artifact root is unsafe")
        return cls(os.open(artifact_root, _DIRECTORY_OPEN_FLAGS))

    def close(self) -> None:
        os.close(self._fd)

    def open_child(self, name: str, *, create: bool) -> _PosixArtifactDirectory:
        if create:
            try:
                os.mkdir(name, mode=0o700, dir_fd=self._fd)
            except FileExistsError:
                pass
        child_fd = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=self._fd)
        if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
            os.close(child_fd)
            raise ArtifactIntegrityError("Artifact directory is unsafe")
        return _PosixArtifactDirectory(child_fd)

    def iter_names(self) -> Iterator[str]:
        with os.scandir(self._fd) as entries:
            for entry in entries:
                yield entry.name

    def leaf_exists(self, name: str) -> bool:
        try:
            os.stat(name, dir_fd=self._fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def create_leaf(self, name: str) -> int:
        return os.open(name, _LEAF_CREATE_FLAGS, 0o600, dir_fd=self._fd)

    def open_leaf(self, name: str) -> int:
        return os.open(name, _LEAF_READ_FLAGS, dir_fd=self._fd)

    def link_leaf(
        self,
        source_name: str,
        target_name: str,
        *,
        target: _ArtifactDirectory | None = None,
    ) -> None:
        destination = self if target is None else target
        self._require_same_backend(destination)
        os.link(
            source_name,
            target_name,
            src_dir_fd=self._fd,
            dst_dir_fd=destination._fd,
            follow_symlinks=False,
        )

    def rename_leaf(self, source_name: str, target_name: str) -> None:
        os.rename(source_name, target_name, src_dir_fd=self._fd, dst_dir_fd=self._fd)

    def unlink_leaf(self, name: str) -> None:
        try:
            os.unlink(name, dir_fd=self._fd)
        except FileNotFoundError:
            pass


class _PortableArtifactDirectory(_ArtifactDirectory):
    """Path-resolved directory for platforms without ``dir_fd`` support.

    Windows has no ``dir_fd``, no ``O_NOFOLLOW`` and no ``O_DIRECTORY``, so this
    backend re-resolves the path on every operation and re-rejects links each
    time, including junctions that ``S_ISLNK`` cannot see. Reads additionally
    compare the identity seen before and after opening.

    This is weaker than the POSIX backend by design: an attacker able to write
    into the artifact root can still swap a directory component between two
    operations, which a pinned descriptor would prevent outright. The store is
    only reachable with ``ARTIFACT_STORAGE_BACKEND=local``, which
    ``Settings.validate_runtime_configuration`` rejects in production.
    """

    __slots__ = ("_path",)

    def __init__(self, directory_path: Path):
        self._path = directory_path

    @classmethod
    def open_root(cls, artifact_root: Path, *, create: bool) -> _PortableArtifactDirectory:
        resolved_root = Path(os.path.abspath(artifact_root))
        if create:
            resolved_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_status = os.lstat(resolved_root)
        if (
            stat.S_ISLNK(root_status.st_mode)
            or _is_reparse_point(root_status)
            or not stat.S_ISDIR(root_status.st_mode)
        ):
            raise ArtifactIntegrityError("Artifact root is unsafe")
        return cls(resolved_root)

    def close(self) -> None:
        return None

    def open_child(self, name: str, *, create: bool) -> _PortableArtifactDirectory:
        child_path = self._path / name
        if create:
            try:
                child_path.mkdir(mode=0o700)
            except FileExistsError:
                pass
        child_status = os.lstat(child_path)
        _reject_linked_component(child_status, child_path)
        if not stat.S_ISDIR(child_status.st_mode):
            raise NotADirectoryError(
                errno.ENOTDIR,
                "Artifact path component is not a directory",
                str(child_path),
            )
        return _PortableArtifactDirectory(child_path)

    def iter_names(self) -> Iterator[str]:
        with os.scandir(self._path) as entries:
            for entry in entries:
                yield entry.name

    def leaf_exists(self, name: str) -> bool:
        try:
            os.stat(self._path / name, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def create_leaf(self, name: str) -> int:
        return os.open(self._path / name, _LEAF_CREATE_FLAGS, 0o600)

    def open_leaf(self, name: str) -> int:
        leaf_path = self._path / name
        before = os.lstat(leaf_path)
        _reject_linked_component(before, leaf_path)
        leaf_fd = os.open(leaf_path, _LEAF_READ_FLAGS)
        try:
            opened = os.fstat(leaf_fd)
            if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
                raise OSError(
                    errno.ELOOP,
                    "Artifact leaf was replaced while opening",
                    str(leaf_path),
                )
        except BaseException:
            os.close(leaf_fd)
            raise
        return leaf_fd

    def link_leaf(
        self,
        source_name: str,
        target_name: str,
        *,
        target: _ArtifactDirectory | None = None,
    ) -> None:
        destination = self if target is None else target
        self._require_same_backend(destination)
        source_path = self._path / source_name
        # os.link cannot take follow_symlinks on Windows, so reject links first.
        _reject_linked_component(os.lstat(source_path), source_path)
        os.link(source_path, destination._path / target_name)

    def rename_leaf(self, source_name: str, target_name: str) -> None:
        os.rename(self._path / source_name, self._path / target_name)

    def unlink_leaf(self, name: str) -> None:
        try:
            os.unlink(self._path / name)
        except FileNotFoundError:
            pass


_DEFAULT_DIRECTORY_BACKEND: type[_ArtifactDirectory] = (
    _PosixArtifactDirectory if SUPPORTS_DIRECTORY_FD else _PortableArtifactDirectory
)


class _ArtifactReferenceBoundary:
    def new_reference(
        self,
        *,
        owner_id: str,
        demo_id: str,
        kind: str,
        state: str,
        artifact_id: str | None = None,
    ) -> str:
        return ArtifactReference(
            owner_id=owner_id,
            demo_id=demo_id,
            kind=kind,
            state=state,
            artifact_id=artifact_id or uuid.uuid4().hex,
        ).to_uri()

    def parse_reference(self, reference: str) -> ArtifactReference:
        return ArtifactReference.parse(reference)

    def require_binding(
        self,
        reference: str,
        *,
        owner_id: str,
        demo_id: str,
        kind: str | None = None,
        state: str | None = None,
    ) -> ArtifactReference:
        parsed = self.parse_reference(reference)
        if (
            parsed.owner_id != owner_id
            or parsed.demo_id != demo_id
            or (kind is not None and parsed.kind != kind)
            or (state is not None and parsed.state != state)
        ):
            raise ArtifactBindingError("Artifact binding does not match")
        return parsed

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
                continue
        return removed


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
                import boto3  # type: ignore[import-not-found]
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
        body: BinaryIO = _SeekableStreamWindow(
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


def create_artifact_store(
    *,
    backend: str,
    artifact_root: Path | None = None,
    bucket: str | None = None,
    prefix: str = "cs2-artifacts-v1",
    region: str | None = None,
    endpoint_url: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    client: Any | None = None,
) -> ArtifactStore:
    normalized_backend = str(backend).strip().lower()
    if normalized_backend == "local":
        if artifact_root is None:
            raise ArtifactStoreError("Local artifact storage root is required")
        return LocalArtifactStore(Path(artifact_root))
    if normalized_backend == "s3":
        if not bucket:
            raise ArtifactStoreError("Object storage bucket is required")
        return S3ArtifactStore(
            bucket=bucket,
            prefix=prefix,
            region=region,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            client=client,
        )
    raise ArtifactStoreError("Artifact storage backend is unsupported")


def artifact_store_from_settings(
    *,
    configured_settings: Any = settings,
    client: Any | None = None,
) -> ArtifactStore:
    return create_artifact_store(
        backend=configured_settings.artifact_storage_backend,
        artifact_root=configured_settings.artifact_storage_root,
        bucket=configured_settings.object_storage_bucket,
        prefix=configured_settings.object_storage_prefix,
        region=configured_settings.object_storage_region,
        endpoint_url=configured_settings.object_storage_endpoint_url or None,
        access_key_id=configured_settings.object_storage_access_key_id or None,
        secret_access_key=configured_settings.object_storage_secret_access_key or None,
        client=client,
    )


class LocalStorageService:
    CATEGORIES: ClassVar[set[str]] = {"uploads", "replays", "summaries", "videos"}

    directory_backend: ClassVar[type[_ArtifactDirectory]] = _DEFAULT_DIRECTORY_BACKEND

    def __init__(
        self,
        artifact_root: Path,
        category_roots: Mapping[str, Path] | None = None,
        media_url_base: str = "",
    ):
        self.artifact_root = artifact_root
        self.media_url_base = media_url_base.rstrip("/")
        self.category_roots = {
            category: (category_roots[category] if category_roots and category in category_roots else artifact_root / category)
            for category in self.CATEGORIES
        }

    @classmethod
    def from_settings(cls) -> LocalStorageService:
        return cls(
            settings.artifact_storage_root,
            {
                "uploads": settings.demo_upload_storage_dir,
                "replays": settings.replay_storage_dir,
                "summaries": settings.summary_storage_dir,
                "videos": settings.video_storage_dir,
            },
            settings.media_url_base,
        )

    def key(self, category: str, *segments: str) -> str:
        self._validate_category(category)
        if not segments:
            raise StorageKeyError("Storage key requires at least one path segment")
        safe_segments = [self._validate_segment(segment) for segment in segments]
        return f"local://{category}/{'/'.join(safe_segments)}"

    def demo_upload_key(self, demo_id: str, filename: str) -> str:
        return self.key("uploads", demo_id, filename)

    def replay_key(self, demo_id: str) -> str:
        return self.key("replays", f"{demo_id}.json")

    def summary_key(self, demo_id: str, filename: str) -> str:
        return self.key("summaries", demo_id, filename)

    def video_key(self, demo_id: str, filename: str) -> str:
        return self.key("videos", demo_id, filename)

    def path_for_key(self, storage_key: str) -> Path:
        category, segments = self._parse_key(storage_key)
        root = self.category_roots[category]
        path = root.joinpath(*segments)
        self._ensure_within_root(path, root)
        return path

    def video_path_for_demo(self, demo_id: str, storage_key: str) -> Path | None:
        opened = self.open_video_for_demo(demo_id, storage_key)
        if opened is None:
            return None
        handle, _ = opened
        handle.close()

        try:
            category, segments = self._parse_key(storage_key)
        except StorageKeyError:
            return None
        return self.category_roots[category].joinpath(*segments)

    def open_video_for_demo(
        self,
        demo_id: str,
        storage_key: str,
    ) -> tuple[BinaryIO, os.stat_result] | None:
        try:
            category, segments = self._parse_key(storage_key)
        except StorageKeyError:
            return None
        if category != "videos" or len(segments) != 2 or segments[0] != demo_id:
            return None
        if Path(segments[1]).suffix.lower() != ".mp4":
            return None

        root_directory: _ArtifactDirectory | None = None
        demo_directory: _ArtifactDirectory | None = None
        video_fd: int | None = None
        try:
            root = self.category_roots["videos"].resolve(strict=True)
            root_directory = self.directory_backend.open_root(root, create=False)
            demo_directory = root_directory.open_child(segments[0], create=False)
            video_fd = demo_directory.open_leaf(segments[1])
            stat_result = os.fstat(video_fd)
            if not stat.S_ISREG(stat_result.st_mode):
                return None
            handle = os.fdopen(video_fd, "rb", closefd=True)
            video_fd = None
            return handle, stat_result
        except (OSError, ArtifactStoreError):
            return None
        finally:
            if video_fd is not None:
                os.close(video_fd)
            if demo_directory is not None:
                demo_directory.close()
            if root_directory is not None:
                root_directory.close()

    def replay_key_belongs_to_demo(self, demo_id: str, storage_key: str) -> bool:
        try:
            category, segments = self._parse_key(storage_key)
        except StorageKeyError:
            return False
        return (
            category == "replays"
            and segments == [f"{demo_id}.json"]
            and storage_key == self.replay_key(demo_id)
        )

    def upload_key_belongs_to_demo(self, demo_id: str, storage_key: str) -> bool:
        try:
            category, segments = self._parse_key(storage_key)
        except StorageKeyError:
            return False
        return category == "uploads" and len(segments) == 2 and segments[0] == demo_id

    def exists(self, storage_key: str) -> bool:
        return self.path_for_key(storage_key).exists()

    def read_bytes(self, storage_key: str) -> bytes:
        return self.path_for_key(storage_key).read_bytes()

    def write_bytes(self, storage_key: str, data: bytes) -> Path:
        path = self.path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read_json(self, storage_key: str) -> dict[str, Any]:
        return json.loads(self.path_for_key(storage_key).read_text(encoding="utf-8"))

    def write_json(self, storage_key: str, payload: dict[str, Any]) -> Path:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.write_bytes(storage_key, data)

    async def write_upload_stream(
        self,
        storage_key: str,
        upload: Any,
        *,
        max_bytes: int,
        chunk_size: int,
    ) -> int:
        path = self.path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        size_bytes = 0
        try:
            with path.open("wb") as handle:
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise StorageWriteError("Artifact exceeds configured size limit")
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return size_bytes

    def delete(self, storage_key: str) -> None:
        self.path_for_key(storage_key).unlink(missing_ok=True)

    def media_url(self, storage_key: str) -> str:
        category, segments = self._parse_key(storage_key)
        if category != "videos":
            raise StorageKeyError("Only video artifacts can produce media URLs")
        return self._public_media_url(f"/media/videos/{'/'.join(segments)}")

    def storage_key_from_media_url(self, url: str) -> str:
        prefix = "/media/videos/"
        parsed = urlparse(url)
        path = parsed.path if parsed.scheme and parsed.netloc else url
        if not path.startswith(prefix):
            raise StorageKeyError("videoUrl must use /media/videos/... for local storage")
        segments = [segment for segment in path[len(prefix) :].split("/") if segment]
        return self.key("videos", *segments)

    def media_url_for_local_path(self, local_path: Path) -> str:
        if not local_path.is_absolute():
            raise StorageKeyError("localMediaPath must be /media/videos/... or an absolute path")
        video_root = self.category_roots["videos"]
        try:
            relative_path = local_path.resolve().relative_to(video_root.resolve())
        except ValueError as exc:
            raise StorageKeyError("localMediaPath must be inside video storage") from exc
        return self._public_media_url(f"/media/videos/{relative_path.as_posix()}")

    def _public_media_url(self, path: str) -> str:
        if not self.media_url_base:
            return path
        return f"{self.media_url_base}{path}"

    def _parse_key(self, storage_key: str) -> tuple[str, list[str]]:
        parsed = urlparse(storage_key)
        if (
            parsed.scheme != "local"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise StorageKeyError("Storage key must use local://<category>/...")
        category = parsed.netloc
        self._validate_category(category)
        segments = [self._validate_segment(segment) for segment in parsed.path.split("/") if segment]
        if not segments:
            raise StorageKeyError("Storage key requires at least one path segment")
        return category, segments

    def _validate_category(self, category: str) -> None:
        if category not in self.CATEGORIES:
            raise StorageKeyError(f"Unsupported storage category: {category}")

    def _validate_segment(self, segment: str) -> str:
        value = str(segment).strip()
        if not value or value in {".", ".."}:
            raise StorageKeyError("Storage key path segment cannot be blank or relative")
        if "/" in value or "\\" in value or "\x00" in value:
            raise StorageKeyError("Storage key path segment cannot contain path separators")
        return value

    def _ensure_within_root(self, path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise StorageKeyError("Storage path escaped its configured root") from exc
