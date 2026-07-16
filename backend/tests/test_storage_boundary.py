import io
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Demo, DemoJob
from app.schemas.demo import RenderWorkerResult
from app.services.demo_service import DemoService
from app.services.storage import ArtifactReference


VALID_DEMO = b"HL2DEMO\x00" + (b"bounded-demo-payload" * 2)


class StorageBackedDemoServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def test_demo_upload_stores_source_storage_key_and_remains_retrievable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                created = DemoService(db, owner_id=settings.dev_user_id).create_real_demo(
                    FakeUpload("match.dem", VALID_DEMO)
                )
                demo = db.query(Demo).filter(Demo.id == created.id).one()
                job = db.query(DemoJob).filter(DemoJob.demo_id == demo.id).one()
                service = DemoService.for_internal(db)

                parsed = ArtifactReference.parse(demo.source_storage_key)
                self.assertEqual(parsed.owner_id, demo.owner_id)
                self.assertEqual(parsed.demo_id, demo.id)
                self.assertEqual(parsed.kind, "source")
                self.assertEqual(parsed.state, "accepted")
                snapshot = service.source_artifact_snapshot(job)
                verified = service.verify_source_artifact(demo, job)
                self.assertEqual(snapshot.reference, demo.source_storage_key)
                self.assertEqual(snapshot, verified.snapshot)
                self.assertEqual(
                    service.artifact_store.head(demo.source_storage_key),
                    verified.metadata,
                )
                with service.materialized_source_demo(demo, job) as source_path:
                    self.assertEqual(source_path.read_bytes(), VALID_DEMO)
                    materialized_path = source_path
                self.assertFalse(materialized_path.exists())

    def test_replay_blob_round_trips_through_storage_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-storage-replay")
                service = DemoService.for_internal(db)

                key = write_and_bind_replay(service, db, demo, replay_contract(demo.id))

                parsed = ArtifactReference.parse(key)
                self.assertEqual(parsed.owner_id, demo.owner_id)
                self.assertEqual(parsed.demo_id, demo.id)
                self.assertEqual(parsed.kind, "replay")
                self.assertEqual(parsed.state, "accepted")
                self.assertIsNotNone(service.artifact_store.head(key))
                self.assertEqual(service.load_replay_blob(demo)["demoId"], demo.id)

    def test_invalid_legacy_source_storage_key_falls_back_to_safe_upload_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-storage-source-fallback")
                demo.original_filename = "../../match.dem"
                demo.source_storage_key = "local://uploads/demo-storage-source-fallback/../evil.dem"
                db.commit()
                service = DemoService.for_internal(db)

                self.assertEqual(
                    service.source_demo_storage_key(demo),
                    "local://uploads/demo-storage-source-fallback/match.dem",
                )

    def test_cross_demo_source_storage_key_falls_back_to_demo_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-storage-source-owner")
                demo.source_storage_key = "local://uploads/another-demo/foreign.dem"
                db.commit()
                service = DemoService.for_internal(db)

                self.assertEqual(
                    service.source_demo_storage_key(demo),
                    "local://uploads/demo-storage-source-owner/demo-storage-source-owner.dem",
                )

    def test_render_worker_local_media_path_must_stay_inside_video_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-path")
                service = DemoService.for_internal(db)
                write_and_bind_replay(service, db, demo, replay_contract(demo.id))
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

    def test_render_worker_cannot_attach_another_demos_video_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo_a = add_completed_demo(db, "demo-render-owner-a")
                demo_b = add_completed_demo(db, "demo-render-owner-b")
                service = DemoService.for_internal(db)
                write_and_bind_replay(service, db, demo_a, replay_contract(demo_a.id))
                write_and_bind_replay(service, db, demo_b, replay_contract(demo_b.id))
                foreign_key = service.storage.video_key(demo_b.id, "clip.mp4")
                service.storage.write_bytes(foreign_key, b"foreign-video")
                job = add_render_job(db, demo_a.id)

                with self.assertRaisesRegex(ValueError, "requested demo"):
                    service.apply_render_worker_result(
                        job,
                        RenderWorkerResult(
                            status="completed",
                            storageKey=foreign_key,
                            tickStart=0,
                            tickEnd=640,
                            tickRate=64,
                            durationSeconds=10,
                        ),
                    )

                db.refresh(job)
                self.assertEqual(job.status, "rendering")
                self.assertIsNone(service.get_video_status(demo_a)["url"])


class FakeUpload:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self.content_type = "application/octet-stream"
        self.file = io.BytesIO(payload)


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
        replay_storage_key=None,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


def add_render_job(db, demo_id: str):
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


def write_and_bind_replay(service, db, demo: Demo, replay: dict) -> str:
    reference = service.write_replay_blob(demo.id, replay)
    demo.replay_storage_key = reference
    db.commit()
    db.refresh(demo)
    return reference


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
    original_artifact_backend = settings.artifact_storage_backend
    original_artifact_root = settings.artifact_storage_root
    original_upload_dir = settings.demo_upload_storage_dir
    original_replay_dir = settings.replay_storage_dir
    original_video_dir = settings.video_storage_dir
    original_summary_dir = settings.summary_storage_dir
    object.__setattr__(settings, "artifact_storage_backend", "local")
    object.__setattr__(settings, "artifact_storage_root", root)
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    object.__setattr__(settings, "summary_storage_dir", root / "summaries")
    try:
        yield
    finally:
        object.__setattr__(settings, "artifact_storage_backend", original_artifact_backend)
        object.__setattr__(settings, "artifact_storage_root", original_artifact_root)
        object.__setattr__(settings, "demo_upload_storage_dir", original_upload_dir)
        object.__setattr__(settings, "replay_storage_dir", original_replay_dir)
        object.__setattr__(settings, "video_storage_dir", original_video_dir)
        object.__setattr__(settings, "summary_storage_dir", original_summary_dir)
