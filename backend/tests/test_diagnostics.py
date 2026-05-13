import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import diagnostics
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import Demo, DemoJob
from app.services.demo_service import DemoService


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
        self.temp_dir.cleanup()
        self.engine.dispose()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def test_diagnostics_reports_safe_dependency_readiness_and_job_counts(self) -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
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
        now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
        diagnostics.write_worker_heartbeat(self.redis, now=now)

        heartbeat = diagnostics.read_worker_heartbeat(
            self.redis,
            now=now + timedelta(seconds=10),
        )

        self.assertTrue(heartbeat["alive"])
        self.assertEqual(heartbeat["ageSeconds"], 10)
        self.assertEqual(heartbeat["lastSeenAt"], now.isoformat())


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def llen(self, _: str) -> int:
        return 0

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, _: int, value: str) -> None:
        self.values[key] = value


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
        created_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 10, tzinfo=timezone.utc),
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
) -> DemoJob:
    job = DemoJob(
        id=f"{demo_id}-{job_type}-{status}",
        demo_id=demo_id,
        job_type=job_type,
        status=status,
        attempts=1,
        error_message=error_message,
        metadata_json=json.dumps(metadata or {}),
        started_at=datetime(2026, 5, 12, 9, 59, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
        if status in {"completed", "failed"}
        else None,
        created_at=datetime(2026, 5, 12, 9, 58, tzinfo=timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


if __name__ == "__main__":
    unittest.main()
