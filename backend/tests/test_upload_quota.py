import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.api import demos, uploads
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings, settings
from app.core.database import SCHEMA_UPGRADE_LOCK_ID, Base, get_db
from app.core.request_limits import MultipartRequestLimitMiddleware
from app.models import Demo, DemoJob
from app.services.auth_service import AuthService, get_auth_service
from app.services.demo_service import ACTIVE_DEMO_STATUSES, DemoService, demo_ingest
from app.services.upload_quota import (
    PARSE_ADMISSION_LOCK_ID,
    UploadQuotaExceeded,
    UploadQuotaService,
    _parse_admission_lock,
    parse_admission,
    upload_quota_precheck,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
OWNER = "owner_v1_quota_owner"
OTHER_OWNER = "owner_v1_quota_other"
ORIGIN = "https://coach.example.test"
SESSION_TOKEN = "quota-owner-session"
VALID_DEMO = b"HL2DEMO\x00upload-quota-fixture"
BACKEND_DIR = Path(__file__).resolve().parents[1]
QUOTA_FIELDS = ("demo_upload_daily_limit", "demo_active_parse_limit", "parse_queue_global_limit")


class UploadQuotaConfigurationTest(unittest.TestCase):
    def test_env_defaults_and_overrides(self) -> None:
        # Settings reads the environment once at import, so read it fresh.
        script = (
            "from app.core.config import settings as s\n"
            "print(s.demo_upload_daily_limit, s.demo_active_parse_limit,"
            " s.parse_queue_global_limit)\n"
        )
        base_env = {
            name: value
            for name, value in os.environ.items()
            if name
            not in {"DEMO_UPLOAD_DAILY_LIMIT", "DEMO_ACTIVE_PARSE_LIMIT", "PARSE_QUEUE_GLOBAL_LIMIT"}
        }
        for overrides, expected in (
            ({}, "10 2 50"),
            (
                {
                    "DEMO_UPLOAD_DAILY_LIMIT": "3",
                    "DEMO_ACTIVE_PARSE_LIMIT": "0",
                    "PARSE_QUEUE_GLOBAL_LIMIT": "7",
                },
                "3 0 7",
            ),
        ):
            with self.subTest(overrides=overrides):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    env={**base_env, **overrides, "PYTHONPATH": str(BACKEND_DIR)},
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[-1], expected)

    def test_app_wires_the_precheck_only_in_production_and_exposes_retry_after(self) -> None:
        script = (
            "import json\n"
            "from fastapi.middleware.cors import CORSMiddleware\n"
            "from app.core.request_limits import MultipartRequestLimitMiddleware\n"
            "from app.main import app\n"
            "kwargs = {m.cls: m.kwargs for m in app.user_middleware}\n"
            "print(json.dumps({"
            "'precheck': kwargs[MultipartRequestLimitMiddleware]['demo_upload_precheck']"
            " is not None,"
            " 'expose': kwargs[CORSMiddleware]['expose_headers']}))\n"
        )
        for auth_mode, expected_precheck in (("production", True), ("development", False)):
            with self.subTest(auth_mode=auth_mode):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    env={**os.environ, "AUTH_MODE": auth_mode, "PYTHONPATH": str(BACKEND_DIR)},
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout.strip().splitlines()[-1]),
                    {"precheck": expected_precheck, "expose": ["Retry-After"]},
                )

    def test_ranges_are_validated_in_every_mode(self) -> None:
        cases = (
            ("demo_upload_daily_limit", "DEMO_UPLOAD_DAILY_LIMIT", 1_000),
            ("demo_active_parse_limit", "DEMO_ACTIVE_PARSE_LIMIT", 100),
            ("parse_queue_global_limit", "PARSE_QUEUE_GLOBAL_LIMIT", 100_000),
        )
        for base in (
            {"auth_mode": "development", "artifact_storage_backend": "local"},
            {"auth_mode": "test", "artifact_storage_backend": "local"},
            production_settings_kwargs(),
        ):
            for field, env_name, maximum in cases:
                for accepted in (0, maximum):
                    with self.subTest(mode=base["auth_mode"], field=field, value=accepted):
                        Settings(**{**base, field: accepted}).validate_runtime_configuration()
                for rejected in (-1, maximum + 1):
                    with (
                        self.subTest(mode=base["auth_mode"], field=field, value=rejected),
                        self.assertRaisesRegex(
                            RuntimeError, f"{env_name} must be between 0 and {maximum}"
                        ),
                    ):
                        Settings(**{**base, field: rejected}).validate_runtime_configuration()


class UploadQuotaServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def service(self, **limits: Any) -> UploadQuotaService:
        values: dict[str, Any] = {
            "auth_mode": "production",
            "demo_upload_daily_limit": 10,
            "demo_active_parse_limit": 2,
            "parse_queue_global_limit": 50,
        }
        values.update(limits)
        return UploadQuotaService(self.db, Settings(**values), clock=lambda: NOW)

    def seed(
        self,
        owner_id: str,
        *,
        status: str = "completed",
        age: timedelta = timedelta(days=3),
        archived: bool = False,
        count: int = 1,
    ) -> None:
        for _ in range(count):
            self.db.add(make_demo(owner_id, status=status, created_at=NOW - age, archived=archived))
        self.db.commit()

    def assert_exceeded(
        self,
        check: Any,
        *,
        status_code: int,
        code: str,
        retry_after: int,
    ) -> UploadQuotaExceeded:
        with self.assertRaises(UploadQuotaExceeded) as raised:
            check(OWNER)
        exceeded = raised.exception
        self.assertEqual(
            (exceeded.status_code, exceeded.code, exceeded.retry_after_seconds),
            (status_code, code, retry_after),
        )
        return exceeded

    def test_owner_active_parse_limit_counts_only_that_owners_in_flight_demos(self) -> None:
        self.seed(OWNER, status="queued")
        self.seed(OWNER, status="completed", count=3)
        self.seed(OWNER, status="failed", count=3)
        self.seed(OTHER_OWNER, status="parsing", count=5)
        service = self.service(demo_active_parse_limit=2)

        service.check_new_upload(OWNER)
        service.check_parse_retry(OWNER)

        # An archived demo that is still parsing is still in flight.
        self.seed(OWNER, status="analyzing", archived=True)
        for check in (service.check_new_upload, service.check_parse_retry):
            with self.subTest(check=check.__name__):
                self.assert_exceeded(
                    check, status_code=429, code="active_parse_limit", retry_after=60
                )

    def test_global_parse_queue_cap_counts_every_owner_and_is_checked_first(self) -> None:
        self.seed(OTHER_OWNER, status="queued", count=2)
        self.seed(OWNER, status="parsing", count=2)
        self.seed(OWNER, status="completed", age=timedelta(hours=1), count=3)
        service = self.service(
            parse_queue_global_limit=4,
            demo_active_parse_limit=2,
            demo_upload_daily_limit=3,
        )

        for check in (service.check_new_upload, service.check_parse_retry):
            with self.subTest(check=check.__name__):
                self.assert_exceeded(check, status_code=503, code="parse_queue_full", retry_after=60)

        # Below the global cap the owner's own in-flight cap is next in line.
        self.assert_exceeded(
            self.service(parse_queue_global_limit=5, demo_upload_daily_limit=3).check_new_upload,
            status_code=429,
            code="active_parse_limit",
            retry_after=60,
        )

    def test_daily_limit_is_a_rolling_window_whose_retry_after_tracks_the_oldest_upload(
        self,
    ) -> None:
        self.seed(OWNER, age=timedelta(hours=24))  # exactly aged out
        self.seed(OWNER, age=timedelta(hours=25))
        self.seed(OTHER_OWNER, age=timedelta(hours=1), count=5)
        self.seed(OWNER, age=timedelta(hours=23))
        self.seed(OWNER, age=timedelta(hours=2), archived=True)
        service = self.service(demo_upload_daily_limit=3)

        service.check_new_upload(OWNER)

        self.seed(OWNER, age=timedelta(minutes=30))
        self.assert_exceeded(
            service.check_new_upload,
            status_code=429,
            code="upload_daily_limit",
            retry_after=3_600,
        )
        # A retry reuses its demo, so the daily count does not apply to it.
        service.check_parse_retry(OWNER)

    def test_daily_retry_after_targets_the_upload_that_frees_a_slot(self) -> None:
        # Four uploads against a limit of two (the limit was lowered): the
        # owner is under it again only once the second newest has aged out.
        for age in (timedelta(hours=23), timedelta(hours=20), timedelta(hours=5), timedelta(hours=1)):
            self.seed(OWNER, age=age)

        self.assert_exceeded(
            self.service(demo_upload_daily_limit=2).check_new_upload,
            status_code=429,
            code="upload_daily_limit",
            retry_after=19 * 3_600,
        )

    def test_daily_retry_after_is_rounded_up_and_bounded(self) -> None:
        self.seed(OWNER, age=timedelta(hours=24) - timedelta(milliseconds=200))
        self.assert_exceeded(
            self.service(demo_upload_daily_limit=1).check_new_upload,
            status_code=429,
            code="upload_daily_limit",
            retry_after=1,
        )

        self.db.query(Demo).delete()
        self.seed(OWNER, age=timedelta(0))
        self.assert_exceeded(
            self.service(demo_upload_daily_limit=1).check_new_upload,
            status_code=429,
            code="upload_daily_limit",
            retry_after=86_400,
        )

    def test_a_zero_limit_disables_only_that_limit(self) -> None:
        self.seed(OWNER, status="queued", age=timedelta(hours=1), count=3)

        self.service(
            demo_upload_daily_limit=0,
            demo_active_parse_limit=0,
            parse_queue_global_limit=0,
        ).check_new_upload(OWNER)
        self.assert_exceeded(
            self.service(demo_active_parse_limit=0, parse_queue_global_limit=0, demo_upload_daily_limit=3)
            .check_new_upload,
            status_code=429,
            code="upload_daily_limit",
            retry_after=23 * 3_600,
        )
        self.assert_exceeded(
            self.service(demo_upload_daily_limit=0, parse_queue_global_limit=0).check_new_upload,
            status_code=429,
            code="active_parse_limit",
            retry_after=60,
        )

    def test_checks_are_no_ops_outside_production(self) -> None:
        self.seed(OWNER, status="queued", age=timedelta(hours=1), count=3)

        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                service = self.service(
                    auth_mode=auth_mode,
                    demo_upload_daily_limit=1,
                    demo_active_parse_limit=1,
                    parse_queue_global_limit=1,
                )
                service.check_new_upload(OWNER)
                service.check_parse_retry(OWNER)

    def test_exceeded_is_not_a_value_error_and_renders_the_structured_response(self) -> None:
        self.seed(OWNER, status="queued")
        exceeded = self.assert_exceeded(
            self.service(demo_active_parse_limit=1).check_new_upload,
            status_code=429,
            code="active_parse_limit",
            retry_after=60,
        )

        self.assertNotIsInstance(exceeded, ValueError)
        response = exceeded.to_response()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(
            json.loads(response.body),
            {"detail": exceeded.response_detail()},
        )
        self.assertEqual(
            sorted(exceeded.response_detail()),
            ["code", "message", "retryAfterSeconds"],
        )

    def test_messages_avoid_the_dashboards_file_validation_patterns(self) -> None:
        self.seed(OWNER, status="queued", age=timedelta(hours=1))
        messages = []
        for limits in (
            {"parse_queue_global_limit": 1},
            {"demo_active_parse_limit": 1},
            {"demo_active_parse_limit": 0, "demo_upload_daily_limit": 1},
        ):
            with self.assertRaises(UploadQuotaExceeded) as raised:
                self.service(**limits).check_new_upload(OWNER)
            messages.append(raised.exception.message)

        for message in messages:
            with self.subTest(message=message):
                for fragment in (".dem", "413", "too large", "maximum upload", OWNER):
                    self.assertNotIn(fragment, message.lower())

    def test_precheck_returns_the_rejection_and_closes_its_session(self) -> None:
        self.seed(OWNER, status="queued", count=2)
        opened: list[Any] = []

        def session_factory() -> Any:
            session = self.Session()
            opened.append(session)
            return session

        runtime = Settings(
            auth_mode="production",
            demo_upload_daily_limit=10,
            demo_active_parse_limit=2,
            parse_queue_global_limit=50,
        )
        precheck = upload_quota_precheck(session_factory, runtime)

        exceeded = precheck(OWNER)
        allowed = precheck(OTHER_OWNER)

        self.assertIsInstance(exceeded, UploadQuotaExceeded)
        assert exceeded is not None
        self.assertEqual(exceeded.code, "active_parse_limit")
        self.assertIsNone(allowed)
        self.assertEqual(len(opened), 2)
        for session in opened:
            self.assertFalse(session.in_transaction())


class ParseAdmissionTest(unittest.TestCase):
    def session(self, dialect: str) -> MagicMock:
        db = MagicMock()
        db.get_bind.return_value.dialect.name = dialect
        return db

    def test_postgres_takes_a_transaction_advisory_lock_inside_the_process_lock(self) -> None:
        db = self.session("postgresql")

        with parse_admission(db, Settings(auth_mode="production")):
            self.assertTrue(_parse_admission_lock.locked())
            db.execute.assert_called_once()
            statement, params = db.execute.call_args.args
            self.assertEqual(str(statement), "SELECT pg_advisory_xact_lock(:lock_id)")
            self.assertEqual(params, {"lock_id": PARSE_ADMISSION_LOCK_ID})

        self.assertFalse(_parse_admission_lock.locked())
        db.rollback.assert_not_called()
        # A signed bigint, distinct from the schema upgrade lock.
        self.assertLess(PARSE_ADMISSION_LOCK_ID, 2**63)
        self.assertNotEqual(PARSE_ADMISSION_LOCK_ID, SCHEMA_UPGRADE_LOCK_ID)

    def test_other_dialects_take_only_the_process_lock(self) -> None:
        db = self.session("sqlite")

        with parse_admission(db, Settings(auth_mode="production")):
            self.assertTrue(_parse_admission_lock.locked())

        db.execute.assert_not_called()
        self.assertFalse(_parse_admission_lock.locked())

    def test_no_lock_is_taken_outside_production(self) -> None:
        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                db = self.session("postgresql")

                with parse_admission(db, Settings(auth_mode=auth_mode)):
                    self.assertFalse(_parse_admission_lock.locked())

                db.get_bind.assert_not_called()
                db.execute.assert_not_called()

    def test_a_failure_inside_rolls_back_and_releases_the_lock(self) -> None:
        db = self.session("postgresql")

        with (
            self.assertRaises(UploadQuotaExceeded),
            parse_admission(db, Settings(auth_mode="production")),
        ):
            raise UploadQuotaExceeded(429, "active_parse_limit", "busy", 60)

        # The rollback ends the transaction, which frees the advisory lock too.
        db.rollback.assert_called_once_with()
        self.assertFalse(_parse_admission_lock.locked())


class ProductionUploadQuotaApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_settings = {
            name: getattr(settings, name)
            for name in (
                "auth_mode",
                "artifact_storage_root",
                "replay_storage_dir",
                "demo_upload_storage_dir",
                "video_storage_dir",
                *QUOTA_FIELDS,
            )
        }
        for name, value in (
            ("auth_mode", "production"),
            ("artifact_storage_root", root),
            ("replay_storage_dir", root / "replays"),
            ("demo_upload_storage_dir", root / "uploads"),
            ("video_storage_dir", root / "videos"),
            ("demo_upload_daily_limit", 10),
            ("demo_active_parse_limit", 2),
            ("parse_queue_global_limit", 50),
        ):
            object.__setattr__(settings, name, value)

        self.redis = FakeRedis()
        self.redis_patch = patch(
            "app.services.demo_service.get_redis_client",
            return_value=self.redis,
        )
        self.redis_patch.start()

    def tearDown(self) -> None:
        self.redis_patch.stop()
        for name, value in self.original_settings.items():
            object.__setattr__(settings, name, value)
        self.temp_dir.cleanup()
        self.engine.dispose()

    def client(self, *, precheck: Any = None) -> TestClient:
        auth_service = AuthService(
            Settings(
                auth_mode="production",
                auth_provider="oidc",
                frontend_public_url=ORIGIN,
                backend_public_url=ORIGIN,
                cors_origins_raw=ORIGIN,
                auth_cookie_secure=True,
            ),
            self.redis,
        )
        self.redis.setex(
            "auth:session:" + hashlib.sha256(SESSION_TOKEN.encode("ascii")).hexdigest(),
            300,
            json.dumps({"ownerId": OWNER, "expiresAt": int(time.time()) + 300}),
        )
        app = FastAPI()
        if precheck is not None:
            app.add_middleware(MultipartRequestLimitMiddleware, demo_upload_precheck=precheck)
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=auth_service.settings,
            auth_service_factory=lambda: auth_service,
        )
        app.include_router(demos.router)
        app.include_router(uploads.router)
        app.dependency_overrides[get_auth_service] = lambda: auth_service

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        client.cookies.set("__Host-cs2_session", SESSION_TOKEN)
        return client

    def seed(self, owner_id: str, *, status: str, age: timedelta, count: int = 1) -> list[str]:
        ids = []
        with self.Session() as db:
            for _ in range(count):
                demo = make_demo(
                    owner_id,
                    status=status,
                    created_at=datetime.now(UTC) - age,
                )
                db.add(demo)
                ids.append(demo.id)
            db.commit()
        return ids

    def demo_ids(self) -> set[str]:
        with self.Session() as db:
            return {demo.id for demo in db.query(Demo).all()}

    def use_file_database(self) -> None:
        # Racing requests need a connection each; StaticPool shares one.
        self.engine.dispose()
        db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(db_dir.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(db_dir.name, 'quota.sqlite').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def upload_failed_demos(self, count: int) -> list[str]:
        """Upload retryable demos with every limit off, then fail their parses."""
        for name in QUOTA_FIELDS:
            object.__setattr__(settings, name, 0)
        client = self.client()
        ids = []
        for _ in range(count):
            response = self.upload(client)
            self.assertEqual(response.status_code, 201, response.text)
            ids.append(response.json()["id"])
        with self.Session() as db:
            for demo in db.query(Demo).all():
                demo.status = "failed"
            for job in db.query(DemoJob).all():
                job.status = "failed"
            db.commit()
        self.redis.payloads.clear()
        return ids

    def active_demo_count(self) -> int:
        with self.Session() as db:
            return db.query(Demo).filter(Demo.status.in_(ACTIVE_DEMO_STATUSES)).count()

    def race(self, requests: list[Callable[[TestClient], Any]]) -> list[int]:
        clients = [self.client() for _ in requests]
        barrier = threading.Barrier(len(requests))
        statuses: list[int] = []

        def fire(client: TestClient, request: Callable[[TestClient], Any]) -> None:
            barrier.wait()
            statuses.append(request(client).status_code)

        threads = [
            threading.Thread(target=fire, args=(client, request))
            for client, request in zip(clients, requests, strict=True)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return sorted(statuses)

    def upload(self, client: TestClient) -> Any:
        return client.post(
            "/uploads/demo",
            files={"file": ("quota.dem", VALID_DEMO, "application/octet-stream")},
        )

    def assert_quota_response(
        self,
        response: Any,
        *,
        status_code: int,
        code: str,
    ) -> int:
        self.assertEqual(response.status_code, status_code, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], code)
        self.assertIsInstance(detail["message"], str)
        self.assertEqual(response.headers["retry-after"], str(detail["retryAfterSeconds"]))
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertNotIn(OWNER, response.text)
        return int(detail["retryAfterSeconds"])

    def assert_no_upload_side_effects(self, before: set[str]) -> None:
        self.assertEqual(self.demo_ids(), before)
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).count(), 0)
        self.assertEqual(self.redis.payloads, [])
        self.assertEqual(
            [path for path in Path(self.temp_dir.name).rglob("*") if path.is_file()],
            [],
        )

    def test_upload_under_the_limits_succeeds_until_the_active_limit_is_reached(self) -> None:
        object.__setattr__(settings, "demo_active_parse_limit", 1)
        client = self.client()

        first = self.upload(client)
        before = self.demo_ids()
        second = self.upload(client)

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(len(self.redis.payloads), 1)
        self.assert_quota_response(second, status_code=429, code="active_parse_limit")
        self.assertEqual(self.demo_ids(), before)
        self.assertEqual(len(self.redis.payloads), 1)

    def test_rejected_uploads_create_no_demo_job_artifact_or_dispatch(self) -> None:
        cases = (
            ("parse_queue_global_limit", 1, OTHER_OWNER, "queued", timedelta(days=2), 503, "parse_queue_full"),
            ("demo_active_parse_limit", 1, OWNER, "parsing", timedelta(days=2), 429, "active_parse_limit"),
            ("demo_upload_daily_limit", 2, OWNER, "completed", timedelta(hours=1), 429, "upload_daily_limit"),
        )
        for field, limit, owner_id, status, age, status_code, code in cases:
            with self.subTest(code=code):
                with self.Session() as db:
                    db.query(Demo).delete()
                    db.commit()
                for name in QUOTA_FIELDS:
                    object.__setattr__(settings, name, 0)
                object.__setattr__(settings, field, limit)
                self.seed(owner_id, status=status, age=age, count=limit)
                before = self.demo_ids()

                retry_after = self.assert_quota_response(
                    self.upload(self.client()), status_code=status_code, code=code
                )

                if code == "upload_daily_limit":
                    self.assertTrue(23 * 3_600 - 120 <= retry_after <= 23 * 3_600, retry_after)
                else:
                    self.assertEqual(retry_after, 60)
                self.assert_no_upload_side_effects(before)

    def test_middleware_precheck_rejects_before_the_route_runs(self) -> None:
        object.__setattr__(settings, "demo_active_parse_limit", 1)
        self.seed(OWNER, status="queued", age=timedelta(hours=1))
        before = self.demo_ids()
        calls: list[str] = []
        precheck = upload_quota_precheck(self.Session)

        def recording_precheck(owner_id: str) -> UploadQuotaExceeded | None:
            calls.append(owner_id)
            return precheck(owner_id)

        with patch("app.api.uploads.UploadQuotaService") as route_quota:
            response = self.upload(self.client(precheck=recording_precheck))

        self.assert_quota_response(response, status_code=429, code="active_parse_limit")
        self.assertEqual(calls, [OWNER])
        route_quota.assert_not_called()
        self.assert_no_upload_side_effects(before)

    def test_parse_retry_honours_the_in_flight_caps_but_not_the_daily_limit(self) -> None:
        admitted_id, capped_id = self.upload_failed_demos(2)
        (stuck_id,) = self.seed(OWNER, status="failed", age=timedelta(hours=1))
        self.seed(OWNER, status="completed", age=timedelta(hours=1), count=3)
        object.__setattr__(settings, "demo_upload_daily_limit", 1)
        object.__setattr__(settings, "demo_active_parse_limit", 2)
        object.__setattr__(settings, "parse_queue_global_limit", 50)
        client = self.client()

        # Well past the daily limit, but a retry reuses its demo row.
        admitted = client.post(f"/demos/{admitted_id}/parse/retry")
        self.assertEqual(admitted.status_code, 200, admitted.text)
        self.assertEqual(admitted.json()["status"], "queued")

        # One queued plus one analyzing puts the owner at the in-flight cap.
        self.seed(OWNER, status="analyzing", age=timedelta(days=2))
        self.assert_quota_response(
            client.post(f"/demos/{capped_id}/parse/retry"),
            status_code=429,
            code="active_parse_limit",
        )

        # Eligibility is checked before the quota, so a demo that could never
        # be retried keeps its own 409 even over the cap.
        stuck = client.post(f"/demos/{stuck_id}/parse/retry")
        self.assertEqual(stuck.status_code, 409)
        self.assertEqual(stuck.json(), {"detail": "Uploaded source demo is not available for retry"})

        object.__setattr__(settings, "parse_queue_global_limit", 2)
        self.assert_quota_response(
            client.post(f"/demos/{capped_id}/parse/retry"),
            status_code=503,
            code="parse_queue_full",
        )

        with self.Session() as db:
            # The refused retries left no job and no status change behind.
            self.assertEqual(db.get(Demo, capped_id).status, "failed")
            self.assertEqual(db.get(Demo, stuck_id).status, "failed")
            jobs = db.query(DemoJob.demo_id, DemoJob.status).all()
        self.assertEqual(
            sorted(jobs),
            sorted([(admitted_id, "failed"), (admitted_id, "queued"), (capped_id, "failed")]),
        )
        self.assertEqual(len(self.redis.payloads), 1)
        self.assertEqual(json.loads(self.redis.payloads[0])["demo_id"], admitted_id)

    def test_parse_retry_dispatches_and_verifies_outside_the_admission(self) -> None:
        (failed_id,) = self.upload_failed_demos(1)
        object.__setattr__(settings, "demo_active_parse_limit", 2)
        object.__setattr__(settings, "parse_queue_global_limit", 50)
        lock_held: dict[str, bool] = {}
        verify = demo_ingest.verify_accepted_artifact
        check = UploadQuotaService.check_parse_retry
        dispatch = demo_ingest.DemoIngest.dispatch_parse_job

        def other_admission_can_enter() -> bool:
            if not _parse_admission_lock.acquire(blocking=False):
                return False
            _parse_admission_lock.release()
            return True

        def recording_verify(*args: Any, **kwargs: Any) -> Any:
            lock_held["verify"] = not other_admission_can_enter()
            return verify(*args, **kwargs)

        def recording_check(quota: UploadQuotaService, owner_id: str) -> None:
            lock_held["check"] = _parse_admission_lock.locked()
            check(quota, owner_id)

        def recording_dispatch(ingest: Any, **kwargs: Any) -> None:
            # A hung LPUSH here must not stall every other upload and retry.
            lock_held["dispatch"] = not other_admission_can_enter()
            dispatch(ingest, **kwargs)

        with (
            patch.object(demo_ingest, "verify_accepted_artifact", recording_verify),
            patch.object(UploadQuotaService, "check_parse_retry", recording_check),
            patch.object(demo_ingest.DemoIngest, "dispatch_parse_job", recording_dispatch),
        ):
            response = self.client().post(f"/demos/{failed_id}/parse/retry")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(lock_held, {"verify": False, "check": True, "dispatch": False})
        self.assertEqual(len(self.redis.payloads), 1)

    def test_concurrent_retries_of_one_demo_queue_it_once(self) -> None:
        self.use_file_database()
        (failed_id,) = self.upload_failed_demos(1)
        object.__setattr__(settings, "demo_active_parse_limit", 2)
        object.__setattr__(settings, "parse_queue_global_limit", 50)
        verify = demo_ingest.verify_accepted_artifact
        both_verified = threading.Barrier(2, timeout=10)

        def verify_together(*args: Any, **kwargs: Any) -> Any:
            # Both requests pass eligibility before either reaches the admission.
            both_verified.wait()
            return verify(*args, **kwargs)

        with patch.object(demo_ingest, "verify_accepted_artifact", verify_together):
            statuses = self.race(
                [lambda client: client.post(f"/demos/{failed_id}/parse/retry")] * 2
            )

        self.assertEqual(statuses, [200, 409])
        with self.Session() as db:
            self.assertEqual(
                db.query(DemoJob).filter(DemoJob.status == "queued").count(), 1
            )
        self.assertEqual(len(self.redis.payloads), 1)

    def test_parse_retry_of_another_owners_demo_is_still_not_found(self) -> None:
        (other_failed,) = self.seed(OTHER_OWNER, status="failed", age=timedelta(hours=1))
        object.__setattr__(settings, "parse_queue_global_limit", 1)
        self.seed(OWNER, status="queued", age=timedelta(hours=1))

        response = self.client().post(f"/demos/{other_failed}/parse/retry")

        self.assertEqual(response.status_code, 404)

    def test_concurrent_parse_retries_admit_at_most_the_active_limit(self) -> None:
        self.use_file_database()
        failed_ids = self.upload_failed_demos(6)
        object.__setattr__(settings, "demo_active_parse_limit", 2)
        object.__setattr__(settings, "parse_queue_global_limit", 50)
        verify = demo_ingest.verify_accepted_artifact

        def slow_verify(*args: Any, **kwargs: Any) -> Any:
            # Holds each retry between its count and its commit, like an S3 HEAD.
            time.sleep(0.05)
            return verify(*args, **kwargs)

        with patch.object(demo_ingest, "verify_accepted_artifact", slow_verify):
            statuses = self.race(
                [
                    lambda client, demo_id=demo_id: client.post(f"/demos/{demo_id}/parse/retry")
                    for demo_id in failed_ids
                ]
            )

        self.assertEqual(statuses, [200, 200, 429, 429, 429, 429])
        self.assertEqual(self.active_demo_count(), 2)
        self.assertEqual(len(self.redis.payloads), 2)

    def test_uploads_racing_parse_retries_share_the_active_limit(self) -> None:
        self.use_file_database()
        failed_ids = self.upload_failed_demos(4)
        object.__setattr__(settings, "demo_active_parse_limit", 2)
        before = self.demo_ids()

        statuses = self.race(
            [
                *(
                    lambda client, demo_id=demo_id: client.post(f"/demos/{demo_id}/parse/retry")
                    for demo_id in failed_ids
                ),
                self.upload,
                self.upload,
            ]
        )

        admitted = [status for status in statuses if status in {200, 201}]
        self.assertEqual(len(statuses), 6)
        self.assertEqual(len(admitted), 2, statuses)
        self.assertEqual(set(statuses) - {200, 201}, {429})
        self.assertEqual(self.active_demo_count(), 2)
        self.assertEqual(len(self.redis.payloads), 2)
        # A refused upload leaves no row behind.
        self.assertEqual(len(self.demo_ids() - before), statuses.count(201))

    def test_an_upload_refused_by_the_final_check_leaves_no_demo_dispatch_or_artifact(self) -> None:
        object.__setattr__(settings, "demo_active_parse_limit", 1)
        client = self.client()
        before = self.demo_ids()
        landed: list[str] = []
        promoted: list[Path] = []
        prepare = DemoService.prepare_real_demo

        def prepare_while_another_upload_lands(service: DemoService, **kwargs: Any) -> Any:
            prepared = prepare(service, **kwargs)
            promoted.extend(path for path in Path(self.temp_dir.name).rglob("*") if path.is_file())
            # Another upload is admitted while this body was streaming.
            landed.extend(self.seed(OWNER, status="queued", age=timedelta(0)))
            return prepared

        with patch.object(DemoService, "prepare_real_demo", prepare_while_another_upload_lands):
            response = self.upload(client)

        self.assert_quota_response(response, status_code=429, code="active_parse_limit")
        self.assertTrue(promoted)
        self.assertEqual(self.demo_ids(), before | set(landed))
        with self.Session() as db:
            self.assertEqual(db.query(DemoJob).count(), 0)
        self.assertEqual(self.redis.payloads, [])
        self.assertEqual(
            [path for path in Path(self.temp_dir.name).rglob("*") if path.is_file()],
            [],
        )


class DevelopmentUploadQuotaApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_settings = {
            name: getattr(settings, name)
            for name in (
                "auth_mode",
                "artifact_storage_root",
                "replay_storage_dir",
                "demo_upload_storage_dir",
                *QUOTA_FIELDS,
            )
        }
        for name, value in (
            ("artifact_storage_root", root),
            ("replay_storage_dir", root / "replays"),
            ("demo_upload_storage_dir", root / "uploads"),
            ("demo_upload_daily_limit", 1),
            ("demo_active_parse_limit", 1),
            ("parse_queue_global_limit", 1),
        ):
            object.__setattr__(settings, name, value)
        self.redis = FakeRedis()
        self.redis_patch = patch(
            "app.services.demo_service.get_redis_client",
            return_value=self.redis,
        )
        self.redis_patch.start()

    def tearDown(self) -> None:
        self.redis_patch.stop()
        for name, value in self.original_settings.items():
            object.__setattr__(settings, name, value)
        self.temp_dir.cleanup()
        self.engine.dispose()

    def test_limits_never_apply_outside_production(self) -> None:
        with self.Session() as db:
            for status in ("queued", "parsing", "failed"):
                db.add(make_demo("dev-user", status=status, created_at=datetime.now(UTC)))
            db.commit()
            failed_id = db.query(Demo.id).filter(Demo.status == "failed").scalar()
        app = FastAPI()
        app.include_router(demos.router)
        app.include_router(uploads.router)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                object.__setattr__(settings, "auth_mode", auth_mode)
                upload = client.post(
                    "/uploads/demo",
                    headers={"X-Dev-User-Id": "dev-user"},
                    files={"file": ("dev.dem", VALID_DEMO, "application/octet-stream")},
                )
                retry = client.post(
                    f"/demos/{failed_id}/parse/retry",
                    headers={"X-Dev-User-Id": "dev-user"},
                )

                self.assertEqual(upload.status_code, 201, upload.text)
                self.assertEqual(retry.status_code, 409)
                self.assertNotIn("retry-after", retry.headers)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.payloads: list[str] = []

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def lpush(self, _queue: str, payload: str) -> None:
        self.payloads.append(payload)


def make_demo(
    owner_id: str,
    *,
    status: str,
    created_at: datetime,
    archived: bool = False,
) -> Demo:
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


def production_settings_kwargs() -> dict[str, Any]:
    return {
        "auth_mode": "production",
        "auth_provider": "steam",
        "frontend_public_url": ORIGIN,
        "backend_public_url": ORIGIN,
        "auth_cookie_secure": True,
        "cors_origins_raw": ORIGIN,
        "render_worker_token": "test-worker-secret-that-is-not-a-default",
        "artifact_storage_backend": "s3",
        "object_storage_bucket": "private-cs2-artifacts",
        "object_storage_prefix": "cs2-artifacts-v1",
        "steam_web_api_key": "a" * 32,
        "steam_login_allowlist_raw": "*",
        "steam_credential_encryption_key": base64.urlsafe_b64encode(b"q" * 32).decode("ascii"),
        "steam_credential_encryption_key_version": "test-v1",
    }


if __name__ == "__main__":
    unittest.main()
