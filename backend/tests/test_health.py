import unittest
from unittest.mock import patch

from app.main import health


class HealthEndpointTest(unittest.TestCase):
    def test_health_reports_only_coarse_public_readiness(self) -> None:
        with patch("app.main.SessionLocal", return_value=FakeSession()), patch(
            "app.main.get_redis_client",
            return_value=FakeRedis(True),
        ):
            payload = health()

        self.assertEqual(payload, {"status": "ok"})

    def test_health_degrades_when_dependency_check_fails(self) -> None:
        with patch("app.main.SessionLocal", return_value=FailingSession()), patch(
            "app.main.get_redis_client",
            return_value=FakeRedis(False),
        ):
            payload = health()

        self.assertEqual(payload, {"status": "degraded"})


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
