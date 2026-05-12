import importlib.util
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
