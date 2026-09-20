"""Directory-handle primitives for the local backend: descriptor-relative, symlink-refusing file operations, with a portable fallback where the platform lacks them."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Self, cast

from app.services.storage.errors import ArtifactIntegrityError, ArtifactStoreError

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

    def _require_same_backend(self, other: _ArtifactDirectory) -> Self:
        if type(other) is not type(self):
            raise ArtifactStoreError("Artifact directory handles are mismatched")
        return cast(Self, other)


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
        destination = self._require_same_backend(self if target is None else target)
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
        destination = self._require_same_backend(self if target is None else target)
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
