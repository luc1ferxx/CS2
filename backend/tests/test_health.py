import unittest
from unittest.mock import patch

from app.main import health


class HealthEndpointTest(unittest.TestCase):
    def test_health_reports_api_dependencies_worker_and_storage(self) -> None:
        with patch("app.main.SessionLocal", return_value=FakeSession()), patch(
            "app.main.get_redis_client",
            return_value=FakeRedis(True),
        ):
            payload = health()

        self.assertEqual(payload["status"], "ok")
        self.assertIs(payload["api"], True)
        self.assertIs(payload["database"], True)
        self.assertIs(payload["redis"], True)
        self.assertEqual(payload["workerDependencies"]["redisQueueName"], "cs2-demo-jobs")
        self.assertIs(payload["workerDependencies"]["redisQueueConfigured"], True)
        self.assertIs(payload["workerDependencies"]["renderWorkerTokenConfigured"], True)
        self.assertEqual(payload["workerDependencies"]["maxRenderClipSeconds"], 60)
        self.assertIn("artifactStorageRoot", payload["storage"])
        self.assertIn("replayStorageDir", payload["storage"])
        self.assertIn("demoUploadStorageDir", payload["storage"])
        self.assertIn("videoStorageDir", payload["storage"])
        self.assertIn("summaryStorageDir", payload["storage"])

    def test_health_degrades_when_dependency_check_fails(self) -> None:
        with patch("app.main.SessionLocal", return_value=FailingSession()), patch(
            "app.main.get_redis_client",
            return_value=FakeRedis(False),
        ):
            payload = health()

        self.assertEqual(payload["status"], "degraded")
        self.assertIs(payload["database"], False)
        self.assertIs(payload["redis"], False)
        self.assertIs(payload["workerDependencies"]["redisQueueConfigured"], True)


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
