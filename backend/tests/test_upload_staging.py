"""UploadStagingStore and ConcatPartStream (S22 chunked uploads), against a temp directory.

Every behaviour runs on the portable directory backend (the one Windows uses)
and, where the platform supports descriptor-relative calls, on the POSIX
backend production uses.
"""

import hashlib
import os
import random
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

from app.core.config import Settings
from app.services.artifact_intake import ArtifactIntakeService, _PrefixCaptureStream
from app.services.storage import (
    MAX_UPLOAD_PART_INDEX,
    SUPPORTS_DIRECTORY_FD,
    ConcatPartStream,
    LocalArtifactStore,
    PartInfo,
    StagingDigestMismatch,
    StagingError,
    StagingIncomplete,
    StagingPartInvalid,
    StagingSessionDir,
    StagingSessionGone,
    UploadStagingStore,
    _PortableArtifactDirectory,
    _PosixArtifactDirectory,
    upload_part_count,
    upload_part_size,
    upload_staging_store_from_settings,
)
from app.services.storage import staging as staging_module
from app.services.storage._directory import _ArtifactDirectory
from app.services.storage.contract import _encode_reference_identity
from app.services.storage.s3 import _SeekableStreamWindow

OWNER = "owner-a"
SESSION = "0123456789abcdef0123456789abcdef"
OTHER_SESSION = "fedcba9876543210fedcba9876543210"
PART = 4096


def _payload(size: int, seed: int = 7) -> bytes:
    generator = random.Random(seed)
    return bytes(generator.getrandbits(8) for _ in range(size))


def _split(data: bytes, part_size: int) -> list[bytes]:
    return [data[offset : offset + part_size] for offset in range(0, len(data), part_size)]


def _tree(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


class _StagingTestBase(unittest.TestCase):
    backend: ClassVar[type[_ArtifactDirectory]] = _PortableArtifactDirectory

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "staging"
        self.store = UploadStagingStore(self.root)
        self.store.directory_backend = self.backend  # type: ignore[misc]

    def session_dir(self, owner: str = OWNER, session: str = SESSION) -> Path:
        return self.root / "v1" / _encode_reference_identity(owner) / session

    def write_all(self, data: bytes, *, owner: str = OWNER, session: str = SESSION, order: list[int] | None = None) -> int:
        parts = _split(data, PART)
        for index in order if order is not None else range(len(parts)):
            self.store.write_part(owner, session, index, parts[index], expected_size=len(parts[index]))
        return len(parts)


class StagingSessionLifecycleMixin:
    def test_create_session_builds_the_layout_with_a_marker(self) -> None:
        self.assertFalse(self.root.exists())
        self.store.create_session(OWNER, SESSION)

        self.assertTrue(self.session_dir().is_dir())
        self.assertEqual(_tree(self.session_dir()), [".session"])
        self.assertTrue(self.store.session_exists(OWNER, SESSION))
        self.assertFalse(self.store.session_exists(OWNER, OTHER_SESSION))
        self.assertEqual(self.store.list_parts(OWNER, SESSION), {})

    def test_create_session_is_exclusive_and_validates_identities(self) -> None:
        self.store.create_session(OWNER, SESSION)
        with self.assertRaisesRegex(StagingError, "already exists"):
            self.store.create_session(OWNER, SESSION)
        for owner, session in (
            ("", SESSION),
            ("bad\nowner", SESSION),
            (OWNER, "ABCDEF0123456789ABCDEF0123456789"),
            (OWNER, "../" + SESSION[3:]),
            (OWNER, SESSION[:-1]),
        ):
            with self.subTest(owner=owner, session=session), self.assertRaises(StagingError):
                self.store.create_session(owner, session)

    def test_write_part_never_creates_directories(self) -> None:
        with self.assertRaises(StagingSessionGone):
            self.store.write_part(OWNER, SESSION, 0, b"x" * 32, expected_size=32)
        self.assertFalse(self.root.exists())

        self.store.create_session(OWNER, OTHER_SESSION)
        before = _tree(self.root)
        with self.assertRaises(StagingSessionGone):
            self.store.write_part(OWNER, SESSION, 0, b"x" * 32, expected_size=32)
        with self.assertRaises(StagingSessionGone):
            self.store.write_part("owner-b", SESSION, 0, b"x" * 32, expected_size=32)
        self.assertEqual(_tree(self.root), before)

    def test_parts_round_trip_and_a_duplicate_replaces_the_earlier_copy(self) -> None:
        self.store.create_session(OWNER, SESSION)
        first = b"a" * 100
        info = self.store.write_part(OWNER, SESSION, 3, first, expected_size=100)
        self.assertEqual(info, PartInfo(index=3, size_bytes=100, sha256=hashlib.sha256(first).hexdigest()))
        self.assertEqual(self.store.list_parts(OWNER, SESSION), {3: 100})
        self.assertEqual(self.store.part_sha256(OWNER, SESSION, 3), hashlib.sha256(first).hexdigest())

        second = b"b" * 64
        self.store.write_part(OWNER, SESSION, 3, second, expected_size=64)
        self.assertEqual(self.store.list_parts(OWNER, SESSION), {3: 64})
        self.assertEqual(self.store.part_sha256(OWNER, SESSION, 3), hashlib.sha256(second).hexdigest())
        self.assertIsNone(self.store.part_sha256(OWNER, SESSION, 4))
        self.assertEqual(_tree(self.session_dir()), [".session", "00003.part"])

    def test_part_validation_writes_nothing(self) -> None:
        self.store.create_session(OWNER, SESSION)
        body = b"z" * 50
        digest = hashlib.sha256(body).hexdigest()
        invalid = (
            ({"index": -1, "data": body, "expected_size": 50}, StagingPartInvalid),
            ({"index": MAX_UPLOAD_PART_INDEX + 1, "data": body, "expected_size": 50}, StagingPartInvalid),
            ({"index": True, "data": body, "expected_size": 50}, StagingPartInvalid),
            ({"index": 0, "data": body, "expected_size": 49}, StagingPartInvalid),
            ({"index": 0, "data": body, "expected_size": 51}, StagingPartInvalid),
            ({"index": 0, "data": b"", "expected_size": 0}, StagingPartInvalid),
            ({"index": 0, "data": "text", "expected_size": 4}, StagingPartInvalid),
            ({"index": 0, "data": body, "expected_size": 50, "expected_sha256": "nothex"}, StagingPartInvalid),
            ({"index": 0, "data": body, "expected_size": 50, "expected_sha256": ""}, StagingPartInvalid),
            ({"index": 0, "data": body, "expected_size": 50, "expected_sha256": "0" * 64}, StagingDigestMismatch),
        )
        for kwargs, error in invalid:
            arguments = dict(kwargs)
            index = arguments.pop("index")
            data = arguments.pop("data")
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                self.store.write_part(OWNER, SESSION, index, data, **arguments)  # type: ignore[arg-type]
        self.assertEqual(_tree(self.session_dir()), [".session"])

        # The client digest is compared case-insensitively and may be padded.
        info = self.store.write_part(OWNER, SESSION, 0, memoryview(body), expected_size=50, expected_sha256=f" {digest.upper()} ")
        self.assertEqual(info.sha256, digest)

    def test_list_parts_ignores_temp_files_junk_and_directories(self) -> None:
        self.store.create_session(OWNER, SESSION)
        self.store.write_part(OWNER, SESSION, 1, b"p" * 10, expected_size=10)
        directory = self.session_dir()
        (directory / ".3.deadbeef.tmp").write_bytes(b"partial")
        (directory / "junk.txt").write_bytes(b"junk")
        (directory / "7.part").write_bytes(b"short name")
        (directory / "00009.part").mkdir()
        (directory / "nested").mkdir()

        self.assertEqual(self.store.list_parts(OWNER, SESSION), {1: 10})

    def test_purge_session_removes_it_and_late_writes_stay_gone(self) -> None:
        self.store.create_session(OWNER, SESSION)
        self.store.create_session(OWNER, OTHER_SESSION)
        self.store.write_part(OWNER, SESSION, 0, b"q" * 20, expected_size=20)
        (self.session_dir() / ".0.cafe.tmp").write_bytes(b"x")

        self.assertTrue(self.store.purge_session(OWNER, SESSION))
        self.assertFalse(self.session_dir().exists())
        self.assertTrue(self.session_dir(session=OTHER_SESSION).is_dir())
        self.assertFalse(self.store.session_exists(OWNER, SESSION))
        self.assertEqual(self.store.list_parts(OWNER, SESSION), {})
        self.assertIsNone(self.store.part_sha256(OWNER, SESSION, 0))
        with self.assertRaises(StagingSessionGone):
            self.store.write_part(OWNER, SESSION, 1, b"q" * 20, expected_size=20)
        self.assertFalse(self.session_dir().exists())
        self.assertTrue(self.store.purge_session(OWNER, SESSION))
        self.assertTrue(self.store.purge_session("never-seen", SESSION))
        self.assertTrue(UploadStagingStore(self.base / "missing").purge_session(OWNER, SESSION))
        with self.assertRaises(StagingError):
            self.store.purge_session(OWNER, "not-a-session")

    def test_purge_racing_a_part_write_reports_gone_and_recreates_nothing(self) -> None:
        original = staging_module._replace_leaf

        def purge_then_replace(directory: Any, source: str, target: str) -> None:
            self.store.purge_session(OWNER, SESSION)
            original(directory, source, target)

        def replace_then_purge(directory: Any, source: str, target: str) -> None:
            original(directory, source, target)
            self.store.purge_session(OWNER, SESSION)

        for hook in (purge_then_replace, replace_then_purge):
            with self.subTest(hook=hook.__name__):
                self.store.create_session(OWNER, SESSION)
                with patch.object(staging_module, "_replace_leaf", hook), self.assertRaises(StagingSessionGone):
                    self.store.write_part(OWNER, SESSION, 0, b"r" * 30, expected_size=30)
                self.assertFalse(self.session_dir().exists())
                self.store.purge_stale_temp(datetime.now(UTC) + timedelta(days=1))
                self.assertEqual(
                    [name for name in _tree(self.root) if SESSION in name or ".purge." in name],
                    [],
                )

    def test_concurrent_writes_of_distinct_parts(self) -> None:
        data = _payload(PART * 12 + 99)
        parts = _split(data, PART)
        self.store.create_session(OWNER, SESSION)
        errors: list[BaseException] = []

        def upload(indexes: list[int]) -> None:
            try:
                for index in indexes:
                    self.store.write_part(
                        OWNER,
                        SESSION,
                        index,
                        parts[index],
                        expected_size=len(parts[index]),
                        expected_sha256=hashlib.sha256(parts[index]).hexdigest(),
                    )
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=upload, args=(list(range(start, len(parts), 4)),)) for start in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        with self.store.open_concat(OWNER, SESSION, part_count=len(parts), part_size=PART, file_size=len(data)) as stream:
            self.assertEqual(stream.read(), data)


class ConcatStreamMixin:
    def setUp(self) -> None:
        super().setUp()
        self.data = _payload(PART * 5 + 777)
        self.store.create_session(OWNER, SESSION)
        self.part_count = self.write_all(self.data, order=[4, 0, 5, 2, 1, 3])

    def open(self) -> ConcatPartStream:
        stream = self.store.open_concat(
            OWNER,
            SESSION,
            part_count=self.part_count,
            part_size=PART,
            file_size=len(self.data),
        )
        self.addCleanup(stream.close)
        return stream

    def test_reads_cross_part_boundaries_with_exact_lengths(self) -> None:
        stream = self.open()
        self.assertEqual(stream.size, len(self.data))
        chunks = []
        while chunk := stream.read(1000):
            self.assertEqual(len(chunk), min(1000, len(self.data) - sum(map(len, chunks))))
            chunks.append(chunk)
        self.assertEqual(b"".join(chunks), self.data)
        self.assertEqual(stream.read(10), b"")
        self.assertEqual(stream.tell(), len(self.data))

        self.assertEqual(stream.seek(0), 0)
        self.assertEqual(stream.read(-1), self.data)
        stream.seek(0)
        self.assertEqual(stream.read(None), self.data)
        stream.seek(0)
        self.assertEqual(stream.read(0), b"")

    def test_seek_supports_every_whence(self) -> None:
        stream = self.open()
        self.assertEqual(stream.seek(0, os.SEEK_END), len(self.data))
        self.assertEqual(stream.tell(), len(self.data))
        self.assertEqual(stream.seek(-5, os.SEEK_END), len(self.data) - 5)
        self.assertEqual(stream.read(), self.data[-5:])
        stream.seek(PART - 3)
        self.assertEqual(stream.seek(10, os.SEEK_CUR), PART + 7)
        self.assertEqual(stream.read(PART * 2), self.data[PART + 7 : PART * 3 + 7])
        self.assertEqual(stream.seek(len(self.data) + 50), len(self.data) + 50)
        self.assertEqual(stream.read(), b"")
        with self.assertRaises(OSError):
            stream.seek(-1)
        with self.assertRaises(ValueError):
            stream.seek(0, 3)
        self.assertTrue(stream.seekable())
        self.assertTrue(stream.readable())
        self.assertFalse(stream.writable())

        stream.close()
        self.assertTrue(stream.closed)
        stream.close()
        for call in (stream.read, stream.tell, lambda: stream.seek(0)):
            with self.assertRaises(ValueError):
                call()

    def test_the_artifact_store_consumers_see_identical_bytes(self) -> None:
        expected = hashlib.sha256(self.data).hexdigest()

        # LocalArtifactStore through the intake's prefix-capturing wrapper.
        store = LocalArtifactStore(self.base / "artifacts")
        reference = store.new_reference(owner_id=OWNER, demo_id="demo-1", kind="source", state="quarantine")
        captured = _PrefixCaptureStream(self.open())
        written = store.write_stream(reference, captured, max_bytes=len(self.data), chunk_size=1024 * 1024)  # type: ignore[arg-type]
        self.assertEqual((written.size_bytes, written.sha256), (len(self.data), expected))
        self.assertEqual(captured.prefix, self.data[:512])

        # The S3 backend hashes one pass, then uploads a window over a second
        # one; botocore may rewind it for its own checksum pass.
        stream = self.open()
        stream.seek(0)
        while stream.read(1024 * 1024):
            pass
        window = _SeekableStreamWindow(stream, start=0, size_bytes=len(self.data))  # type: ignore[arg-type]
        for _ in range(2):
            window.seek(0)
            digest = hashlib.sha256()
            while chunk := window.read(1500):
                digest.update(chunk)
            self.assertEqual(digest.hexdigest(), expected)
        self.assertEqual(window.seek(0, os.SEEK_END), len(self.data))

    def test_the_demo_intake_accepts_the_stream_as_is(self) -> None:
        store = LocalArtifactStore(self.base / "artifacts")
        stream = self.open()
        accepted = ArtifactIntakeService(store).intake_demo(
            owner_id=OWNER,
            demo_id="demo-2",
            filename="match.dem",
            content_type="application/octet-stream",
            stream=stream,  # type: ignore[arg-type]
            release_stream_before_promotion=True,
        )
        self.assertTrue(stream.closed)
        self.assertEqual(accepted.size_bytes, len(self.data))
        self.assertEqual(accepted.sha256, hashlib.sha256(self.data).hexdigest())

    def test_pinned_descriptors_ignore_a_later_duplicate_part(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows cannot replace a file that is open for reading")
        stream = self.open()
        replacement = b"\x00" * PART
        self.store.write_part(OWNER, SESSION, 1, replacement, expected_size=PART)
        self.assertEqual(stream.read(), self.data)
        stream.close()
        with self.open() as reopened:
            self.assertEqual(reopened.read()[PART : PART * 2], replacement)

    def test_missing_wrong_sized_and_gone_parts_are_reported(self) -> None:
        part_files = sorted(self.session_dir().glob("*.part"))
        part_files[2].unlink()
        part_files[4].write_bytes(b"short")
        with self.assertRaises(StagingIncomplete) as caught:
            self.open()
        self.assertEqual(caught.exception.missing, (2, 4))

        # An extra part past the layout is ignored.
        self.write_all(self.data, order=[2, 4])
        self.store.write_part(OWNER, SESSION, 6, b"extra", expected_size=5)
        with self.open() as stream:
            self.assertEqual(stream.read(), self.data)

        with self.assertRaises(StagingPartInvalid):
            self.store.open_concat(OWNER, SESSION, part_count=self.part_count + 1, part_size=PART, file_size=len(self.data))

        # A purged (or never restored) directory can never be completed.
        self.assertTrue(self.store.purge_session(OWNER, SESSION))
        with self.assertRaises(StagingSessionGone):
            self.open()

    def test_purge_never_raises_while_parts_are_open(self) -> None:
        stream = self.open()
        purged = self.store.purge_session(OWNER, SESSION)
        if os.name == "nt":
            # Open files cannot be deleted on Windows; the sweep finishes later.
            self.assertFalse(purged)
        stream.close()
        self.store.purge_session(OWNER, SESSION)
        self.store.purge_stale_temp(datetime.now(UTC) + timedelta(days=1))
        self.assertFalse(self.session_dir().exists())
        self.assertEqual([name for name in _tree(self.root) if ".purge." in name], [])


class StagingSweepMixin:
    def test_purge_owner_does_not_touch_an_owner_with_a_longer_token(self) -> None:
        # "a" and "ab" encode to "YQ" and "YWI": exact directory names, never prefixes.
        self.store.create_session("a", SESSION)
        self.store.create_session("ab", OTHER_SESSION)
        self.store.write_part("ab", OTHER_SESSION, 0, b"k" * 8, expected_size=8)

        self.assertTrue(self.store.purge_owner("a"))
        self.assertFalse(self.session_dir(owner="a").exists())
        self.assertFalse(self.session_dir(owner="a").parent.exists())
        self.assertEqual(self.store.list_parts("ab", OTHER_SESSION), {0: 8})
        self.assertEqual([(entry.owner_id, entry.session_id) for entry in self.store.iter_session_dirs()], [("ab", OTHER_SESSION)])
        self.assertTrue(self.store.purge_owner("a"))
        self.assertTrue(UploadStagingStore(self.base / "missing").purge_owner("a"))
        with self.assertRaises(StagingSessionGone):
            self.store.write_part("a", SESSION, 0, b"k" * 8, expected_size=8)

    def test_iter_session_dirs_reports_marker_time_and_skips_junk(self) -> None:
        self.store.create_session(OWNER, SESSION)
        created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        os.utime(self.session_dir() / ".session", (created.timestamp(), created.timestamp()))
        self.store.write_part(OWNER, SESSION, 0, b"m" * 8, expected_size=8)

        owner_dir = self.session_dir().parent
        (owner_dir / "not-a-session").mkdir()
        (owner_dir / OTHER_SESSION).write_bytes(b"a file, not a directory")
        (owner_dir / ".purge.0011").mkdir()
        (self.root / "v1" / "!!!").mkdir()
        (self.root / "v1" / ".purge.2233").mkdir()
        unmarked = "00000000000000000000000000000001"
        (owner_dir / unmarked).mkdir()

        entries = list(self.store.iter_session_dirs())
        self.assertEqual([(entry.owner_id, entry.session_id) for entry in entries], [(OWNER, unmarked), (OWNER, SESSION)])
        by_session = {entry.session_id: entry for entry in entries}
        self.assertIsInstance(by_session[SESSION], StagingSessionDir)
        self.assertEqual(by_session[SESSION].created_at, created)
        # Without a marker, the directory's own mtime is the fallback.
        self.assertLess(abs((by_session[unmarked].created_at - datetime.now(UTC)).total_seconds()), 300)
        self.assertEqual(list(UploadStagingStore(self.base / "missing").iter_session_dirs()), [])

    def test_purge_stale_temp_removes_old_temps_tombstones_and_empty_owner_dirs(self) -> None:
        self.store.create_session(OWNER, SESSION)
        self.store.write_part(OWNER, SESSION, 0, b"s" * 8, expected_size=8)
        directory = self.session_dir()
        old = time.time() - 7_200
        stale = directory / ".1.aaaa.tmp"
        stale.write_bytes(b"stale")
        os.utime(stale, (old, old))
        fresh = directory / ".2.bbbb.tmp"
        fresh.write_bytes(b"fresh")
        session_tombstone = directory.parent / ".purge.cccc"
        session_tombstone.mkdir()
        (session_tombstone / "00000.part").write_bytes(b"left")
        owner_tombstone = self.root / "v1" / ".purge.dddd"
        (owner_tombstone / SESSION).mkdir(parents=True)
        (owner_tombstone / SESSION / "00000.part").write_bytes(b"left")

        empty_old = self.root / "v1" / _encode_reference_identity("gone-owner")
        empty_old.mkdir()
        os.utime(empty_old, (old, old))
        empty_new = self.root / "v1" / _encode_reference_identity("new-owner")
        empty_new.mkdir()

        removed = self.store.purge_stale_temp(datetime.now(UTC) - timedelta(hours=1))
        self.assertEqual(removed, 4)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertFalse(session_tombstone.exists())
        self.assertFalse(owner_tombstone.exists())
        self.assertFalse(empty_old.exists())
        self.assertTrue(empty_new.exists())
        self.assertTrue((directory / "00000.part").exists())
        self.assertTrue((directory / ".session").exists())
        self.assertEqual(UploadStagingStore(self.base / "missing").purge_stale_temp(datetime.now(UTC)), 0)
        with self.assertRaises(StagingError):
            self.store.purge_stale_temp(datetime.now())

    def test_free_bytes_walks_up_to_an_existing_ancestor(self) -> None:
        store = UploadStagingStore(self.base / "not" / "yet" / "created")
        self.assertGreater(store.free_bytes(), 0)
        self.assertFalse((self.base / "not").exists())


class PortableStagingLifecycleTest(StagingSessionLifecycleMixin, _StagingTestBase):
    backend = _PortableArtifactDirectory


class PortableConcatStreamTest(ConcatStreamMixin, _StagingTestBase):
    backend = _PortableArtifactDirectory


class PortableStagingSweepTest(StagingSweepMixin, _StagingTestBase):
    backend = _PortableArtifactDirectory


@unittest.skipUnless(SUPPORTS_DIRECTORY_FD, "descriptor-relative directory calls are unavailable")
class PosixStagingLifecycleTest(StagingSessionLifecycleMixin, _StagingTestBase):
    backend = _PosixArtifactDirectory


@unittest.skipUnless(SUPPORTS_DIRECTORY_FD, "descriptor-relative directory calls are unavailable")
class PosixConcatStreamTest(ConcatStreamMixin, _StagingTestBase):
    backend = _PosixArtifactDirectory


@unittest.skipUnless(SUPPORTS_DIRECTORY_FD, "descriptor-relative directory calls are unavailable")
class PosixStagingSweepTest(StagingSweepMixin, _StagingTestBase):
    backend = _PosixArtifactDirectory


class PartLayoutTest(unittest.TestCase):
    def test_part_count_and_sizes(self) -> None:
        mib = 1024 * 1024
        self.assertEqual(upload_part_count(1024 * mib, 8 * mib), 128)
        self.assertEqual(upload_part_count(450_000_000, 8 * mib), 54)
        self.assertEqual(upload_part_count(16, 8 * mib), 1)
        self.assertEqual(upload_part_size(0, file_size=16, part_size=8 * mib), 16)
        self.assertEqual(upload_part_size(52, file_size=450_000_000, part_size=8 * mib), 8 * mib)
        self.assertEqual(upload_part_size(53, file_size=450_000_000, part_size=8 * mib), 450_000_000 - 53 * 8 * mib)
        self.assertEqual(upload_part_size(127, file_size=1024 * mib, part_size=8 * mib), 8 * mib)
        for index in (-1, 128, True):
            with self.subTest(index=index), self.assertRaises(StagingPartInvalid):
                upload_part_size(index, file_size=1024 * mib, part_size=8 * mib)  # type: ignore[arg-type]
        for file_size, part_size in ((0, 8), (8, 0), (-1, 8), (True, 8), (MAX_UPLOAD_PART_INDEX + 2, 1)):
            with self.subTest(file_size=file_size, part_size=part_size), self.assertRaises(StagingPartInvalid):
                upload_part_count(file_size, part_size)

    def test_store_from_settings_uses_the_configured_root(self) -> None:
        configured = Settings(upload_staging_root=Path("/srv/upload-staging"))
        store = upload_staging_store_from_settings(configured_settings=configured)
        self.assertIsInstance(store, UploadStagingStore)
        self.assertEqual(store.root, Path("/srv/upload-staging"))


if __name__ == "__main__":
    unittest.main()
