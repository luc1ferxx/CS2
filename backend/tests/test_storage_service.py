import tempfile
import unittest
from pathlib import Path

from app.services.storage import LocalStorageService, StorageKeyError


class LocalStorageServiceTest(unittest.TestCase):
    def test_storage_keys_reject_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorageService(Path(directory))

            with self.assertRaises(StorageKeyError):
                storage.key("uploads", "demo-1", "../evil.dem")
            with self.assertRaises(StorageKeyError):
                storage.key("uploads", "demo-1", "nested/evil.dem")
            with self.assertRaises(StorageKeyError):
                storage.path_for_key("local://uploads/demo-1/../../evil.dem")

    def test_storage_writes_and_reads_json_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorageService(Path(directory))
            key = storage.replay_key("demo-1")

            storage.write_json(key, {"demoId": "demo-1", "rounds": []})

            self.assertTrue(storage.exists(key))
            self.assertEqual(storage.read_json(key), {"demoId": "demo-1", "rounds": []})
            self.assertEqual(storage.path_for_key(key), Path(directory) / "replays" / "demo-1.json")

    def test_media_url_is_generated_from_video_storage_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorageService(Path(directory))
            key = storage.video_key("demo-1", "clip.mp4")

            self.assertEqual(storage.media_url(key), "/media/videos/demo-1/clip.mp4")
            self.assertEqual(storage.storage_key_from_media_url("/media/videos/demo-1/clip.mp4"), key)
