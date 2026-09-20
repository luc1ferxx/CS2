import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import coaching, demos, replay, uploads
from app.core import database
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import Demo, DemoJob
from app.schemas.demo import RenderClipRequest
from app.services.artifact_intake import ArtifactIntakeService
from app.services.demo_service import DemoService

OWNER_A = "owner-a"
OWNER_B = "owner-b"


class AuthOwnerBoundaryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_artifact_root = settings.artifact_storage_root
        self.original_replay_dir = settings.replay_storage_dir
        self.original_upload_dir = settings.demo_upload_storage_dir
        self.original_video_dir = settings.video_storage_dir
        self.original_auth_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "test")
        root = Path(self.temp_dir.name)
        object.__setattr__(settings, "artifact_storage_root", root)
        object.__setattr__(settings, "replay_storage_dir", root / "replays")
        object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
        object.__setattr__(settings, "video_storage_dir", root / "videos")

        self.fake_redis = FakeRedis()
        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=self.fake_redis)
        self.redis_patch.start()

        self.app = FastAPI()
        self.app.include_router(demos.router)
        self.app.include_router(uploads.router)
        self.app.include_router(replay.router)
        self.app.include_router(coaching.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.redis_patch.stop()
        object.__setattr__(settings, "artifact_storage_root", self.original_artifact_root)
        object.__setattr__(settings, "replay_storage_dir", self.original_replay_dir)
        object.__setattr__(settings, "demo_upload_storage_dir", self.original_upload_dir)
        object.__setattr__(settings, "video_storage_dir", self.original_video_dir)
        object.__setattr__(settings, "auth_mode", self.original_auth_mode)
        self.temp_dir.cleanup()
        self.app.dependency_overrides.clear()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def test_demo_list_filters_by_current_owner(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-a", OWNER_A)
            add_demo(db, "demo-owner-b", OWNER_B)

        response = self.client.get("/demos", headers=owner_headers(OWNER_A))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], ["demo-owner-a"])
        self.assertNotIn("owner_id", response.json()[0])

    def test_demo_status_cannot_read_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B)

        response = self.client.get("/demos/demo-owner-b/status", headers=owner_headers(OWNER_A))

        self.assertEqual(response.status_code, 404)

    def test_replay_cannot_read_another_owners_demo(self) -> None:
        with self.Session() as db:
            other_demo = add_demo(db, "demo-owner-b", OWNER_B)
            bind_replay(db, other_demo, replay_contract(other_demo.id))

        response = self.client.get("/demos/demo-owner-b/replay", headers=owner_headers(OWNER_A))

        self.assertEqual(response.status_code, 404)

    def test_archived_demo_is_hidden_from_list_but_openable_by_owner(self) -> None:
        with self.Session() as db:
            archived = add_demo(db, "demo-owner-a-archived", OWNER_A, archived=True)
            bind_replay(db, archived, replay_contract(archived.id))

        list_response = self.client.get("/demos", headers=owner_headers(OWNER_A))
        status_response = self.client.get(
            "/demos/demo-owner-a-archived/status",
            headers=owner_headers(OWNER_A),
        )
        replay_response = self.client.get(
            "/demos/demo-owner-a-archived/replay",
            headers=owner_headers(OWNER_A),
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json(), [])
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json()["archived"])
        self.assertEqual(replay_response.status_code, 200)
        self.assertEqual(replay_response.json()["demoId"], "demo-owner-a-archived")

    def test_replay_response_includes_legacy_contract_diagnostics(self) -> None:
        with self.Session() as db:
            demo = add_demo(db, "demo-owner-a-legacy", OWNER_A)
            bind_replay(
                db,
                demo,
                {
                    "demoId": demo.id,
                    "mapName": "de_inferno",
                    "tickRate": 64,
                    "rounds": [{"roundNumber": 1, "startTick": 0, "endTick": 640}],
                    "players": [],
                    "frames": [],
                    "generatedAt": "2026-05-08T00:00:00Z",
                },
            )

        response = self.client.get("/demos/demo-owner-a-legacy/replay", headers=owner_headers(OWNER_A))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["events"], [])
        self.assertEqual(body["diagnostics"]["contractVersion"], "legacy")
        self.assertTrue(body["diagnostics"]["normalizedLegacy"])
        self.assertEqual(body["diagnostics"]["parserEventCount"], 0)
        self.assertIn("events", body["diagnostics"]["missingFields"])

    def test_rename_cannot_modify_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B, name="Original Name")

        response = self.client.patch(
            "/demos/demo-owner-b",
            headers=owner_headers(OWNER_A),
            json={"name": "Cross Owner Rename"},
        )

        self.assertEqual(response.status_code, 404)
        with self.Session() as db:
            demo = db.query(Demo).filter(Demo.id == "demo-owner-b").one()
            self.assertEqual(demo.name, "Original Name")

    def test_archive_cannot_modify_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B)

        response = self.client.post("/demos/demo-owner-b/archive", headers=owner_headers(OWNER_A))

        self.assertEqual(response.status_code, 404)
        with self.Session() as db:
            demo = db.query(Demo).filter(Demo.id == "demo-owner-b").one()
            self.assertFalse(demo.archived)

    def test_unarchive_cannot_modify_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B, archived=True)

        response = self.client.patch(
            "/demos/demo-owner-b",
            headers=owner_headers(OWNER_A),
            json={"archived": False},
        )

        self.assertEqual(response.status_code, 404)
        with self.Session() as db:
            demo = db.query(Demo).filter(Demo.id == "demo-owner-b").one()
            self.assertTrue(demo.archived)

    def test_mock_and_real_uploads_receive_current_owner(self) -> None:
        mock_response = self.client.post("/uploads/mock", headers=owner_headers(OWNER_A))
        real_response = self.client.post(
            "/uploads/demo",
            headers=owner_headers(OWNER_B),
            files={
                "file": (
                    "owner-b.dem",
                    b"HL2DEMO\x00owner-boundary-fixture",
                    "application/octet-stream",
                )
            },
        )

        self.assertEqual(mock_response.status_code, 201)
        self.assertEqual(real_response.status_code, 201)
        self.assertNotIn("owner_id", mock_response.json())
        self.assertNotIn("owner_id", real_response.json())
        with self.Session() as db:
            mock_demo = db.query(Demo).filter(Demo.id == mock_response.json()["id"]).one()
            real_demo = db.query(Demo).filter(Demo.id == real_response.json()["id"]).one()
            self.assertEqual(mock_demo.owner_id, OWNER_A)
            self.assertEqual(mock_demo.legacy_user_id, OWNER_A)
            self.assertEqual(real_demo.owner_id, OWNER_B)
            self.assertEqual(real_demo.legacy_user_id, OWNER_B)
            self.assertTrue(real_demo.source_storage_key.startswith("artifact://v1/accepted/source/"))
            job = (
                db.query(DemoJob)
                .filter(DemoJob.demo_id == real_demo.id, DemoJob.job_type == "real_parse")
                .one()
            )
            source_snapshot = json.loads(job.metadata_json)["sourceArtifact"]
            self.assertEqual(source_snapshot["reference"], real_demo.source_storage_key)

    def test_rejected_public_uploads_have_stable_safe_errors_and_no_dispatch(self) -> None:
        cases = (
            ("archive.zip", b"HL2DEMO\x00archive-fixture", "INTAKE_TYPE_REJECTED"),
            ("empty.dem", b"", "INTAKE_EMPTY"),
            ("short.dem", b"short", "INTAKE_TRUNCATED"),
            ("masked.dem", b"PK\x03\x04archive-payload", "INTAKE_CONTENT_MISMATCH"),
        )
        for filename, body, expected_code in cases:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/uploads/demo",
                    headers=owner_headers(OWNER_A),
                    files={"file": (filename, body, "application/octet-stream")},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["errorCode"], expected_code)
                self.assertNotIn("artifact://", response.text)
                self.assertNotIn(str(settings.artifact_storage_root), response.text)
                self.assertNotIn("traceback", response.text.lower())

        with self.Session() as db:
            self.assertEqual(db.query(Demo).count(), 0)
            self.assertEqual(db.query(DemoJob).count(), 0)
        self.assertEqual(self.fake_redis.payloads, [])
        self.assertEqual(
            [path for path in Path(settings.artifact_storage_root).rglob("*") if path.is_file()],
            [],
        )

    def test_render_clip_cannot_be_created_for_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B)

        response = self.client.post(
            "/demos/demo-owner-b/render/clip",
            headers=owner_headers(OWNER_A),
            json={"tickStart": 0, "tickEnd": 640, "tickRate": 64},
        )

        self.assertEqual(response.status_code, 404)
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).count(), 0)

    def test_render_job_list_does_not_expose_another_owners_jobs(self) -> None:
        with self.Session() as db:
            own_demo = add_demo(db, "demo-owner-a", OWNER_A)
            other_demo = add_demo(db, "demo-owner-b", OWNER_B)
            service = DemoService(db, owner_id=OWNER_A)
            bind_replay(db, own_demo, replay_contract(own_demo.id))
            own_job = service.create_render_clip_job(
                own_demo,
                RenderClipRequest(tickStart=0, tickEnd=640, tickRate=64),
            )
            db.add(
                DemoJob(
                    id="other-owner-render-job",
                    demo_id=other_demo.id,
                    job_type="render_clip",
                    status="queued",
                    metadata_json=json.dumps(render_job_metadata(0, 640)),
                )
            )
            db.commit()
            own_job_id = own_job.id

        own_response = self.client.get(
            "/demos/demo-owner-a/render/jobs",
            headers=owner_headers(OWNER_A),
        )
        other_response = self.client.get(
            "/demos/demo-owner-b/render/jobs",
            headers=owner_headers(OWNER_A),
        )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual([job["job_id"] for job in own_response.json()], [own_job_id])
        self.assertNotIn("local://", own_response.text)
        self.assertNotIn("demoStorageKey", own_response.text)
        self.assertNotIn("replayStorageKey", own_response.text)
        self.assertEqual(other_response.status_code, 404)

    def test_user_media_responses_expose_only_owner_scoped_private_url(self) -> None:
        with self.Session() as db:
            demo = add_demo(db, "demo-owner-a-private-video", OWNER_A)
            bind_replay(db, demo, replay_contract(demo.id))

        upload_response = self.client.post(
            "/demos/demo-owner-a-private-video/video/upload",
            headers=owner_headers(OWNER_A),
            files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
        )
        replay_response = self.client.get(
            "/demos/demo-owner-a-private-video/replay",
            headers=owner_headers(OWNER_A),
        )
        video_response = self.client.get(
            "/demos/demo-owner-a-private-video/video",
            headers=owner_headers(OWNER_A),
        )
        list_response = self.client.get("/demos", headers=owner_headers(OWNER_A))

        private_url = "/demos/demo-owner-a-private-video/media/video"
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()["url"], private_url)
        self.assertEqual(replay_response.json()["video"]["url"], private_url)
        self.assertEqual(video_response.json()["url"], private_url)
        self.assertNotIn("video_url", list_response.json()[0])
        for response in (
            upload_response,
            replay_response,
            video_response,
            list_response,
        ):
            self.assertNotIn("storageKey", response.text)
            self.assertNotIn("local://", response.text)
            self.assertNotIn("/media/videos/", response.text)

    def test_owner_can_complete_the_protected_review_and_mutation_journey(self) -> None:
        with self.Session() as db:
            demo = add_demo(db, "demo-owner-a-journey", OWNER_A)
            bind_replay(db, demo, replay_contract(demo.id))

        headers = owner_headers(OWNER_A)
        read_paths = (
            "/demos/demo-owner-a-journey/status",
            "/demos/demo-owner-a-journey/replay",
            "/demos/demo-owner-a-journey/coaching",
            "/demos/demo-owner-a-journey/video",
        )
        for path in read_paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 200)

        renamed = self.client.patch(
            "/demos/demo-owner-a-journey",
            headers=headers,
            json={"name": "Owned Journey"},
        )
        archived = self.client.post(
            "/demos/demo-owner-a-journey/archive",
            headers=headers,
        )
        unarchived = self.client.patch(
            "/demos/demo-owner-a-journey",
            headers=headers,
            json={"archived": False},
        )
        video = self.client.post(
            "/demos/demo-owner-a-journey/video/upload",
            headers=headers,
            files={"file": ("owned.mp4", b"owned-video", "video/mp4")},
        )
        calibrated = self.client.post(
            "/demos/demo-owner-a-journey/video/calibration",
            headers=headers,
            json={"tickStart": 10, "tickEnd": 620, "timeOriginSeconds": 1.5},
        )
        mock_render = self.client.post(
            "/demos/demo-owner-a-journey/render/mock",
            headers=headers,
        )
        clip_render = self.client.post(
            "/demos/demo-owner-a-journey/render/clip",
            headers=headers,
            json={"tickStart": 10, "tickEnd": 620, "tickRate": 64},
        )
        jobs = self.client.get(
            "/demos/demo-owner-a-journey/render/jobs",
            headers=headers,
        )
        library = self.client.get("/demos", headers=headers)

        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["name"], "Owned Journey")
        self.assertTrue(archived.json()["archived"])
        self.assertFalse(unarchived.json()["archived"])
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.json()["url"], "/demos/demo-owner-a-journey/media/video")
        self.assertEqual(calibrated.status_code, 200)
        self.assertEqual(calibrated.json()["timeOriginSeconds"], 1.5)
        self.assertEqual(mock_render.status_code, 201)
        self.assertEqual(clip_render.status_code, 201)
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(len(jobs.json()), 1)
        self.assertEqual(library.status_code, 200)
        self.assertEqual(library.json()[0]["name"], "Owned Journey")

    def test_coaching_video_status_and_mock_render_hide_cross_owner_demo(self) -> None:
        with self.Session() as db:
            demo = add_demo(db, "demo-owner-b-surfaces", OWNER_B)
            bind_replay(db, demo, replay_contract(demo.id))

        headers = owner_headers(OWNER_A)
        responses = (
            self.client.get(
                "/demos/demo-owner-b-surfaces/coaching",
                headers=headers,
            ),
            self.client.get(
                "/demos/demo-owner-b-surfaces/video",
                headers=headers,
            ),
            self.client.post(
                "/demos/demo-owner-b-surfaces/render/mock",
                headers=headers,
            ),
        )

        self.assertEqual([response.status_code for response in responses], [404, 404, 404])
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).count(), 0)

    def test_manual_video_surfaces_cannot_modify_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B)

        upload_response = self.client.post(
            "/demos/demo-owner-b/video/upload",
            headers=owner_headers(OWNER_A),
            files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
        )
        calibration_response = self.client.post(
            "/demos/demo-owner-b/video/calibration",
            headers=owner_headers(OWNER_A),
            json={"tickStart": 10, "tickEnd": 100},
        )

        self.assertEqual(upload_response.status_code, 404)
        self.assertEqual(calibration_response.status_code, 404)
        self.assertEqual(list(settings.video_storage_dir.rglob("*")), [])

    def test_retry_parse_requeues_failed_uploaded_demo_for_owner(self) -> None:
        with self.Session() as db:
            demo = add_demo(
                db,
                "demo-owner-a-failed",
                OWNER_A,
                status="failed",
            )
            accepted = accept_source_artifact(db, demo)
            add_job(
                db,
                "old-parse-job",
                demo.id,
                "real_parse",
                status="failed",
                attempts=1,
                metadata={"phase": "failed", "sourceArtifact": accepted.as_snapshot()},
            )

        response = self.client.post(
            "/demos/demo-owner-a-failed/parse/retry",
            headers=owner_headers(OWNER_A),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "demo-owner-a-failed")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["ingestion"]["phase"], "uploaded")
        self.assertEqual(body["ingestion"]["jobType"], "real_parse")
        self.assertEqual(body["ingestion"]["jobStatus"], "queued")
        self.assertFalse(body["ingestion"]["retryable"])
        self.assertEqual(len(self.fake_redis.payloads), 1)
        payload = json.loads(self.fake_redis.payloads[0])
        self.assertEqual(payload["demo_id"], "demo-owner-a-failed")

        with self.Session() as db:
            demo = db.query(Demo).filter(Demo.id == "demo-owner-a-failed").one()
            jobs = (
                db.query(DemoJob)
                .filter(DemoJob.demo_id == demo.id, DemoJob.job_type == "real_parse")
                .order_by(DemoJob.created_at.asc())
                .all()
            )
            self.assertEqual(demo.source_storage_key, accepted.reference)
            self.assertEqual(demo.error_message, None)
            self.assertEqual([job.status for job in jobs], ["failed", "queued"])
            self.assertEqual(jobs[-1].attempts, 0)
            self.assertEqual(payload["job_id"], jobs[-1].id)
            self.assertEqual(list(settings.video_storage_dir.rglob("*")), [])

    def test_retry_parse_cannot_modify_another_owners_demo(self) -> None:
        with self.Session() as db:
            demo = add_demo(
                db,
                "demo-owner-b-failed",
                OWNER_B,
                status="failed",
            )
            accepted = accept_source_artifact(db, demo)
            add_job(
                db,
                "owner-b-old-parse",
                demo.id,
                "real_parse",
                status="failed",
                attempts=1,
                metadata={"phase": "failed", "sourceArtifact": accepted.as_snapshot()},
            )

        response = self.client.post(
            "/demos/demo-owner-b-failed/parse/retry",
            headers=owner_headers(OWNER_A),
        )

        self.assertEqual(response.status_code, 404)
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).count(), 1)
        self.assertEqual(self.fake_redis.payloads, [])

    def test_retry_parse_rejects_invalid_state_and_missing_source_artifact(self) -> None:
        with self.Session() as db:
            completed = add_demo(
                db,
                "demo-owner-a-completed",
                OWNER_A,
                status="completed",
            )
            completed_accepted = accept_source_artifact(db, completed)
            # A readable replay is what makes this demo healthy, and healthy is
            # the state under test. Without one it is a completed demo whose
            # replay is gone -- which is now a retryable state, not a rejected one.
            completed.replay_storage_key = DemoService(db, owner_id=OWNER_A).write_replay_blob(
                completed.id,
                replay_contract(completed.id),
            )
            db.commit()
            missing_source = add_demo(
                db,
                "demo-owner-a-missing-source",
                OWNER_A,
                status="failed",
            )
            add_job(
                db,
                "completed-parse-job",
                completed.id,
                "real_parse",
                status="completed",
                attempts=1,
                metadata={"phase": "ready", "sourceArtifact": completed_accepted.as_snapshot()},
            )
            add_job(db, "missing-source-job", missing_source.id, "real_parse", status="failed", attempts=1)

        completed_response = self.client.post(
            "/demos/demo-owner-a-completed/parse/retry",
            headers=owner_headers(OWNER_A),
        )
        missing_source_response = self.client.post(
            "/demos/demo-owner-a-missing-source/parse/retry",
            headers=owner_headers(OWNER_A),
        )

        self.assertEqual(completed_response.status_code, 409)
        self.assertEqual(missing_source_response.status_code, 409)
        self.assertIn("failed parse", completed_response.json()["detail"].lower())
        self.assertIn("source demo", missing_source_response.json()["detail"].lower())
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).count(), 2)
        self.assertEqual(self.fake_redis.payloads, [])


class OwnerBackfillTest(unittest.TestCase):
    def test_old_user_id_rows_receive_owner_id_backfill(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE demos ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "user_id VARCHAR(64) NOT NULL, "
                    "archived BOOLEAN DEFAULT FALSE NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO demos (id, user_id, archived) VALUES ('legacy-demo', 'legacy-owner', 0)")
            )

        original_engine = database.engine
        database.engine = engine
        try:
            database.ensure_schema_backfills()
        finally:
            database.engine = original_engine

        with engine.connect() as connection:
            owner_id = connection.execute(
                text("SELECT owner_id FROM demos WHERE id = 'legacy-demo'")
            ).scalar_one()

        self.assertEqual(owner_id, "legacy-owner")

    def test_legacy_rows_receive_storage_key_backfills(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE demos ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "user_id VARCHAR(64) NOT NULL, "
                    "original_filename VARCHAR(255) NOT NULL, "
                    "status VARCHAR(32) NOT NULL, "
                    "archived BOOLEAN DEFAULT FALSE NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO demos "
                    "(id, user_id, original_filename, status, archived) "
                    "VALUES ('legacy-demo', 'legacy-owner', 'legacy.dem', 'completed', 0)"
                )
            )

        original_engine = database.engine
        database.engine = engine
        try:
            database.ensure_schema_backfills()
        finally:
            database.engine = original_engine

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT source_storage_key, replay_storage_key "
                    "FROM demos WHERE id = 'legacy-demo'"
                )
            ).one()

        self.assertEqual(row.source_storage_key, "local://uploads/legacy-demo/legacy.dem")
        self.assertEqual(row.replay_storage_key, "local://replays/legacy-demo.json")

    def test_production_backfill_never_assigns_missing_owners_to_dev_user(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE demos ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "user_id VARCHAR(64), "
                    "archived BOOLEAN DEFAULT FALSE NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO demos (id, user_id, archived) VALUES ('orphan-demo', '', 0)")
            )

        original_engine = database.engine
        original_auth_mode = settings.auth_mode
        database.engine = engine
        object.__setattr__(settings, "auth_mode", "production")
        try:
            with self.assertRaisesRegex(RuntimeError, "owner"):
                database.ensure_schema_backfills()
        finally:
            object.__setattr__(settings, "auth_mode", original_auth_mode)
            database.engine = original_engine

        with engine.connect() as connection:
            owner_id = connection.execute(
                text("SELECT owner_id FROM demos WHERE id = 'orphan-demo'")
            ).scalar_one()

        self.assertIsNone(owner_id)


class FakeRedis:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def lpush(self, _: str, payload: str) -> None:
        self.payloads.append(payload)


def owner_headers(owner_id: str) -> dict[str, str]:
    return {"X-Dev-User-Id": owner_id}


def add_demo(
    db,
    demo_id: str,
    owner_id: str,
    *,
    name: str | None = None,
    status: str = "completed",
    archived: bool = False,
    source_storage_key: str | None = None,
) -> Demo:
    timestamp = datetime(2026, 5, 8, tzinfo=UTC)
    demo = Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=name or f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        map_name="de_dust2",
        tick_rate=64,
        round_count=1,
        coaching_event_count=1,
        status=status,
        archived=archived,
        source_storage_key=source_storage_key,
        replay_storage_key=f"local://replays/{demo_id}.json" if status == "completed" else None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


def add_job(
    db,
    job_id: str,
    demo_id: str,
    job_type: str,
    *,
    status: str,
    attempts: int,
    metadata: dict | None = None,
) -> DemoJob:
    job = DemoJob(
        id=job_id,
        demo_id=demo_id,
        job_type=job_type,
        status=status,
        attempts=attempts,
        metadata_json=json.dumps(metadata) if metadata is not None else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def bind_replay(db, demo: Demo, replay: dict) -> str:
    reference = DemoService(db, owner_id=demo.owner_id).write_replay_blob(
        demo.id,
        replay,
    )
    demo.replay_storage_key = reference
    db.commit()
    db.refresh(demo)
    return reference


def accept_source_artifact(db, demo: Demo):
    service = DemoService(db, owner_id=demo.owner_id)
    accepted = ArtifactIntakeService(service.artifact_store).intake_demo(
        owner_id=demo.owner_id,
        demo_id=demo.id,
        filename=demo.original_filename,
        content_type="application/octet-stream",
        stream=io.BytesIO(b"HL2DEMO\x00accepted-owner-boundary-fixture"),
    )
    demo.source_storage_key = accepted.reference
    db.commit()
    db.refresh(demo)
    return accepted


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


def render_job_metadata(tick_start: int, tick_end: int) -> dict:
    return {
        "tickStart": tick_start,
        "tickEnd": tick_end,
        "tickRate": 64,
        "durationSeconds": round((tick_end - tick_start) / 64, 3),
        "renderPreset": "event_clip_v1",
    }


if __name__ == "__main__":
    unittest.main()
