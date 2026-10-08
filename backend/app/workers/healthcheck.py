"""Compose healthcheck for the parse worker: exit 0 while its heartbeat is fresh.

Usage (inside the worker container): python -m app.workers.healthcheck

The worker writes a Redis heartbeat at least every PARSE_LEASE_RENEW_SECONDS,
during a long parse as well as when idle (worker.run_worker's tick). This reads
it the way GET /health/worker and /diagnostics do: alive when the last beat is
at most WORKER_HEARTBEAT_ALIVE_SECONDS old. Exit 1 when it is missing, stale or
unreadable, or Redis cannot be reached.

Deliberately tiny: stdlib plus the redis client, REDIS_URL straight from the
environment. It runs every few seconds next to a parse that may be close to its
memory limit, so it must not import the database layer or the services
(app.services.diagnostics does). The constants below mirror diagnostics.py, and
a unit test keeps them equal.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any, Protocol

WORKER_HEARTBEAT_KEY = "cs2-demo-coach:worker:heartbeat"
WORKER_HEARTBEAT_ALIVE_SECONDS = 90
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_TIMEOUT_SECONDS = 3


class HeartbeatStore(Protocol):
    def get(self, name: str) -> Any: ...


def heartbeat_age_seconds(payload: object, now: datetime) -> int | None:
    """Seconds since the heartbeat in `payload`, or None when it is unreadable."""
    if payload is None:
        return None
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    try:
        body = json.loads(str(payload))
        last_seen = datetime.fromisoformat(str(body["lastSeenAt"]))
    except (ValueError, TypeError, KeyError):
        return None
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    return max(0, int((now - last_seen).total_seconds()))


def check(redis_client: HeartbeatStore, *, now: datetime | None = None) -> tuple[bool, str]:
    """(alive, one-line verdict) for the heartbeat behind `redis_client`."""
    current = now or datetime.now(UTC)
    try:
        payload = redis_client.get(WORKER_HEARTBEAT_KEY)
    except Exception as exc:
        # The type only: a connection error's text can carry the Redis address.
        return False, f"worker heartbeat unreachable ({type(exc).__name__})"
    if payload is None:
        return False, "worker heartbeat missing"
    age = heartbeat_age_seconds(payload, current)
    if age is None:
        return False, "worker heartbeat unreadable"
    if age > WORKER_HEARTBEAT_ALIVE_SECONDS:
        return False, f"worker heartbeat stale ({age}s old)"
    return True, f"worker heartbeat ok ({age}s old)"


def main() -> int:
    import redis

    client = redis.from_url(
        os.environ.get("REDIS_URL", DEFAULT_REDIS_URL),
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )
    try:
        alive, verdict = check(client)
    finally:
        try:
            client.close()
        except Exception:
            pass
    print(verdict, flush=True)
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
