import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Demo
from app.services.demo_service import DemoService
from app.services.storage import ArtifactReference


class DemoServiceVideoTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()

    def test_legacy_video_contract_defaults_time_origin_to_zero(self) -> None:
        demo = add_demo(self.db, "demo-video-legacy")

        with tempfile.TemporaryDirectory() as directory:
            service = DemoService.for_internal(self.db)
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

            with storage_dirs(Path(directory)):
                write_and_bind_replay(self.db, service, demo, replay)
                video = service.get_video_status(demo)

        self.assertEqual(video["timeOriginSeconds"], 0)

    def test_legacy_replay_without_events_defaults_events_to_empty_list(self) -> None:
        demo = add_demo(self.db, "demo-events-legacy")

        with tempfile.TemporaryDirectory() as directory:
            service = DemoService.for_internal(self.db)
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

            with storage_dirs(Path(directory)):
                write_and_bind_replay(self.db, service, demo, replay)
                loaded = service.load_replay_blob(demo)

        self.assertEqual(loaded["events"], [])

    def test_video_calibration_updates_timing_without_losing_media_metadata(self) -> None:
        demo = add_demo(self.db, "demo-video-calibration")

        with tempfile.TemporaryDirectory() as directory:
            service = DemoService.for_internal(self.db)
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

            with storage_dirs(Path(directory)):
                write_and_bind_replay(self.db, service, demo, replay)
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


def add_demo(db, demo_id: str) -> Demo:
    demo = Demo(
        id=demo_id,
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        source_storage_key=None,
        replay_storage_key=None,
        map_name="de_dust2",
        tick_rate=64,
        round_count=1,
        coaching_event_count=0,
        status="completed",
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


def write_and_bind_replay(db, service: DemoService, demo: Demo, replay: dict) -> str:
    reference = service.write_replay_blob(demo.id, replay)
    parsed = ArtifactReference.parse(reference)
    if parsed.demo_id != demo.id or parsed.kind != "replay" or parsed.state != "accepted":
        raise AssertionError("Replay fixture did not produce a demo-bound accepted artifact")
    demo.replay_storage_key = reference
    db.commit()
    db.refresh(demo)
    return reference


@contextmanager
def storage_dirs(root: Path):
    original = (
        settings.artifact_storage_backend,
        settings.artifact_storage_root,
        settings.replay_storage_dir,
        settings.demo_upload_storage_dir,
        settings.video_storage_dir,
        settings.summary_storage_dir,
    )
    object.__setattr__(settings, "artifact_storage_backend", "local")
    object.__setattr__(settings, "artifact_storage_root", root)
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    object.__setattr__(settings, "summary_storage_dir", root / "summaries")
    try:
        yield
    finally:
        (
            artifact_backend,
            artifact_root,
            replay_dir,
            upload_dir,
            video_dir,
            summary_dir,
        ) = original
        object.__setattr__(settings, "artifact_storage_backend", artifact_backend)
        object.__setattr__(settings, "artifact_storage_root", artifact_root)
        object.__setattr__(settings, "replay_storage_dir", replay_dir)
        object.__setattr__(settings, "demo_upload_storage_dir", upload_dir)
        object.__setattr__(settings, "video_storage_dir", video_dir)
        object.__setattr__(settings, "summary_storage_dir", summary_dir)


if __name__ == "__main__":
    unittest.main()
