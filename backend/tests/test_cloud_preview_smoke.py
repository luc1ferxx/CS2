import importlib.util
import io
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import coaching as coaching_api
from app.api import demos, replay
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.redis import get_redis_client
from app.models.coaching import CoachingEvent
from app.services.demo_service import DemoService

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"


def load_smoke_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "cloud_preview_smoke.py"
    spec = importlib.util.spec_from_file_location("cloud_preview_smoke", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load cloud_preview_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CloudPreviewSampleConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.smoke = load_smoke_module()

    def test_optional_absent_sample_skips_cleanly(self) -> None:
        self.assertIsNone(
            self.smoke.resolve_sample_demo_path(None, require_sample=False)
        )

    def test_required_absent_sample_fails(self) -> None:
        with self.assertRaises(self.smoke.SmokeFailure) as raised:
            self.smoke.resolve_sample_demo_path(None, require_sample=True)

        self.assertIn("SAMPLE_DEMO_PATH is required", str(raised.exception))

    def test_configured_missing_sample_fails_even_when_optional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.dem"

            with self.assertRaises(self.smoke.SmokeFailure) as raised:
                self.smoke.resolve_sample_demo_path(str(missing), require_sample=False)

        self.assertIn("SAMPLE_DEMO_PATH does not exist", str(raised.exception))

    def test_configured_directory_sample_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(self.smoke.SmokeFailure) as raised:
                self.smoke.resolve_sample_demo_path(directory, require_sample=False)

        self.assertIn("SAMPLE_DEMO_PATH is not a file", str(raised.exception))

    def test_require_sample_flag_reads_cli_and_env(self) -> None:
        self.assertTrue(self.smoke.require_sample_enabled(["--require-sample"], {}))
        self.assertTrue(self.smoke.require_sample_enabled([], {"REQUIRE_SAMPLE_DEMO": "true"}))
        self.assertTrue(self.smoke.require_sample_enabled([], {"SAMPLE_DEMO_REQUIRED": "1"}))
        self.assertFalse(self.smoke.require_sample_enabled([], {"REQUIRE_SAMPLE_DEMO": "0"}))

    def test_sample_completion_message_includes_counts(self) -> None:
        message = self.smoke.sample_completion_message(
            "demo-1",
            {
                "name": "Sample Match",
                "map_name": "de_mirage",
                "round_count": 24,
                "coaching_event_count": 12,
            },
        )

        self.assertIn("demo-1", message)
        self.assertIn("Sample Match", message)
        self.assertIn("de_mirage", message)
        self.assertIn("24 rounds", message)
        self.assertIn("12 coaching", message)

    def test_sample_completion_message_includes_calibration_metadata(self) -> None:
        message = self.smoke.sample_completion_message(
            "demo-1",
            {
                "name": "Sample Match",
                "map_name": "de_anubis",
                "round_count": 24,
                "coaching_event_count": 12,
            },
            {
                "mapMetadata": {
                    "displayName": "Anubis",
                    "confidence": "approximate",
                    "calibrated": False,
                }
            },
        )

        self.assertIn("Anubis", message)
        self.assertIn("approximate", message)
        self.assertIn("uncalibrated", message)

    def test_rename_demo_trims_name_and_returns_updated_payload(self) -> None:
        calls = []

        def fake_request_json(method, path, payload=None):
            calls.append((method, path, payload))
            return {"id": "demo-1", "name": payload["name"]}

        self.smoke.request_json = fake_request_json

        payload = self.smoke.rename_demo("demo-1", "  Local Sample  ")

        self.assertEqual(payload["name"], "Local Sample")
        self.assertEqual(
            calls,
            [("PATCH", "/demos/demo-1", {"name": "Local Sample"})],
        )

    def test_failure_diagnostics_summary_handles_unavailable_endpoint(self) -> None:
        def fake_request_json(method, path, payload=None):
            raise self.smoke.SmokeFailure(f"{method} {path} unavailable")

        self.smoke.request_json = fake_request_json

        summary = self.smoke.failure_diagnostics_summary()

        self.assertIn("diagnostics unavailable", summary)
        self.assertIn("GET /diagnostics unavailable", summary)

    def test_diagnostics_summary_is_compact(self) -> None:
        summary = self.smoke.diagnostics_summary(
            {
                "status": "degraded",
                "dependencies": {
                    "database": {"ok": True},
                    "redis": {"ok": False},
                    "storage": {"ok": True},
                    "worker": {"ok": True},
                },
                "worker": {
                    "queueName": "cs2-demo-jobs",
                    "queueLength": 2,
                    "heartbeat": {"alive": False, "ageSeconds": 120},
                },
                "jobs": {
                    "recentFailures": [
                        {
                            "jobType": "real_parse",
                            "status": "failed",
                            "errorCode": "INVALID_DEMO",
                            "message": "Invalid or unreadable demo file.",
                        }
                    ]
                },
            }
        )

        self.assertIn("diagnostics: status=degraded", summary)
        self.assertIn("db=ok", summary)
        self.assertIn("redis=fail", summary)
        self.assertIn("queue=cs2-demo-jobs", summary)
        self.assertIn("heartbeat=stale", summary)
        self.assertIn("recentFailures=1", summary)
        self.assertNotIn("Invalid or unreadable demo file.", summary)

    def test_smoke_uses_opaque_session_cookie_without_dev_owner_header(self) -> None:
        self.smoke.AUTH_SESSION_COOKIE = "local-fixture-session"
        self.smoke.FRONTEND_URL = "https://coach.example.test/dashboard"

        get_headers = self.smoke.auth_headers("GET")
        post_headers = self.smoke.auth_headers("POST")

        self.assertEqual(
            get_headers,
            {"Cookie": "__Host-cs2_session=local-fixture-session"},
        )
        self.assertEqual(post_headers["Origin"], "https://coach.example.test")
        self.assertNotIn("X-Dev-User-Id", post_headers)

    def test_smoke_dev_mode_uses_only_the_explicit_dev_owner_header(self) -> None:
        self.smoke.AUTH_SESSION_COOKIE = ""
        self.smoke.OWNER_ID = "smoke-owner"

        self.assertEqual(
            self.smoke.auth_headers("POST"),
            {"X-Dev-User-Id": "smoke-owner"},
        )


class CloudPreviewCapabilitiesTest(unittest.TestCase):
    """main() follows the /auth/me capabilities a production preview reports."""

    def setUp(self) -> None:
        self.smoke = load_smoke_module()
        self.smoke.SAMPLE_DEMO_PATH = None
        self.smoke.SAMPLE_DEMO_NAME = None
        self.calls: list[tuple[str, str]] = []
        self.payloads: dict[str, dict] = {}
        self.uploaded: list[str] = []
        self.media_urls: list[str | None] = []
        self.smoke.check_frontend = lambda: None
        self.smoke.check_media_route = self.media_urls.append
        self.smoke.upload_sample_demo = self.fake_upload_sample_demo
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("REQUIRE_SAMPLE_DEMO", None)
        os.environ.pop("SAMPLE_DEMO_REQUIRED", None)

    def fake_upload_sample_demo(self, path: Path) -> str:
        self.uploaded.append(path.name)
        return "sample-1"

    def install_api(
        self,
        capabilities: dict[str, bool],
        *,
        players: list[dict] | None = None,
        coaching: list[dict] | None = None,
    ) -> None:
        replay = {
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 0, "endTick": 640}],
            "players": players or [],
        }

        def fake_request_json(method, path, payload=None):
            self.calls.append((method, path))
            if payload is not None:
                self.payloads[path] = payload
            if path == "/health":
                return {"status": "ok"}
            if path == "/auth/me":
                return {"authenticated": True, "capabilities": capabilities}
            if path == "/uploads/mock":
                return {"id": "mock-1"}
            if path.endswith("/status"):
                return {"status": "completed", "map_name": "de_mirage", "round_count": 1}
            if path.endswith("/replay"):
                return replay
            if path.endswith("/coaching"):
                return coaching or []
            if path.endswith("/render/clip"):
                return {"job_id": "job-1"}
            if path.endswith("/render/jobs"):
                return [{"job_id": "job-1"}]
            if path == "/diagnostics":
                raise self.smoke.SmokeFailure("GET /diagnostics failed with HTTP 404")
            raise AssertionError(f"unexpected request {method} {path}")

        self.smoke.request_json = fake_request_json

    def run_main(self) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.smoke.main([]), 0)
        return output.getvalue()

    def test_production_capabilities_smoke_the_sample_and_skip_gated_steps(self) -> None:
        self.install_api({"devTools": False, "renderClips": False})
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "sample.dem"
            sample.write_bytes(b"demo")
            self.smoke.SAMPLE_DEMO_PATH = str(sample)

            output = self.run_main()

        self.assertEqual(self.uploaded, ["sample.dem"])
        self.assertNotIn(("POST", "/uploads/mock"), self.calls)
        self.assertFalse(any(path.endswith("/render/clip") for _, path in self.calls))
        self.assertIn(("GET", "/demos/sample-1/replay"), self.calls)
        self.assertIn(("GET", "/demos/sample-1/coaching"), self.calls)
        self.assertEqual(self.media_urls, [None])
        self.assertIn("mock upload skipped", output)
        self.assertIn("render_clip skipped", output)
        self.assertIn("cloud preview smoke passed", output)

    def test_production_capabilities_require_a_sample_before_any_write(self) -> None:
        self.install_api({"devTools": False, "renderClips": False})

        with self.assertRaises(self.smoke.SmokeFailure) as raised:
            with redirect_stdout(io.StringIO()):
                self.smoke.main([])

        self.assertIn("SAMPLE_DEMO_PATH is required", str(raised.exception))
        self.assertEqual([call for call in self.calls if call[0] != "GET"], [])
        self.assertEqual(self.uploaded, [])

    def install_sample(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        sample = Path(directory.name) / "sample.dem"
        sample.write_bytes(b"demo")
        self.smoke.SAMPLE_DEMO_PATH = str(sample)

    def test_render_clips_enabled_in_production_renders_from_the_sample(self) -> None:
        self.install_api(
            {"devTools": False, "renderClips": True},
            players=[{"id": STEAM_A, "name": "a"}, {"id": STEAM_B, "name": "b"}],
            coaching=[{"id": "event-1", "tick_start": 320, "player_id": STEAM_B}],
        )
        self.install_sample()

        self.run_main()

        self.assertIn(("POST", "/demos/sample-1/render/clip"), self.calls)
        self.assertNotIn(("POST", "/uploads/mock"), self.calls)
        # External mode refuses a clip without a SteamID64 POV, so the smoke has
        # to name one; the coaching event's own player is the natural choice.
        request = self.payloads["/demos/sample-1/render/clip"]
        self.assertEqual(request["playerId"], STEAM_B)
        self.assertEqual(request["eventId"], "event-1")

    def test_production_render_without_a_steam_pov_fails_before_posting(self) -> None:
        self.install_api(
            {"devTools": False, "renderClips": True},
            players=[{"id": "bot-1", "name": "bot"}],
        )
        self.install_sample()

        with self.assertRaises(self.smoke.SmokeFailure) as raised:
            with redirect_stdout(io.StringIO()):
                self.smoke.main([])

        self.assertIn("SteamID64", str(raised.exception))
        self.assertFalse(any(path.endswith("/render/clip") for _, path in self.calls))

    def test_development_capabilities_keep_the_mock_and_render_steps(self) -> None:
        # The mock replay's players have no Steam IDs, and the fallback renderer
        # takes a clip without a POV, so development keeps today's request.
        self.install_api(
            {"devTools": True, "renderClips": True},
            players=[{"id": "t-entry", "name": "aimclub.entry"}],
        )

        output = self.run_main()

        self.assertIn(("POST", "/uploads/mock"), self.calls)
        self.assertIn(("POST", "/demos/mock-1/render/clip"), self.calls)
        self.assertNotIn("playerId", self.payloads["/demos/mock-1/render/clip"])
        self.assertEqual(self.uploaded, [])
        self.assertIn("sample demo upload skipped", output)

    def test_missing_capabilities_count_as_off(self) -> None:
        self.assertEqual(
            self.smoke.capabilities_from_auth_me({"authenticated": True}),
            {"devTools": False, "renderClips": False},
        )


class CloudPreviewRenderPovTest(unittest.TestCase):
    """render_clip_request names a POV the render API resolves to one SteamID64 player."""

    def setUp(self) -> None:
        self.smoke = load_smoke_module()
        self.replay = {
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 0, "endTick": 640}],
            "players": [
                {"id": "bot-1", "name": "bot"},
                {"id": STEAM_A, "name": "a"},
                {"id": STEAM_B, "name": "b"},
            ],
        }

    def request(self, event: dict | None = None, **kwargs) -> dict:
        return self.smoke.render_clip_request(self.replay, [event] if event else [], **kwargs)

    def test_prefers_an_evidence_player_that_has_a_steam_id(self) -> None:
        event = {
            "id": "event-1",
            "tick_start": 320,
            "player_id": "bot-1",
            "structured_context_json": {"involvedPlayerIds": ["bot-1", "missing", STEAM_B]},
        }

        self.assertEqual(self.request(event)["playerId"], STEAM_B)

    def test_falls_back_to_the_first_player_with_a_steam_id(self) -> None:
        event = {"id": "event-1", "player_id": "bot-1", "structured_context_json": {"involvedPlayerIds": "bad"}}

        self.assertEqual(self.request(event)["playerId"], STEAM_A)
        self.assertEqual(self.request()["playerId"], STEAM_A)

    def test_reads_steam_id_before_id_and_skips_ambiguous_ids(self) -> None:
        self.replay["players"] = [
            {"id": STEAM_A, "name": "a"},
            {"id": STEAM_A, "name": "a-again"},
            {"id": "p-2", "steamId": STEAM_B, "name": "b"},
            {"id": "76561198000000003", "steamId": "not-a-steam-id", "name": "c"},
        ]

        # The API matches playerId exactly once and reads steamId before id, so
        # a duplicated id or a malformed steamId would be refused there too.
        self.assertEqual(self.request()["playerId"], "p-2")

    def test_development_keeps_the_request_without_a_steam_player(self) -> None:
        self.replay["players"] = [{"id": "t-entry", "name": "aimclub.entry"}]

        request = self.request({"id": "event-1", "tick_start": 320, "player_id": "t-entry"})

        self.assertNotIn("playerId", request)
        self.assertEqual(request["eventId"], "event-1")

    def test_production_fails_clearly_without_a_steam_player(self) -> None:
        self.replay["players"] = [{"id": "t-entry", "name": "aimclub.entry"}]

        with self.assertRaises(self.smoke.SmokeFailure) as raised:
            self.request(require_pov=True)

        self.assertIn("17-digit SteamID64", str(raised.exception))


class CloudPreviewRenderContractTest(unittest.TestCase):
    """The smoke's render_clip request passes the real route in RENDER_WORKER_MODE=external."""

    def setUp(self) -> None:
        self.smoke = load_smoke_module()
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        changed = {
            "auth_mode": "development",
            "artifact_storage_backend": "local",
            "artifact_storage_root": root / "artifacts",
            "replay_storage_dir": root / "replays",
            "render_worker_mode": "external",
        }
        for key, value in changed.items():
            self.addCleanup(object.__setattr__, settings, key, getattr(settings, key))
            object.__setattr__(settings, key, value)

        app = FastAPI()
        for router in (demos.router, replay.router, coaching_api.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = self.override_get_db
        app.dependency_overrides[get_redis_client] = FakeRedis
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

        with self.Session() as db:
            service = DemoService(db, owner_id="dev-user")
            prepared = service.prepare_real_demo(
                stream=io.BytesIO(b"PBDEMS2\0" + b"\0" * 64), filename="sample.dem",
                content_type="application/octet-stream",
            )
            service.commit_prepared_real_demo(prepared)
            demo = prepared.demo
            demo.status = prepared.job.status = "completed"
            demo.map_name = "de_nuke"
            self.demo_id = demo.id
            demo.replay_storage_key = service.write_replay_blob(demo.id, {
                "demoId": demo.id, "mapName": "de_nuke", "tickRate": 64,
                "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 5760}],
                "players": [{"id": "bot-1", "name": "bot", "side": "T"},
                            {"id": STEAM_A, "name": "a", "side": "T"},
                            {"id": STEAM_B, "name": "b", "side": "CT"}],
                "frames": [], "events": [],
                "video": {"status": "ready", "source": "mock", "url": None,
                          "tickStart": 0, "tickEnd": 5760, "tickRate": 64,
                          "durationSeconds": 90, "timeOriginSeconds": 0},
            })
            db.add(CoachingEvent(
                id=str(uuid.uuid4()), demo_id=demo.id, round_number=1,
                player_id="bot-1", player_name="bot", tick_start=1280, tick_end=1400,
                category="trading", severity="medium", title="Untraded death", message="Review the trade.",
                structured_context_json={"ruleId": "untraded_death", "involvedPlayerIds": ["bot-1", STEAM_B]},
                confidence=0.8,
            ))
            db.commit()

    def override_get_db(self):
        with self.Session() as db:
            yield db

    def smoke_request(self) -> dict:
        replay_response = self.client.get(f"/demos/{self.demo_id}/replay")
        self.assertEqual(replay_response.status_code, 200, replay_response.text)
        coaching_response = self.client.get(f"/demos/{self.demo_id}/coaching")
        self.assertEqual(coaching_response.status_code, 200, coaching_response.text)
        return self.smoke.render_clip_request(
            replay_response.json(), coaching_response.json(), require_pov=True,
        )

    def test_smoke_request_creates_an_external_render_job(self) -> None:
        request = self.smoke_request()

        response = self.client.post(f"/demos/{self.demo_id}/render/clip", json=request)

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["player_id"], STEAM_B)
        self.assertEqual(response.json()["pov_steam_id"], STEAM_B)
        self.assertEqual(response.json()["status"], "queued")

    def test_the_request_without_a_pov_is_what_external_mode_refuses(self) -> None:
        request = self.smoke_request()
        request.pop("playerId")

        response = self.client.post(f"/demos/{self.demo_id}/render/clip", json=request)

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "Select a player with a Steam ID before rendering")


class FakeRedis:
    """Keeps the route's render-worker heartbeat read off any local Redis."""

    def get(self, key: str) -> str | None:
        return None

    def setex(self, key: str, ttl: int, value: str) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
