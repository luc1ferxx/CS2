import hashlib
import json
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import uploads
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.models import Demo
from app.services.auth_service import AuthService, get_auth_service
from app.services.upload_quota import UploadQuotaService, UploadQuotaSnapshot

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
OWNER = "owner_v1_quota_endpoint_owner"
OTHER_OWNER = "owner_v1_quota_endpoint_other"
ORIGIN = "https://coach.example.test"
SESSION_TOKEN = "quota-endpoint-session"
QUOTA_FIELDS = (
    "auth_mode",
    "demo_upload_daily_limit",
    "demo_active_parse_limit",
    "parse_queue_global_limit",
    "max_demo_upload_bytes",
)


def make_demo(owner_id: str, *, status: str, created_at: datetime, archived: bool = False) -> Demo:
    demo_id = str(uuid.uuid4())
    return Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        map_name="de_dust2",
        tick_rate=64,
        round_count=1,
        coaching_event_count=0,
        status=status,
        archived=archived,
        created_at=created_at,
        updated_at=created_at,
    )


class DatabaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def seed(
        self,
        owner_id: str,
        *,
        status: str = "completed",
        created_at: datetime,
        archived: bool = False,
        count: int = 1,
    ) -> None:
        with self.Session() as db:
            for _ in range(count):
                db.add(make_demo(owner_id, status=status, created_at=created_at, archived=archived))
            db.commit()


class UploadQuotaSnapshotTest(DatabaseCase):
    def snapshot(self, **overrides: Any) -> UploadQuotaSnapshot:
        values: dict[str, Any] = {
            "auth_mode": "production",
            "demo_upload_daily_limit": 10,
            "demo_active_parse_limit": 2,
            "parse_queue_global_limit": 50,
        }
        values.update(overrides)
        with self.Session() as db:
            return UploadQuotaService(db, Settings(**values), clock=lambda: NOW).snapshot(OWNER)

    def test_counts_only_the_owners_uploads_inside_the_rolling_window(self) -> None:
        self.seed(OWNER, created_at=NOW - timedelta(hours=2), count=2)
        # Failed and archived uploads still used a slot.
        self.seed(OWNER, status="failed", created_at=NOW - timedelta(hours=3))
        self.seed(OWNER, created_at=NOW - timedelta(hours=4), archived=True)
        self.seed(OWNER, status="parsing", created_at=NOW - timedelta(hours=1))
        self.seed(OWNER, created_at=NOW - timedelta(hours=30))
        self.seed(OTHER_OWNER, status="queued", created_at=NOW - timedelta(hours=1), count=3)

        self.assertEqual(
            self.snapshot(),
            UploadQuotaSnapshot(
                daily_limit=10,
                daily_used=5,
                daily_reset_seconds=None,
                active_limit=2,
                active_count=1,
            ),
        )

    def test_reports_when_a_used_up_daily_quota_frees_its_next_slot(self) -> None:
        self.seed(OWNER, created_at=NOW - timedelta(hours=20))
        self.seed(OWNER, created_at=NOW - timedelta(hours=1), count=2)

        snapshot = self.snapshot(demo_upload_daily_limit=3)

        self.assertEqual((snapshot.daily_limit, snapshot.daily_used), (3, 3))
        self.assertEqual(snapshot.daily_reset_seconds, 4 * 60 * 60)

    def test_a_zero_limit_is_reported_as_off(self) -> None:
        self.seed(OWNER, status="queued", created_at=NOW - timedelta(hours=1))

        snapshot = self.snapshot(demo_upload_daily_limit=0, demo_active_parse_limit=0)

        self.assertEqual(snapshot, UploadQuotaSnapshot(None, 0, None, None, 1))

    def test_limits_are_off_outside_production(self) -> None:
        self.seed(OWNER, status="parsing", created_at=NOW - timedelta(hours=1), count=3)

        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                snapshot = self.snapshot(auth_mode=auth_mode, demo_upload_daily_limit=1)
                self.assertEqual(snapshot, UploadQuotaSnapshot(None, 0, None, None, 3))


class UploadQuotaApiTest(DatabaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.original_settings = {name: getattr(settings, name) for name in QUOTA_FIELDS}
        for name, value in (
            ("demo_upload_daily_limit", 3),
            ("demo_active_parse_limit", 2),
            ("parse_queue_global_limit", 50),
            ("max_demo_upload_bytes", 512 * 1024 * 1024),
        ):
            object.__setattr__(settings, name, value)
        self.redis_values: dict[str, str] = {}

    def tearDown(self) -> None:
        for name, value in self.original_settings.items():
            object.__setattr__(settings, name, value)
        super().tearDown()

    def app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(uploads.router)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        return app

    def production_client(self, *, signed_in: bool = True) -> TestClient:
        object.__setattr__(settings, "auth_mode", "production")
        redis = _FakeRedis(self.redis_values)
        auth_service = AuthService(
            Settings(
                auth_mode="production",
                auth_provider="oidc",
                frontend_public_url=ORIGIN,
                backend_public_url=ORIGIN,
                cors_origins_raw=ORIGIN,
                auth_cookie_secure=True,
            ),
            redis,
        )
        redis.setex(
            "auth:session:" + hashlib.sha256(SESSION_TOKEN.encode("ascii")).hexdigest(),
            300,
            json.dumps({"ownerId": OWNER, "expiresAt": int(time.time()) + 300}),
        )
        app = self.app()
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=auth_service.settings,
            auth_service_factory=lambda: auth_service,
        )
        app.dependency_overrides[get_auth_service] = lambda: auth_service
        client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        if signed_in:
            client.cookies.set("__Host-cs2_session", SESSION_TOKEN)
        return client

    def test_production_reports_the_signed_in_owners_quota(self) -> None:
        now = datetime.now(UTC)
        self.seed(OWNER, created_at=now - timedelta(hours=1))
        self.seed(OWNER, status="parsing", created_at=now - timedelta(minutes=5))
        self.seed(OTHER_OWNER, status="queued", created_at=now - timedelta(minutes=5), count=4)

        response = self.production_client().get("/uploads/quota")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "dailyLimit": 3,
                "dailyUsed": 2,
                "dailyResetSeconds": None,
                "activeLimit": 2,
                "activeCount": 1,
                "maxUploadBytes": 512 * 1024 * 1024,
            },
        )
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertNotIn(OWNER, response.text)

    def test_production_reports_the_wait_once_the_daily_quota_is_used_up(self) -> None:
        now = datetime.now(UTC)
        self.seed(OWNER, created_at=now - timedelta(hours=23), count=3)

        body = self.production_client().get("/uploads/quota").json()

        self.assertEqual((body["dailyLimit"], body["dailyUsed"]), (3, 3))
        self.assertIsInstance(body["dailyResetSeconds"], int)
        self.assertTrue(0 < body["dailyResetSeconds"] <= 60 * 60)

    def test_production_requires_a_session(self) -> None:
        response = self.production_client(signed_in=False).get("/uploads/quota")

        self.assertEqual(response.status_code, 401)

    def test_development_reports_no_limits(self) -> None:
        object.__setattr__(settings, "auth_mode", "development")
        self.seed("dev-user", status="parsing", created_at=datetime.now(UTC), count=2)
        self.seed("someone-else", status="parsing", created_at=datetime.now(UTC))

        response = TestClient(self.app()).get("/uploads/quota", headers={"X-Dev-User-Id": "dev-user"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "dailyLimit": None,
                "dailyUsed": 0,
                "dailyResetSeconds": None,
                "activeLimit": None,
                "activeCount": 2,
                "maxUploadBytes": 512 * 1024 * 1024,
            },
        )


class _FakeRedis:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


if __name__ == "__main__":
    unittest.main()
