import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import demos, uploads
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings, _bool_from_env, settings
from app.core.database import Base, get_db
from app.core.features import (
    api_docs_kwargs,
    feature_capabilities,
    require_dev_tools,
    require_render_clips,
)
from app.core.redis import get_redis_client
from app.models import Demo, DemoJob
from app.services.account_service import AccountService
from app.services.auth_service import (
    AuthService,
    derive_owner_id,
    get_auth_service,
    oidc_provider_key,
)

ORIGIN = "https://coach.example.test"
ISSUER = "https://issuer.example.test"
SUBJECT = "beta-reviewer"
SESSION_TOKEN = "beta-owner-session"
BACKEND_DIR = Path(__file__).resolve().parents[1]


class FeatureGateSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_auth_mode = settings.auth_mode
        self.original_render_clips_enabled = settings.render_clips_enabled

    def tearDown(self) -> None:
        object.__setattr__(settings, "auth_mode", self.original_auth_mode)
        object.__setattr__(settings, "render_clips_enabled", self.original_render_clips_enabled)

    def test_render_clips_enabled_is_read_as_an_env_boolean_that_fails_closed(self) -> None:
        for raw, expected in (
            ("1", True),
            ("true", True),
            ("on", True),
            ("0", False),
            ("", False),
            ("enabled", False),
        ):
            with self.subTest(raw=raw), patch.dict(os.environ, {"RENDER_CLIPS_ENABLED": raw}):
                self.assertIs(_bool_from_env("RENDER_CLIPS_ENABLED"), expected)
        with patch.dict(os.environ):
            os.environ.pop("RENDER_CLIPS_ENABLED", None)
            self.assertFalse(_bool_from_env("RENDER_CLIPS_ENABLED"))

    def test_render_clips_are_production_opt_in_and_always_on_elsewhere(self) -> None:
        for auth_mode in ("development", "test"):
            for enabled in (False, True):
                with self.subTest(auth_mode=auth_mode, enabled=enabled):
                    runtime = Settings(auth_mode=auth_mode, render_clips_enabled=enabled)
                    self.assertTrue(runtime.render_clips_available)
        self.assertFalse(Settings(auth_mode="production").render_clips_available)
        self.assertTrue(
            Settings(auth_mode="production", render_clips_enabled=True).render_clips_available
        )

    def test_capabilities_mirror_the_route_gates(self) -> None:
        self.assertEqual(
            feature_capabilities(Settings(auth_mode="test")),
            {"devTools": True, "renderClips": True},
        )
        self.assertEqual(
            feature_capabilities(Settings(auth_mode="production")),
            {"devTools": False, "renderClips": False},
        )
        self.assertEqual(
            feature_capabilities(Settings(auth_mode="production", render_clips_enabled=True)),
            {"devTools": False, "renderClips": True},
        )

    def test_gates_read_the_process_settings(self) -> None:
        object.__setattr__(settings, "render_clips_enabled", False)
        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                object.__setattr__(settings, "auth_mode", auth_mode)
                self.assertIsNone(require_dev_tools())
                self.assertIsNone(require_render_clips())

        object.__setattr__(settings, "auth_mode", "production")
        for gate in (require_dev_tools, require_render_clips):
            with self.subTest(gate=gate.__name__), self.assertRaises(HTTPException) as raised:
                gate()
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(raised.exception.detail, "Not found")

        object.__setattr__(settings, "render_clips_enabled", True)
        self.assertIsNone(require_render_clips())
        with self.assertRaises(HTTPException):
            require_dev_tools()

    def test_api_docs_kwargs_remove_the_schema_routes_only_in_production(self) -> None:
        for auth_mode, expected_status in (("production", 404), ("test", 200)):
            app = FastAPI(**api_docs_kwargs(Settings(auth_mode=auth_mode)))
            client = TestClient(app)
            for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
                with self.subTest(auth_mode=auth_mode, path=path):
                    self.assertEqual(client.get(path).status_code, expected_status)

    def test_app_construction_follows_auth_mode(self) -> None:
        # app.main reads the environment once at import, so build it fresh in a
        # child process instead of reloading it under the other tests.
        script = (
            "import json\n"
            "from app.core.request_limits import MultipartRequestLimitMiddleware\n"
            "from app.main import app\n"
            "limits = next(m for m in app.user_middleware"
            " if m.cls is MultipartRequestLimitMiddleware)\n"
            "print(json.dumps({'docs': [app.docs_url, app.redoc_url, app.openapi_url],"
            " 'manualVideo': limits.kwargs['manual_video_upload_enabled']}))\n"
        )
        expectations = {
            "production": {"docs": [None, None, None], "manualVideo": False},
            "development": {"docs": ["/docs", "/redoc", "/openapi.json"], "manualVideo": True},
        }
        for auth_mode, expected in expectations.items():
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
                self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), expected)


class ProductionFeatureGateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        self.original_auth_mode = settings.auth_mode
        self.original_render_clips_enabled = settings.render_clips_enabled
        object.__setattr__(settings, "auth_mode", "production")
        object.__setattr__(settings, "render_clips_enabled", False)

        self.redis = FakeRedis()
        self.redis_patch = patch(
            "app.services.demo_service.get_redis_client",
            return_value=self.redis,
        )
        self.redis_patch.start()

        self.owner_id = derive_owner_id(ISSUER, SUBJECT)
        with self.Session() as db:
            AccountService(db).resolve_or_create_identity(
                provider=oidc_provider_key(ISSUER),
                subject=SUBJECT,
                preferred_owner_id=self.owner_id,
            )
            db.add(make_demo("demo-done", self.owner_id, status="completed"))
            db.add(make_demo("demo-parsing", self.owner_id, status="parsing"))
            db.add(
                DemoJob(
                    id="clip-failed",
                    demo_id="demo-done",
                    job_type="render_clip",
                    status="failed",
                    attempts=1,
                    metadata_json="{}",
                )
            )
            db.commit()

    def tearDown(self) -> None:
        self.redis_patch.stop()
        object.__setattr__(settings, "auth_mode", self.original_auth_mode)
        object.__setattr__(settings, "render_clips_enabled", self.original_render_clips_enabled)
        self.engine.dispose()

    def client(self, **auth_overrides: object) -> TestClient:
        auth_service = AuthService(production_auth_settings(**auth_overrides), self.redis)
        seed_session(self.redis, self.owner_id)
        app = FastAPI()
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=auth_service.settings,
            auth_service_factory=lambda: auth_service,
        )
        app.include_router(auth_api.router)
        app.include_router(demos.router)
        app.include_router(uploads.router)
        app.dependency_overrides[get_auth_service] = lambda: auth_service
        app.dependency_overrides[get_redis_client] = lambda: self.redis

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

    def assert_nothing_was_created(self) -> None:
        with self.Session() as db:
            self.assertEqual(
                sorted(demo.id for demo in db.query(Demo).all()),
                ["demo-done", "demo-parsing"],
            )
            jobs = db.query(DemoJob).all()
            self.assertEqual([(job.id, job.status) for job in jobs], [("clip-failed", "failed")])
        self.assertEqual(self.redis.payloads, [])

    def test_dev_tool_routes_are_not_found_for_an_authenticated_production_owner(self) -> None:
        client = self.client()
        requests = (
            ("/uploads/mock", {}),
            (
                "/demos/demo-done/video/upload",
                {"files": {"file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")}},
            ),
            (
                "/demos/demo-done/video/calibration",
                {"json": {"tickStart": 0, "tickEnd": 640, "tickRate": 64}},
            ),
            ("/demos/demo-done/render/mock", {}),
        )

        with patch("app.api.demos.store_video_artifact") as store_video:
            for path, kwargs in requests:
                with self.subTest(path=path):
                    response = client.post(path, **kwargs)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {"detail": "Not found"})
                    self.assertEqual(response.headers["cache-control"], "private, no-store")

        store_video.assert_not_called()
        self.assert_nothing_was_created()

    def test_anonymous_production_writes_still_get_401_not_404(self) -> None:
        client = self.client()
        client.cookies.clear()

        response = client.post("/uploads/mock")

        self.assertEqual(response.status_code, 401)
        self.assert_nothing_was_created()

    def test_render_clip_routes_are_not_found_in_production_by_default(self) -> None:
        client = self.client()

        for path, kwargs in (
            (
                "/demos/demo-done/render/clip",
                {"json": {"tickStart": 0, "tickEnd": 640, "tickRate": 64}},
            ),
            ("/demos/demo-done/render/jobs/clip-failed/retry", {}),
        ):
            with self.subTest(path=path):
                response = client.post(path, **kwargs)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Not found"})

        self.assert_nothing_was_created()

    def test_render_clip_routes_reach_their_handlers_when_enabled(self) -> None:
        object.__setattr__(settings, "render_clips_enabled", True)
        client = self.client(render_clips_enabled=True)

        clip = client.post(
            "/demos/demo-parsing/render/clip",
            json={"tickStart": 0, "tickEnd": 640, "tickRate": 64},
        )
        retry = client.post("/demos/demo-done/render/jobs/clip-missing/retry")

        self.assertEqual(clip.status_code, 409)
        self.assertEqual(clip.json(), {"detail": "Demo parse must complete before rendering"})
        self.assertEqual(retry.status_code, 404)
        self.assertEqual(retry.json(), {"detail": "Render job not found"})

    def test_kept_read_routes_still_answer_in_production(self) -> None:
        client = self.client()

        jobs = client.get("/demos/demo-done/render/jobs")
        video = client.get("/demos/demo-done/video")

        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(video.status_code, 200)

    def test_auth_me_reports_production_capabilities(self) -> None:
        self.assertEqual(
            self.client().get("/auth/me").json()["capabilities"],
            {"devTools": False, "renderClips": False},
        )
        self.assertEqual(
            self.client(render_clips_enabled=True).get("/auth/me").json()["capabilities"],
            {"devTools": False, "renderClips": True},
        )

    def test_auth_me_reports_dev_capabilities_outside_production(self) -> None:
        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                auth_service = AuthService(Settings(auth_mode=auth_mode), FakeRedis())
                app = FastAPI()
                app.include_router(auth_api.router)
                app.dependency_overrides[get_auth_service] = lambda: auth_service
                app.dependency_overrides[get_db] = lambda: object()

                me = TestClient(app).get("/auth/me")

                self.assertEqual(me.status_code, 200)
                self.assertEqual(
                    me.json()["capabilities"],
                    {"devTools": True, "renderClips": True},
                )
                # Local QA keeps its account shape: no Steam identity to expose.
                self.assertEqual(
                    me.json()["account"],
                    {
                        "displayName": "Local development",
                        "avatarUrl": None,
                        "provider": "development",
                    },
                )


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


def production_auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_mode": "production",
        "auth_provider": "oidc",
        "frontend_public_url": ORIGIN,
        "backend_public_url": ORIGIN,
        "cors_origins_raw": ORIGIN,
        "auth_cookie_secure": True,
    }
    values.update(overrides)
    return Settings(**values)


def seed_session(redis: FakeRedis, owner_id: str) -> None:
    redis.setex(
        "auth:session:" + hashlib.sha256(SESSION_TOKEN.encode("ascii")).hexdigest(),
        300,
        json.dumps({"ownerId": owner_id, "expiresAt": int(time.time()) + 300}),
    )


def make_demo(demo_id: str, owner_id: str, *, status: str) -> Demo:
    now = datetime(2026, 9, 1, tzinfo=UTC)
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
        archived=False,
        created_at=now,
        updated_at=now,
    )


if __name__ == "__main__":
    unittest.main()
