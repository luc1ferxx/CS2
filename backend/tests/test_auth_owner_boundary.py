import json
import tempfile
import unittest
from datetime import datetime, timezone
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
        self.original_replay_dir = settings.replay_storage_dir
        self.original_upload_dir = settings.demo_upload_storage_dir
        self.original_video_dir = settings.video_storage_dir
        root = Path(self.temp_dir.name)
        object.__setattr__(settings, "replay_storage_dir", root / "replays")
        object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
        object.__setattr__(settings, "video_storage_dir", root / "videos")

        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=FakeRedis())
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
        object.__setattr__(settings, "replay_storage_dir", self.original_replay_dir)
        object.__setattr__(settings, "demo_upload_storage_dir", self.original_upload_dir)
        object.__setattr__(settings, "video_storage_dir", self.original_video_dir)
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
        self.assertEqual(response.json()[0]["owner_id"], OWNER_A)

    def test_demo_status_cannot_read_another_owners_demo(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-owner-b", OWNER_B)

        response = self.client.get("/demos/demo-owner-b/status", headers=owner_headers(OWNER_A))

        self.assertEqual(response.status_code, 404)

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
            files={"file": ("owner-b.dem", b"demo-bytes", "application/octet-stream")},
        )

        self.assertEqual(mock_response.status_code, 201)
        self.assertEqual(real_response.status_code, 201)
        self.assertEqual(mock_response.json()["owner_id"], OWNER_A)
        self.assertEqual(real_response.json()["owner_id"], OWNER_B)
        with self.Session() as db:
            mock_demo = db.query(Demo).filter(Demo.id == mock_response.json()["id"]).one()
            real_demo = db.query(Demo).filter(Demo.id == real_response.json()["id"]).one()
            self.assertEqual(mock_demo.owner_id, OWNER_A)
            self.assertEqual(mock_demo.legacy_user_id, OWNER_A)
            self.assertEqual(real_demo.owner_id, OWNER_B)
            self.assertEqual(real_demo.legacy_user_id, OWNER_B)

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
            service.write_replay_blob(own_demo.id, replay_contract(own_demo.id))
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
        self.assertEqual(other_response.status_code, 404)

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
) -> Demo:
    timestamp = datetime(2026, 5, 8, tzinfo=timezone.utc)
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
        replay_storage_key=f"local://replays/{demo_id}.json" if status == "completed" else None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


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
