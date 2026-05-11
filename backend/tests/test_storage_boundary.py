import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Demo
from app.schemas.demo import RenderWorkerResult
from app.services.demo_service import DemoService


class StorageBackedDemoServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    async def test_demo_upload_stores_source_storage_key_and_remains_retrievable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                created = await DemoService(db).create_real_demo(
                    FakeUpload("../match.dem", [b"demo-bytes"])
                )
                demo = db.query(Demo).filter(Demo.id == created.id).one()
                service = DemoService(db)

                self.assertEqual(
                    demo.source_storage_key,
                    f"local://uploads/{demo.id}/match.dem",
                )
                self.assertEqual(service.source_demo_path(demo).read_bytes(), b"demo-bytes")

    async def test_replay_blob_round_trips_through_storage_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-storage-replay")
                service = DemoService(db)

                key = service.write_replay_blob(demo.id, replay_contract(demo.id))
                demo.replay_storage_key = key
                db.commit()

                self.assertEqual(key, "local://replays/demo-storage-replay.json")
                self.assertEqual(service.load_replay_blob(demo)["demoId"], demo.id)

    async def test_render_worker_local_media_path_must_stay_inside_video_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-path")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))
                job = add_render_job(db, demo.id)
                outside_path = Path(directory) / "outside.mp4"
                outside_path.write_bytes(b"not from video storage")

                with self.assertRaisesRegex(ValueError, "inside video storage"):
                    service.apply_render_worker_result(
                        job,
                        RenderWorkerResult(
                            status="completed",
                            localMediaPath=str(outside_path),
                            tickStart=0,
                            tickEnd=640,
                            tickRate=64,
                            durationSeconds=10,
                        ),
                    )


class FakeUpload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self.chunks = chunks

    async def read(self, _: int) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


class FakeRedis:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def lpush(self, _: str, payload: str) -> None:
        self.payloads.append(payload)


def add_completed_demo(db, demo_id: str) -> Demo:
    demo = Demo(
        id=demo_id,
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        source_storage_key=f"local://uploads/{demo_id}/{demo_id}.dem",
        map_name="de_dust2",
        tick_rate=64,
        round_count=1,
        coaching_event_count=1,
        status="completed",
        replay_storage_key=f"local://replays/{demo_id}.json",
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


def add_render_job(db, demo_id: str):
    from app.models import DemoJob

    job = DemoJob(
        id="render-job-storage-path",
        demo_id=demo_id,
        job_type="render_clip",
        status="rendering",
        metadata_json='{"tickStart":0,"tickEnd":640,"tickRate":64,"durationSeconds":10}',
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def replay_contract(demo_id: str) -> dict:
    return {
        "demoId": demo_id,
        "mapName": "de_dust2",
        "tickRate": 64,
        "video": {
            "status": "ready",
            "url": None,
            "durationSeconds": 10,
            "tickStart": 0,
            "tickEnd": 640,
            "tickRate": 64,
            "source": "mock",
            "errorMessage": None,
            "timeOriginSeconds": 0,
        },
        "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 640}],
        "players": [],
        "frames": [],
        "generatedAt": "2026-05-08T00:00:00Z",
    }


@contextmanager
def storage_dirs(root: Path):
    original_upload_dir = settings.demo_upload_storage_dir
    original_replay_dir = settings.replay_storage_dir
    original_video_dir = settings.video_storage_dir
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    try:
        yield
    finally:
        object.__setattr__(settings, "demo_upload_storage_dir", original_upload_dir)
        object.__setattr__(settings, "replay_storage_dir", original_replay_dir)
        object.__setattr__(settings, "video_storage_dir", original_video_dir)
