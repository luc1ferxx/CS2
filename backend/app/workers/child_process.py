"""Spawning and reaping the parse child (app.workers.parse_child).

Shared by the worker's full parse (worker.run_parse_subprocess) and the match
summary backfill's names-only read. The child feeds untrusted bytes to a native
extension, so it gets as little as possible:

* An allow-listed environment: PATH, PYTHONPATH, OPENBLAS_NUM_THREADS, LANG,
  TMPDIR and HOME. Never DATABASE_URL, REDIS_URL, object storage keys, the
  render worker token or Steam/auth secrets. Nothing under app/parser reads
  settings or the environment.
* Its own unprivileged account when the worker runs as root on POSIX
  (PARSE_CHILD_USER, `parser` in the backend image): the workspace is chowned to
  it and the materialized source made readable to its group.

And it leaves evidence behind: stderr goes to a file in the workspace whose last
2 KiB the caller gets back, and on POSIX the child is reaped with os.wait4 so its
peak resident memory (ru_maxrss) is known.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from app.core.config import settings

CHILD_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "PYTHONPATH",
    "OPENBLAS_NUM_THREADS",
    "LANG",
    "TMPDIR",
    "HOME",
)
# Local Windows development only: CPython cannot even initialise its random
# seed without SYSTEMROOT, and TEMP/TMP are where tempfile looks there.
WINDOWS_ENV_ALLOWLIST: tuple[str, ...] = ("SYSTEMROOT", "TEMP", "TMP")
STDERR_TAIL_BYTES = 2048
STDERR_FILENAME = "child-stderr.log"

# Captured at import, before any test patches the module attribute: only a real
# Popen has a pid this process can wait4 on.
_POPEN_TYPE = subprocess.Popen


@dataclass(frozen=True)
class ChildIdentity:
    """The unprivileged account a parse child is switched to."""

    name: str
    uid: int
    gid: int


@dataclass
class ChildUsage:
    """What os.wait4 reported about a reaped child (POSIX only)."""

    peak_rss_kib: int | None = None

    @property
    def peak_rss_mib(self) -> int | None:
        if self.peak_rss_kib is None:
            return None
        return round(self.peak_rss_kib / 1024)


def child_environment(
    package_root: str,
    *,
    base: Mapping[str, str] | None = None,
    home: str | None = None,
) -> dict[str, str]:
    """The parse child's whole environment: the allow-list, nothing inherited beyond it."""
    source = os.environ if base is None else base
    names = CHILD_ENV_ALLOWLIST + (WINDOWS_ENV_ALLOWLIST if sys.platform == "win32" else ())
    environment = {name: source[name] for name in names if name in source}
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{existing_path}" if existing_path else package_root
    )
    # pandas pulls in numpy's OpenBLAS, which reserves buffers for every core
    # at import (1.3 GB of the child's RLIMIT_DATA on a 32-thread host);
    # the parse never uses BLAS. Measured: Mirage peak 4.3 GB -> 2.9 GB.
    environment.setdefault("OPENBLAS_NUM_THREADS", "1")
    if home is not None:
        environment["HOME"] = home
    return environment


def resolve_child_identity(user: str | None = None) -> ChildIdentity | None:
    """The account the parse child runs as, or None to run it as this process.

    None on Windows, when PARSE_CHILD_USER is empty, or when this process is not
    root: only root can switch users, and a non-root worker is already
    unprivileged. Raises RuntimeError when the configured account is missing or
    is root itself, so a misbuilt image fails at worker startup, not per parse.
    """
    name = (settings.parse_child_user if user is None else user).strip()
    if sys.platform == "win32":
        return None
    if not name or os.geteuid() != 0:
        return None
    import pwd

    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise RuntimeError(f"PARSE_CHILD_USER {name!r} does not exist in this image") from None
    if entry.pw_uid == 0:
        raise RuntimeError("PARSE_CHILD_USER must name an unprivileged account, not root")
    return ChildIdentity(name=name, uid=entry.pw_uid, gid=entry.pw_gid)


@contextmanager
def child_workspace(prefix: str, identity: ChildIdentity | None) -> Iterator[Path]:
    """A temporary directory the child may write its output into."""
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        workspace = Path(directory)
        if identity is not None and sys.platform != "win32":
            os.chown(workspace, identity.uid, identity.gid)
            os.chmod(workspace, 0o700)
        yield workspace


def share_private_source(source_path: Path, identity: ChildIdentity | None) -> None:
    """Let the child's group read a privately materialized source demo.

    The storage package materializes every parse's source as this process's own
    copy: a 0600 file in a 0700 directory of its own (storage materialize()).
    That copy is widened to the child's group -- the file to 0640, the directory
    to 0710 (traverse, no listing). Anything not shaped like that private copy is
    left exactly as it is.
    """
    if identity is None or sys.platform == "win32":
        return
    euid = os.geteuid()
    directory = source_path.parent
    try:
        file_stat = os.lstat(source_path)
        directory_stat = os.lstat(directory)
    except OSError:
        # Missing or unreadable: the child reports that itself, classified as
        # STORAGE_READ_FAILED, like any other source it cannot open.
        return
    if not (
        stat.S_ISREG(file_stat.st_mode)
        and stat.S_ISDIR(directory_stat.st_mode)
        and file_stat.st_uid == euid
        and directory_stat.st_uid == euid
        and stat.S_IMODE(file_stat.st_mode) & 0o077 == 0
        and stat.S_IMODE(directory_stat.st_mode) & 0o077 == 0
    ):
        return
    os.chown(source_path, -1, identity.gid)
    os.chmod(source_path, 0o640)
    os.chown(directory, -1, identity.gid)
    os.chmod(directory, 0o710)


@contextmanager
def stderr_capture(workspace: Path) -> Iterator[IO[bytes]]:
    """The child's stderr: a file this process keeps open and later reads by handle.

    Reading through the handle, never the path, means a child that swaps the
    file for a symlink cannot make the worker read anything else.
    """
    with open(workspace / STDERR_FILENAME, "w+b") as handle:
        yield handle


def read_stderr_tail(handle: IO[bytes], limit: int = STDERR_TAIL_BYTES) -> str:
    try:
        handle.flush()
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read(limit).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def spawn_child(
    argv: Sequence[str],
    *,
    package_root: str,
    identity: ChildIdentity | None,
    workspace: Path,
    stderr: IO[bytes],
) -> Any:
    """Start the child with the allow-listed environment, as `identity` when given.

    Fixed argv, no shell: the caller passes only paths it produced and integers.
    """
    extra: dict[str, Any] = {}
    home: str | None = None
    if identity is not None:
        # setgroups([]) too: switching uid/gid alone would keep root's
        # supplementary groups.
        extra = {"user": identity.uid, "group": identity.gid, "extra_groups": []}
        home = str(workspace)
    return subprocess.Popen(
        list(argv),
        env=child_environment(package_root, home=home),
        stderr=stderr,
        **extra,
    )


def wait_for_exit(process: Any, timeout: float, usage: ChildUsage) -> bool:
    """Wait up to `timeout` seconds; True once the child has exited.

    A real POSIX child is reaped here with os.wait4, which hands back its
    resource usage that Popen.wait would reap and discard. The exit status goes
    back onto the Popen (negative signal number, as Popen itself reports), since
    Popen would otherwise read the missing child as a clean exit 0.
    """
    if sys.platform == "win32":
        return _wait_portable(process, timeout)
    if not isinstance(process, _POPEN_TYPE):
        return _wait_portable(process, timeout)
    deadline = time.perf_counter() + timeout
    delay = 0.0005
    while process.returncode is None:
        try:
            pid, status, rusage = os.wait4(process.pid, os.WNOHANG)
        except ChildProcessError:
            # Already reaped elsewhere; whatever Popen recorded stands.
            process.poll()
            return True
        if pid == process.pid:
            process.returncode = os.waitstatus_to_exitcode(status)
            usage.peak_rss_kib = _rss_kib(rusage.ru_maxrss)
            return True
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return False
        delay = min(delay * 2, remaining, 0.05)
        time.sleep(delay)
    return True


def read_child_json(path: Path) -> Any:
    """Load the child's JSON output without following a link it may have planted.

    The child owns its workspace, so the output path is whatever it left there.
    Only a regular, singly linked file is read; anything else raises OSError.
    """
    if sys.platform == "win32":
        return json.loads(path.read_text(encoding="utf-8"))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        file_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise OSError("parse child output is not a regular file")
        return json.loads(handle.read().decode("utf-8"))


def _wait_portable(process: Any, timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _rss_kib(ru_maxrss: int) -> int:
    # Linux reports kilobytes, macOS bytes.
    return ru_maxrss // 1024 if sys.platform == "darwin" else ru_maxrss
