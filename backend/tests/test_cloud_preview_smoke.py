import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import uuid
from contextlib import redirect_stdout
from email.message import Message
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
from app.models.demo import Demo
from app.models.upload_session import UploadSession
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


class CloudPreviewHealthTest(unittest.TestCase):
    """check_health fails on the 503 a degraded API answers, and says why."""

    def setUp(self) -> None:
        self.smoke = load_smoke_module()
        self.smoke.API_BASE_URL = "https://coach.example.test"
        self.smoke.AUTH_SESSION_COOKIE = ""

    def serve(self, status: int, body: bytes) -> list[str]:
        urls: list[str] = []

        def fake_urlopen(request, timeout=None):
            urls.append(request.full_url)
            if status >= 400:
                raise urllib.error.HTTPError(request.full_url, status, "error", Message(), io.BytesIO(body))
            return FakeResponse(body)

        patcher = mock.patch.object(self.smoke.urllib.request, "urlopen", side_effect=fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        return urls

    def test_ok_health_passes(self) -> None:
        urls = self.serve(200, b'{"status":"ok"}')
        output = io.StringIO()

        with redirect_stdout(output):
            self.smoke.check_health()

        self.assertEqual(urls, ["https://coach.example.test/health"])
        self.assertIn("health ok", output.getvalue())

    def test_degraded_503_is_a_clear_health_failure(self) -> None:
        self.serve(503, b'{"status":"degraded"}')

        with self.assertRaises(self.smoke.SmokeFailure) as raised:
            self.smoke.check_health()

        message = str(raised.exception)
        self.assertIn('API health is degraded (HTTP 503 {"status":"degraded"})', message)
        self.assertIn("database, Redis or worker configuration", message)

    def test_other_http_errors_name_the_status(self) -> None:
        self.serve(502, b"Bad Gateway")

        with self.assertRaises(self.smoke.SmokeFailure) as raised:
            self.smoke.check_health()

        self.assertEqual(str(raised.exception), "GET /health failed with HTTP 502: Bad Gateway")

    def test_degraded_health_stops_the_smoke_before_any_write(self) -> None:
        urls = self.serve(503, b'{"status":"degraded"}')
        self.smoke.check_frontend = mock.Mock()
        self.smoke.SAMPLE_DEMO_PATH = None
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("REQUIRE_SAMPLE_DEMO", None)
        os.environ.pop("SAMPLE_DEMO_REQUIRED", None)

        with self.assertRaises(self.smoke.SmokeFailure), redirect_stdout(io.StringIO()):
            self.smoke.main([])

        self.assertEqual(urls, ["https://coach.example.test/health"])
        self.smoke.check_frontend.assert_not_called()


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


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


class CloudPreviewChunkedUploadTest(unittest.TestCase):
    """upload_sample_demo goes through the chunked upload session the browser uses."""

    PART = 4

    def setUp(self) -> None:
        self.smoke = load_smoke_module()
        self.smoke.API_BASE_URL = "https://coach.example.test"
        self.smoke.FRONTEND_URL = "https://coach.example.test"
        self.smoke.UPLOAD_SESSION_POLL_SECONDS = 0
        self.sleeps: list[float] = []
        sleeper = mock.patch.object(self.smoke.time, "sleep", side_effect=self.sleeps.append)
        sleeper.start()
        self.addCleanup(sleeper.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = b"PBDEMS2\0" + bytes(range(10))  # 18 bytes -> parts of 4, 4, 4, 4, 2
        self.sample = Path(directory.name) / "sample.dem"
        self.sample.write_bytes(self.data)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.parts: dict[int, bytes] = {}
        self.part_headers: list[tuple[str, int, str]] = []
        self.part_replies: dict[int, list[tuple[int, str, float | None]]] = {}
        self.complete_replies: list[object] = []
        self.status_replies: list[dict] = []
        self.create_replies: list[object] = []
        self.smoke.request_json = self.fake_request_json
        self.smoke.put_upload_part = self.fake_put_upload_part

    def session(self, **extra: object) -> dict:
        return {
            "sessionId": "a" * 32,
            "uploadToken": "upload-token",
            "partSize": self.PART,
            "partCount": 5,
            "maxParallelParts": 4,
            "expiresAt": "2026-10-05T00:00:00Z",
            "receivedParts": [],
            **extra,
        }

    def http_error(self, status: int, body: dict) -> Exception:
        return self.smoke.HttpStatusFailure(
            f"HTTP {status}", status=status, detail=json.dumps(body)
        )

    def fake_request_json(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if (method, path) == ("POST", "/uploads/sessions"):
            reply = self.create_replies.pop(0) if self.create_replies else self.session()
        elif path.endswith("/complete"):
            reply = self.complete_replies.pop(0)
        elif method == "GET" and path.startswith("/uploads/sessions/"):
            reply = self.status_replies.pop(0)
        else:
            raise AssertionError(f"unexpected request {method} {path}")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def fake_put_upload_part(self, session_id, token, index, data, digest):
        self.part_headers.append((token, index, digest))
        queued = self.part_replies.get(index)
        if queued:
            return queued.pop(0)
        self.parts[index] = data
        return 200, json.dumps({"index": index, "sizeBytes": len(data), "sha256": digest, "receivedCount": len(self.parts)}), None

    def test_the_plan_covers_the_file_and_the_last_part_is_the_remainder(self) -> None:
        plan = self.smoke.UploadPlan(file_size=18, part_size=4, part_count=5)

        self.assertEqual([plan.part_range(index) for index in range(5)], [(0, 4), (4, 4), (8, 4), (12, 4), (16, 2)])
        self.assertEqual(plan.missing({0, 3}), [1, 2, 4])
        with self.assertRaises(self.smoke.SmokeFailure):
            plan.part_range(5)
        with self.assertRaises(self.smoke.SmokeFailure):
            self.smoke.UploadPlan(file_size=18, part_size=4, part_count=4)

    def test_every_part_is_sent_with_its_token_and_digest_then_completed(self) -> None:
        self.complete_replies = [{"id": "demo-1", "status": "queued"}]
        output = io.StringIO()

        with redirect_stdout(output):
            demo_id = self.smoke.upload_sample_demo(self.sample)

        self.assertEqual(demo_id, "demo-1")
        self.assertEqual(b"".join(self.parts[index] for index in range(5)), self.data)
        self.assertEqual(
            self.part_headers,
            [("upload-token", index, hashlib.sha256(self.parts[index]).hexdigest()) for index in range(5)],
        )
        create = self.calls[0]
        self.assertEqual(create[2], {"filename": "sample.dem", "size": 18, "contentType": "application/octet-stream"})
        self.assertEqual(self.calls[-1][:2], ("POST", f"/uploads/sessions/{'a' * 32}/complete"))
        self.assertIn("5 part(s) of 4 bytes, 18 bytes sent", output.getvalue())

    def test_an_unfinished_session_from_an_earlier_run_is_replaced(self) -> None:
        self.create_replies = [
            self.http_error(409, {"detail": {"code": "upload_session_exists", "message": "x", "sessionId": "b" * 32}}),
            self.session(),
        ]
        self.complete_replies = [{"id": "demo-1"}]

        with redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample)

        creates = [payload for method, path, payload in self.calls if path == "/uploads/sessions"]
        self.assertEqual(len(creates), 2)
        self.assertNotIn("replace", creates[0])
        self.assertIs(creates[1]["replace"], True)

    def test_other_create_errors_are_not_retried(self) -> None:
        self.create_replies = [self.http_error(429, {"detail": {"code": "upload_daily_limit", "message": "x"}})]

        with self.assertRaises(self.smoke.HttpStatusFailure), redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample)

        self.assertEqual(self.parts, {})

    def test_a_busy_part_is_resent_after_retry_after(self) -> None:
        self.part_replies[2] = [(503, '{"detail":{"code":"INTAKE_BUSY"}}', 2.0)]
        self.complete_replies = [{"id": "demo-1"}]

        with redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample)

        self.assertEqual([index for _, index, _ in self.part_headers], [0, 1, 2, 2, 3, 4])
        self.assertIn(2.0, self.sleeps)
        self.assertEqual(len(self.parts), 5)

    def test_a_rejected_part_fails_the_smoke(self) -> None:
        self.part_replies[0] = [(400, '{"detail":"Unsupported content.","errorCode":"INTAKE_CONTENT_MISMATCH"}', None)]

        with self.assertRaises(self.smoke.SmokeFailure) as raised, redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample)

        self.assertIn("HTTP 400", str(raised.exception))
        self.assertFalse(any(path.endswith("/complete") for _, path, _ in self.calls))

    def test_missing_parts_are_resent_before_completing_again(self) -> None:
        self.complete_replies = [
            self.http_error(409, {"detail": {"code": "upload_parts_missing", "message": "x", "missingParts": [1, 4]}}),
            {"id": "demo-1"},
        ]

        with redirect_stdout(io.StringIO()):
            demo_id = self.smoke.upload_sample_demo(self.sample)

        self.assertEqual(demo_id, "demo-1")
        self.assertEqual([index for _, index, _ in self.part_headers], [0, 1, 2, 3, 4, 1, 4])

    def test_a_202_is_completed_again_until_the_demo_exists(self) -> None:
        # Completing again (not reading the state) is what takes over a lease
        # an API restart ended; the finished attempt then answers 200.
        self.complete_replies = [{"state": "completing"}, {"state": "completing"}, {"id": "demo-9"}]

        with redirect_stdout(io.StringIO()):
            demo_id = self.smoke.upload_sample_demo(self.sample)

        self.assertEqual(demo_id, "demo-9")
        completes = [path for method, path, _ in self.calls if path.endswith("/complete")]
        self.assertEqual(completes, [f"/uploads/sessions/{'a' * 32}/complete"] * 3)
        self.assertFalse([path for method, path, _ in self.calls if method == "GET"])
        self.assertEqual(self.sleeps, [self.smoke.UPLOAD_SESSION_POLL_SECONDS] * 2)

    def test_a_session_that_failed_while_completing_fails_the_smoke(self) -> None:
        self.complete_replies = [
            {"state": "completing"},
            self.http_error(400, {"detail": "Uploaded demo is truncated", "errorCode": "INTAKE_TRUNCATED"}),
        ]

        with self.assertRaises(self.smoke.SmokeFailure) as raised, redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample)

        self.assertIn("HTTP 400", str(raised.exception))
        self.assertIn("INTAKE_TRUNCATED", raised.exception.detail)

    def test_parts_in_flight_and_a_busy_intake_slot_wait_and_complete_again(self) -> None:
        self.complete_replies = [
            self.http_error(409, {"detail": {"code": "upload_parts_in_flight", "message": "x", "retryAfterSeconds": 1}}),
            self.http_error(503, {"detail": {"code": "INTAKE_BUSY", "message": "x"}}),
            {"id": "demo-1"},
        ]

        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.smoke.upload_sample_demo(self.sample), "demo-1")

        self.assertEqual(self.sleeps, [1.0, 2])

    def test_a_resumed_session_sends_only_the_parts_it_lacks(self) -> None:
        self.create_replies = [self.session(receivedParts=[0, 1, 3])]
        self.complete_replies = [{"id": "demo-1"}]

        with redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample)

        self.assertEqual([index for _, index, _ in self.part_headers], [2, 4])


class CloudPreviewChunkedUploadContractTest(unittest.TestCase):
    """The smoke's chunked upload against the real upload session routes (development auth)."""

    PART_BYTES = 4 * 1024 * 1024

    def setUp(self) -> None:
        from app.api import uploads as uploads_api
        from app.core.upload_slots import IntakeSlot, get_intake_slot
        from app.services.storage import UploadStagingStore
        from app.services.upload_session_service import get_upload_session_factory, get_upload_staging

        self.smoke = load_smoke_module()
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        changed = {
            "auth_mode": "development",
            "artifact_storage_backend": "local",
            "artifact_storage_root": self.root / "artifacts",
            "upload_staging_root": self.root / "staging",
            "upload_part_bytes": self.PART_BYTES,
            "upload_staging_min_free_bytes": 0,
        }
        for key, value in changed.items():
            self.addCleanup(object.__setattr__, settings, key, getattr(settings, key))
            object.__setattr__(settings, key, value)
        redis_patch = mock.patch("app.services.demo_service.get_redis_client", return_value=QueueRedis())
        redis_patch.start()
        self.addCleanup(redis_patch.stop)

        self.staging = UploadStagingStore(self.root / "staging")
        slot = IntakeSlot()
        app = FastAPI()
        app.include_router(uploads_api.router)
        app.dependency_overrides[get_db] = self.override_get_db
        app.dependency_overrides[get_upload_staging] = lambda: self.staging
        app.dependency_overrides[get_upload_session_factory] = lambda: self.Session
        app.dependency_overrides[get_intake_slot] = lambda: slot
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.smoke.request_json = self.forward_json
        self.smoke.put_upload_part = self.forward_part
        sleeper = mock.patch.object(self.smoke.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def override_get_db(self):
        with self.Session() as db:
            yield db

    def forward_json(self, method, path, payload=None):
        response = self.client.request(
            method, path, json=payload, headers={"X-Dev-User-Id": "smoke-owner"}
        )
        if response.status_code >= 400:
            raise self.smoke.HttpStatusFailure(
                f"{method} {path} failed with HTTP {response.status_code}",
                status=response.status_code,
                detail=response.text,
            )
        return response.json() if response.content else None

    def forward_part(self, session_id, token, index, data, digest):
        # Exactly the headers the smoke's http.client PUT sends.
        response = self.client.put(
            f"/uploads/sessions/{session_id}/parts/{index}",
            content=data,
            headers={
                "X-Upload-Token": token,
                "X-Part-SHA256": digest,
                "Content-Type": "application/octet-stream",
                "Origin": self.smoke.frontend_origin(),
            },
        )
        retry_after = response.headers.get("Retry-After")
        return response.status_code, response.text, float(retry_after) if retry_after else None

    def sample(self, data: bytes) -> Path:
        path = self.root / "sample.dem"
        path.write_bytes(data)
        return path

    def test_the_smoke_uploads_a_sample_through_a_session(self) -> None:
        data = b"PBDEMS2\x00" + b"\x02" * (self.PART_BYTES + 100)

        with redirect_stdout(io.StringIO()) as output:
            demo_id = self.smoke.upload_sample_demo(self.sample(data))

        self.assertIn("2 part(s)", output.getvalue())
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            self.assertIsNotNone(demo)
            self.assertEqual(demo.owner_id, "smoke-owner")
            session = db.query(UploadSession).one()
            self.assertEqual((session.state, session.demo_id), ("completed", demo_id))
        self.assertEqual(self.staging.list_parts("smoke-owner", session.id), {})

    def test_a_second_run_replaces_an_unfinished_session(self) -> None:
        data = b"PBDEMS2\x00" + b"\x03" * 64
        left = self.forward_json("POST", "/uploads/sessions", {"filename": "old.dem", "size": len(data)})

        with redirect_stdout(io.StringIO()):
            demo_id = self.smoke.upload_sample_demo(self.sample(data))

        with self.Session() as db:
            self.assertIsNone(db.get(UploadSession, left["sessionId"]))
            self.assertIsNotNone(db.get(Demo, demo_id))

    def test_an_archive_fails_on_its_first_part(self) -> None:
        data = b"PK\x03\x04" + b"\x00" * (self.PART_BYTES + 100)

        with self.assertRaises(self.smoke.SmokeFailure) as raised, redirect_stdout(io.StringIO()):
            self.smoke.upload_sample_demo(self.sample(data))

        self.assertIn("INTAKE_CONTENT_MISMATCH", str(raised.exception))
        with self.Session() as db:
            self.assertEqual(db.query(Demo).count(), 0)


class QueueRedis:
    """Accepts the parser dispatch of a completed upload."""

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def lpush(self, _queue: str, payload: str) -> None:
        self.payloads.append(payload)

    def setex(self, key: str, ttl: int, value: str) -> None:
        return None

    def get(self, key: str) -> str | None:
        return None


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
