import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings
from app.services.upload_service import (
    DemoUploadValidationError,
    MAX_DEMO_UPLOAD_BYTES,
    MAX_VIDEO_UPLOAD_BYTES,
    store_video_upload,
    validate_demo_upload,
    validate_video_upload,
)


class DemoUploadValidationTest(unittest.TestCase):
    def test_accepts_dem_and_zip_under_size_limit(self) -> None:
        self.assertEqual(validate_demo_upload("match.dem", 1024), ".dem")
        self.assertEqual(validate_demo_upload("MATCH.ZIP", 1024), ".zip")

    def test_rejects_unsupported_extensions(self) -> None:
        with self.assertRaises(DemoUploadValidationError):
            validate_demo_upload("match.rar", 1024)

    def test_rejects_files_over_size_limit(self) -> None:
        with self.assertRaises(DemoUploadValidationError):
            validate_demo_upload("match.dem", MAX_DEMO_UPLOAD_BYTES + 1)


class VideoUploadValidationTest(unittest.TestCase):
    def test_accepts_mp4_under_size_limit(self) -> None:
        self.assertEqual(validate_video_upload("clip.MP4", 1024), ".mp4")

    def test_rejects_non_mp4_extensions(self) -> None:
        with self.assertRaises(DemoUploadValidationError):
            validate_video_upload("clip.mov", 1024)

    def test_rejects_video_over_size_limit(self) -> None:
        with self.assertRaises(DemoUploadValidationError):
            validate_video_upload("clip.mp4", MAX_VIDEO_UPLOAD_BYTES + 1)


class VideoUploadStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_mp4_before_writing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with video_storage_dir(Path(directory)):
                with self.assertRaises(DemoUploadValidationError):
                    await store_video_upload("demo-1", FakeUpload("clip.mov", [b"not-mp4"]))

                self.assertEqual(list(Path(directory).rglob("*")), [])

    async def test_removes_empty_mp4_after_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with video_storage_dir(Path(directory)):
                with self.assertRaises(DemoUploadValidationError):
                    await store_video_upload("demo-1", FakeUpload("clip.mp4", []))

                self.assertEqual(list(Path(directory).rglob("*")), [Path(directory) / "demo-1"])
                self.assertEqual(list((Path(directory) / "demo-1").iterdir()), [])


class FakeUpload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self.chunks = chunks

    async def read(self, _: int) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


@contextmanager
def video_storage_dir(path: Path):
    original = settings.video_storage_dir
    object.__setattr__(settings, "video_storage_dir", path)
    try:
        yield
    finally:
        object.__setattr__(settings, "video_storage_dir", original)


if __name__ == "__main__":
    unittest.main()
