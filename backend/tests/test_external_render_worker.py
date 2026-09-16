import hashlib
import io
import json
import tempfile
import unittest
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
from app.models import DemoJob
from app.schemas.demo import RenderClipRequest
from app.services.demo_service import DemoService
from app.services.upload_service import store_video_artifact
from app.workers.worker import process_render_clip_job

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

    def complete_job(self, job_id: str, payload: bytes = b"rendered-video") -> dict:
        claimed = self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.headers)
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
            snapshot = service._job_output_artifact(db.get(DemoJob, first))
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
            with patch.object(service, "load_replay_blob", wraps=service.load_replay_blob) as load_replay:
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


if __name__ == "__main__":
    unittest.main()
