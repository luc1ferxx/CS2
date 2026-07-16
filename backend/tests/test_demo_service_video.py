import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.core.config import settings
from app.services.demo_service import DemoService


class DemoServiceVideoTest(unittest.TestCase):
    def test_legacy_video_contract_defaults_time_origin_to_zero(self) -> None:
        demo = SimpleNamespace(id="demo-video-legacy", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory:
            service = DemoService.for_internal(db=None)
            replay = {
                "demoId": demo.id,
                "tickRate": 64,
                "video": {
                    "status": "ready",
                    "url": None,
                    "durationSeconds": 10,
                    "tickStart": 128,
                    "tickEnd": 768,
                    "tickRate": 64,
                    "source": "mock",
                    "errorMessage": None,
                },
                "rounds": [{"startTick": 128, "endTick": 768}],
            }

            with replay_storage_dir(Path(directory)):
                service.write_replay_blob(demo.id, replay)
                video = service.get_video_status(demo)

        self.assertEqual(video["timeOriginSeconds"], 0)

    def test_legacy_replay_without_events_defaults_events_to_empty_list(self) -> None:
        demo = SimpleNamespace(id="demo-events-legacy", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory:
            service = DemoService.for_internal(db=None)
            replay = {
                "demoId": demo.id,
                "tickRate": 64,
                "video": {
                    "status": "ready",
                    "url": None,
                    "durationSeconds": 10,
                    "tickStart": 128,
                    "tickEnd": 768,
                    "tickRate": 64,
                    "source": "mock",
                    "errorMessage": None,
                },
                "rounds": [{"startTick": 128, "endTick": 768}],
                "frames": [],
                "players": [],
            }

            with replay_storage_dir(Path(directory)):
                service.write_replay_blob(demo.id, replay)
                loaded = service.load_replay_blob(demo)

        self.assertEqual(loaded["events"], [])

    def test_video_calibration_updates_timing_without_losing_media_metadata(self) -> None:
        demo = SimpleNamespace(id="demo-video-calibration", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory:
            service = DemoService.for_internal(db=None)
            replay = {
                "demoId": demo.id,
                "tickRate": 64,
                "video": {
                    "status": "ready",
                    "url": "/media/videos/demo-video-calibration/clip.mp4",
                    "durationSeconds": 90,
                    "tickStart": 100,
                    "tickEnd": 2020,
                    "tickRate": 64,
                    "source": "manual_upload",
                    "errorMessage": None,
                    "timeOriginSeconds": 2,
                },
                "rounds": [{"startTick": 100, "endTick": 2020}],
            }

            with replay_storage_dir(Path(directory)):
                service.write_replay_blob(demo.id, replay)
                video = service.update_video_calibration(
                    demo,
                    tick_start=128,
                    tick_end=2048,
                    time_origin_seconds=3.5,
                    duration_seconds=95,
                )

        self.assertEqual(video["status"], "ready")
        self.assertEqual(video["url"], "/media/videos/demo-video-calibration/clip.mp4")
        self.assertEqual(video["source"], "manual_upload")
        self.assertEqual(video["tickStart"], 128)
        self.assertEqual(video["tickEnd"], 2048)
        self.assertEqual(video["durationSeconds"], 95)
        self.assertEqual(video["timeOriginSeconds"], 3.5)


@contextmanager
def replay_storage_dir(path: Path):
    original = settings.replay_storage_dir
    object.__setattr__(settings, "replay_storage_dir", path)
    try:
        yield
    finally:
        object.__setattr__(settings, "replay_storage_dir", original)


if __name__ == "__main__":
    unittest.main()
