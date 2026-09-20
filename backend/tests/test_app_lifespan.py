import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

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
