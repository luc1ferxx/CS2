import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


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

    def install_api(self, capabilities: dict[str, bool]) -> None:
        def fake_request_json(method, path, payload=None):
            self.calls.append((method, path))
            if path == "/health":
                return {"status": "ok"}
            if path == "/auth/me":
                return {"authenticated": True, "capabilities": capabilities}
            if path == "/uploads/mock":
                return {"id": "mock-1"}
            if path.endswith("/status"):
                return {"status": "completed", "map_name": "de_mirage", "round_count": 1}
            if path.endswith("/replay"):
                return {"tickRate": 64, "rounds": [{"roundNumber": 1, "startTick": 0, "endTick": 640}]}
            if path.endswith("/coaching"):
                return []
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

    def test_render_clips_enabled_in_production_renders_from_the_sample(self) -> None:
        self.install_api({"devTools": False, "renderClips": True})
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "sample.dem"
            sample.write_bytes(b"demo")
            self.smoke.SAMPLE_DEMO_PATH = str(sample)

            self.run_main()

        self.assertIn(("POST", "/demos/sample-1/render/clip"), self.calls)
        self.assertNotIn(("POST", "/uploads/mock"), self.calls)

    def test_development_capabilities_keep_the_mock_and_render_steps(self) -> None:
        self.install_api({"devTools": True, "renderClips": True})

        output = self.run_main()

        self.assertIn(("POST", "/uploads/mock"), self.calls)
        self.assertIn(("POST", "/demos/mock-1/render/clip"), self.calls)
        self.assertEqual(self.uploaded, [])
        self.assertIn("sample demo upload skipped", output)

    def test_missing_capabilities_count_as_off(self) -> None:
        self.assertEqual(
            self.smoke.capabilities_from_auth_me({"authenticated": True}),
            {"devTools": False, "renderClips": False},
        )


if __name__ == "__main__":
    unittest.main()
