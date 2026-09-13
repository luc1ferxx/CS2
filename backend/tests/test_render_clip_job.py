import io
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
from app.services.upload_service import StoredVideoUpload, store_video_artifact
from app.workers.worker import (
    RENDER_CLIP_NOT_CONNECTED_ERROR,
    _log_job_failure,
    fail_job,
    process_render_clip_job,
)


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
                service = DemoService.for_internal(db)
                replay_reference = persist_replay(
                    service,
                    demo,
                    replay_contract(demo.id),
                )

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
                self.assertEqual(metadata["demoStorageKey"], f"local://uploads/{demo.id}/{demo.original_filename}")
                self.assertEqual(metadata["replayStorageKey"], demo.replay_storage_key)
                self.assertNotEqual(metadata["replayStorageKey"], replay_reference)
                self.assertIsNone(service.artifact_store.head(replay_reference))
                self.assertEqual(metadata["durationSeconds"], 40)
                self.assertEqual(metadata["maxDurationSeconds"], 60)
                self.assertNotIn("demoFilePath", metadata)
                self.assertNotIn("localMediaPath", metadata)
                self.assertNotIn("videoUrl", metadata)
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
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))

                with self.assertRaisesRegex(ValueError, "tickEnd must be greater than tickStart"):
                    service.create_render_clip_job(
                        demo,
                        RenderClipRequest(tickStart=3200, tickEnd=3200, tickRate=64),
                    )

                self.assertEqual(db.query(DemoJob).count(), 0)

    def test_reused_fallback_request_is_dispatched_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client", return_value=FakeRedis(),
            ) as redis_factory:
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-reuse-dispatch")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                request = RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64)
                first = service.create_render_clip_job(demo, request)
                again = service.create_render_clip_job(
                    demo, request.model_copy(update={"eventId": "another-card"}),
                )
                self.assertEqual(again.id, first.id)
                self.assertEqual(db.query(DemoJob).count(), 1)
                self.assertEqual(len(redis_factory.return_value.payloads), 1)

    def test_too_long_clip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-too-long")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))

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
                service = DemoService.for_internal(db)

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
                service = DemoService.for_internal(db)
                # Legacy local media is retained as a compatibility-boundary fixture.
                persist_replay(
                    service,
                    demo,
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
                service.storage.write_bytes(
                    service.storage.video_key(demo.id, "clip.mp4"),
                    b"manual-video",
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
                self.assertIn("GPU worker not connected", job.error_message)
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "manual_upload")
                self.assertEqual(video["url"], "/media/videos/demo-render-manual-video/clip.mp4")
                self.assertEqual(video["timeOriginSeconds"], 1.25)

    def test_claim_render_clip_job_marks_rendering_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-claim")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64),
                )

                claimed = service.claim_render_clip_job(job)
                with self.assertRaisesRegex(ValueError, "already claimed"):
                    service.claim_render_clip_job(claimed)
                video = service.get_video_status(demo)

                self.assertEqual(claimed.status, "rendering")
                self.assertEqual(claimed.attempts, 1)
                self.assertIsNotNone(claimed.started_at)
                self.assertIsNone(claimed.finished_at)
                self.assertEqual(video["status"], "rendering")
                self.assertEqual(video["source"], "rendered")
                self.assertIsNone(video["errorMessage"])

    def test_stub_worker_skips_render_clip_job_already_completed_by_external_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-already-completed")
                service = DemoService.for_internal(db)
                persist_replay(
                    service,
                    demo,
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
                service = DemoService.for_internal(db)
                replay_reference = persist_replay(
                    service,
                    demo,
                    replay_contract(demo.id),
                )
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
                self.assertEqual(manifest.demoFilePath, "")
                self.assertEqual(manifest.demoStorageKey, f"local://uploads/{demo.id}/{demo.original_filename}")
                self.assertEqual(manifest.replayStorageKey, demo.replay_storage_key)
                self.assertNotEqual(manifest.replayStorageKey, replay_reference)
                self.assertIsNone(service.artifact_store.head(replay_reference))
                self.assertEqual(manifest.mapName, "de_dust2")
                self.assertEqual(manifest.eventId, "event-1")
                self.assertEqual(manifest.povSteamId, "76561190000000001")
                self.assertEqual(manifest.tickStart, 640)
                self.assertEqual(manifest.tickEnd, 1920)
                self.assertEqual(manifest.tickRate, 64)
                self.assertEqual(manifest.roundNumber, 2)
                self.assertEqual(manifest.renderPreset, "first_person_1080p30")

    def test_render_worker_manifest_can_claim_job_for_processing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-manifest-claim")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64),
                )

                claimed = service.claim_render_clip_job(job)
                manifest = service.render_job_manifest(claimed)

                self.assertEqual(manifest.status, "rendering")
                self.assertEqual(claimed.status, "rendering")

    def test_next_render_clip_job_returns_oldest_queued_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-next-job")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
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
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
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
                self.assertNotIn("video_url", status.model_dump())
                self.assertEqual(status.tick_start, 1280)
                self.assertEqual(status.tick_end, 1920)
                self.assertEqual(status.tick_rate, 64)
                self.assertEqual(status.duration_seconds, 10)
                self.assertEqual(status.event_id, "event-operator")
                self.assertEqual(status.player_id, "t-entry")
                self.assertEqual(status.pov_steam_id, "76561190000000001")
                self.assertEqual(status.round_number, 4)
                self.assertEqual(status.render_preset, "operator_panel_probe")
                self.assertNotIn("demoStorageKey", status.metadata)
                self.assertNotIn("replayStorageKey", status.metadata)
                self.assertNotIn("local://", status.model_dump_json())

    def test_render_job_created_schema_preserves_operator_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-created-schema")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
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

                created = RenderJobCreated(**status.model_dump(exclude={"video"}), video=video)

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
                service = DemoService.for_internal(db)
                manual_video = store_accepted_video(
                    service,
                    demo,
                    filename="manual.mp4",
                    payload=b"manual-video",
                )
                persist_replay(
                    service,
                    demo,
                    replay_contract(
                        demo.id,
                        video={
                            "status": "ready",
                            "url": manual_video.url,
                            "storageKey": manual_video.storage_key,
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
                self.assertNotIn("video_url", failed_status.model_dump())
                self.assertEqual(failed_status.error_code, "RENDER_FAILED")
                self.assertEqual(
                    failed_status.error_message,
                    "Render output could not be produced.",
                )

                completed_job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64),
                )
                service.claim_render_clip_job(completed_job)
                rendered_video = store_bound_render_worker_video(
                    service,
                    completed_job,
                )
                service.apply_render_worker_result(
                    completed_job,
                    RenderWorkerResult(
                        status="completed",
                        videoUrl=rendered_video.url,
                        storageKey=rendered_video.storage_key,
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
                self.assertNotIn("video_url", completed_status.model_dump())

    def test_render_worker_completed_result_updates_rendered_video_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-completed")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=100, tickEnd=3044, tickRate=64),
                )
                service.claim_render_clip_job(job)
                rendered_video = store_bound_render_worker_video(service, job)

                video = service.apply_render_worker_result(
                    job,
                    RenderWorkerResult(
                        status="completed",
                        videoUrl=rendered_video.url,
                        storageKey=rendered_video.storage_key,
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
                self.assertEqual(video["url"], rendered_video.url)
                self.assertEqual(video["storageKey"], rendered_video.storage_key)
                self.assertEqual(video["tickStart"], 100)
                self.assertEqual(video["tickEnd"], 3044)
                self.assertEqual(video["tickRate"], 64)
                self.assertEqual(video["timeOriginSeconds"], 1.25)
                self.assertEqual(video["durationSeconds"], 46)

    def test_render_worker_output_is_bound_to_one_exact_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-output-binding")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job_a = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=0, tickEnd=640, tickRate=64),
                )
                job_b = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64),
                )
                service.claim_render_clip_job(job_a)
                service.claim_render_clip_job(job_b)
                rendered_video = store_bound_render_worker_video(service, job_a)

                with self.assertRaisesRegex(ValueError, "not bound to this job"):
                    service.apply_render_worker_result(
                        job_b,
                        RenderWorkerResult(
                            status="completed",
                            videoUrl=rendered_video.url,
                            storageKey=rendered_video.storage_key,
                            tickStart=640,
                            tickEnd=1280,
                            tickRate=64,
                            durationSeconds=10,
                        ),
                    )

                db.refresh(job_b)
                self.assertEqual(job_b.status, "rendering")
                self.assertIsNotNone(
                    service.artifact_store.head(rendered_video.storage_key)
                )

    def test_render_worker_output_must_match_requested_tick_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-tick-binding")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=640, tickEnd=1280, tickRate=64),
                )
                service.claim_render_clip_job(job)
                rendered_video = store_bound_render_worker_video(service, job)

                with self.assertRaisesRegex(ValueError, "requested clip"):
                    service.apply_render_worker_result(
                        job,
                        RenderWorkerResult(
                            status="completed",
                            videoUrl=rendered_video.url,
                            storageKey=rendered_video.storage_key,
                            tickStart=0,
                            tickEnd=640,
                            tickRate=64,
                            durationSeconds=10,
                        ),
                    )

                db.refresh(job)
                self.assertEqual(job.status, "rendering")
                self.assertIsNotNone(
                    service.artifact_store.head(rendered_video.storage_key)
                )

    def test_failed_render_callback_removes_bound_candidate_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-failed-candidate")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=0, tickEnd=640, tickRate=64),
                )
                service.claim_render_clip_job(job)
                rendered_video = store_bound_render_worker_video(service, job)

                service.apply_render_worker_result(
                    job,
                    RenderWorkerResult(
                        status="failed",
                        tickStart=0,
                        tickEnd=640,
                        tickRate=64,
                        durationSeconds=10,
                        errorMessage="renderer failed",
                    ),
                )

                db.refresh(job)
                self.assertEqual(job.status, "failed")
                self.assertNotIn("outputArtifact", json.loads(job.metadata_json))
                self.assertIsNone(
                    service.artifact_store.head(rendered_video.storage_key)
                )

    def test_completed_callback_rolls_back_replay_and_job_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with replay_storage_dir(root), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-atomic-callback")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=0, tickEnd=640, tickRate=64),
                )
                service.claim_render_clip_job(job)
                rendered_video = store_bound_render_worker_video(service, job)
                previous_replay = demo.replay_storage_key
                before_files = {
                    path.relative_to(root)
                    for path in root.rglob("*")
                    if path.is_file()
                }

                with patch.object(db, "commit", side_effect=RuntimeError("db down")):
                    with self.assertRaisesRegex(RuntimeError, "db down"):
                        service.apply_render_worker_result(
                            job,
                            RenderWorkerResult(
                                status="completed",
                                videoUrl=rendered_video.url,
                                storageKey=rendered_video.storage_key,
                                tickStart=0,
                                tickEnd=640,
                                tickRate=64,
                                durationSeconds=10,
                            ),
                        )

                db.refresh(demo)
                db.refresh(job)
                after_files = {
                    path.relative_to(root)
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(demo.replay_storage_key, previous_replay)
                self.assertEqual(job.status, "rendering")
                self.assertEqual(after_files, before_files)
                self.assertIsNotNone(
                    service.artifact_store.head(rendered_video.storage_key)
                )

    def test_render_worker_failed_result_preserves_manual_upload_video_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-callback-failed")
                service = DemoService.for_internal(db)
                manual_video = store_accepted_video(
                    service,
                    demo,
                    filename="manual.mp4",
                    payload=b"manual-video",
                )
                persist_replay(
                    service,
                    demo,
                    replay_contract(
                        demo.id,
                        video={
                            "status": "ready",
                            "url": manual_video.url,
                            "storageKey": manual_video.storage_key,
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
                self.assertEqual(job.error_message, "Render output could not be produced.")
                status = service.render_job_status(job)
                self.assertEqual(status.error_code, "RENDER_FAILED")
                self.assertEqual(status.error_message, "Render output could not be produced.")
                self.assertNotIn("Renderer crashed", status.model_dump_json())
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "manual_upload")
                self.assertEqual(video["url"], manual_video.url)
                self.assertEqual(video["storageKey"], manual_video.storage_key)
                self.assertEqual(video["timeOriginSeconds"], 2)

    def test_render_failure_details_never_enter_user_visible_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-safe-failure")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=0, tickEnd=640, tickRate=64),
                )
                service.claim_render_clip_job(job)
                raw_error = (
                    "/data/videos/private.mp4 token=secret-value\n"
                    "Traceback (most recent call last): ..."
                )

                with self.assertLogs(
                    "app.services.demo_service",
                    level="WARNING",
                ) as captured_logs:
                    video = service.apply_render_worker_result(
                        job,
                        RenderWorkerResult(
                            status="failed",
                            tickStart=0,
                            tickEnd=640,
                            tickRate=64,
                            durationSeconds=10,
                            errorMessage=raw_error,
                        ),
                    )

                db.refresh(job)
                public_video = service.public_video_status(demo)
                public_job = service.render_job_status(job)
                combined = json.dumps(
                    {
                        "video": video,
                        "publicVideo": public_video,
                        "job": public_job.model_dump(mode="json"),
                    }
                )
                self.assertEqual(job.error_message, "Render output could not be produced.")
                self.assertEqual(video["errorCode"], "RENDER_FAILED")
                self.assertEqual(video["errorMessage"], "Render output could not be produced.")
                self.assertEqual(public_video["errorCode"], "RENDER_FAILED")
                self.assertEqual(public_job.error_code, "RENDER_FAILED")
                self.assertNotIn("/data/", combined)
                self.assertNotIn("secret-value", combined)
                self.assertNotIn("Traceback", combined)
                self.assertNotIn("/data/", "\n".join(captured_logs.output))
                self.assertNotIn("secret-value", "\n".join(captured_logs.output))
                self.assertNotIn("Traceback", "\n".join(captured_logs.output))

    def test_worker_exception_persists_only_safe_render_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)):
                db = self.Session()
                demo = add_completed_demo(db, "demo-worker-safe-failure")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = DemoJob(
                    id="render-job-worker-safe-failure",
                    demo_id=demo.id,
                    job_type="render_clip",
                    status="rendering",
                    metadata_json=json.dumps(render_job_metadata(0, 640)),
                )
                db.add(job)
                db.commit()

                fail_job(
                    db,
                    job.id,
                    demo.id,
                    RuntimeError("/private/tmp/clip.mp4 api_token=secret Traceback"),
                )

                db.refresh(job)
                video = service.get_video_status(demo)
                self.assertEqual(job.error_message, "Render output could not be produced.")
                self.assertEqual(video["errorCode"], "RENDER_FAILED")
                self.assertEqual(video["errorMessage"], "Render output could not be produced.")
                self.assertNotIn("/private/tmp", json.dumps(video))
                self.assertNotIn("api_token", json.dumps(video))

    def test_worker_failure_log_omits_exception_text(self) -> None:
        with patch("builtins.print") as print_mock:
            _log_job_failure(
                "job-safe-log",
                "process",
                RuntimeError("/private/tmp/clip.mp4 token=secret Traceback"),
            )

        rendered_log = " ".join(str(value) for value in print_mock.call_args.args)
        self.assertIn("job-safe-log", rendered_log)
        self.assertIn("RuntimeError", rendered_log)
        self.assertNotIn("/private/tmp", rendered_log)
        self.assertNotIn("token=secret", rendered_log)
        self.assertNotIn("Traceback", rendered_log)

    def test_terminal_render_job_rejects_later_callbacks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with replay_storage_dir(Path(directory)), patch(
                "app.services.demo_service.get_redis_client",
                return_value=FakeRedis(),
            ):
                db = self.Session()
                demo = add_completed_demo(db, "demo-render-terminal-callback")
                service = DemoService.for_internal(db)
                persist_replay(service, demo, replay_contract(demo.id))
                job = service.create_render_clip_job(
                    demo,
                    RenderClipRequest(tickStart=100, tickEnd=740, tickRate=64),
                )
                service.claim_render_clip_job(job)
                rendered_video = store_bound_render_worker_video(service, job)
                service.apply_render_worker_result(
                    job,
                    RenderWorkerResult(
                        status="completed",
                        videoUrl=rendered_video.url,
                        storageKey=rendered_video.storage_key,
                        tickStart=100,
                        tickEnd=740,
                        tickRate=64,
                        durationSeconds=10,
                    ),
                )

                with self.assertRaisesRegex(ValueError, "already completed"):
                    service.apply_render_worker_result(
                        job,
                        RenderWorkerResult(
                            status="failed",
                            tickStart=100,
                            tickEnd=740,
                            tickRate=64,
                            durationSeconds=10,
                            errorMessage="late failure",
                        ),
                    )

                db.refresh(job)
                video = service.get_video_status(demo)
                self.assertEqual(job.status, "completed")
                self.assertIsNone(job.error_message)
                self.assertEqual(video["status"], "ready")
                self.assertEqual(video["source"], "rendered")


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
        "players": [
            {"id": "player-1", "name": "Player", "side": "CT"},
            {"id": "t-entry", "name": "Entry", "side": "T", "steamId": "76561190000000001"},
        ],
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


def persist_replay(service: DemoService, demo: Demo, replay: dict) -> str:
    reference = service.write_replay_blob(demo.id, replay)
    demo.replay_storage_key = reference
    service.db.commit()
    return reference


def store_accepted_video(
    service: DemoService,
    demo: Demo,
    filename: str = "rendered.mp4",
    payload: bytes = b"rendered-video",
) -> StoredVideoUpload:
    return store_video_artifact(
        owner_id=demo.owner_id,
        demo_id=demo.id,
        upload=FakeUpload(filename, payload, "video/mp4"),
        store=service.artifact_store,
    )


def store_bound_render_worker_video(
    service: DemoService,
    job: DemoJob,
    filename: str = "rendered.mp4",
    payload: bytes = b"rendered-video",
) -> StoredVideoUpload:
    stored = store_accepted_video(
        service,
        job.demo,
        filename=filename,
        payload=payload,
    )
    service.bind_render_worker_media(job, stored)
    return stored


class FakeUpload:
    def __init__(self, filename: str, payload: bytes, content_type: str):
        self.filename = filename
        self.file = io.BytesIO(payload)
        self.content_type = content_type


@contextmanager
def replay_storage_dir(path: Path):
    original = (
        settings.artifact_storage_backend,
        settings.artifact_storage_root,
        settings.replay_storage_dir,
        settings.video_storage_dir,
    )
    object.__setattr__(settings, "artifact_storage_backend", "local")
    object.__setattr__(settings, "artifact_storage_root", path / "artifacts")
    object.__setattr__(settings, "replay_storage_dir", path)
    object.__setattr__(settings, "video_storage_dir", path / "videos")
    try:
        yield
    finally:
        artifact_backend, artifact_root, replay_dir, video_dir = original
        object.__setattr__(settings, "artifact_storage_backend", artifact_backend)
        object.__setattr__(settings, "artifact_storage_root", artifact_root)
        object.__setattr__(settings, "replay_storage_dir", replay_dir)
        object.__setattr__(settings, "video_storage_dir", video_dir)


if __name__ == "__main__":
    unittest.main()
