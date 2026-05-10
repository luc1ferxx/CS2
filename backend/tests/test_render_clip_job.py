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
from app.schemas.demo import RenderClipRequest, RenderJobCreated, RenderWorkerResult, ReplayVideoStatus
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

    def test_stub_worker_skips_render_clip_job_already_completed_by_external_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-already-completed")
                service = DemoService(db)
                service.write_replay_blob(
                    demo.id,
                    replay_contract(
                        demo.id,
                        video={
                            "status": "ready",
                            "url": "/media/videos/demo-render-already-completed/rendered.mp4",
                            "durationSeconds": 20,
                            "tickStart": 0,
                            "tickEnd": 1280,
                            "tickRate": 64,
                            "source": "rendered",
                            "errorMessage": None,
                            "timeOriginSeconds": 0,
                        },
                    ),
                )
                job = DemoJob(
                    id="render-job-already-completed",
                    demo_id=demo.id,
                    job_type="render_clip",
                    status="completed",
                    metadata_json=json.dumps(render_job_metadata(0, 1280)),
                )
                db.add(job)
                db.commit()

                process_render_clip_job(db, demo, job)

                db.refresh(job)
                video = service.get_video_status(demo)
                self.assertEqual(job.status, "completed")
                self.assertIsNone(job.error_message)
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "rendered")
                self.assertEqual(video["url"], "/media/videos/demo-render-already-completed/rendered.mp4")

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

    def test_next_render_clip_job_returns_oldest_queued_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-next-job")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))
                db.add_all(
                    [
                        DemoJob(
                            id="render-job-failed",
                            demo_id=demo.id,
                            job_type="render_clip",
                            status="failed",
                            metadata_json=json.dumps(render_job_metadata(256, 512)),
                        ),
                        DemoJob(
                            id="mock-render-queued",
                            demo_id=demo.id,
                            job_type="mock_render",
                            status="queued",
                        ),
                        DemoJob(
                            id="render-job-queued-oldest",
                            demo_id=demo.id,
                            job_type="render_clip",
                            status="queued",
                            metadata_json=json.dumps(render_job_metadata(640, 1280)),
                        ),
                        DemoJob(
                            id="render-job-queued-newest",
                            demo_id=demo.id,
                            job_type="render_clip",
                            status="queued",
                            metadata_json=json.dumps(render_job_metadata(1280, 1920)),
                        ),
                    ]
                )
                db.commit()

                job = service.next_render_clip_job()

                self.assertIsNotNone(job)
                self.assertEqual(job.id, "render-job-queued-oldest")
                manifest = service.render_job_manifest(job)
                self.assertEqual(manifest.jobId, "render-job-queued-oldest")
                self.assertEqual(manifest.tickStart, 640)

    def test_list_render_clip_jobs_serializes_operator_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-operator-list")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(
                        eventId="event-operator",
                        playerId="t-entry",
                        povSteamId="76561190000000001",
                        tickStart=1280,
                        tickEnd=1920,
                        tickRate=64,
                        roundNumber=4,
                        renderPreset="operator_panel_probe",
                    ),
                )

                statuses = service.list_render_clip_jobs(demo)

                self.assertEqual(len(statuses), 1)
                status = statuses[0]
                self.assertEqual(status.job_id, job.id)
                self.assertEqual(status.source, "rendered")
                self.assertEqual(status.video_status, "queued")
                self.assertIsNone(status.video_url)
                self.assertEqual(status.tick_start, 1280)
                self.assertEqual(status.tick_end, 1920)
                self.assertEqual(status.tick_rate, 64)
                self.assertEqual(status.duration_seconds, 10)
                self.assertEqual(status.event_id, "event-operator")
                self.assertEqual(status.player_id, "t-entry")
                self.assertEqual(status.pov_steam_id, "76561190000000001")
                self.assertEqual(status.round_number, 4)
                self.assertEqual(status.render_preset, "operator_panel_probe")

    def test_render_job_created_schema_preserves_operator_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-created-schema")
                service = DemoService(db)
                service.write_replay_blob(demo.id, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(
                        eventId="event-created",
                        tickStart=640,
                        tickEnd=1280,
                        tickRate=64,
                    ),
                )
                status = service.render_job_status(job)
                video = ReplayVideoStatus.model_validate(service.get_video_status(demo))

                created = RenderJobCreated(**status.model_dump(), video=video)

                self.assertEqual(created.source, "rendered")
                self.assertEqual(created.video_status, "queued")
                self.assertEqual(created.tick_start, 640)
                self.assertEqual(created.tick_end, 1280)
                self.assertEqual(created.event_id, "event-created")

    def test_render_job_status_serializes_failed_manual_and_completed_video_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-status-source")
                service = DemoService(db)
                service.write_replay_blob(
                    demo.id,
                    replay_contract(
                        demo.id,
                        video={
                            "status": "ready",
                            "url": "/media/videos/demo-render-status-source/manual.mp4",
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
                failed_job = DemoJob(
                    id="render-job-failed-manual-state",
                    demo_id=demo.id,
                    job_type="render_clip",
                    status="failed",
                    error_message="Operator cancelled capture",
                    metadata_json=json.dumps(render_job_metadata(200, 2120)),
                )
                db.add(failed_job)
                db.commit()

                failed_status = service.render_job_status(failed_job)

                self.assertEqual(failed_status.status, "failed")
                self.assertEqual(failed_status.source, "manual_upload")
                self.assertEqual(failed_status.video_status, "ready")
                self.assertEqual(
                    failed_status.video_url,
                    "/media/videos/demo-render-status-source/manual.mp4",
                )
                self.assertEqual(failed_status.error_message, "Operator cancelled capture")

                completed_job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64),
                )
                service.apply_render_worker_result(
                    completed_job,
                    RenderWorkerResult(
                        status="completed",
                        videoUrl="/media/videos/demo-render-status-source/rendered.mp4",
                        tickStart=640,
                        tickEnd=1280,
                        tickRate=64,
                        durationSeconds=10,
                    ),
                )

                completed_status = service.render_job_status(completed_job)
                self.assertEqual(completed_status.status, "completed")
                self.assertEqual(completed_status.source, "rendered")
                self.assertEqual(completed_status.video_status, "ready")
                self.assertEqual(
                    completed_status.video_url,
                    "/media/videos/demo-render-status-source/rendered.mp4",
                )

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
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
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


def render_job_metadata(tick_start: int, tick_end: int) -> dict:
    return {
        "tickStart": tick_start,
        "tickEnd": tick_end,
        "tickRate": 64,
        "durationSeconds": round((tick_end - tick_start) / 64, 3),
        "renderPreset": "event_clip_v1",
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
