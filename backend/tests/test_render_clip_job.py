import json
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
from app.schemas.demo import RenderClipRequest, RenderWorkerResult
from app.services.demo_service import DemoService
from app.workers.worker import RENDER_CLIP_NOT_CONNECTED_ERROR, process_render_clip_job


class RenderClipJobTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def test_valid_render_clip_request_creates_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ) as redis_factory:
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-valid")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))

                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(
                        eventId="event-1",
                        playerId="player-1",
                        tickStart=640,
                        tickEnd=3200,
                        tickRate=64,
                        roundNumber=3,
                    ),
                )

                db.refresh(job)
                metadata = json.loads(job.metadata_json)
                queued_payload = json.loads(redis_factory.return_value.payloads[0])

                self.assertEqual(job.job_type, "render_clip")
                self.assertEqual(job.status, "queued")
                self.assertEqual(metadata["eventId"], "event-1")
                self.assertEqual(metadata["playerId"], "player-1")
                self.assertEqual(metadata["durationSeconds"], 40)
                self.assertEqual(metadata["maxDurationSeconds"], 60)
                self.assertEqual(queued_payload["job_id"], job.id)
                self.assertEqual(queued_payload["demo_id"], demo.id)
                self.assertEqual(queued_payload["job_type"], "render_clip")

    def test_invalid_tick_range_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-invalid-range")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))

                with self.assertRaisesRegex(ValueError, "tickEnd must be greater than tickStart"):
                    service.create_render_clip_job(
                        demo,
                        RenderClipRequest(tickStart=3200, tickEnd=3200, tickRate=64),
                    )

                self.assertEqual(db.query(DemoJob).count(), 0)

    def test_too_long_clip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-too-long")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))

                with self.assertRaisesRegex(ValueError, "60 seconds or less"):
                    service.create_render_clip_job(
                        demo,
                        RenderClipRequest(tickStart=0, tickEnd=64 * 61, tickRate=64),
                    )

                self.assertEqual(db.query(DemoJob).count(), 0)

    def test_missing_replay_blob_returns_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-missing-replay")
                service = DemoService(db)

                with self.assertRaisesRegex(ValueError, "Replay blob is not ready"):
                    service.create_render_clip_job(
                        demo,
                        RenderClipRequest(tickStart=0, tickEnd=128, tickRate=64),
                    )

                self.assertEqual(db.query(DemoJob).count(), 0)

    def test_stub_failure_does_not_remove_manual_upload_video_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-manual-video")
                service = DemoService(db)
                service.write_replay_blob(
                    demo.id,
                    replay_contract(
                        demo.id,
                        video={
                            "status": "ready",
                            "url": "/media/videos/demo-render-manual-video/clip.mp4",
                            "durationSeconds": 46,
                            "tickStart": 100,
                            "tickEnd": 3044,
                            "tickRate": 64,
                            "source": "manual_upload",
                            "errorMessage": None,
                            "timeOriginSeconds": 1.25,
                        },
                    ),
                )
                job = DemoJob(
                    id="render-job-manual-video",
                    demo_id=demo.id,
                    job_type="render_clip",
                    status="queued",
                    metadata_json=json.dumps(
                        {
                            "tickStart": 100,
                            "tickEnd": 3044,
                            "tickRate": 64,
                            "durationSeconds": 46,
                        }
                    ),
                )
                db.add(job)
                db.commit()

                process_render_clip_job(db, demo, job)

                db.refresh(job)
                video = service.get_video_status(demo)
                self.assertEqual(job.status, "failed")
                self.assertEqual(job.error_message, RENDER_CLIP_NOT_CONNECTED_ERROR)
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "manual_upload")
                self.assertEqual(video["url"], "/media/videos/demo-render-manual-video/clip.mp4")
                self.assertEqual(video["timeOriginSeconds"], 1.25)

    def test_render_worker_manifest_includes_demo_and_clip_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-manifest")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(
                        eventId="event-1",
                        povSteamId="76561190000000001",
                        tickStart=640,
                        tickEnd=1920,
                        tickRate=64,
                        roundNumber=2,
                        renderPreset="first_person_1080p30",
                    ),
                )

                manifest = service.render_job_manifest(job)

                self.assertEqual(manifest.manifestVersion, "render_worker_v1")
                self.assertEqual(manifest.jobId, job.id)
                self.assertEqual(manifest.demoId, demo.id)
                self.assertEqual(manifest.demoFilePath, f"/data/uploads/{demo.id}/{demo.original_filename}")
                self.assertEqual(manifest.demoStorageKey, f"local://uploads/{demo.id}/{demo.original_filename}")
                self.assertEqual(manifest.mapName, "de_dust2")
                self.assertEqual(manifest.eventId, "event-1")
                self.assertEqual(manifest.povSteamId, "76561190000000001")
                self.assertEqual(manifest.tickStart, 640)
                self.assertEqual(manifest.tickEnd, 1920)
                self.assertEqual(manifest.tickRate, 64)
                self.assertEqual(manifest.roundNumber, 2)
                self.assertEqual(manifest.renderPreset, "first_person_1080p30")

    def test_render_worker_completed_result_updates_rendered_video_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-completed")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=100, tickEnd=3044, tickRate=64),
                )

                video = service.apply_render_worker_result(
                    job,
                    RenderWorkerResult(
                        status="completed",
                        videoUrl="/media/videos/demo-render-completed/rendered.mp4",
                        tickStart=100,
                        tickEnd=3044,
                        tickRate=64,
                        timeOriginSeconds=1.25,
                        durationSeconds=46,
                    ),
                )

                db.refresh(job)
                self.assertEqual(job.status, "completed")
                self.assertIsNone(job.error_message)
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "rendered")
                self.assertEqual(video["url"], "/media/videos/demo-render-completed/rendered.mp4")
                self.assertEqual(video["tickStart"], 100)
                self.assertEqual(video["tickEnd"], 3044)
                self.assertEqual(video["tickRate"], 64)
                self.assertEqual(video["timeOriginSeconds"], 1.25)
                self.assertEqual(video["durationSeconds"], 46)

    def test_render_worker_failed_result_preserves_manual_upload_video_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-callback-failed")
                service = DemoService(db)
                service.write_replay_blob(
                    demo.id,
                    replay_contract(
                        demo.id,
                        video={
                            "status": "ready",
                            "url": "/media/videos/demo-render-callback-failed/manual.mp4",
                            "durationSeconds": 30,
                            "tickStart": 200,
                            "tickEnd": 2120,
                            "tickRate": 64,
                            "source": "manual_upload",
                            "errorMessage": None,
                            "timeOriginSeconds": 2,
                        },
                    ),
                )
                job = DemoJob(
                    id="render-job-callback-failed",
                    demo_id=demo.id,
                    job_type="render_clip",
                    status="rendering",
                    metadata_json=json.dumps(
                        {
                            "tickStart": 200,
                            "tickEnd": 2120,
                            "tickRate": 64,
                            "durationSeconds": 30,
                        }
                    ),
                )
                db.add(job)
                db.commit()

                video = service.apply_render_worker_result(
                    job,
                    RenderWorkerResult(
                        status="failed",
                        tickStart=200,
                        tickEnd=2120,
                        tickRate=64,
                        timeOriginSeconds=2,
                        durationSeconds=30,
                        errorMessage="Renderer crashed before capture",
                    ),
                )

                db.refresh(job)
                self.assertEqual(job.status, "failed")
                self.assertEqual(job.error_message, "Renderer crashed before capture")
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "manual_upload")
                self.assertEqual(video["url"], "/media/videos/demo-render-callback-failed/manual.mp4")
                self.assertEqual(video["timeOriginSeconds"], 2)


class FakeRedis:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def lpush(self, _: str, payload: str) -> None:
        self.payloads.append(payload)


def add_completed_demo(db, demo_id: str) -> Demo:
    demo = Demo(
        id=demo_id,
        user_id=settings.dev_user_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
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


def replay_contract(demo_id: str, video: dict | None = None) -> dict:
    return {
        "demoId": demo_id,
        "mapName": "de_dust2",
        "tickRate": 64,
        "video": video
        or {
            "status": "ready",
            "url": None,
            "durationSeconds": 90,
            "tickStart": 0,
            "tickEnd": 5760,
            "tickRate": 64,
            "source": "mock",
            "errorMessage": None,
            "timeOriginSeconds": 0,
        },
        "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 5760}],
        "players": [],
        "frames": [],
        "generatedAt": "2026-05-08T00:00:00Z",
    }


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
