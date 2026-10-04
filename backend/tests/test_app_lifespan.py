import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import settings
from app.main import app


class AppLifespanTest(unittest.TestCase):
    def test_startup_tasks_run_once_through_lifespan(self) -> None:
        # Starlette only runs lifespan inside the client's context; entering it
        # is what a real server does before serving the first request.
        with patch("app.main.on_startup") as on_startup, TestClient(app):
            on_startup.assert_called_once_with()

    def test_startup_is_not_registered_through_deprecated_on_event(self) -> None:
        # The legacy handler lists must stay empty: FastAPI ignores them once a
        # lifespan is configured, so a task added there would silently not run.
        self.assertEqual(app.router.on_startup, [])
        self.assertEqual(app.router.on_shutdown, [])


class UploadSessionStartupTest(unittest.TestCase):
    def setUp(self) -> None:
        startup_settings = SimpleNamespace(
            validate_runtime_configuration=lambda: None,
            max_demo_upload_bytes=settings.max_demo_upload_bytes,
            upload_chunk_bytes=settings.upload_chunk_bytes,
            artifact_quarantine_ttl_seconds=settings.artifact_quarantine_ttl_seconds,
            artifact_storage_backend="s3",
        )
        self.store = MagicMock(name="artifact_store")
        self.staging = MagicMock(name="staging")
        quiet = [
            patch.object(main_module, "settings", startup_settings),
            patch.object(main_module, "init_db"),
            patch.object(main_module, "artifact_store_from_settings", return_value=self.store),
            patch.object(main_module, "ArtifactIntakeService"),
            patch.object(main_module, "SessionLocal"),
            patch.object(main_module, "DeletionService"),
            patch.object(main_module, "prune_upload_ledger"),
            patch.object(main_module, "upload_staging_from_settings", return_value=self.staging),
        ]
        for item in quiet:
            item.start()
            self.addCleanup(item.stop)

    def test_startup_resets_completing_leases_then_sweeps_with_the_artifact_store(self) -> None:
        calls: list[str] = []
        with (
            patch.object(main_module, "reset_completing_leases", side_effect=lambda db: calls.append("reset")),
            patch.object(
                main_module,
                "sweep_upload_sessions",
                side_effect=lambda db, staging, store: calls.append("sweep"),
            ) as sweep,
            patch.object(main_module, "start_replay_warmer") as warmer,
        ):
            main_module.on_startup()

        self.assertEqual(calls, ["reset", "sweep"])
        _db, staging, store = sweep.call_args.args
        self.assertIs(staging, self.staging)
        self.assertIs(store, self.store)
        warmer.assert_called_once_with()

    def test_a_failing_lease_reset_or_sweep_never_stops_startup(self) -> None:
        with (
            patch.object(main_module, "reset_completing_leases", side_effect=RuntimeError("db down")),
            patch.object(main_module, "sweep_upload_sessions", side_effect=OSError("disk gone")) as sweep,
            patch.object(main_module, "start_replay_warmer") as warmer,
            self.assertLogs(main_module.logger, level="WARNING") as logs,
        ):
            main_module.on_startup()

        # The sweep still ran after the reset failed, and startup carried on.
        sweep.assert_called_once()
        warmer.assert_called_once_with()
        logged = " ".join(logs.output)
        self.assertIn("lease reset", logged)
        self.assertIn("sweep", logged)
        self.assertNotIn("db down", logged)
        self.assertNotIn("disk gone", logged)



class UploadSpeedLogTest(unittest.TestCase):
    def test_the_completed_upload_line_reaches_stderr_under_uvicorns_logging(self) -> None:
        # The way the API really runs: uvicorn configures logging (its own
        # loggers only), then imports the app. The speed line must still
        # reach stderr, while other app.* INFO records stay as quiet as before.
        script = "\n".join(
            [
                "import logging, logging.config",
                "from datetime import UTC, datetime",
                "import uvicorn.config",
                "logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)",
                "import app.main",
                "from app.services import upload_session_service as s",
                "now = datetime.now(UTC)",
                "snapshot = s.SessionSnapshot(id='a' * 32, owner_id='owner', state='completing',",
                "    display_filename='secret-name.dem', content_type=None, file_size=1234,",
                "    part_size=1024, part_count=2, pending_demo_id=None, demo_id=None,",
                "    error_code=None, lease_until=None, created_at=now, updated_at=now, expires_at=now)",
                "s.UploadSessionService(None, 'owner', staging=None)._log_completed(snapshot, now)",
                "logging.getLogger('app.services.upload_session_service').info('other app info')",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stderr.splitlines() if "Upload session completed" in line]
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn("bytes=1234 parts=2", lines[0])
        # Content-free: no file name, owner or session id.
        for private in ("secret-name", "owner", "a" * 32):
            self.assertNotIn(private, lines[0])
        self.assertNotIn("other app info", result.stderr)


if __name__ == "__main__":
    unittest.main()
