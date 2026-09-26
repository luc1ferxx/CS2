import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fixtures.fake_redis import FakeRedis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.api import diagnostics
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import Demo, DemoJob
from app.services import diagnostics as diagnostics_service
from app.services.demo_service import DemoService
from app.workers.queue import ParseQueue


class DiagnosticsEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_dirs = (
            settings.artifact_storage_root,
            settings.replay_storage_dir,
            settings.demo_upload_storage_dir,
            settings.video_storage_dir,
            settings.summary_storage_dir,
        )
        self.original_auth_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "test")
        # The mode decides whether a missing render worker is worth reporting, so
        # it is pinned here instead of inherited from the developer's .env.
        self.original_render_worker_mode = settings.render_worker_mode
        object.__setattr__(settings, "render_worker_mode", "external")
        root = Path(self.temp_dir.name)
        object.__setattr__(settings, "artifact_storage_root", root)
        object.__setattr__(settings, "replay_storage_dir", root / "replays")
        object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
        object.__setattr__(settings, "video_storage_dir", root / "videos")
        object.__setattr__(settings, "summary_storage_dir", root / "summaries")

        self.redis = FakeRedis()
        self.app = FastAPI()
        self.app.include_router(diagnostics.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(self.app)
        self.redis_patch = patch("app.api.diagnostics.get_redis_client", return_value=self.redis)
        self.redis_patch.start()

    def tearDown(self) -> None:
        self.redis_patch.stop()
        self.app.dependency_overrides.clear()
        (
            artifact_root,
            replay_dir,
            upload_dir,
            video_dir,
            summary_dir,
        ) = self.original_dirs
        object.__setattr__(settings, "artifact_storage_root", artifact_root)
        object.__setattr__(settings, "replay_storage_dir", replay_dir)
        object.__setattr__(settings, "demo_upload_storage_dir", upload_dir)
        object.__setattr__(settings, "video_storage_dir", video_dir)
        object.__setattr__(settings, "summary_storage_dir", summary_dir)
        object.__setattr__(settings, "auth_mode", self.original_auth_mode)
        object.__setattr__(settings, "render_worker_mode", self.original_render_worker_mode)
        self.temp_dir.cleanup()
        self.engine.dispose()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def test_diagnostics_reports_safe_dependency_readiness_and_job_counts(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics.write_worker_heartbeat(self.redis, now=now)
        with self.Session() as db:
            add_demo(db, "demo-ok", status="completed")
            failed = add_demo(
                db,
                "demo-failed",
                status="failed",
                error_message="/private/tmp/source.dem exploded\nTraceback should not leak",
            )
            add_job(
                db,
                failed.id,
                "real_parse",
                "failed",
                error_message="/private/tmp/source.dem exploded\nTraceback should not leak",
                metadata={
                    "failure": {
                        "errorCode": "INVALID_DEMO",
                        "message": "/private/tmp/source.dem exploded\nTraceback should not leak",
                        "failedAt": now.isoformat(),
                    }
                },
            )

        with patch("app.services.diagnostics.utc_now", return_value=now):
            response = self.client.get("/diagnostics")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        encoded = json.dumps(body)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["dependencies"]["database"]["ok"])
        self.assertTrue(body["dependencies"]["redis"]["ok"])
        self.assertTrue(body["dependencies"]["storage"]["ok"])
        self.assertEqual(body["worker"]["queueName"], "cs2-demo-jobs")
        self.assertTrue(body["worker"]["heartbeat"]["alive"])
        self.assertEqual(body["worker"]["queueLength"], 0)
        self.assertEqual(body["worker"]["inFlight"], 0)
        self.assertEqual(body["jobs"]["counts"]["real_parse"]["failed"], 1)
        self.assertEqual(body["jobs"]["counts"]["mock_parse"]["completed"], 1)
        self.assertEqual(body["jobs"]["recentFailures"][0]["errorCode"], "INVALID_DEMO")
        self.assertNotIn("/private", encoded)
        self.assertNotIn(str(self.temp_dir.name), encoded)
        self.assertNotIn("Traceback", encoded)
        self.assertNotIn("DATABASE_URL", encoded)
        self.assertNotIn("dev-render-worker-token", encoded)

    def test_demo_diagnostics_are_owner_scoped_and_report_storage_presence(self) -> None:
        with self.Session() as db:
            demo = add_demo(
                db,
                "demo-owned",
                owner_id="owner-a",
                status="failed",
                source_storage_key="local://uploads/demo-owned/source.dem",
                replay_storage_key="local://replays/demo-owned.json",
                error_message="Invalid or unreadable demo file.",
            )
            service = DemoService(db, owner_id="owner-a")
            service.storage.write_bytes(demo.source_storage_key, b"demo-bytes")
            add_job(db, demo.id, "real_parse", "failed", error_message="Invalid or unreadable demo file.")

        allowed = self.client.get("/demos/demo-owned/diagnostics", headers={"X-Dev-User-Id": "owner-a"})
        denied = self.client.get("/demos/demo-owned/diagnostics", headers={"X-Dev-User-Id": "owner-b"})

        self.assertEqual(allowed.status_code, 200)
        body = allowed.json()
        self.assertEqual(body["demoId"], "demo-owned")
        self.assertEqual(body["parse"]["failure"]["message"], "Invalid or unreadable demo file.")
        self.assertTrue(body["storage"]["sourceDemo"]["keyPresent"])
        self.assertTrue(body["storage"]["sourceDemo"]["artifactPresent"])
        self.assertTrue(body["storage"]["replay"]["keyPresent"])
        self.assertFalse(body["storage"]["replay"]["artifactPresent"])
        self.assertNotIn("local://uploads", json.dumps(body))
        self.assertEqual(denied.status_code, 404)

    def test_worker_heartbeat_can_be_written_and_read(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics.write_worker_heartbeat(self.redis, now=now)

        heartbeat = diagnostics.read_worker_heartbeat(
            self.redis,
            now=now + timedelta(seconds=10),
        )

        self.assertTrue(heartbeat["alive"])
        self.assertEqual(heartbeat["ageSeconds"], 10)
        self.assertEqual(heartbeat["lastSeenAt"], now.isoformat())

    def test_in_flight_counts_work_a_worker_has_taken_but_not_finished(self) -> None:
        # queueLength alone stopped telling the whole story once reserving a
        # message moved it onto a processing list: the queue reads empty while a
        # worker is still holding the job.
        self.redis.lpush(settings.redis_queue_name, json.dumps({"job_id": "j-1"}))
        queue = ParseQueue(
            self.redis,
            queue_name=settings.redis_queue_name,
            consumer_id="worker-a",
            lease_ttl_seconds=60,
        )
        queue.register()
        payload = queue.reserve(timeout=1)

        response = self.client.get("/diagnostics")

        worker = response.json()["worker"]
        self.assertEqual(worker["queueLength"], 0)
        self.assertEqual(worker["inFlight"], 1)

        queue.release(str(payload))
        self.assertEqual(self.client.get("/diagnostics").json()["worker"]["inFlight"], 0)

    def test_render_worker_heartbeat_can_be_written_and_read(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics_service.write_render_worker_heartbeat(self.redis, now=now)

        heartbeat = diagnostics_service.read_render_worker_heartbeat(
            self.redis,
            now=now + timedelta(seconds=10),
        )

        self.assertTrue(heartbeat["alive"])
        self.assertEqual(heartbeat["ageSeconds"], 10)
        self.assertEqual(heartbeat["lastSeenAt"], now.isoformat())
        # The two workers are separate processes; one polling must never make the
        # other look alive.
        self.assertFalse(diagnostics.read_worker_heartbeat(self.redis, now=now)["alive"])

    def test_render_worker_availability_reports_never_seen_before_any_poll(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)

        with self.Session() as db:
            availability = self.availability(db, now)

        self.assertEqual(availability["status"], "never_seen")
        self.assertFalse(availability["connected"])
        self.assertTrue(availability["required"])
        self.assertIsNone(availability["lastSeenAt"])
        self.assertIsNone(availability["ageSeconds"])
        self.assertFalse(availability["busyRendering"])

    def test_render_worker_availability_keeps_last_seen_after_going_stale(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics_service.write_render_worker_heartbeat(self.redis, now=now)

        with self.Session() as db:
            availability = self.availability(db, now + timedelta(minutes=5))

        self.assertEqual(availability["status"], "offline")
        self.assertFalse(availability["connected"])
        # "Last polled 5 minutes ago" and "never connected" mean very different
        # things to an operator, so the timestamp has to outlive the alive window.
        self.assertEqual(availability["lastSeenAt"], now.isoformat())
        self.assertEqual(availability["ageSeconds"], 300)

    def test_render_worker_availability_treats_an_active_render_as_liveness(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics_service.write_render_worker_heartbeat(self.redis, now=now)
        with self.Session() as db:
            demo = add_demo(db, "demo-rendering", status="completed")
            add_job(
                db,
                demo.id,
                "render_clip",
                "rendering",
                started_at=now + timedelta(minutes=1),
            )

            # A render blocks the 5s poll loop for minutes, so a stale heartbeat
            # alone would false-alarm halfway through a job that is going fine.
            availability = self.availability(db, now + timedelta(minutes=5))

        self.assertTrue(availability["connected"])
        self.assertEqual(availability["status"], "rendering")
        self.assertTrue(availability["busyRendering"])
        self.assertEqual(availability["ageSeconds"], 300)

    def test_render_worker_availability_bounds_how_long_a_render_implies_liveness(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics_service.write_render_worker_heartbeat(self.redis, now=now)
        with self.Session() as db:
            demo = add_demo(db, "demo-stuck", status="completed")
            add_job(db, demo.id, "render_clip", "rendering", started_at=now)

            # A worker that died mid-render must not read as alive forever.
            grace = diagnostics_service.RENDER_WORKER_BUSY_GRACE_SECONDS
            availability = self.availability(db, now + timedelta(seconds=grace + 1))

        self.assertFalse(availability["connected"])
        self.assertEqual(availability["status"], "offline")
        self.assertFalse(availability["busyRendering"])

    def test_a_reclaimed_render_job_does_not_fake_worker_liveness(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        with self.Session() as db:
            demo = add_demo(db, "demo-reclaimed", status="completed")
            job = add_job(db, demo.id, "render_clip", "queued")
            # Exactly the shape reclaim_stale_render_clip_jobs leaves behind:
            # back in the queue with the dead worker's claim stamp cleared.
            job.started_at = None
            db.commit()

            availability = self.availability(db, now)

        # Reclaiming must not invent liveness. The job is waiting for a worker,
        # which is the opposite of proof that one is there.
        self.assertFalse(availability["connected"])
        self.assertFalse(availability["busyRendering"])
        self.assertEqual(availability["status"], "never_seen")

    def test_render_worker_availability_is_not_required_in_fallback_mode(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        original_mode = settings.render_worker_mode
        object.__setattr__(settings, "render_worker_mode", "fallback")
        try:
            with self.Session() as db:
                availability = self.availability(db, now)
        finally:
            object.__setattr__(settings, "render_worker_mode", original_mode)

        # The fallback mode fails render jobs itself with its own error, so no
        # caller should tell the user a render worker is missing.
        self.assertFalse(availability["required"])
        self.assertEqual(availability["mode"], "fallback")

    def test_render_worker_availability_survives_a_redis_outage(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)

        with self.Session() as db:
            availability = diagnostics_service.render_worker_availability(
                db,
                BrokenRedis(),
                now=now,
            )

        self.assertFalse(availability["connected"])
        self.assertEqual(availability["status"], "never_seen")

    def test_system_diagnostics_report_render_worker_liveness_not_error_strings(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        diagnostics_service.write_render_worker_heartbeat(self.redis, now=now)
        with self.Session() as db:
            demo = add_demo(db, "demo-render", status="completed")
            add_job(db, demo.id, "render_clip", "queued")

        with patch("app.services.diagnostics.utc_now", return_value=now):
            response = self.client.get("/diagnostics")

        render_worker = response.json()["renderWorker"]
        # A queued job used to report "unknown" no matter what the worker was
        # doing; liveness now comes from the heartbeat instead.
        self.assertTrue(render_worker["connected"])
        self.assertEqual(render_worker["status"], "connected")
        self.assertEqual(render_worker["lastStatus"], "queued")
        self.assertEqual(render_worker["lastJobId"], "demo-render-render_clip-queued")

    def availability(self, db, now: datetime) -> dict:
        return diagnostics_service.render_worker_availability(db, self.redis, now=now)

    def test_system_diagnostics_are_disabled_in_production(self) -> None:
        object.__setattr__(settings, "auth_mode", "production")

        response = self.client.get("/diagnostics")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("dependencies", response.text)

    def test_public_health_returns_only_coarse_readiness(self) -> None:
        with patch("app.main.SessionLocal", return_value=FakeDatabaseSession()), patch(
            "app.main.get_redis_client",
            return_value=self.redis,
        ):
            response = main.health()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(bytes(response.body)), {"status": "ok"})


class BrokenRedis:
    """Stands in for Redis being down while the rest of the stack is fine."""

    def get(self, _: str) -> str:
        raise ConnectionError("redis is unreachable")

    def setex(self, *_: object) -> None:
        raise ConnectionError("redis is unreachable")


class FakeDatabaseSession:
    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _: object) -> None:
        return None


def add_demo(
    db,
    demo_id: str,
    *,
    owner_id: str = "dev-user",
    status: str,
    source_storage_key: str | None = None,
    replay_storage_key: str | None = None,
    error_message: str | None = None,
) -> Demo:
    demo = Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        map_name="de_dust2" if status == "completed" else "unknown",
        tick_rate=64,
        round_count=12 if status == "completed" else 0,
        coaching_event_count=3 if status == "completed" else 0,
        status=status,
        source_storage_key=source_storage_key,
        replay_storage_key=replay_storage_key,
        error_message=error_message,
        created_at=datetime(2026, 5, 12, tzinfo=UTC),
        updated_at=datetime(2026, 5, 12, 10, tzinfo=UTC),
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    if status == "completed":
        add_job(db, demo.id, "mock_parse", "completed")
    return demo


def add_job(
    db,
    demo_id: str,
    job_type: str,
    status: str,
    *,
    error_message: str | None = None,
    metadata: dict | None = None,
    started_at: datetime | None = None,
) -> DemoJob:
    job = DemoJob(
        id=f"{demo_id}-{job_type}-{status}",
        demo_id=demo_id,
        job_type=job_type,
        status=status,
        attempts=1,
        error_message=error_message,
        metadata_json=json.dumps(metadata or {}),
        started_at=started_at or datetime(2026, 5, 12, 9, 59, tzinfo=UTC),
        finished_at=datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        if status in {"completed", "failed"}
        else None,
        created_at=datetime(2026, 5, 12, 9, 58, tzinfo=UTC),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


if __name__ == "__main__":
    unittest.main()
