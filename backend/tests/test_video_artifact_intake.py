import io
import tempfile
import unittest
from pathlib import Path

from app.services.storage import ArtifactStoreError, LocalArtifactStore
from app.services.upload_service import (
    DemoUploadValidationError,
    store_video_artifact,
)


class FakeUpload:
    def __init__(
        self,
        filename: str,
        body: bytes,
        content_type: str = "application/x-untrusted",
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(body)


class TrackingStore(LocalArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.references: list[str] = []

    def new_reference(self, **kwargs):
        reference = super().new_reference(**kwargs)
        self.references.append(reference)
        return reference


class FailingReferenceStore(TrackingStore):
    def new_reference(self, **kwargs):
        raise ArtifactStoreError("s3://secret@internal/private-key traceback")


class VideoArtifactIntakeTest(unittest.TestCase):
    def test_video_is_private_bound_and_uses_server_derived_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrackingStore(Path(directory))
            stored = store_video_artifact(
                owner_id="owner-a",
                demo_id="demo-a",
                upload=FakeUpload("../../clip.mp4", b"private-video-bytes"),
                store=store,
                max_bytes=1024,
                chunk_size=4,
            )

            metadata = store.head(stored.storage_key)
            self.assertIsNotNone(metadata)
            self.assertEqual(stored.original_filename, "clip.mp4")
            self.assertEqual(metadata.owner_id, "owner-a")
            self.assertEqual(metadata.demo_id, "demo-a")
            self.assertEqual(metadata.kind, "video")
            self.assertEqual(metadata.state, "accepted")
            self.assertEqual(metadata.content_type, "video/mp4")
            self.assertEqual(stored.url, "/demos/demo-a/media/video")

    def test_invalid_empty_and_oversized_video_leave_no_artifact(self) -> None:
        cases = (
            (FakeUpload("clip.txt", b"video"), 1024, "Only .mp4"),
            (FakeUpload("clip.mp4", b""), 1024, "empty"),
            (FakeUpload("clip.mp4", b"123456789"), 8, "exceeds"),
        )
        for upload, max_bytes, expected in cases:
            with self.subTest(filename=upload.filename, max_bytes=max_bytes):
                with tempfile.TemporaryDirectory() as directory:
                    store = TrackingStore(Path(directory))
                    with self.assertRaisesRegex(DemoUploadValidationError, expected):
                        store_video_artifact(
                            owner_id="owner-a",
                            demo_id="demo-a",
                            upload=upload,
                            store=store,
                            max_bytes=max_bytes,
                            chunk_size=4,
                        )

                    self.assertEqual(
                        [path for path in Path(directory).rglob("*") if path.is_file()],
                        [],
                    )

    def test_reference_creation_failure_returns_only_safe_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FailingReferenceStore(Path(directory))
            with self.assertRaises(DemoUploadValidationError) as raised:
                store_video_artifact(
                    owner_id="owner-a",
                    demo_id="demo-a",
                    upload=FakeUpload("clip.mp4", b"video"),
                    store=store,
                    max_bytes=1024,
                    chunk_size=4,
                )

            message = str(raised.exception).lower()
            self.assertEqual(message, "video artifact could not be stored")
            self.assertNotIn("secret", message)
            self.assertNotIn("internal", message)
            self.assertNotIn("traceback", message)


if __name__ == "__main__":
    unittest.main()
