"""Local-disk staging for chunked .dem uploads (S22).

Parts of an unfinished upload session live on the API host's own disk until
`complete` hands them, as one seekable stream, to the normal artifact intake.
This is deliberately not an artifact kind or state: it ignores
ARTIFACT_STORAGE_BACKEND, never touches the artifact-v1 layout, and is not in
the deletion outbox. Callers still reach these bytes only through this module.

Layout under UPLOAD_STAGING_ROOT::

    v1/{b64url(owner_id)}/{session_id}/.session          marker written at creation
    v1/{b64url(owner_id)}/{session_id}/{index:05d}.part  one published part
    v1/{b64url(owner_id)}/{session_id}/.{index}.{uuid}.tmp  part being written
    v1/.purge.{uuid}/ and v1/{owner}/.purge.{uuid}/      directories being purged

Rules this module guarantees:

- The session directory is created only by `create_session`, never by a part
  write. Once a purge has removed it, a late `write_part` raises
  `StagingSessionGone` and nothing is recreated.
- A part is published with a temp file and an atomic replace, so "part i was
  received" is all-or-nothing and a duplicate upload of part i simply replaces
  it. The parts on disk, not the database, say what was received.
- Links are never followed (the directory primitives of the local artifact
  store are reused), and identities are validated before any path is built.
- Purges never raise on I/O: they return False when something was left
  behind, which the hourly sweep (`iter_session_dirs` / `purge_stale_temp`)
  removes later. Malformed owner or session identifiers always raise.

Everything here is process-agnostic filesystem state; the single-process
assumptions (part pool, in-flight counts) belong to the upload session service.
"""

from __future__ import annotations

import bisect
import errno
import hashlib
import hmac
import os
import re
import shutil
import stat
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from app.core.config import settings
from app.services.storage._directory import (
    _DEFAULT_DIRECTORY_BACKEND,
    _ArtifactDirectory,
    _is_reparse_point,
    _PortableArtifactDirectory,
    _PosixArtifactDirectory,
)
from app.services.storage.contract import _decode_reference_identity, _encode_reference_identity
from app.services.storage.errors import ArtifactIntegrityError, ArtifactReferenceError

STAGING_LAYOUT_VERSION = "v1"
# Part indexes are five digits on disk and in the PUT route.
MAX_UPLOAD_PART_INDEX = 99_999

_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_PART_NAME_PATTERN = re.compile(r"^([0-9]{5})\.part$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SESSION_MARKER = ".session"
# Content-free on purpose: the marker exists only for its creation time.
_SESSION_MARKER_CONTENT = b"cs2-upload-staging-v1\n"
_TEMP_SUFFIX = ".tmp"
_TOMBSTONE_PREFIX = ".purge."
_PURGE_ROUNDS = 3
_HASH_CHUNK_BYTES = 1024 * 1024


class StagingError(RuntimeError):
    """Safe staging failure; never carries a local path. Not an ArtifactStoreError."""


class StagingSessionGone(StagingError):
    """The session directory does not exist (never created, purged, or expired)."""


class StagingDigestMismatch(StagingError):
    """The part body does not match the client's X-Part-SHA256."""


class StagingPartInvalid(StagingError, ValueError):
    """A part index, size, body or digest header is malformed."""


class StagingIncomplete(StagingError):
    """Parts are missing (or have the wrong size); `missing` lists their indexes."""

    def __init__(self, missing: list[int] | tuple[int, ...] | range):
        self.missing: tuple[int, ...] = tuple(sorted(set(missing)))
        super().__init__("Upload parts are missing")


@dataclass(frozen=True)
class PartInfo:
    index: int
    size_bytes: int
    sha256: str


class StagingSessionDir(NamedTuple):
    owner_id: str
    session_id: str
    # When create_session wrote the marker (the directory's own mtime changes
    # with every part, so it cannot say how old the session is). A directory
    # without a marker falls back to the directory's mtime.
    created_at: datetime


def upload_part_count(file_size: int, part_size: int) -> int:
    """Number of parts for a file: ceil(file_size / part_size)."""
    if _invalid_positive(file_size) or _invalid_positive(part_size):
        raise StagingPartInvalid("Upload size is invalid")
    count = -(-file_size // part_size)
    if count - 1 > MAX_UPLOAD_PART_INDEX:
        raise StagingPartInvalid("Upload has too many parts")
    return count


def upload_part_size(index: int, *, file_size: int, part_size: int) -> int:
    """Exact byte length of part `index`: part_size, or the remainder for the last part."""
    count = upload_part_count(file_size, part_size)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
        raise StagingPartInvalid("Upload part index is out of range")
    if index < count - 1:
        return part_size
    return file_size - part_size * (count - 1)


def _invalid_positive(value: Any) -> bool:
    return isinstance(value, bool) or not isinstance(value, int) or value < 1


class ConcatPartStream:
    """The session's parts as one read-only, seekable stream, without building a whole file.

    Every part is opened when the stream is created and its size checked then.
    Writers publish by replacing the file, so these descriptors keep reading
    the bytes that were present at open even if a duplicate part lands later:
    the S3 backend's two passes and botocore's checksum pass see identical
    bytes. `read(n)` fills across part boundaries and returns exactly
    min(n, remaining) bytes; seek supports whence 0, 1 and 2 and may be
    repeated any number of times.
    """

    def __init__(self, handles: list[Any], sizes: list[int]):
        if len(handles) != len(sizes) or not sizes:
            raise StagingError("Upload part stream is invalid")
        self._handles = handles
        self._sizes = sizes
        self._starts: list[int] = []
        total = 0
        for size in sizes:
            self._starts.append(total)
            total += size
        self._size = total
        self._position = 0
        self._closed = False

    @property
    def size(self) -> int:
        return self._size

    @property
    def closed(self) -> bool:
        return self._closed

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        self._require_open()
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self._require_open()
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._position + offset
        elif whence == os.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError("Upload part stream seek whence is invalid")
        if target < 0:
            raise OSError(errno.EINVAL, "Upload part stream seek is out of range")
        self._position = target
        return target

    def read(self, size: int | None = -1) -> bytes:
        self._require_open()
        remaining = self._size - self._position
        if remaining <= 0:
            return b""
        wanted = remaining if size is None or size < 0 else min(size, remaining)
        if wanted <= 0:
            return b""
        output = bytearray()
        while len(output) < wanted:
            part = bisect.bisect_right(self._starts, self._position) - 1
            offset = self._position - self._starts[part]
            take = min(wanted - len(output), self._sizes[part] - offset)
            handle = self._handles[part]
            handle.seek(offset)
            chunk = _read_exact(handle, take)
            output += chunk
            self._position += take
        return bytes(output)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in self._handles:
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self) -> ConcatPartStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file.")


def _read_exact(handle: Any, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = handle.read(size - len(chunks))
        if not chunk:
            raise StagingError("Upload part changed while reading")
        chunks += chunk
    return bytes(chunks)


def _replace_leaf(directory: _ArtifactDirectory, source: str, target: str) -> None:
    """os.replace inside one directory handle: atomic, and an earlier `target` is replaced.

    `_ArtifactDirectory.rename_leaf` is os.rename, which refuses an existing
    target on Windows; a re-uploaded part must replace the earlier copy.
    """
    if isinstance(directory, _PosixArtifactDirectory):
        os.replace(source, target, src_dir_fd=directory._fd, dst_dir_fd=directory._fd)
        return
    if isinstance(directory, _PortableArtifactDirectory):
        os.replace(directory._path / source, directory._path / target)
        return
    raise StagingError("Upload staging directory backend is unsupported")


class _UnsafeEntry(Exception):
    """A staging path component exists but is not a plain directory."""


class UploadStagingStore:
    """Upload parts on local disk, one directory per (owner, session)."""

    directory_backend: ClassVar[type[_ArtifactDirectory]] = _DEFAULT_DIRECTORY_BACKEND

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- sessions ---------------------------------------------------------

    def create_session(self, owner_id: str, session_id: str) -> None:
        """Create the session directory (and root/v1/owner as needed).

        Raises StagingError if the session already exists or the disk is
        unusable. Call it before committing the session row, and purge the
        session if that commit fails.
        """
        owner_token = self._owner_token(owner_id)
        name = self._session_name(session_id)
        for attempt in range(2):
            try:
                self._create_session_once(owner_token, name)
                return
            except FileNotFoundError:
                # The empty owner directory was swept between opening it and
                # creating the session inside; create it again once.
                if attempt:
                    raise StagingError("Upload staging is unavailable") from None
            except FileExistsError:
                raise StagingError("Upload staging session already exists") from None
            except StagingError:
                raise
            except (OSError, ArtifactIntegrityError) as exc:
                raise StagingError("Upload staging is unavailable") from exc

    def session_exists(self, owner_id: str, session_id: str) -> bool:
        directory = self._open_session_directory(self._owner_token(owner_id), self._session_name(session_id))
        if directory is None:
            return False
        directory.close()
        return True

    # -- parts ------------------------------------------------------------

    def write_part(
        self,
        owner_id: str,
        session_id: str,
        index: int,
        data: bytes | bytearray | memoryview,
        *,
        expected_size: int,
        expected_sha256: str | None = None,
    ) -> PartInfo:
        """Verify and publish one part, replacing an earlier copy of the same index.

        Raises StagingPartInvalid (index, size or digest header malformed, or
        len(data) != expected_size), StagingDigestMismatch (body differs from
        expected_sha256; nothing is written) or StagingSessionGone (the session
        directory does not exist, including when it was purged mid-write).
        """
        owner_token = self._owner_token(owner_id)
        name = self._session_name(session_id)
        self._validate_index(index)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise StagingPartInvalid("Upload part body is invalid")
        if _invalid_positive(expected_size):
            raise StagingPartInvalid("Upload part size is invalid")
        view = memoryview(data).cast("B")
        if view.nbytes != expected_size:
            raise StagingPartInvalid("Upload part size does not match")
        digest = hashlib.sha256(view).hexdigest()
        if expected_sha256 is not None:
            normalized = str(expected_sha256).strip().lower()
            if not _SHA256_PATTERN.fullmatch(normalized):
                raise StagingPartInvalid("Upload part digest is malformed")
            if not hmac.compare_digest(digest, normalized):
                raise StagingDigestMismatch("Upload part digest does not match")

        directory = self._open_session_directory(owner_token, name)
        if directory is None:
            raise StagingSessionGone("Upload session is gone")
        temp_name = f".{index}.{uuid.uuid4().hex}{_TEMP_SUFFIX}"
        try:
            try:
                file_fd = directory.create_leaf(temp_name)
                with os.fdopen(file_fd, "wb", closefd=True) as handle:
                    handle.write(view)
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_leaf(directory, temp_name, self._part_name(index))
            except FileNotFoundError:
                raise StagingSessionGone("Upload session is gone") from None
            except OSError as exc:
                raise StagingError("Upload part write failed safely") from exc
        finally:
            try:
                directory.unlink_leaf(temp_name)
            except OSError:
                pass
            directory.close()
        # A purge that renamed the directory after it was opened above leaves
        # this part in a tombstone being deleted: report the session as gone.
        if not self.session_exists(owner_id, session_id):
            raise StagingSessionGone("Upload session is gone")
        return PartInfo(index=index, size_bytes=expected_size, sha256=digest)

    def list_parts(self, owner_id: str, session_id: str) -> dict[int, int]:
        """{index: size_bytes} of the published parts; {} when the session directory is gone."""
        directory = self._open_session_directory(self._owner_token(owner_id), self._session_name(session_id))
        if directory is None:
            return {}
        try:
            return self._published_parts(directory)
        except OSError as exc:
            raise StagingError("Upload staging is unavailable") from exc
        finally:
            directory.close()

    def part_sha256(self, owner_id: str, session_id: str, index: int) -> str | None:
        """sha256 of one published part, computed on demand; None when it is missing."""
        owner_token = self._owner_token(owner_id)
        name = self._session_name(session_id)
        self._validate_index(index)
        directory = self._open_session_directory(owner_token, name)
        if directory is None:
            return None
        try:
            try:
                file_fd = directory.open_leaf(self._part_name(index))
            except FileNotFoundError:
                return None
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise StagingError("Upload part is unsafe")
                digest = hashlib.sha256()
                with os.fdopen(file_fd, "rb", closefd=False) as handle:
                    while chunk := handle.read(_HASH_CHUNK_BYTES):
                        digest.update(chunk)
                return digest.hexdigest()
            finally:
                os.close(file_fd)
        except StagingError:
            raise
        except OSError as exc:
            raise StagingError("Upload staging is unavailable") from exc
        finally:
            directory.close()

    def open_concat(
        self,
        owner_id: str,
        session_id: str,
        *,
        part_count: int,
        part_size: int,
        file_size: int,
    ) -> ConcatPartStream:
        """Open all parts as one stream of exactly `file_size` bytes; the caller closes it.

        Raises StagingIncomplete(missing) when any part is absent or has the
        wrong size (extra parts past `part_count` are ignored), and
        StagingSessionGone when the session directory itself is gone (purged,
        or a database restored without the volume): no part can be written
        into it again, so re-uploading the missing parts could never succeed.
        """
        owner_token = self._owner_token(owner_id)
        name = self._session_name(session_id)
        if upload_part_count(file_size, part_size) != part_count:
            raise StagingPartInvalid("Upload part layout is inconsistent")
        expected = [upload_part_size(index, file_size=file_size, part_size=part_size) for index in range(part_count)]
        directory = self._open_session_directory(owner_token, name)
        if directory is None:
            raise StagingSessionGone("Upload session is gone")
        handles: list[Any] = []
        try:
            try:
                present = self._published_parts(directory)
                missing = [index for index, size in enumerate(expected) if present.get(index) != size]
                if missing:
                    raise StagingIncomplete(missing)
                for index, size in enumerate(expected):
                    try:
                        file_fd = directory.open_leaf(self._part_name(index))
                    except FileNotFoundError:
                        raise StagingIncomplete([index]) from None
                    handle = os.fdopen(file_fd, "rb", buffering=0, closefd=True)
                    handles.append(handle)
                    status = os.fstat(handle.fileno())
                    if not stat.S_ISREG(status.st_mode) or status.st_size != size:
                        raise StagingIncomplete([index])
            except OSError as exc:
                raise StagingError("Upload staging is unavailable") from exc
        except BaseException:
            for handle in handles:
                try:
                    handle.close()
                except OSError:
                    pass
            raise
        finally:
            directory.close()
        return ConcatPartStream(handles, expected)

    # -- purge and sweep --------------------------------------------------

    def purge_session(self, owner_id: str, session_id: str) -> bool:
        """Remove one session directory. True when nothing is left; never raises on I/O."""
        owner_token = self._owner_token(owner_id)
        name = self._session_name(session_id)
        try:
            root = self._open_root(create=False)
        except FileNotFoundError:
            return True
        except (OSError, ArtifactIntegrityError):
            return False
        handles = [root]
        try:
            v1 = self._open_child(root, STAGING_LAYOUT_VERSION)
            if v1 is None:
                return True
            handles.append(v1)
            owner = self._open_child(v1, owner_token)
            if owner is None:
                return True
            handles.append(owner)
            return self._purge_entry(owner, name, depth=1)
        except _UnsafeEntry:
            return False
        except OSError:
            return False
        finally:
            for handle in reversed(handles):
                handle.close()

    def purge_owner(self, owner_id: str) -> bool:
        """Remove every session of one owner (account deletion). True when nothing is left."""
        owner_token = self._owner_token(owner_id)
        try:
            root = self._open_root(create=False)
        except FileNotFoundError:
            return True
        except (OSError, ArtifactIntegrityError):
            return False
        handles = [root]
        try:
            v1 = self._open_child(root, STAGING_LAYOUT_VERSION)
            if v1 is None:
                return True
            handles.append(v1)
            return self._purge_entry(v1, owner_token, depth=2)
        except _UnsafeEntry:
            return False
        except OSError:
            return False
        finally:
            for handle in reversed(handles):
                handle.close()

    def iter_session_dirs(self) -> Iterator[StagingSessionDir]:
        """Every well-formed session directory on disk, for the orphan sweep.

        Entries with undecodable owner tokens, malformed session ids, links, or
        purge tombstones are skipped, never reported.
        """
        try:
            root = self._open_root(create=False)
        except FileNotFoundError:
            return
        except (OSError, ArtifactIntegrityError) as exc:
            raise StagingError("Upload staging is unavailable") from exc
        try:
            v1 = self._open_optional(root, STAGING_LAYOUT_VERSION)
            if v1 is None:
                return
            try:
                for owner_token in sorted(v1.iter_names()):
                    if owner_token.startswith("."):
                        continue
                    try:
                        owner_id = _decode_reference_identity(owner_token)
                    except ArtifactReferenceError:
                        continue
                    owner = self._open_optional(v1, owner_token)
                    if owner is None:
                        continue
                    try:
                        found = list(self._iter_owner_sessions(owner, owner_id))
                    finally:
                        owner.close()
                    yield from found
            finally:
                v1.close()
        finally:
            root.close()

    def purge_stale_temp(self, before: datetime) -> int:
        """Remove `.tmp` part files older than `before`, purge tombstones, and empty owner directories.

        Returns how many entries were removed. Never raises on I/O.
        """
        if before.tzinfo is None:
            raise StagingError("Upload staging cutoff must be timezone-aware")
        cutoff = before.timestamp()
        removed = 0
        try:
            root = self._open_root(create=False)
        except (OSError, ArtifactIntegrityError):
            return 0
        try:
            v1 = self._open_optional(root, STAGING_LAYOUT_VERSION)
            if v1 is None:
                return 0
            try:
                for owner_token in sorted(v1.iter_names()):
                    if owner_token.startswith(_TOMBSTONE_PREFIX):
                        removed += int(self._remove_tree(v1, owner_token, depth=2))
                        continue
                    if owner_token.startswith("."):
                        continue
                    owner = self._open_optional(v1, owner_token)
                    if owner is None:
                        continue
                    try:
                        removed += self._sweep_owner(owner, cutoff)
                    finally:
                        owner.close()
                    status = v1.leaf_status(owner_token)
                    if status is not None and status.st_mtime < cutoff and v1.remove_empty_child(owner_token):
                        removed += 1
            finally:
                v1.close()
        except OSError:
            return removed
        finally:
            root.close()
        return removed

    def free_bytes(self) -> int:
        """Free bytes on the staging filesystem (the nearest existing ancestor before first use)."""
        path = Path(os.path.abspath(self.root))
        while True:
            try:
                return int(shutil.disk_usage(path).free)
            except FileNotFoundError:
                parent = path.parent
                if parent == path:
                    raise StagingError("Upload staging free space is unavailable") from None
                path = parent
            except OSError as exc:
                raise StagingError("Upload staging free space is unavailable") from exc

    # -- internals --------------------------------------------------------

    def _create_session_once(self, owner_token: str, name: str) -> None:
        root = self._open_root(create=True)
        handles = [root]
        session: _ArtifactDirectory | None = None
        try:
            v1 = root.open_child(STAGING_LAYOUT_VERSION, create=True)
            handles.append(v1)
            owner = v1.open_child(owner_token, create=True)
            handles.append(owner)
            if owner.leaf_status(name) is not None:
                raise StagingError("Upload staging session already exists")
            session = owner.open_child(name, create=True)
            # O_EXCL on the marker is what makes creation exclusive; when it
            # fails the marker (if any) belongs to another creator, so leave it.
            marker_fd = session.create_leaf(_SESSION_MARKER)
            try:
                with os.fdopen(marker_fd, "wb", closefd=True) as marker:
                    marker.write(_SESSION_MARKER_CONTENT)
                    marker.flush()
                    os.fsync(marker.fileno())
            except BaseException:
                try:
                    session.unlink_leaf(_SESSION_MARKER)
                except OSError:
                    pass
                session.close()
                session = None
                owner.remove_empty_child(name)
                raise
        finally:
            if session is not None:
                session.close()
            for handle in reversed(handles):
                handle.close()

    def _open_root(self, *, create: bool) -> _ArtifactDirectory:
        return self.directory_backend.open_root(self.root, create=create)

    @staticmethod
    def _open_child(parent: _ArtifactDirectory, name: str) -> _ArtifactDirectory | None:
        """None when missing; _UnsafeEntry when it exists but is a link or not a directory."""
        try:
            return parent.open_child(name, create=False)
        except FileNotFoundError:
            return None
        except (OSError, ArtifactIntegrityError) as exc:
            raise _UnsafeEntry from exc

    @staticmethod
    def _open_optional(parent: _ArtifactDirectory, name: str) -> _ArtifactDirectory | None:
        try:
            return parent.open_child(name, create=False)
        except (OSError, ArtifactIntegrityError):
            return None

    def _open_session_directory(self, owner_token: str, name: str) -> _ArtifactDirectory | None:
        """The session directory without creating anything; None when any level is missing."""
        try:
            current = self._open_root(create=False)
        except FileNotFoundError:
            return None
        except (OSError, ArtifactIntegrityError) as exc:
            raise StagingError("Upload staging is unavailable") from exc
        try:
            for segment in (STAGING_LAYOUT_VERSION, owner_token, name):
                child = self._open_child(current, segment)
                current.close()
                if child is None:
                    return None
                current = child
            return current
        except _UnsafeEntry as exc:
            raise StagingError("Upload staging directory is unsafe") from exc
        except BaseException:
            current.close()
            raise

    def _published_parts(self, directory: _ArtifactDirectory) -> dict[int, int]:
        parts: dict[int, int] = {}
        for entry in directory.iter_names():
            match = _PART_NAME_PATTERN.fullmatch(entry)
            if match is None:
                continue
            status = directory.leaf_status(entry)
            if status is None or not stat.S_ISREG(status.st_mode) or _is_reparse_point(status):
                continue
            parts[int(match.group(1))] = status.st_size
        return parts

    def _iter_owner_sessions(self, owner: _ArtifactDirectory, owner_id: str) -> Iterator[StagingSessionDir]:
        for name in sorted(owner.iter_names()):
            if not _SESSION_ID_PATTERN.fullmatch(name):
                continue
            status = owner.leaf_status(name)
            if status is None or not stat.S_ISDIR(status.st_mode) or _is_reparse_point(status):
                continue
            session = self._open_optional(owner, name)
            if session is None:
                continue
            try:
                marker = session.leaf_status(_SESSION_MARKER)
            finally:
                session.close()
            created = marker.st_mtime if marker is not None and stat.S_ISREG(marker.st_mode) else status.st_mtime
            yield StagingSessionDir(owner_id=owner_id, session_id=name, created_at=datetime.fromtimestamp(created, UTC))

    def _sweep_owner(self, owner: _ArtifactDirectory, cutoff: float) -> int:
        removed = 0
        for name in sorted(owner.iter_names()):
            if name.startswith(_TOMBSTONE_PREFIX):
                removed += int(self._remove_tree(owner, name, depth=1))
                continue
            if not _SESSION_ID_PATTERN.fullmatch(name):
                continue
            session = self._open_optional(owner, name)
            if session is None:
                continue
            try:
                for leaf in list(session.iter_names()):
                    if not (leaf.startswith(".") and leaf.endswith(_TEMP_SUFFIX)):
                        continue
                    status = session.leaf_status(leaf)
                    if status is None or stat.S_ISDIR(status.st_mode) or status.st_mtime >= cutoff:
                        continue
                    try:
                        session.unlink_leaf(leaf)
                        removed += 1
                    except OSError:
                        continue
            finally:
                session.close()
        return removed

    def _purge_entry(self, parent: _ArtifactDirectory, name: str, *, depth: int) -> bool:
        """Rename `name` to a tombstone (so it vanishes at once for writers), then delete it."""
        if parent.leaf_status(name) is None:
            return True
        tombstone = f"{_TOMBSTONE_PREFIX}{uuid.uuid4().hex}"
        try:
            parent.rename_leaf(name, tombstone)
            target = tombstone
        except FileNotFoundError:
            return True
        except OSError:
            # Windows refuses to rename a directory with open files inside;
            # delete in place instead and let the sweep finish the rest.
            target = name
        return self._remove_tree(parent, target, depth=depth)

    def _remove_tree(self, parent: _ArtifactDirectory, name: str, *, depth: int) -> bool:
        """Delete a staging directory at most `depth` levels deep, never following links."""
        for _ in range(_PURGE_ROUNDS):
            try:
                child = parent.open_child(name, create=False)
            except FileNotFoundError:
                return True
            except (OSError, ArtifactIntegrityError):
                # A link (or file) where a directory belongs: remove the entry
                # itself, never what it points to.
                try:
                    parent.unlink_leaf(name)
                    return True
                except OSError:
                    return False
            try:
                for entry in list(child.iter_names()):
                    status = child.leaf_status(entry)
                    if status is None:
                        continue
                    if stat.S_ISDIR(status.st_mode) and not _is_reparse_point(status):
                        if depth > 0:
                            self._remove_tree(child, entry, depth=depth - 1)
                        continue
                    try:
                        child.unlink_leaf(entry)
                    except OSError:
                        continue
            except OSError:
                pass
            finally:
                child.close()
            if parent.remove_empty_child(name) or parent.leaf_status(name) is None:
                return True
        return False

    @staticmethod
    def _owner_token(owner_id: str) -> str:
        try:
            return _encode_reference_identity(owner_id)
        except ArtifactReferenceError:
            raise StagingError("Upload owner is invalid") from None

    @staticmethod
    def _session_name(session_id: str) -> str:
        if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise StagingError("Upload session id is invalid")
        return session_id

    @staticmethod
    def _validate_index(index: int) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= MAX_UPLOAD_PART_INDEX:
            raise StagingPartInvalid("Upload part index is invalid")

    @staticmethod
    def _part_name(index: int) -> str:
        return f"{index:05d}.part"


def upload_staging_store_from_settings(*, configured_settings: Any = settings) -> UploadStagingStore:
    return UploadStagingStore(Path(configured_settings.upload_staging_root))
