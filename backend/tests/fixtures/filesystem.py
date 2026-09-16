"""Filesystem capability probes so storage tests state what they assume.

The artifact store is hardened against link and swap attacks, and the tests for
that hardening were written against POSIX primitives that Windows does not
offer: unprivileged symlinks, ``0o600`` mode bits, unlinking an open file, and
nanosecond timestamps. Probing each capability keeps a test honest about which
platform it is proving something on, and lets the directory-link cases run on
Windows through junctions instead of being skipped there.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


def _probe_symlinks() -> bool:
    """Report whether this process may create symlinks.

    Windows gates ``os.symlink`` behind ``SeCreateSymbolicLinkPrivilege`` unless
    Developer Mode is on, so this is a per-machine answer, not a per-OS one.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "symlink-probe-target"
        target.write_bytes(b"probe")
        try:
            (root / "symlink-probe").symlink_to(target)
        except (OSError, NotImplementedError, AttributeError):
            return False
        return True


def _probe_junctions() -> bool:
    """Report whether ``mklink /J`` can create a directory junction."""
    if os.name != "nt":
        return False
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "junction-probe-target"
        target.mkdir()
        return _make_junction(root / "junction-probe", target)


def _probe_posix_mode_bits() -> bool:
    """Report whether ``st_mode`` round-trips the permission bits we request.

    NTFS has no POSIX mode bits; Python reports a synthesised ``0o666`` there no
    matter what mode a file was created with, so an equality assertion on
    ``0o600`` proves nothing about who can read the file.
    """
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "mode-probe"
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        return probe.stat().st_mode & 0o777 == 0o600


def _probe_unlink_of_open_file() -> bool:
    """Report whether an open file can be unlinked or renamed out of the way.

    POSIX unlinks the name and keeps the inode alive for the open descriptor;
    Windows refuses with ``ERROR_SHARING_VIOLATION`` unless the handle was
    opened with ``FILE_SHARE_DELETE``, which ``os.open`` does not request.
    """
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "unlink-probe"
        probe.write_bytes(b"probe")
        descriptor = os.open(probe, os.O_RDONLY)
        try:
            probe.unlink()
        except OSError:
            return False
        finally:
            os.close(descriptor)
        return True


def _make_junction(link: Path, target: Path) -> bool:
    """Create a directory junction, reporting whether it worked.

    A junction is the reparse point an unprivileged Windows process can make, so
    it is the realistic form of the "swap a directory component" attack there.
    """
    try:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and link.exists()


SYMLINKS_AVAILABLE = _probe_symlinks()
JUNCTIONS_AVAILABLE = _probe_junctions()
DIRECTORY_LINKS_AVAILABLE = SYMLINKS_AVAILABLE or JUNCTIONS_AVAILABLE
POSIX_MODE_BITS = _probe_posix_mode_bits()
CAN_UNLINK_OPEN_FILE = _probe_unlink_of_open_file()

requires_symlinks = unittest.skipUnless(
    SYMLINKS_AVAILABLE,
    "Creating symlinks is not permitted for this process",
)
requires_directory_links = unittest.skipUnless(
    DIRECTORY_LINKS_AVAILABLE,
    "Neither symlinks nor directory junctions can be created",
)


def create_directory_link(link: Path, target: Path) -> str:
    """Point ``link`` at directory ``target`` and report the link kind used.

    Prefers a symlink and falls back to a junction, so a caller gets the
    strongest link the platform allows without having to branch on ``os.name``.
    """
    if SYMLINKS_AVAILABLE:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    if JUNCTIONS_AVAILABLE and _make_junction(link, target):
        return "junction"
    raise unittest.SkipTest(f"Cannot link {link} to {target} on this platform")


def assert_private_file_mode(test: unittest.TestCase, path: Path) -> None:
    """Assert ``path`` is owner-only, where the filesystem records that.

    NTFS has no POSIX mode bits -- Python synthesises ``0o666`` no matter what
    mode the file was created with -- so privacy there rests on the ACL the file
    inherits from its parent directory, which this suite cannot read without
    pywin32. Assert the real guarantee where it exists and the weaker structural
    one where it does not, rather than dropping the check on one platform.
    """
    mode = path.stat().st_mode & 0o777
    if POSIX_MODE_BITS:
        test.assertEqual(mode, 0o600)
        return
    test.assertTrue(path.is_file(), f"{path} should be a regular file")
    test.assertFalse(mode & 0o111, f"{path} should not be executable")


def try_replace_with_link(path: Path, target: Path) -> bool:
    """Try to swap ``path`` for a link to ``target``, reporting whether it worked.

    This simulates an attacker swapping a path that a descriptor is already open
    on. POSIX permits the unlink and the open descriptor keeps reading the old
    inode; Windows refuses the unlink outright while a handle is open, which
    enforces the same binding one step earlier. The unlink is therefore always
    attempted, so a ``False`` result is real evidence that the platform blocked
    the swap rather than that this helper declined to try it.
    """
    try:
        path.unlink()
    except OSError:
        return False
    if not SYMLINKS_AVAILABLE:
        raise unittest.SkipTest(
            f"Unlinked {path} but cannot create a link to {target} to swap it for"
        )
    path.symlink_to(target)
    return True


def advance_mtime(path: Path) -> int:
    """Move ``path``'s mtime to the next value the filesystem actually stores.

    ``st_mtime_ns + 1`` is a silent no-op wherever timestamps are coarser than a
    nanosecond -- NTFS keeps 100ns ticks and truncates the write straight back
    to the original value -- which makes a drift check comparing ``st_mtime_ns``
    look broken when it is the perturbation that never landed. Stepping up until
    the stored value moves keeps the smallest change the filesystem can
    represent, on whichever filesystem the test is running.
    """
    original = os.stat(path)
    for delta in (1, 100, 1_000, 1_000_000, 1_000_000_000, 2_000_000_000):
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns + delta))
        current = os.stat(path).st_mtime_ns
        if current != original.st_mtime_ns:
            return current
    raise unittest.SkipTest(f"Filesystem does not record mtime changes for {path}")
