import hashlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import demos, private_media
from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.core.redis import get_redis_client
from app.models import DemoJob
from app.schemas.demo import RenderClipRequest
from app.services.demo_service import (
    RENDER_CLIP_IDLE_RECLAIM_SECONDS,
    RENDER_CLIP_MAX_ATTEMPTS,
    RENDER_CLIP_STALE_AFTER_SECONDS,
    RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE,
    RENDER_TIMED_OUT_PUBLIC_MESSAGE,
    DemoService,
)
from app.services.upload_service import store_video_artifact
from app.workers.worker import (
    process_render_clip_job,
    sweep_stale_render_clip_jobs,
    sweep_unclaimed_render_clip_jobs,
)

XELEX_ID = "76561198998266210"
OTHER_ID = "76561190000000001"
SOURCE_BYTES = b"PBDEMS2\0" + b"\0" * 64


class ExternalRenderWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        changed = {
            "auth_mode": "development",
            "artifact_storage_backend": "local",
            "artifact_storage_root": root / "artifacts",
            "replay_storage_dir": root / "replays",
            "render_worker_mode": "external",
        }
        self.original_settings = {key: getattr(settings, key) for key in changed}
        for key, value in changed.items():
            object.__setattr__(settings, key, value)
        self.app = FastAPI()
        self.app.include_router(demos.router)
        self.app.include_router(private_media.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        # Without this the heartbeat would reach whatever Redis the developer
        # happens to have running locally.
        self.redis = FakeRedis()
        self.app.dependency_overrides[get_redis_client] = lambda: self.redis
        self.client = TestClient(self.app)
        self.headers = {"X-Render-Worker-Token": settings.render_worker_token}
        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            prepared = service.prepare_real_demo(
                stream=io.BytesIO(SOURCE_BYTES), filename="sample.dem",
                content_type="application/octet-stream",
            )
            service.commit_prepared_real_demo(prepared)
            demo = prepared.demo
            demo.status = prepared.job.status = "completed"
            demo.map_name = "de_nuke"
            self.demo_id = demo.id
            replay = {
                "demoId": demo.id, "mapName": "de_nuke", "tickRate": 64,
                "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 5760}],
                "players": [{"id": XELEX_ID, "name": "xelex", "side": "T"},
                            {"id": OTHER_ID, "name": "other", "side": "CT"}],
                "frames": [], "events": [],
                "video": {"status": "ready", "source": "mock", "url": None,
                          "tickStart": 0, "tickEnd": 5760, "tickRate": 64,
                          "durationSeconds": 90, "timeOriginSeconds": 0},
            }
            demo.replay_storage_key = service.write_replay_blob(demo.id, replay)
            db.commit()

    def tearDown(self) -> None:
        self.client.close()
        for key, value in self.original_settings.items():
            object.__setattr__(settings, key, value)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def override_get_db(self):
        with self.Session() as db:
            yield db

    def create_job(self, **overrides) -> str:
        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            request = RenderClipRequest(**{
                "playerId": XELEX_ID, "tickStart": 640, "tickEnd": 1920,
                "tickRate": 64, **overrides,
            })
            return service.create_render_clip_job(service.get_demo(self.demo_id), request).id

    def complete_job(self, job_id: str, payload: bytes = b"rendered-video", *, claim: bool = True) -> dict:
        claimed = self.client.get(
            f"/render-worker/jobs/{job_id}/manifest",
            params={"claim": claim},
            headers=self.headers,
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        manifest = claimed.json()
        upload = self.client.post(
            f"/render-worker/jobs/{job_id}/media", headers=self.headers,
            files={"file": ("clip.mp4", payload, "video/mp4")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        result = self.client.post(
            f"/render-worker/jobs/{job_id}/result", headers=self.headers,
            json={"status": "completed", "storageKey": upload.json()["storageKey"],
                  "tickStart": manifest["tickStart"], "tickEnd": manifest["tickEnd"],
                  "tickRate": manifest["tickRate"], "timeOriginSeconds": 1.25,
                  "durationSeconds": (manifest["tickEnd"] - manifest["tickStart"]) / manifest["tickRate"] + 1.25},
        )
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["job"]["video"]

    def request_clip(self, **overrides):
        return self.client.post(f"/demos/{self.demo_id}/render/clip", json={
            "playerId": XELEX_ID, "tickStart": 640, "tickEnd": 1920,
            "tickRate": 64, **overrides,
        })

    def test_unreadable_replay_artifact_answers_409_and_names_the_fix(self) -> None:
        # The status used to be chosen by substring-matching the error message,
        # so rewording the error silently downgraded this to 400 and the client
        # stopped reading it as "the demo is in a state you can recover from".
        # Pin the code and the wording separately.
        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            demo = service.get_demo(self.demo_id)
            self.assertEqual(demo.status, "completed")
            service.artifact_store.delete(demo.replay_storage_key)
        response = self.request_clip(eventId="replay-artifact-gone")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Re-parse the demo to rebuild it", response.json()["detail"])

    def test_identical_active_requests_reuse_job_across_event_and_player_alias(self) -> None:
        with patch("app.services.demo_service.get_redis_client") as redis_factory:
            first = self.request_clip(eventId="event-a")
            self.assertEqual(first.status_code, 201, first.text)
            job_id = first.json()["job_id"]
            for status in ("queued", "pending", "rendering"):
                with self.Session() as db:
                    db.get(DemoJob, job_id).status = status
                    db.commit()
                reused = self.request_clip(playerId=None, povSteamId=XELEX_ID, eventId="event-b")
                self.assertEqual(reused.status_code, 201, reused.text)
                self.assertEqual(reused.json()["job_id"], job_id)
            redis_factory.assert_not_called()
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).filter(DemoJob.job_type == "render_clip").count(), 1)

    def test_completed_clip_reuses_own_video_after_another_clip_is_active(self) -> None:
        first = self.create_job()
        first_video = self.complete_job(first, b"first-video")
        second = self.create_job(tickStart=1920, tickEnd=3200)
        second_video = self.complete_job(second, b"second-video")
        reused = self.request_clip(eventId="new-card-same-footage")
        self.assertEqual(reused.status_code, 201, reused.text)
        self.assertEqual(reused.json()["job_id"], first)
        self.assertEqual(reused.json()["video"], first_video)
        self.assertNotEqual(first_video["url"], second_video["url"])
        self.assertEqual(self.client.get(first_video["url"]).content, b"first-video")
        self.assertEqual(self.client.get(second_video["url"]).content, b"second-video")
        self.assertEqual(self.client.get(f"/demos/{self.demo_id}/media/video").content, b"second-video")

    def test_pending_and_failed_job_preserve_playable_completed_video(self) -> None:
        first = self.create_job()
        self.complete_job(first)
        before = self.client.get(f"/demos/{self.demo_id}/video").json()
        second = self.create_job(tickStart=1920, tickEnd=3200)
        self.assertEqual(self.client.get(f"/demos/{self.demo_id}/video").json(), before)
        self.client.get(f"/render-worker/jobs/{second}/manifest", headers=self.headers)
        self.assertEqual(self.client.get(f"/demos/{self.demo_id}/video").json(), before)
        failed = self.client.post(
            f"/render-worker/jobs/{second}/result", headers=self.headers,
            json={"status": "failed", "tickStart": 1920, "tickEnd": 3200,
                  "tickRate": 64, "durationSeconds": 20, "errorMessage": "capture unavailable"},
        )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(self.client.get(f"/demos/{self.demo_id}/video").json(), before)
        self.assertNotEqual(self.create_job(tickStart=1920, tickEnd=3200), second)

    def test_missing_completed_output_allows_new_render(self) -> None:
        first = self.create_job()
        video = self.complete_job(first)
        with self.Session() as db:
            service = DemoService.for_internal(db)
            snapshot = service.worker_media.job_output_artifact(db.get(DemoJob, first))
            service.artifact_store.delete(snapshot.reference, expected_generation=snapshot.generation)
        self.assertEqual(self.client.get(video["url"]).status_code, 404)
        jobs = self.client.get(f"/demos/{self.demo_id}/render/jobs").json()
        self.assertIsNone(next(job for job in jobs if job["job_id"] == first)["video"])
        self.assertNotEqual(self.create_job(), first)

    def test_stale_worker_refreshes_demo_before_marking_another_clip_rendering(self) -> None:
        first = self.create_job()
        second = self.create_job(tickStart=1920, tickEnd=3200)
        with self.Session() as stale_db:
            stale_service = DemoService.for_internal(stale_db)
            stale_job = stale_service.get_render_clip_job(second)
            self.assertEqual(stale_service.get_video_status(stale_job.demo)["status"], "queued")
            first_video = self.complete_job(first)
            stale_service.claim_render_clip_job(stale_job)
        active = self.client.get(f"/demos/{self.demo_id}/video").json()
        self.assertEqual(active["renderJobId"], first)
        self.assertEqual(active["status"], "ready")
        self.assertEqual(self.client.get(first_video["url"]).status_code, 200)

    def test_stale_media_upload_cannot_replace_a_completed_clip(self) -> None:
        first = self.create_job()
        claimed = self.client.get(f"/render-worker/jobs/{first}/manifest", headers=self.headers)
        self.assertEqual(claimed.status_code, 200, claimed.text)
        with self.Session() as stale_db:
            stale_service = DemoService.for_internal(stale_db)
            stale_job = stale_service.get_render_clip_job(first)
            self.assertEqual(stale_job.status, "rendering")
            upload = self.client.post(
                f"/render-worker/jobs/{first}/media", headers=self.headers,
                files={"file": ("original.mp4", b"original-saved-video", "video/mp4")},
            )
            self.assertEqual(upload.status_code, 200, upload.text)
            completed = self.client.post(
                f"/render-worker/jobs/{first}/result", headers=self.headers,
                json={"status": "completed", "storageKey": upload.json()["storageKey"],
                      "tickStart": 640, "tickEnd": 1920, "tickRate": 64,
                      "durationSeconds": 20, "timeOriginSeconds": 0},
            )
            self.assertEqual(completed.status_code, 200, completed.text)
            saved_video = completed.json()["job"]["video"]
            with self.Session() as current_db:
                saved_metadata = current_db.get(DemoJob, first).metadata_json

            from starlette.datastructures import Headers, UploadFile
            late_upload = store_video_artifact(
                owner_id="dev-user", demo_id=self.demo_id,
                upload=UploadFile(
                    file=io.BytesIO(b"late-replacement-video"), filename="late.mp4",
                    headers=Headers({"content-type": "video/mp4"}),
                ),
                store=stale_service.artifact_store,
            )
            with self.assertRaisesRegex(ValueError, "requires a rendering job"):
                stale_service.bind_render_worker_media(stale_job, late_upload)
            stale_db.rollback()

        with self.Session() as db:
            job = db.get(DemoJob, first)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.metadata_json, saved_metadata)
        self.assertEqual(self.client.get(saved_video["url"]).content, b"original-saved-video")
        self.assertEqual(self.request_clip().json()["video"], saved_video)

    def test_render_identity_changes_require_a_distinct_job(self) -> None:
        first = self.create_job()
        for overrides in (
            {"tickStart": 641}, {"tickEnd": 1921},
            {"playerId": OTHER_ID}, {"renderPreset": "selected_tick_v1"},
        ):
            self.assertNotEqual(self.create_job(**overrides), first)
        with self.Session() as db:
            job = db.get(DemoJob, first)
            metadata = json.loads(job.metadata_json)
            metadata["sourceArtifact"]["sha256"] = "0" * 64
            job.metadata_json = json.dumps(metadata)
            db.commit()
        self.assertNotEqual(self.create_job(), first)

    def test_legacy_active_clip_is_readable_without_writes_and_retained_on_replacement(self) -> None:
        first = self.create_job()
        original_video = self.complete_job(first, b"legacy-video")
        with self.Session() as db:
            job = db.get(DemoJob, first)
            metadata = json.loads(job.metadata_json)
            metadata.pop("completedVideo")
            job.metadata_json = json.dumps(metadata)
            db.commit()
            original_metadata = job.metadata_json
        listed = self.client.get(f"/demos/{self.demo_id}/render/jobs").json()
        self.assertEqual(listed[0]["video"], original_video)
        with self.Session() as db:
            self.assertEqual(db.get(DemoJob, first).metadata_json, original_metadata)
        self.assertEqual(self.request_clip().json()["job_id"], first)
        second = self.create_job(tickStart=1920, tickEnd=3200)
        self.complete_job(second)
        listed = self.client.get(f"/demos/{self.demo_id}/render/jobs").json()
        self.assertEqual(next(job for job in listed if job["job_id"] == first)["video"], original_video)
        self.assertEqual(self.client.get(original_video["url"]).content, b"legacy-video")
        with self.Session() as db:
            self.assertEqual(json.loads(db.get(DemoJob, first).metadata_json)["completedVideo"]["timeOriginSeconds"], 1.25)

    def test_manual_upload_preserves_completed_render_artifact(self) -> None:
        first = self.create_job()
        video = self.complete_job(first, b"kept-render")
        response = self.client.post(
            f"/demos/{self.demo_id}/video/upload",
            files={"file": ("manual.mp4", b"new-manual-video", "video/mp4")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(video["url"]).content, b"kept-render")
        self.assertEqual(self.request_clip().json()["video"], video)

    def test_saved_clip_media_has_owner_demo_job_and_range_boundaries(self) -> None:
        first = self.create_job()
        video = self.complete_job(first, b"0123456789abcdef")
        url = video["url"]
        full = self.client.get(url)
        self.assertEqual(full.status_code, 200, full.text)
        self.assertEqual(full.headers["cache-control"], "private, no-store")
        self.assertEqual(full.headers["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(full.headers["x-content-type-options"], "nosniff")
        ranged = self.client.get(url, headers={"Range": "bytes=2-5"})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, b"2345")
        self.assertEqual(ranged.headers["content-range"], "bytes 2-5/16")
        head = self.client.head(url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["content-length"], "16")
        self.assertEqual(self.client.get(url, headers={"Range": "bytes=99-100"}).status_code, 416)
        self.assertEqual(self.client.get(url, headers={"X-Dev-User-Id": "other-owner"}).status_code, 404)
        self.assertEqual(self.client.get(url.replace(self.demo_id, "different-demo")).status_code, 404)
        self.assertEqual(self.client.get(url.replace(first, "different-job")).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)
        original_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "production")
        try:
            from app.core.auth import get_current_owner_id
            self.app.dependency_overrides[get_current_owner_id] = lambda: "dev-user"
            self.assertEqual(self.client.get(url, headers={"Sec-Fetch-Site": "same-site"}).status_code, 404)
        finally:
            self.app.dependency_overrides.pop(get_current_owner_id, None)
            object.__setattr__(settings, "auth_mode", original_mode)

    def test_saved_video_metadata_never_exposes_artifact_or_unvalidated_output(self) -> None:
        first = self.create_job()
        video = self.complete_job(first)
        listed = self.client.get(f"/demos/{self.demo_id}/render/jobs")
        self.assertNotIn("artifact://", listed.text)
        self.assertNotIn("completedVideo", listed.text)
        self.assertNotIn(str(self.temp_dir.name), listed.text)
        with self.Session() as db:
            job = db.get(DemoJob, first)
            metadata = json.loads(job.metadata_json)
            metadata["outputArtifact"]["generation"] = "wrong-generation"
            job.metadata_json = json.dumps(metadata)
            db.commit()
        self.assertIsNone(self.client.get(f"/demos/{self.demo_id}/render/jobs").json()[0]["video"])
        self.assertEqual(self.client.get(video["url"]).status_code, 404)

    def test_history_does_not_hide_saved_clip_after_twenty_newer_requests(self) -> None:
        first = self.create_job()
        video = self.complete_job(first)
        for index in range(21):
            self.create_job(tickStart=2000 + index, tickEnd=3280 + index)
        jobs = self.client.get(f"/demos/{self.demo_id}/render/jobs").json()
        self.assertEqual(len(jobs), 22)
        self.assertEqual(next(job for job in jobs if job["job_id"] == first)["video"], video)
        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            with patch.object(service.replay, "load_replay_blob", wraps=service.replay.load_replay_blob) as load_replay:
                self.assertEqual(len(service.list_render_clip_jobs(service.get_demo(self.demo_id))), 22)
                self.assertLessEqual(load_replay.call_count, 2)

    def test_external_dispatch_keeps_durable_job_and_stub_ignores_stale_delivery(self) -> None:
        with patch("app.services.demo_service.get_redis_client") as redis_factory:
            job_id = self.create_job()
        redis_factory.assert_not_called()
        with self.Session() as db:
            service = DemoService.for_internal(db)
            job = service.get_render_clip_job(job_id)
            process_render_clip_job(db, job.demo, job)
            db.refresh(job)
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.attempts, 0)
            self.assertEqual(json.loads(job.metadata_json)["povSteamId"], XELEX_ID)
            self.assertNotIn("sourceArtifact", service.render_job_status(job).metadata)

    def test_manifest_and_source_download_pin_length_and_checksum_without_host_paths(self) -> None:
        job_id = self.create_job()
        response = self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        manifest = response.json()
        self.assertEqual(manifest["povSteamId"], XELEX_ID)
        self.assertEqual(manifest["demoFilePath"], "")
        self.assertEqual(manifest["sourceSizeBytes"], len(SOURCE_BYTES))
        self.assertEqual(manifest["sourceSha256"], hashlib.sha256(SOURCE_BYTES).hexdigest())
        self.assertNotIn(str(self.temp_dir.name), response.text)
        downloaded = self.client.get(manifest["demoDownloadPath"], headers=self.headers)
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, SOURCE_BYTES)
        self.assertEqual(downloaded.headers["content-length"], str(len(SOURCE_BYTES)))
        self.assertEqual(downloaded.headers["x-content-sha256"], manifest["sourceSha256"])
        self.assertEqual(downloaded.headers["cache-control"], "private, no-store")

    def test_source_requires_service_token_and_rendering_job(self) -> None:
        job_id = self.create_job()
        url = f"/render-worker/jobs/{job_id}/source"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 409)
        self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.headers)
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            job.status = "failed"
            db.commit()
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.get("/render-worker/jobs/missing/source", headers=self.headers).status_code, 404)

    def test_changed_source_generation_or_demo_binding_cannot_be_downloaded(self) -> None:
        job_id = self.create_job()
        self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.headers)
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            metadata = json.loads(job.metadata_json)
            metadata["sourceArtifact"]["generation"] = "tampered-generation"
            job.metadata_json = json.dumps(metadata)
            db.commit()
        response = self.client.get(f"/render-worker/jobs/{job_id}/source", headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(str(self.temp_dir.name), response.text)
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            job.demo.owner_id = "other-owner"
            db.commit()
        response = self.client.get(f"/render-worker/jobs/{job_id}/source", headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_stale_session_cannot_claim_same_job_twice(self) -> None:
        job_id = self.create_job()
        with self.Session() as first_db, self.Session() as stale_db:
            first = DemoService.for_internal(first_db)
            stale = DemoService.for_internal(stale_db)
            stale_job = stale.get_render_clip_job(job_id)
            first.claim_render_clip_job(first.get_render_clip_job(job_id))
            self.assertEqual(stale_job.status, "queued")
            with self.assertRaisesRegex(ValueError, "already claimed"):
                stale.claim_render_clip_job(stale_job)
            stale_db.refresh(stale_job)
            self.assertEqual(stale_job.attempts, 1)
        rejected = self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.headers)
        self.assertEqual(rejected.status_code, 409)
        inspected = self.client.get(f"/render-worker/jobs/{job_id}/manifest?claim=false", headers=self.headers)
        self.assertEqual(inspected.status_code, 200)

    def test_pov_and_tick_rate_must_match_the_stored_replay(self) -> None:
        for overrides in (
            {"povSteamId": OTHER_ID}, {"playerId": "missing"},
            {"playerId": None, "povSteamId": "76561190000000099"},
            {"tickRate": 128}, {"playerId": None},
            {"tickStart": -1, "tickEnd": 100}, {"tickStart": 5700, "tickEnd": 5800},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.create_job(**overrides)
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).filter(DemoJob.job_type == "render_clip").count(), 0)

    def test_invalid_dispatch_mode_fails_both_api_and_worker_startup(self) -> None:
        config = Settings(auth_mode="development", render_worker_mode="typo")
        for validate in (config.validate_runtime_configuration, config.validate_worker_runtime_configuration):
            with self.assertRaisesRegex(RuntimeError, "RENDER_WORKER_MODE"):
                validate()

    def test_completed_video_uses_trusted_job_pov_and_job_id_not_callback_fields(self) -> None:
        job_id = self.create_job()
        self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.headers)
        upload = self.client.post(
            f"/render-worker/jobs/{job_id}/media", headers=self.headers,
            files={"file": ("clip.mp4", b"rendered-video", "video/mp4")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        media = upload.json()
        response = self.client.post(
            f"/render-worker/jobs/{job_id}/result", headers=self.headers,
            json={"status": "completed", "videoUrl": media["videoUrl"],
                  "storageKey": media["storageKey"], "tickStart": 640,
                  "tickEnd": 1920, "tickRate": 64, "durationSeconds": 20,
                  "povSteamId": OTHER_ID,
                  "renderJobId": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["video"]["povSteamId"], XELEX_ID)
        self.assertEqual(response.json()["video"]["renderJobId"], job_id)
        with self.Session() as db:
            service = DemoService.for_internal(db)
            demo = service.get_render_clip_job(job_id).demo
            for video in (service.get_video_status(demo), service.public_video_status(demo), service.public_replay(demo)["video"]):
                self.assertEqual(video["povSteamId"], XELEX_ID)
                self.assertEqual(video["renderJobId"], job_id)

        replacement = self.client.post(
            f"/demos/{self.demo_id}/video/upload",
            files={"file": ("manual.mp4", b"manual-video", "video/mp4")},
        )
        self.assertEqual(replacement.status_code, 200, replacement.text)
        self.assertIsNone(replacement.json()["povSteamId"])
        self.assertIsNone(replacement.json()["renderJobId"])

    def test_polling_records_a_heartbeat_even_with_no_work_to_claim(self) -> None:
        self.assertEqual(self.worker_status()["status"], "never_seen")

        empty = self.client.get("/render-worker/jobs/next", headers=self.headers)

        self.assertEqual(empty.status_code, 204)
        # An idle renderer polls an empty queue every 5 seconds; that is exactly
        # the state this endpoint has to prove is healthy.
        status = self.worker_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["age_seconds"], 0)
        self.assertIsNotNone(status["last_seen_at"])

    def test_claiming_a_job_records_a_heartbeat_and_an_invalid_token_does_not(self) -> None:
        job_id = self.create_job()

        rejected = self.client.get(
            "/render-worker/jobs/next",
            headers={"X-Render-Worker-Token": "wrong-token"},
        )

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(self.worker_status()["status"], "never_seen")

        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)

        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["jobId"], job_id)
        self.assertTrue(self.worker_status()["connected"])

    def test_worker_status_reports_the_configured_mode_before_any_poll(self) -> None:
        status = self.worker_status()

        self.assertTrue(status["required"])
        self.assertEqual(status["mode"], "external")
        self.assertFalse(status["connected"])
        self.assertFalse(status["busy_rendering"])
        self.assertIsNone(status["age_seconds"])

    def test_clip_request_still_queues_durably_while_the_renderer_is_offline(self) -> None:
        with patch("app.services.demo_service.get_redis_client") as redis_factory:
            response = self.request_clip()
        redis_factory.assert_not_called()

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        # The caller learns immediately rather than watching "queued" until the
        # next poll, but the job itself is untouched: it waits for the renderer.
        self.assertFalse(body["render_worker"]["connected"])
        self.assertTrue(body["render_worker"]["required"])
        self.assertEqual(body["render_worker"]["status"], "never_seen")
        with self.Session() as db:
            self.assertEqual(db.get(DemoJob, body["job_id"]).status, "queued")

        self.client.get("/render-worker/jobs/next", headers=self.headers)
        second = self.request_clip()
        self.assertEqual(second.status_code, 201, second.text)
        self.assertTrue(second.json()["render_worker"]["connected"])

    def test_a_render_in_progress_counts_as_liveness_once_the_heartbeat_goes_stale(self) -> None:
        job_id = self.create_job()
        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)
        self.assertEqual(claimed.status_code, 200, claimed.text)
        with self.Session() as db:
            self.assertEqual(db.get(DemoJob, job_id).status, "rendering")

        # A single render blocks the 5s poll loop for minutes, so the heartbeat
        # ages out long before anything is actually wrong.
        self.redis.values.clear()

        status = self.worker_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["status"], "rendering")
        self.assertTrue(status["busy_rendering"])

    def test_render_worker_endpoints_survive_a_redis_outage(self) -> None:
        job_id = self.create_job()
        self.app.dependency_overrides[get_redis_client] = BrokenRedis

        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)

        # The heartbeat is an observability signal; losing Redis must not take a
        # healthy renderer offline.
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["jobId"], job_id)
        status = self.worker_status()
        self.assertEqual(status["status"], "rendering")
        self.assertTrue(status["connected"])

    def worker_status(self) -> dict:
        response = self.client.get("/render/worker")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # --- timeout reclamation -------------------------------------------------

    def claim(self, job_id: str) -> None:
        """Claim through the service, bypassing the endpoint's own reclaim."""
        with self.Session() as db:
            service = DemoService.for_internal(db)
            service.claim_render_clip_job(service.get_render_clip_job(job_id))

    def abandon(self, job_id: str, seconds: int = RENDER_CLIP_STALE_AFTER_SECONDS + 60) -> None:
        """Rewind started_at so a claimed job looks like a dead worker's orphan."""
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            job.started_at = datetime.now(UTC) - timedelta(seconds=seconds)
            db.commit()

    def reclaim(self, older_than_seconds: int = RENDER_CLIP_STALE_AFTER_SECONDS) -> list[str]:
        with self.Session() as db:
            return DemoService.for_internal(db).reclaim_stale_render_clip_jobs(
                older_than_seconds=older_than_seconds,
            )

    def job_state(self, job_id: str) -> dict:
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            return {
                "status": job.status,
                "attempts": job.attempts,
                "started_at": job.started_at,
                "queued_at": job.queued_at,
                "error_message": job.error_message,
            }

    def video_state(self) -> dict:
        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            return service.get_video_status(service.get_demo(self.demo_id))

    def test_a_job_abandoned_mid_render_returns_to_the_queue_with_its_video(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.assertEqual(self.job_state(job_id)["status"], "rendering")
        self.assertEqual(self.video_state()["status"], "rendering")

        self.abandon(job_id)

        self.assertEqual(self.reclaim(), [job_id])
        job = self.job_state(job_id)
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["started_at"])
        # The whole point of doing this per job rather than in one bulk UPDATE:
        # the demo's video status has to come back with it, or the page shows
        # "generating" over a job that is plainly queued.
        self.assertEqual(self.video_state()["status"], "queued")

    def test_a_reclaimed_job_is_visible_to_the_next_poller_again(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.abandon(job_id)
        self.reclaim()

        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)

        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["jobId"], job_id)
        self.assertEqual(self.job_state(job_id)["attempts"], 2)

    def test_a_requeued_job_is_not_reclaimed_again_the_moment_it_restarts(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.abandon(job_id)
        self.reclaim()

        self.claim(job_id)

        # claim_render_clip_job stamps started_at through a coalesce, so it only
        # ever writes the first claim's time. If the requeue had kept the old
        # value, this second claim would inherit it and be judged stale on the
        # very next pass -- reclaimed forever, never finished.
        self.assertEqual(self.job_state(job_id)["status"], "rendering")
        self.assertEqual(self.reclaim(), [])
        self.assertEqual(self.job_state(job_id)["status"], "rendering")

    def test_a_job_claimed_moments_ago_is_left_alone(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)

        self.assertEqual(self.reclaim(), [])
        self.assertEqual(self.reclaim(RENDER_CLIP_IDLE_RECLAIM_SECONDS), [])
        self.assertEqual(self.job_state(job_id)["status"], "rendering")

    def test_a_clip_that_keeps_killing_the_renderer_fails_after_the_cap(self) -> None:
        job_id = self.create_job()
        for attempt in range(1, RENDER_CLIP_MAX_ATTEMPTS):
            self.claim(job_id)
            self.abandon(job_id)
            self.assertEqual(self.reclaim(), [job_id])
            self.assertEqual(self.job_state(job_id)["attempts"], attempt)

        self.claim(job_id)
        self.abandon(job_id)

        self.assertEqual(self.reclaim(), [job_id])
        job = self.job_state(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["attempts"], RENDER_CLIP_MAX_ATTEMPTS)
        # Whatever the internal reason, the caller gets the vetted public text.
        self.assertEqual(job["error_message"], RENDER_TIMED_OUT_PUBLIC_MESSAGE)
        self.assertEqual(self.video_state()["status"], "failed")
        self.assertEqual(self.video_state()["errorCode"], "RENDER_TIMED_OUT")

        status = self.client.get(f"/demos/{self.demo_id}/render/jobs")
        self.assertEqual(status.status_code, 200, status.text)
        reported = next(item for item in status.json() if item["job_id"] == job_id)
        # render_job_status can only pass error_code=None -- demo_jobs has no
        # such column -- so asserting on the row alone would miss the page
        # falling back to the generic "could not be produced".
        self.assertEqual(reported["error_code"], "RENDER_TIMED_OUT")
        self.assertEqual(reported["error_message"], RENDER_TIMED_OUT_PUBLIC_MESSAGE)

    def test_the_next_poll_after_a_crash_hands_the_orphan_straight_back(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.abandon(job_id, RENDER_CLIP_IDLE_RECLAIM_SECONDS + 1)

        # A worker asking for work is not rendering, so this request is itself
        # the evidence that the job it is holding was orphaned.
        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)

        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["jobId"], job_id)
        self.assertEqual(self.job_state(job_id)["attempts"], 2)

    def test_the_fast_path_is_off_when_several_workers_share_the_api(self) -> None:
        object.__setattr__(settings, "render_clip_single_consumer", False)
        self.addCleanup(object.__setattr__, settings, "render_clip_single_consumer", True)
        job_id = self.create_job()
        self.claim(job_id)
        self.abandon(job_id, RENDER_CLIP_IDLE_RECLAIM_SECONDS + 1)

        empty = self.client.get("/render-worker/jobs/next", headers=self.headers)

        # Another worker may well be rendering it; only the timeout may judge.
        self.assertEqual(empty.status_code, 204)
        self.assertEqual(self.job_state(job_id)["status"], "rendering")
        # The switch gates the endpoint, not the mechanism: the sweep still runs.
        self.assertEqual(self.reclaim(RENDER_CLIP_IDLE_RECLAIM_SECONDS), [job_id])

    def test_finished_jobs_are_never_reclaimed(self) -> None:
        completed_id = self.create_job()
        self.complete_job(completed_id)
        failed_id = self.create_job(tickStart=1920, tickEnd=2560)
        self.claim(failed_id)
        with self.Session() as db:
            service = DemoService.for_internal(db)
            service.fail_render_clip_job(
                service.get_render_clip_job(failed_id), "boom", error_code="RENDER_FAILED",
            )
        for job_id in (completed_id, failed_id):
            self.abandon(job_id)

        self.assertEqual(self.reclaim(), [])
        self.assertEqual(self.job_state(completed_id)["status"], "completed")
        self.assertEqual(self.job_state(failed_id)["status"], "failed")

    def test_a_worker_that_finishes_during_the_sweep_keeps_its_result(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.abandon(job_id)

        with self.Session() as db:
            candidates = DemoService.for_internal(db).render._stale_render_clip_jobs(
                datetime.now(UTC), limit=20,
            )
            self.assertEqual([job.id for job in candidates], [job_id])

        # The worker was alive after all and reports in right here, between the
        # sweep's candidate query and the update that would requeue the job.
        self.complete_job(job_id, claim=False)

        with self.Session() as db:
            service = DemoService.for_internal(db)
            stale = service.get_render_clip_job(job_id)
            self.assertFalse(service.render._requeue_render_clip_job(stale))

        self.assertEqual(self.job_state(job_id)["status"], "completed")
        self.assertEqual(self.video_state()["status"], "ready")

    def test_a_race_on_one_stale_job_does_not_cost_the_rest_of_the_batch(self) -> None:
        raced_id = self.create_job()
        self.claim(raced_id)
        with self.Session() as db:
            db.get(DemoJob, raced_id).attempts = RENDER_CLIP_MAX_ATTEMPTS
            db.commit()
        self.abandon(raced_id)
        other_id = self.create_job(tickStart=1920, tickEnd=2560)
        self.claim(other_id)
        self.abandon(other_id)

        # The capped job's worker reports in before the sweep gets to its row,
        # so failing it raises -- the one candidate ahead of every other.
        self.complete_job(raced_id, claim=False)

        with self.Session() as db:
            service = DemoService.for_internal(db)
            candidates = [service.get_render_clip_job(raced_id), service.get_render_clip_job(other_id)]
            with patch.object(service.render, "_stale_render_clip_jobs", return_value=candidates):
                reclaimed = service.reclaim_stale_render_clip_jobs(
                    older_than_seconds=RENDER_CLIP_STALE_AFTER_SECONDS,
                )

        self.assertEqual(reclaimed, [other_id])
        self.assertEqual(self.job_state(raced_id)["status"], "completed")
        self.assertEqual(self.job_state(other_id)["status"], "queued")

    def test_the_worker_sweep_reclaims_with_no_renderer_polling_at_all(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.abandon(job_id)

        with patch("app.workers.worker.SessionLocal", self.Session):
            reclaimed = sweep_stale_render_clip_jobs(force=True)

            self.assertEqual(reclaimed, [job_id])
            self.assertEqual(self.job_state(job_id)["status"], "queued")
            # Rate-limited: the idle tick runs every 5 seconds and this is a query.
            self.claim(job_id)
            self.abandon(job_id)
            self.assertEqual(sweep_stale_render_clip_jobs(), [])

    # --- manual retry --------------------------------------------------------

    def retry(self, job_id: str, demo_id: str | None = None):
        return self.client.post(f"/demos/{demo_id or self.demo_id}/render/jobs/{job_id}/retry")

    def fail_job(self, job_id: str) -> None:
        # Not named "fail": that is unittest.TestCase.fail, and shadowing it
        # makes every failing assertion in this class raise UnmappedInstanceError
        # from inside this helper instead of printing its own message.
        with self.Session() as db:
            service = DemoService.for_internal(db)
            service.fail_render_clip_job(
                service.get_render_clip_job(job_id), "capture unavailable",
                error_code="RENDER_FAILED",
            )

    def test_retrying_a_failed_clip_requeues_the_row_instead_of_cloning_it(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.fail_job(job_id)
        self.assertEqual(self.video_state()["status"], "failed")

        response = self.retry(job_id)

        # 200, not the create endpoint's 201: nothing new came into being.
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["job_id"], job_id)
        job = self.job_state(job_id)
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["started_at"])
        self.assertIsNone(job["error_message"])
        self.assertEqual(job["attempts"], 0)
        # The page reads the demo's video status, not the job row, so it has to
        # come back with it or the clip stays "failed" over a queued job.
        self.assertEqual(self.video_state()["status"], "queued")
        with self.Session() as db:
            self.assertEqual(
                db.query(DemoJob).filter(DemoJob.job_type == "render_clip").count(), 1,
            )

    def test_a_retried_clip_is_handed_to_the_next_poller(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.fail_job(job_id)
        self.assertEqual(self.retry(job_id).status_code, 200)

        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)

        # External mode has no Redis dispatch to repeat, so the durable queue is
        # the whole delivery mechanism: requeued has to mean claimable.
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["jobId"], job_id)
        self.assertEqual(self.job_state(job_id)["attempts"], 1)

    def test_a_clip_that_burned_its_attempt_budget_gets_a_fresh_one(self) -> None:
        job_id = self.create_job()
        for _ in range(RENDER_CLIP_MAX_ATTEMPTS):
            self.claim(job_id)
            self.abandon(job_id)
            self.reclaim()
        self.assertEqual(self.job_state(job_id)["status"], "failed")

        self.assertEqual(self.retry(job_id).status_code, 200)

        # Without the attempts reset the next sweep would fail the job straight
        # back, so the person who clicked retry never gets another render.
        self.claim(job_id)
        self.abandon(job_id)
        self.assertEqual(self.reclaim(), [job_id])
        self.assertEqual(self.job_state(job_id)["status"], "queued")

    def test_only_a_failed_clip_can_be_retried(self) -> None:
        job_id = self.create_job()
        for status in ("queued", "pending", "rendering", "completed"):
            with self.subTest(status=status):
                with self.Session() as db:
                    db.get(DemoJob, job_id).status = status
                    db.commit()

                response = self.retry(job_id)

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"], "Only failed render jobs can be retried",
                )
                self.assertEqual(self.job_state(job_id)["status"], status)

    def test_a_retry_cannot_reach_a_job_the_demo_does_not_own(self) -> None:
        job_id = self.create_job()
        self.claim(job_id)
        self.fail_job(job_id)
        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            prepared = service.prepare_real_demo(
                stream=io.BytesIO(b"PBDEMS2\0" + b"\1" * 64), filename="other.dem",
                content_type="application/octet-stream",
            )
            service.commit_prepared_real_demo(prepared)
            other_demo_id = prepared.demo.id

        # A job id is not a capability: it only works against its own demo, and
        # an unknown id is the same generic 404 as a known one asked for wrongly.
        self.assertEqual(self.retry(job_id, demo_id=other_demo_id).status_code, 404)
        self.assertEqual(self.retry("00000000-0000-0000-0000-000000000000").status_code, 404)
        self.assertEqual(self.retry(job_id, demo_id="no-such-demo").status_code, 404)
        self.assertEqual(self.job_state(job_id)["status"], "failed")

    # --- unclaimed queue timeout ---------------------------------------------

    QUEUE_TIMEOUT = 1800

    def strand(self, job_id: str, seconds: int | None = None, *, created_at: int = 0) -> None:
        """Rewind a queued job's clock so it looks like nobody ever claimed it."""
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            if seconds is not None:
                job.queued_at = datetime.now(UTC) - timedelta(seconds=seconds)
            if created_at:
                job.created_at = datetime.now(UTC) - timedelta(seconds=created_at)
            db.commit()

    def queue_sweep(self, older_than_seconds: int | None = None) -> list[str]:
        with self.Session() as db:
            return DemoService.for_internal(db).fail_unclaimed_render_clip_jobs(
                older_than_seconds=older_than_seconds or self.QUEUE_TIMEOUT,
            )

    def test_a_clip_nobody_ever_claimed_fails_instead_of_waiting_forever(self) -> None:
        job_id = self.create_job()
        self.strand(job_id, self.QUEUE_TIMEOUT + 60)

        self.assertEqual(self.queue_sweep(), [job_id])

        job = self.job_state(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_message"], RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE)
        # The clip never ran, so it has not earned a strike against its budget.
        self.assertEqual(job["attempts"], 0)
        video = self.video_state()
        self.assertEqual(video["status"], "failed")
        self.assertEqual(video["errorCode"], "RENDER_QUEUE_TIMED_OUT")

    def test_failing_an_unclaimed_clip_is_what_makes_retry_reachable(self) -> None:
        job_id = self.create_job()
        self.strand(job_id, self.QUEUE_TIMEOUT + 60)

        # Before the sweep the row is "queued", and that is the dead end: the
        # one action the page offers is refused outright.
        refused = self.retry(job_id)
        self.assertEqual(refused.status_code, 409, refused.text)

        self.assertEqual(self.queue_sweep(), [job_id])
        response = self.retry(job_id)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.job_state(job_id)["status"], "queued")
        self.assertEqual(self.video_state()["status"], "queued")

    def test_a_retried_clip_is_judged_by_its_wait_not_by_the_clips_age(self) -> None:
        job_id = self.create_job()
        # The state this deployment actually produced: a clip created days ago
        # that has been sitting on the queue ever since.
        self.strand(job_id, 9 * 86400, created_at=9 * 86400)
        self.assertEqual(self.queue_sweep(), [job_id])
        self.assertEqual(self.retry(job_id).status_code, 200)

        # created_at is still nine days old. Aging the row by it -- the obvious
        # implementation, since created_at is the only timestamp a queued row
        # used to have -- would fail the retry before any worker could claim it,
        # making the button that just became available useless.
        self.assertEqual(self.queue_sweep(), [])
        self.assertEqual(self.job_state(job_id)["status"], "queued")

        claimed = self.client.get("/render-worker/jobs/next", headers=self.headers)
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["jobId"], job_id)

    def test_a_clip_still_inside_the_window_is_left_on_the_queue(self) -> None:
        job_id = self.create_job()

        self.assertEqual(self.queue_sweep(), [])

        self.strand(job_id, self.QUEUE_TIMEOUT - 60)
        self.assertEqual(self.queue_sweep(), [])
        self.assertEqual(self.job_state(job_id)["status"], "queued")

    def test_a_clip_the_rendering_sweep_requeued_gets_a_fresh_wait(self) -> None:
        job_id = self.create_job()
        # Old enough that its original queue wait is long spent -- which is the
        # normal case for a render that ran for a while before its worker died.
        self.strand(job_id, 9 * 86400, created_at=9 * 86400)
        self.claim(job_id)
        self.abandon(job_id)

        self.assertEqual(self.reclaim(), [job_id])

        # The two sweeps share the "queued" state and would otherwise fight over
        # it: the rendering sweep puts the job back on the queue, and the queue
        # sweep would take it straight off again by a clock the requeue never
        # touched, so a dead worker would look like an absent one.
        self.assertEqual(self.queue_sweep(), [])
        self.assertEqual(self.job_state(job_id)["status"], "queued")

    def test_a_row_written_before_queued_at_existed_ages_by_created_at(self) -> None:
        job_id = self.create_job()
        with self.Session() as db:
            job = db.get(DemoJob, job_id)
            # What ensure_schema_backfills' UPDATE would have missed, and what a
            # row inserted by older code looks like.
            job.queued_at = None
            job.created_at = datetime.now(UTC) - timedelta(seconds=self.QUEUE_TIMEOUT + 60)
            db.commit()

        self.assertEqual(self.queue_sweep(), [job_id])
        self.assertEqual(self.job_state(job_id)["status"], "failed")

    def test_the_queue_sweep_leaves_rendering_and_finished_jobs_alone(self) -> None:
        rendering_id = self.create_job()
        self.claim(rendering_id)
        completed_id = self.create_job(tickStart=1920, tickEnd=2560)
        self.complete_job(completed_id)
        failed_id = self.create_job(tickStart=2560, tickEnd=3200)
        self.claim(failed_id)
        self.fail_job(failed_id)
        for job_id in (rendering_id, completed_id, failed_id):
            self.strand(job_id, 9 * 86400)

        # A rendering job belongs to the other sweep, which judges it by
        # started_at and hands it back to the queue rather than failing it.
        self.assertEqual(self.queue_sweep(), [])
        self.assertEqual(self.job_state(rendering_id)["status"], "rendering")
        self.assertEqual(self.job_state(completed_id)["status"], "completed")
        self.assertEqual(self.job_state(failed_id)["status"], "failed")

    def test_a_worker_that_claims_during_the_queue_sweep_keeps_the_job(self) -> None:
        raced_id = self.create_job()
        other_id = self.create_job(tickStart=1920, tickEnd=2560)
        self.strand(raced_id, 9 * 86400)
        self.strand(other_id, 9 * 86400)

        with self.Session() as db:
            service = DemoService.for_internal(db)
            candidates = [
                service.get_render_clip_job(raced_id),
                service.get_render_clip_job(other_id),
            ]
            # A renderer came online and claimed the first candidate between the
            # sweep's query and the failure it was about to write. Nothing in
            # fail_render_clip_job refuses a "rendering" row -- it is a perfectly
            # normal thing to fail -- so only the sweep's own re-read under a row
            # lock stands between this claim and a killed live render.
            self.claim(raced_id)
            with patch.object(service.render, "_unclaimed_render_clip_jobs", return_value=candidates):
                failed = service.fail_unclaimed_render_clip_jobs(
                    older_than_seconds=self.QUEUE_TIMEOUT,
                )

        self.assertEqual(failed, [other_id])
        self.assertEqual(self.job_state(raced_id)["status"], "rendering")
        self.assertEqual(self.job_state(other_id)["status"], "failed")

    def test_a_failed_clip_is_no_longer_handed_back_by_a_fresh_request(self) -> None:
        job_id = self.create_job()
        # Deduplication is the other half of the dead end: while the corpse is
        # "queued", asking for the same clip again returns that same row, so the
        # user cannot even route around it by clicking generate a second time.
        self.assertEqual(self.create_job(), job_id)

        self.strand(job_id, self.QUEUE_TIMEOUT + 60)
        self.assertEqual(self.queue_sweep(), [job_id])

        replacement_id = self.create_job()
        self.assertNotEqual(replacement_id, job_id)
        self.assertEqual(self.job_state(replacement_id)["status"], "queued")

    def test_the_worker_sweep_fails_unclaimed_clips_with_nothing_polling(self) -> None:
        job_id = self.create_job()
        self.strand(job_id, self.QUEUE_TIMEOUT + 60)

        with patch("app.workers.worker.SessionLocal", self.Session):
            self.assertEqual(sweep_unclaimed_render_clip_jobs(force=True), [job_id])
            self.assertEqual(self.job_state(job_id)["status"], "failed")
            # Rate-limited: the idle tick runs every 5 seconds and this is a query.
            stranded_id = self.create_job(tickStart=1920, tickEnd=2560)
            self.strand(stranded_id, self.QUEUE_TIMEOUT + 60)
            self.assertEqual(sweep_unclaimed_render_clip_jobs(), [])


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, _: int, value: str) -> None:
        self.values[key] = value


class BrokenRedis:
    """Stands in for Redis being down while the rest of the stack is fine."""

    def get(self, _: str) -> str:
        raise ConnectionError("redis is unreachable")

    def setex(self, *_: object) -> None:
        raise ConnectionError("redis is unreachable")


if __name__ == "__main__":
    unittest.main()
