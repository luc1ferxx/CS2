import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.core.config import settings
from app.services import diagnostics
from app.workers import healthcheck
from app.workers.child_process import child_environment
from app.workers.worker import PACKAGE_ROOT


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



class WorkerHealthEndpointTest(unittest.TestCase):
    """GET /health/worker: 200 while the parse worker's heartbeat is fresh, else 503."""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def get_worker_health(self, redis: object) -> tuple[int, object]:
        with patch("app.main.get_redis_client", return_value=redis):
            response = self.client.get("/health/worker")
        return response.status_code, response.json()

    def test_a_fresh_heartbeat_is_ok(self) -> None:
        redis = HeartbeatRedis()
        diagnostics.write_worker_heartbeat(redis)
        self.assertEqual(self.get_worker_health(redis), (200, {"status": "ok"}))

    def test_a_missing_stale_or_unreadable_heartbeat_is_a_503(self) -> None:
        stale = HeartbeatRedis()
        diagnostics.write_worker_heartbeat(stale, now=datetime.now(UTC) - timedelta(seconds=91))
        cases = {
            "missing": HeartbeatRedis(),
            "stale": stale,
            "unreadable": HeartbeatRedis({diagnostics.WORKER_HEARTBEAT_KEY: "not json"}),
            "redis unreachable": UnreachableRedis(),
        }
        for label, redis in cases.items():
            with self.subTest(label):
                self.assertEqual(self.get_worker_health(redis), (503, {"status": "degraded"}))

    def test_it_is_served_in_production_and_never_folded_into_health(self) -> None:
        original = settings.auth_mode
        self.addCleanup(object.__setattr__, settings, "auth_mode", original)
        object.__setattr__(settings, "auth_mode", "production")
        self.assertEqual(self.get_worker_health(HeartbeatRedis()), (503, {"status": "degraded"}))
        # A dead worker leaves /health alone: caddy and the frontend wait on it.
        with patch("app.main.SessionLocal", return_value=FakeSession()), patch(
            "app.main.get_redis_client", return_value=HeartbeatRedis(ping_result=True),
        ):
            self.assertEqual(self.client.get("/health").status_code, 200)


class WorkerHeartbeatCheckTest(unittest.TestCase):
    """python -m app.workers.healthcheck, the worker container's compose healthcheck."""

    def test_it_reads_the_same_key_and_window_as_diagnostics(self) -> None:
        self.assertEqual(healthcheck.WORKER_HEARTBEAT_KEY, diagnostics.WORKER_HEARTBEAT_KEY)
        self.assertEqual(
            healthcheck.WORKER_HEARTBEAT_ALIVE_SECONDS, diagnostics.WORKER_HEARTBEAT_ALIVE_SECONDS,
        )

    def test_verdicts(self) -> None:
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        fresh, stale = HeartbeatRedis(), HeartbeatRedis()
        diagnostics.write_worker_heartbeat(fresh, now=now - timedelta(seconds=90))
        diagnostics.write_worker_heartbeat(stale, now=now - timedelta(seconds=91))
        self.assertEqual(healthcheck.check(fresh, now=now), (True, "worker heartbeat ok (90s old)"))
        self.assertEqual(healthcheck.check(stale, now=now), (False, "worker heartbeat stale (91s old)"))
        self.assertEqual(healthcheck.check(HeartbeatRedis(), now=now), (False, "worker heartbeat missing"))
        unreadable = HeartbeatRedis({diagnostics.WORKER_HEARTBEAT_KEY: '{"lastSeenAt": 5}'})
        self.assertEqual(healthcheck.check(unreadable, now=now), (False, "worker heartbeat unreadable"))
        alive, verdict = healthcheck.check(UnreachableRedis(), now=now)
        self.assertFalse(alive)
        self.assertEqual(verdict, "worker heartbeat unreachable (ConnectionError)")

    def test_main_exits_0_only_while_alive(self) -> None:
        fresh = HeartbeatRedis()
        diagnostics.write_worker_heartbeat(fresh)
        with patch("redis.from_url", return_value=fresh), patch("builtins.print"):
            self.assertEqual(healthcheck.main(), 0)
        with patch("redis.from_url", return_value=HeartbeatRedis()), patch("builtins.print"):
            self.assertEqual(healthcheck.main(), 1)

    def test_the_entry_point_stays_tiny(self) -> None:
        # It runs every 30 s beside a parse that may be near its memory limit.
        probe = (
            "import sys, app.workers.healthcheck; "
            "print(','.join(m for m in ('sqlalchemy', 'fastapi', 'app.core.config', 'app.services') "
            "if m in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            env=child_environment(PACKAGE_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


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

    def get(self, _key: str) -> str | None:
        raise ConnectionError("redis is unreachable")


class HeartbeatRedis(FakeRedis):
    """Just enough Redis for the heartbeat: setex/get on a dict."""

    def __init__(self, values: dict[str, str] | None = None, *, ping_result: bool = True) -> None:
        super().__init__(ping_result)
        self.values = dict(values or {})

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

