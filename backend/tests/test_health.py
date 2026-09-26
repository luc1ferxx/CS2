import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.core.config import settings


class HealthEndpointTest(unittest.TestCase):
    """GET /health: 200 {"status": "ok"}, or 503 {"status": "degraded"} on any failed check."""

    def setUp(self) -> None:
        # No context manager: the lifespan (init_db, storage cleanup) stays off.
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def get_health(self, session: object, redis: object) -> tuple[int, object]:
        with patch("app.main.SessionLocal", return_value=session), patch(
            "app.main.get_redis_client",
            return_value=redis,
        ):
            response = self.client.get("/health")
        return response.status_code, response.json()

    def test_health_reports_only_coarse_public_readiness(self) -> None:
        self.assertEqual(self.get_health(FakeSession(), FakeRedis(True)), (200, {"status": "ok"}))

    def test_health_degrades_with_503_when_every_dependency_fails(self) -> None:
        self.assertEqual(
            self.get_health(FailingSession(), FakeRedis(False)),
            (503, {"status": "degraded"}),
        )

    def test_any_single_failed_check_is_a_503(self) -> None:
        cases = {
            "database": (FailingSession(), FakeRedis(True)),
            "redis ping false": (FakeSession(), FakeRedis(False)),
            "redis unreachable": (FakeSession(), UnreachableRedis()),
        }
        for label, (session, redis) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.get_health(session, redis), (503, {"status": "degraded"}))

    def test_missing_worker_configuration_is_a_503(self) -> None:
        original = settings.render_worker_token
        self.addCleanup(object.__setattr__, settings, "render_worker_token", original)
        object.__setattr__(settings, "render_worker_token", "")

        self.assertEqual(
            self.get_health(FakeSession(), FakeRedis(True)),
            (503, {"status": "degraded"}),
        )

    def test_health_function_returns_the_status_code_it_serves(self) -> None:
        with patch("app.main.SessionLocal", return_value=FailingSession()), patch(
            "app.main.get_redis_client",
            return_value=FakeRedis(True),
        ):
            response = main.health()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(bytes(response.body)), {"status": "degraded"})


class FakeSession:
    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _: object) -> None:
        return None


class FailingSession(FakeSession):
    def execute(self, _: object) -> None:
        raise RuntimeError("database unavailable")


class FakeRedis:
    def __init__(self, ping_result: bool) -> None:
        self.ping_result = ping_result

    def ping(self) -> bool:
        return self.ping_result


class UnreachableRedis:
    def ping(self) -> bool:
        raise ConnectionError("redis is unreachable")
