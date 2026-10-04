"""Background warming of the replay response cache (replay_response_cache.py).

A cache miss on ``GET /demos/{id}/replay`` costs about a second (read and parse
the artifact, defaults, projection, serialization, gzip). Misses come from the
first open after a parse, a replay upgrade or a video write, and from every
open after an API restart. The warmer does that work before anyone asks, in
the same in-memory cache, so those opens are hits too. Nothing new is stored:
no artifact, no row, nothing for the deletion protocol to purge.

One daemon thread per API process (production runs one uvicorn process),
started by ``main.on_startup`` when ``REPLAY_WARM_ENABLED`` is on and the cache
has a budget. The CLI and the worker never start one. It takes demo ids from a
small de-duplicating queue (``MAX_PENDING``, oldest dropped beyond it) and warms
them one at a time, each with its own short database session, through
``warm_replay_response`` -- the route's own miss path, so the stored body and
its ETag are exactly what a request would have built.

Where the ids come from:

* API startup seeds the ``REPLAY_WARM_RECENT`` most recently completed demos,
  oldest first, so the newest ends up most recently used.
* The worker announces a finished parse and a replay-upgrade swap after their
  commit on the Redis channel ``REPLAY_READY_CHANNEL``; the warmer's thread
  subscribes to it. Redis being down only delays this: one warning, then
  retries with a backoff capped at ``BACKOFF_CAP_SECONDS``.
* Every video write (``ReplayBlob.finish_replay_update``, after its commit)
  announces too: in the API process straight into the queue, in the worker on
  the channel.

``announce_replay_ready`` is that single entry point. It is a no-op in a
process that has neither a running warmer nor an installed publisher (tests,
the CLI), and it never raises.

Deletion: a demo deleted in this process is refused by the cache's
recently-deleted guard (``ReplayResponseCache.refuses``), checked before the
work, again before the projection, and by ``put`` itself, so a warm racing a
delete never stores it. Every error is logged as one line (demo id and
exception class only, no paths or messages) and the thread carries on.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeGuard

from app.core import database as core_database
from app.core import redis as core_redis
from app.core.config import settings
from app.models.demo import Demo
from app.services.demo_service.replay_response_cache import (
    ReplayResponseCache,
    is_cacheable_replay_key,
    replay_response_cache,
    warm_replay_response,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_PENDING = 32
POLL_SECONDS = 0.5
MAX_MESSAGES_PER_POLL = 64
BACKOFF_CAP_SECONDS = 60.0
# Demo.id is String(36): uuid4 in production, short slugs in tests.
_DEMO_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,35}")


def is_valid_demo_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _DEMO_ID_PATTERN.fullmatch(value) is not None


class ReplayWarmer:
    """One thread, one bounded queue of demo ids, the Redis subscription in the same loop."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        cache: ReplayResponseCache | None = None,
        redis_factory: Callable[[], Any] | None = None,
        channel: str | None = None,
        max_pending: int = MAX_PENDING,
        poll_seconds: float = POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._cache = replay_response_cache if cache is None else cache
        self._redis_factory = redis_factory
        self._channel = settings.replay_ready_channel if channel is None else channel
        self._max_pending = max(1, max_pending)
        self._poll_seconds = max(0.01, poll_seconds)
        self._clock = clock
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._condition = threading.Condition()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._pubsub: Any | None = None
        self._retry_at = 0.0
        self._backoff = 0.0
        self._redis_warned = False

    # -- queue ------------------------------------------------------------------

    def enqueue(self, demo_id: str) -> bool:
        """Queue one demo; False for a malformed id or one already waiting."""
        if not is_valid_demo_id(demo_id):
            return False
        with self._condition:
            if demo_id in self._pending:
                return False
            self._pending[demo_id] = None
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
            self._condition.notify()
        return True

    def pending(self) -> list[str]:
        with self._condition:
            return list(self._pending)

    def seed_recent(self, limit: int) -> list[str]:
        """Queue the ``limit`` most recently completed cacheable demos, oldest of them first.

        Real, unarchived matches only: a mock demo (no source artifact) answers in
        milliseconds cold, and spending a seed slot on it left real matches cold.
        """
        if limit <= 0:
            return []
        with self._sessions()() as db:
            rows = (
                db.query(Demo.id)
                .filter(
                    Demo.status == "completed",
                    Demo.replay_storage_key.like("artifact://%"),
                    Demo.source_storage_key.isnot(None),
                    Demo.archived.is_(False),
                    # Postgres sorts NULLs first in DESC; a completed demo always has one.
                    Demo.completed_at.isnot(None),
                )
                .order_by(Demo.completed_at.desc(), Demo.id.desc())
                .limit(limit)
                .all()
            )
        seeded = [str(row[0]) for row in reversed(rows)]
        for demo_id in seeded:
            self.enqueue(demo_id)
        return seeded

    def process_pending(self) -> list[str]:
        """Warm everything queued right now, in order, on the calling thread."""
        outcomes: list[str] = []
        while (demo_id := self._next(0)) is not None:
            outcomes.append(self._process(demo_id))
        return outcomes

    def _next(self, timeout: float) -> str | None:
        with self._condition:
            if not self._pending and timeout > 0 and not self._stopping.is_set():
                self._condition.wait(timeout)
            if not self._pending:
                return None
            demo_id, _ = self._pending.popitem(last=False)
            return demo_id

    # -- warming ----------------------------------------------------------------

    def _process(self, demo_id: str) -> str:
        started = time.perf_counter()
        try:
            outcome = self._warm_one(demo_id)
        except Exception as exc:
            logger.warning("replay warm of demo %s failed: %s", demo_id, type(exc).__name__)
            return "failed"
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if outcome == "warmed":
            logger.info("replay response warmed for demo %s in %d ms", demo_id, elapsed_ms)
        else:
            logger.debug("replay warm of demo %s skipped: %s", demo_id, outcome)
        return outcome

    def _warm_one(self, demo_id: str) -> str:
        if not self._cache.enabled:
            return "disabled"
        if self._cache.refuses(demo_id):
            return "refused"
        # Imported here: the package __init__ imports this module (through
        # replay_blob, parse_lifecycle and replay_upgrade).
        from app.services.demo_service import DemoService

        with self._sessions()() as db:
            demo = db.query(Demo).filter(Demo.id == demo_id).one_or_none()
            if demo is None:
                return "missing"
            if demo.status != "completed":
                return "not_completed"
            if not is_cacheable_replay_key(demo.replay_storage_key):
                return "uncacheable"
            return warm_replay_response(DemoService.for_internal(db), demo, cache=self._cache)

    def _sessions(self) -> Callable[[], Session]:
        # Resolved per call so tests (and a reconfigured engine) take effect.
        return self._session_factory or core_database.SessionLocal

    # -- Redis notifications ----------------------------------------------------

    def _poll_notifications(self) -> None:
        if not self._channel:
            return
        if self._pubsub is None and not self._subscribe():
            return
        pubsub: Any = self._pubsub
        try:
            for _ in range(MAX_MESSAGES_PER_POLL):
                message = pubsub.get_message(timeout=0)
                if message is None:
                    break
                self._handle_message(message)
        except Exception as exc:
            self._close_pubsub()
            self._redis_down(exc)

    def _subscribe(self) -> bool:
        if self._clock() < self._retry_at:
            return False
        pubsub: Any | None = None
        try:
            pubsub = (self._redis_factory or core_redis.get_redis_client)().pubsub(
                ignore_subscribe_messages=True
            )
            pubsub.subscribe(self._channel)
        except Exception as exc:
            if pubsub is not None:
                _close_quietly(pubsub)
            self._redis_down(exc)
            return False
        self._pubsub = pubsub
        self._backoff = 0.0
        if self._redis_warned:
            self._redis_warned = False
            logger.info("replay-ready notifications subscribed again")
        return True

    def _handle_message(self, message: Any) -> bool:
        if not isinstance(message, dict) or message.get("type") != "message":
            return False
        data = message.get("data")
        if isinstance(data, bytes):
            try:
                data = data.decode("ascii")
            except UnicodeDecodeError:
                return False
        return isinstance(data, str) and self.enqueue(data)

    def _redis_down(self, exc: BaseException) -> None:
        self._backoff = min(BACKOFF_CAP_SECONDS, max(1.0, self._backoff * 2))
        self._retry_at = self._clock() + self._backoff
        if not self._redis_warned:
            self._redis_warned = True
            logger.warning(
                "replay-ready notifications unavailable (%s); retrying with backoff", type(exc).__name__
            )
        else:
            logger.debug("replay-ready notifications still unavailable (%s)", type(exc).__name__)

    def _close_pubsub(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            _close_quietly(pubsub)

    # -- thread -----------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="replay-warmer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stopping.set()
        with self._condition:
            self._condition.notify_all()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._close_pubsub()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self._poll_notifications()
                demo_id = self._next(self._idle_wait())
            except Exception as exc:
                logger.warning("replay warmer loop error: %s", type(exc).__name__)
                self._stopping.wait(1.0)
                continue
            if demo_id is not None:
                self._process(demo_id)

    def _idle_wait(self) -> float:
        if self._channel and self._pubsub is None:
            # Waiting out the backoff; an enqueue still wakes the thread at once.
            return min(BACKOFF_CAP_SECONDS, max(self._poll_seconds, self._retry_at - self._clock()))
        if not self._channel:
            return BACKOFF_CAP_SECONDS
        return self._poll_seconds


def _close_quietly(pubsub: Any) -> None:
    try:
        pubsub.close()
    except Exception:
        pass


# -- process-wide wiring ----------------------------------------------------------


@dataclass(frozen=True)
class _Publisher:
    client: Any
    channel: str


_state_lock = threading.Lock()
_running: ReplayWarmer | None = None
_publisher: _Publisher | None = None
_publish_failing = False


def start_replay_warmer(
    *,
    session_factory: Callable[[], Session] | None = None,
    cache: ReplayResponseCache | None = None,
    redis_factory: Callable[[], Any] | None = None,
) -> ReplayWarmer | None:
    """Start this process's warmer and seed it; None when warming is off or the cache has no budget."""
    global _running
    with _state_lock:
        if _running is not None:
            return _running
        cache = replay_response_cache if cache is None else cache
        if not settings.replay_warm_enabled or not cache.enabled:
            return None
        warmer = ReplayWarmer(session_factory=session_factory, cache=cache, redis_factory=redis_factory)
        try:
            warmer.seed_recent(settings.replay_warm_recent)
        except Exception as exc:
            logger.warning("replay warm seeding was unavailable: %s", type(exc).__name__)
        warmer.start()
        _running = warmer
        return warmer


def stop_replay_warmer() -> None:
    """Stop this process's warmer, if any; never raises."""
    global _running
    with _state_lock:
        warmer, _running = _running, None
    if warmer is None:
        return
    try:
        warmer.stop()
    except Exception as exc:
        logger.warning("replay warmer did not stop cleanly: %s", type(exc).__name__)


def running_replay_warmer() -> ReplayWarmer | None:
    return _running


def install_replay_ready_publisher(client: Any) -> bool:
    """Make this process (the worker) announce ready replays on the Redis channel.

    Decided once, here: nothing is published while warming is off or no channel is set.
    """
    global _publisher, _publish_failing
    channel = settings.replay_ready_channel
    with _state_lock:
        if not settings.replay_warm_enabled or not channel:
            _publisher = None
            return False
        _publisher = _Publisher(client=client, channel=channel)
        _publish_failing = False
        return True


def uninstall_replay_ready_publisher() -> None:
    global _publisher
    with _state_lock:
        _publisher = None


def announce_replay_ready(demo_id: str | None) -> None:
    """A demo's replay changed and is committed: warm it here, or tell the API. Best effort, never raises."""
    global _publish_failing
    if not is_valid_demo_id(demo_id):
        return
    warmer, publisher = _running, _publisher
    try:
        if warmer is not None:
            warmer.enqueue(demo_id)
            return
        if publisher is None:
            return
        publisher.client.publish(publisher.channel, demo_id)
    except Exception as exc:
        if not _publish_failing:
            _publish_failing = True
            logger.warning("replay-ready notification could not be sent: %s", type(exc).__name__)
        return
    if publisher is not None and _publish_failing:
        _publish_failing = False
        logger.info("replay-ready notifications are being sent again")
