"""Background warming of the replay response cache (services/demo_service/replay_warmer.py).

A warmed demo's first GET is a cache hit with the bytes and ETag the cold path
would have produced; demos that must not be cached are skipped; a deletion
racing a warm stores nothing; startup seeds the most recently completed demos;
the worker announces finished parses, upgrades and video writes after their
commit, best effort, on the Redis channel the warmer subscribes to.
"""

import copy
import io
import json
import logging
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_auth_owner_boundary import OWNER_A, FakeRedis, add_demo, bind_replay, owner_headers
from test_replay_response_cache import _contract, _raw_body
from test_replay_upgrade import OLD_VERSION, fk_engine, parsed_match

from app import main as main_module
from app.api import deletion, replay
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.json_compression import JsonGzipMiddleware
from app.models import Demo, DemoJob
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import REPLAY_CONTRACT_VERSION
from app.services.deletion_service import DeletionService
from app.services.demo_service import DemoService
from app.services.demo_service import replay_blob as replay_blob_module
from app.services.demo_service import replay_response_cache as cache_module
from app.services.demo_service import replay_warmer as warmer_module
from app.services.demo_service.replay_response_cache import replay_response_cache
from app.services.demo_service.replay_upgrade import VERSION_KEY
from app.services.demo_service.replay_warmer import (
    BACKOFF_CAP_SECONDS,
    ReplayWarmer,
    announce_replay_ready,
    install_replay_ready_publisher,
    running_replay_warmer,
    start_replay_warmer,
    stop_replay_warmer,
    uninstall_replay_ready_publisher,
)
from app.services.storage import LocalArtifactStore, LocalStorageService
from app.workers import replay_upgrade
from app.workers.replay_upgrade import upgrade_stale_replays
from app.workers.worker import process_real_parse_job

DEMO_ID = "demo-warm"
PATH = f"/demos/{DEMO_ID}/replay"
CHANNEL = "cs2:replay-ready"
SETTING_NAMES = (
    "auth_mode",
    "artifact_storage_root",
    "replay_storage_dir",
    "demo_upload_storage_dir",
    "video_storage_dir",
    "replay_response_cache_mb",
    "replay_warm_enabled",
    "replay_warm_recent",
    "replay_ready_channel",
)


class _SettingsMixin:
    def swap_settings(self, **changes: Any) -> None:
        if not hasattr(self, "_original_settings"):
            self._original_settings = {name: getattr(settings, name) for name in SETTING_NAMES}
        for name, value in changes.items():
            object.__setattr__(settings, name, value)

    def restore_settings(self) -> None:
        for name, value in getattr(self, "_original_settings", {}).items():
            object.__setattr__(settings, name, value)


def _unavailable_redis() -> Any:
    raise ConnectionError("redis is down")


class FakePubSub:
    def __init__(self, messages: list[Any] | None = None) -> None:
        self.messages = list(messages or [])
        self.subscribed: list[str] = []
        self.closed = False
        self.fail_reads = False

    def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    def get_message(self, timeout: float = 0.0) -> Any:
        if self.fail_reads:
            raise ConnectionError("connection reset")
        return self.messages.pop(0) if self.messages else None

    def close(self) -> None:
        self.closed = True


class FakePubSubRedis:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub
        self.pubsub_kwargs: list[dict[str, Any]] = []

    def pubsub(self, **kwargs: Any) -> FakePubSub:
        self.pubsub_kwargs.append(kwargs)
        return self._pubsub


def _message(data: Any, kind: str = "message") -> dict[str, Any]:
    return {"type": kind, "pattern": None, "channel": CHANNEL, "data": data}


class ReplayWarmerCacheTest(_SettingsMixin, unittest.TestCase):
    """The warmer against the real route: same bytes, same ETag, no request-time read."""

    def setUp(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.swap_settings(
            auth_mode="test",
            artifact_storage_root=root,
            replay_storage_dir=root / "replays",
            demo_upload_storage_dir=root / "uploads",
            video_storage_dir=root / "videos",
            replay_response_cache_mb=64,
            replay_warm_enabled=True,
            replay_warm_recent=5,
            replay_ready_channel=CHANNEL,
        )
        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=FakeRedis())
        self.redis_patch.start()
        replay_response_cache.clear()

        app = FastAPI()
        app.include_router(replay.router)
        app.include_router(deletion.router)
        app.add_middleware(JsonGzipMiddleware, minimum_size=1024, compresslevel=5)
        app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(app)

        with self.Session() as db:
            demo = add_demo(db, DEMO_ID, OWNER_A)
            # A real upload (a mock demo has no source artifact and is not seeded).
            demo.source_storage_key = f"artifact://v1/accepted/source/x/y/{DEMO_ID}"
            bind_replay(db, demo, _contract(DEMO_ID))

    def tearDown(self) -> None:
        stop_replay_warmer()
        uninstall_replay_ready_publisher()
        replay_response_cache.clear()
        self.redis_patch.stop()
        self.restore_settings()
        self.temp_dir.cleanup()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def warmer(self, **kwargs: Any) -> ReplayWarmer:
        return ReplayWarmer(session_factory=self.Session, redis_factory=_unavailable_redis, channel="", **kwargs)

    def count_blob_reads(self):
        return patch.object(
            replay_blob_module,
            "read_accepted_replay_json",
            wraps=replay_blob_module.read_accepted_replay_json,
        )

    def gzip_get(self, path: str = PATH) -> tuple[dict[str, str], bytes]:
        return _raw_body(self.client, path, {**owner_headers(OWNER_A), "Accept-Encoding": "gzip"})

    # -- warmed entries -------------------------------------------------------------

    def test_a_warmed_demo_is_a_hit_with_the_cold_body_and_etag(self) -> None:
        cold_headers, cold_body = self.gzip_get()
        replay_response_cache.clear()  # a fresh process

        warmer = self.warmer()
        self.assertTrue(warmer.enqueue(DEMO_ID))
        self.assertEqual(warmer.process_pending(), ["warmed"])

        with self.count_blob_reads() as reads:
            warm_headers, warm_body = self.gzip_get()
            plain = self.client.get(PATH, headers=owner_headers(OWNER_A))
        self.assertEqual(reads.call_count, 0)
        self.assertEqual(warm_headers["etag"], cold_headers["etag"])
        self.assertEqual(warm_headers["content-encoding"], "gzip")
        self.assertEqual(warm_body, cold_body)
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(plain.headers["etag"], cold_headers["etag"])
        self.assertEqual(plain.json()["demoId"], DEMO_ID)

    def test_an_entry_already_present_is_not_rebuilt(self) -> None:
        self.gzip_get()
        warmer = self.warmer()
        warmer.enqueue(DEMO_ID)

        with self.count_blob_reads() as reads, patch.object(
            cache_module, "_public_replay_contract", wraps=cache_module._public_replay_contract
        ) as project:
            self.assertEqual(warmer.process_pending(), ["present"])
        self.assertEqual(reads.call_count, 0)
        project.assert_not_called()

    # -- skipped ----------------------------------------------------------------------

    def test_missing_unfinished_and_legacy_demos_are_not_warmed(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-parsing", OWNER_A, status="parsing")
            legacy = add_demo(db, "demo-legacy", OWNER_A)
            self.assertTrue(legacy.replay_storage_key.startswith("local://"))
            DemoService(db, owner_id=OWNER_A).storage.write_json(legacy.replay_storage_key, _contract("demo-legacy"))
        warmer = self.warmer()
        for demo_id in ("demo-missing", "demo-parsing", "demo-legacy"):
            self.assertTrue(warmer.enqueue(demo_id))

        self.assertEqual(warmer.process_pending(), ["missing", "not_completed", "uncacheable"])
        self.assertEqual(replay_response_cache.stats()["entries"], 0)

    def test_a_disabled_cache_is_not_warmed(self) -> None:
        self.swap_settings(replay_response_cache_mb=0)
        warmer = self.warmer()
        warmer.enqueue(DEMO_ID)

        with self.count_blob_reads() as reads:
            self.assertEqual(warmer.process_pending(), ["disabled"])
        self.assertEqual(reads.call_count, 0)
        self.assertEqual(replay_response_cache.stats(), {"entries": 0, "bytes": 0, "videoMemos": 0})

    def test_a_demo_deleted_before_its_warm_is_skipped(self) -> None:
        warmer = self.warmer()
        warmer.enqueue(DEMO_ID)
        self.assertEqual(self.client.delete(f"/demos/{DEMO_ID}", headers=owner_headers(OWNER_A)).status_code, 204)

        self.assertEqual(warmer.process_pending(), ["refused"])
        self.assertFalse(replay_response_cache.holds_demo(DEMO_ID))

    def test_a_demo_deleted_while_warming_is_never_stored(self) -> None:
        original = cache_module._public_replay_contract

        def deleted_meanwhile(*args: Any, **kwargs: Any) -> Any:
            # What DeletionService.delete_demo does right after its commit.
            replay_response_cache.evict_demo(DEMO_ID)
            return original(*args, **kwargs)

        warmer = self.warmer()
        warmer.enqueue(DEMO_ID)
        with patch.object(cache_module, "_public_replay_contract", side_effect=deleted_meanwhile):
            self.assertEqual(warmer.process_pending(), ["refused"])
        self.assertFalse(replay_response_cache.holds_demo(DEMO_ID))
        self.assertEqual(replay_response_cache.stats(), {"entries": 0, "bytes": 0, "videoMemos": 0})

    def test_a_bad_item_never_stops_the_queue(self) -> None:
        calls = {"count": 0}

        def flaky_sessions() -> Any:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("database briefly unreachable at /secret/path")
            return self.Session()

        warmer = ReplayWarmer(session_factory=flaky_sessions, channel="")
        warmer.enqueue("demo-first")
        warmer.enqueue(DEMO_ID)

        with self.assertLogs(warmer_module.logger, level="WARNING") as logs:
            self.assertEqual(warmer.process_pending(), ["failed", "warmed"])
        self.assertEqual(len(logs.records), 1)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("/secret/path", logs.output[0])

    # -- API-side video writes ----------------------------------------------------------

    def test_a_video_write_in_the_api_process_warms_the_new_replay(self) -> None:
        cold = self.client.get(PATH, headers=owner_headers(OWNER_A))
        warmer = self.warmer()
        with patch.object(warmer_module, "_running", warmer):
            with self.Session() as db:
                demo = db.get(Demo, DEMO_ID)
                service = DemoService(db, owner_id=OWNER_A)
                service.update_video_calibration(demo, time_origin_seconds=12.5)
            self.assertEqual(warmer.pending(), [DEMO_ID])
            self.assertEqual(warmer.process_pending(), ["warmed"])

        with self.count_blob_reads() as reads:
            after = self.client.get(PATH, headers=owner_headers(OWNER_A))
        self.assertEqual(reads.call_count, 0)
        self.assertNotEqual(after.headers["etag"], cold.headers["etag"])
        self.assertEqual(after.json()["video"]["timeOriginSeconds"], 12.5)

    # -- the thread -------------------------------------------------------------------

    def test_the_thread_warms_queued_demos_and_survives_redis_being_down(self) -> None:
        warmer = ReplayWarmer(
            session_factory=self.Session, redis_factory=_unavailable_redis, channel=CHANNEL, poll_seconds=0.05
        )
        with self.assertLogs(warmer_module.logger, level="WARNING"):
            warmer.start()
            try:
                warmer.enqueue("demo-gone")
                warmer.enqueue(DEMO_ID)
                deadline = time.monotonic() + 10
                while not replay_response_cache.holds_demo(DEMO_ID) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(replay_response_cache.holds_demo(DEMO_ID))
                self.assertTrue(warmer.running)
            finally:
                warmer.stop()
        self.assertFalse(warmer.running)

    # -- startup ----------------------------------------------------------------------

    def add_completed(
        self, demo_id: str, completed_at: datetime | None, *, artifact: bool = True, mock: bool = False,
        archived: bool = False,
    ) -> None:
        with self.Session() as db:
            demo = add_demo(db, demo_id, OWNER_A)
            demo.completed_at = completed_at
            demo.source_storage_key = None if mock else f"artifact://v1/accepted/source/x/y/{demo_id}"
            demo.archived = archived
            db.commit()
            if artifact:
                bind_replay(db, demo, _contract(demo_id))

    def test_seeding_picks_the_most_recently_completed_demos_oldest_first(self) -> None:
        base = datetime(2026, 10, 1, tzinfo=UTC)
        with self.Session() as db:
            db.get(Demo, DEMO_ID).completed_at = base
            db.commit()
        for index in range(1, 5):
            self.add_completed(f"demo-done-{index}", base + timedelta(hours=index))
        self.add_completed("demo-legacy-newest", base + timedelta(days=3), artifact=False)
        self.add_completed("demo-no-completed-at", None)
        # Mock and archived demos never take a seed slot, however recent.
        self.add_completed("demo-mock-newest", base + timedelta(days=4), mock=True)
        self.add_completed("demo-archived-newest", base + timedelta(days=5), archived=True)
        with self.Session() as db:
            add_demo(db, "demo-parsing", OWNER_A, status="parsing")

        self.assertEqual(self.warmer().seed_recent(3), ["demo-done-2", "demo-done-3", "demo-done-4"])
        self.assertEqual(self.warmer().seed_recent(0), [])
        everything = self.warmer()
        self.assertEqual(
            everything.seed_recent(50),
            [DEMO_ID, "demo-done-1", "demo-done-2", "demo-done-3", "demo-done-4"],
        )
        self.assertEqual(everything.pending(), [DEMO_ID, "demo-done-1", "demo-done-2", "demo-done-3", "demo-done-4"])

    def test_startup_seeds_and_starts_one_warmer_and_shutdown_stops_it(self) -> None:
        with self.Session() as db:
            db.get(Demo, DEMO_ID).completed_at = datetime(2026, 10, 1, tzinfo=UTC)
            db.commit()
        self.swap_settings(replay_warm_recent=1)

        with patch.object(ReplayWarmer, "start") as start:
            warmer = start_replay_warmer(session_factory=self.Session, redis_factory=_unavailable_redis)
            assert warmer is not None
            self.assertEqual(warmer.pending(), [DEMO_ID])
            start.assert_called_once_with()
            self.assertIs(start_replay_warmer(session_factory=self.Session), warmer)  # one per process
            self.assertIs(running_replay_warmer(), warmer)
        stop_replay_warmer()
        self.assertIsNone(running_replay_warmer())

        self.swap_settings(replay_warm_recent=0)
        with patch.object(ReplayWarmer, "start"):
            warmer = start_replay_warmer(session_factory=self.Session)
            assert warmer is not None
            self.assertEqual(warmer.pending(), [])

    def test_a_disabled_flag_or_cache_starts_nothing(self) -> None:
        for changes in ({"replay_warm_enabled": False}, {"replay_response_cache_mb": 0}):
            with self.subTest(changes=changes), patch.object(ReplayWarmer, "start") as start:
                self.swap_settings(**{"replay_warm_enabled": True, "replay_response_cache_mb": 64, **changes})
                self.assertIsNone(start_replay_warmer(session_factory=self.Session))
                start.assert_not_called()
                self.assertIsNone(running_replay_warmer())
                announce_replay_ready(DEMO_ID)  # nothing running, nothing installed: a no-op

    def test_a_real_start_runs_the_thread_until_stopped(self) -> None:
        self.swap_settings(replay_warm_recent=0, replay_ready_channel="")
        warmer = start_replay_warmer(session_factory=self.Session)
        assert warmer is not None
        self.assertTrue(warmer.running)
        stop_replay_warmer()
        self.assertFalse(warmer.running)


class ReplayWarmerQueueTest(unittest.TestCase):
    """The queue and the Redis subscription, without a thread or a database."""

    def warmer(self, pubsub: FakePubSub | None = None, **kwargs: Any) -> ReplayWarmer:
        factory = (lambda: FakePubSubRedis(pubsub)) if pubsub is not None else _unavailable_redis
        return ReplayWarmer(session_factory=MagicMock(), redis_factory=factory, channel=CHANNEL, **kwargs)

    def test_duplicates_are_dropped_and_the_queue_is_bounded(self) -> None:
        warmer = self.warmer(max_pending=3)
        self.assertTrue(warmer.enqueue("demo-1"))
        self.assertFalse(warmer.enqueue("demo-1"))
        for index in range(2, 6):
            self.assertTrue(warmer.enqueue(f"demo-{index}"))
        self.assertEqual(warmer.pending(), ["demo-3", "demo-4", "demo-5"])

    def test_malformed_ids_are_refused(self) -> None:
        warmer = self.warmer()
        for value in ("", " demo-1", "../replays/x", "demo/1", "a" * 37, "demo\n1", "dé", None, 42):
            with self.subTest(value=value):
                self.assertFalse(warmer.enqueue(value))  # type: ignore[arg-type]
        self.assertTrue(warmer.enqueue("4e0078b6-2f33-4918-bb8d-0791237d72d6"))
        self.assertEqual(warmer.pending(), ["4e0078b6-2f33-4918-bb8d-0791237d72d6"])

    def test_a_notification_enqueues_and_junk_is_ignored(self) -> None:
        pubsub = FakePubSub([
            _message("demo-a"),
            _message("demo-a"),
            _message(b"demo-b"),
            _message("demo-c", kind="subscribe"),
            _message(b"\xff\xfe"),
            _message("x" * 200),
            _message("demo-1; rm -rf /"),
            _message(7),
            _message(None),
            "not a dict",
        ])
        redis_client = FakePubSubRedis(pubsub)
        warmer = ReplayWarmer(session_factory=MagicMock(), redis_factory=lambda: redis_client, channel=CHANNEL)

        warmer._poll_notifications()

        self.assertEqual(pubsub.subscribed, [CHANNEL])
        self.assertEqual(redis_client.pubsub_kwargs, [{"ignore_subscribe_messages": True}])
        self.assertEqual(warmer.pending(), ["demo-a", "demo-b"])

    def test_redis_down_warns_once_and_backs_off(self) -> None:
        attempts = {"count": 0}
        pubsub = FakePubSub([_message("demo-late")])

        def factory() -> Any:
            attempts["count"] += 1
            if attempts["count"] <= 7:
                raise ConnectionError("redis://user:secret@redis:6379 refused")
            return FakePubSubRedis(pubsub)

        clock = {"now": 1000.0}
        warmer = ReplayWarmer(
            session_factory=MagicMock(), redis_factory=factory, channel=CHANNEL, clock=lambda: clock["now"]
        )
        with self.assertLogs(warmer_module.logger, level="DEBUG") as logs:
            warmer._poll_notifications()
            self.assertEqual(attempts["count"], 1)
            warmer._poll_notifications()  # still backing off: no new attempt
            self.assertEqual(attempts["count"], 1)
            backoffs = []
            for _ in range(6):
                clock["now"] = warmer._retry_at
                warmer._poll_notifications()
                backoffs.append(warmer._backoff)
            self.assertEqual(attempts["count"], 7)
            self.assertEqual(backoffs, [2.0, 4.0, 8.0, 16.0, 32.0, BACKOFF_CAP_SECONDS])
            clock["now"] = warmer._retry_at
            warmer._poll_notifications()  # Redis is back

        warnings = [record for record in logs.records if record.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertIn("ConnectionError", warnings[0].getMessage())
        self.assertNotIn("secret", "\n".join(logs.output))
        self.assertTrue(any("subscribed again" in record.getMessage() for record in logs.records))
        self.assertEqual(warmer.pending(), ["demo-late"])
        self.assertEqual(warmer._backoff, 0.0)

    def test_a_broken_subscription_is_closed_and_retried(self) -> None:
        pubsub = FakePubSub()
        warmer = self.warmer(pubsub)
        warmer._poll_notifications()
        self.assertIs(warmer._pubsub, pubsub)

        pubsub.fail_reads = True
        with self.assertLogs(warmer_module.logger, level="WARNING"):
            warmer._poll_notifications()
        self.assertTrue(pubsub.closed)
        self.assertIsNone(warmer._pubsub)
        self.assertGreater(warmer._retry_at, 0)

    def test_no_channel_never_touches_redis(self) -> None:
        factory = MagicMock(side_effect=AssertionError("Redis must not be used"))
        warmer = ReplayWarmer(session_factory=MagicMock(), redis_factory=factory, channel="")
        warmer._poll_notifications()
        factory.assert_not_called()


class ReplayReadyNotificationTest(_SettingsMixin, unittest.TestCase):
    """The worker side: finished parses, upgrades and video writes are announced after their commit."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = fk_engine(root / "notify.db")
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        self.legacy = LocalStorageService(root / "legacy")
        self.swap_settings(replay_warm_enabled=True, replay_ready_channel=CHANNEL)

        def internal(db: Any) -> DemoService:
            return DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)

        self.patches = [
            patch("app.workers.worker.DemoService.for_internal", side_effect=internal),
            patch("app.workers.replay_upgrade.DemoService.for_internal", side_effect=internal),
            patch("app.services.demo_service.get_redis_client"),
        ]
        for item in self.patches:
            item.start()
        replay_upgrade.reset_upgrade_state()
        self.redis = MagicMock()
        self.assertTrue(install_replay_ready_publisher(self.redis))
        # Each test reaches a fresh "healthy" state for the transition-based warning.
        warmer_module._publish_failing = False

    def tearDown(self) -> None:
        uninstall_replay_ready_publisher()
        warmer_module._publish_failing = False
        stop_replay_warmer()
        replay_upgrade.reset_upgrade_state()
        for item in reversed(self.patches):
            item.stop()
        self.restore_settings()
        self.engine.dispose()
        self.scratch.cleanup()

    # -- helpers ----------------------------------------------------------------------

    def internal(self, db: Any) -> DemoService:
        return DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)

    def upload(self) -> str:
        with self.Session() as db:
            created = DemoService(
                db, owner_id=settings.dev_user_id, storage=self.legacy, artifact_store=self.store
            ).create_real_demo(SimpleNamespace(
                filename="spirit-vs-mouz.dem",
                content_type="application/octet-stream",
                file=io.BytesIO(b"HL2DEMO\x00" + b"warm-payload" * 4),
            ))
            return created.id

    def parse(self, demo_id: str) -> None:
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
            with patch("app.workers.worker.run_parse_subprocess", return_value=parsed_match()):
                process_real_parse_job(db, demo, job)

    def committed_state(self, demo_id: str) -> tuple[str | None, str | None]:
        """Read from a separate session: only committed rows are visible here."""
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            return (demo.status, demo.replay_storage_key) if demo is not None else (None, None)

    def make_old(self, demo_id: str) -> str:
        with self.Session() as db:
            service = self.internal(db)
            demo = db.get(Demo, demo_id)
            replay = copy.deepcopy(service.load_replay_blob(demo))
            assert replay is not None
            replay["contractVersion"] = OLD_VERSION
            previous = demo.replay_storage_key
            demo.replay_storage_key = service.write_replay_blob(demo_id, replay)
            job = service.latest_parse_job(demo)
            assert job is not None
            metadata = json.loads(job.metadata_json)
            metadata.pop(VERSION_KEY, None)
            job.metadata_json = json.dumps(metadata, separators=(",", ":"))
            db.commit()
            service.delete_artifact_safely(previous)
            return str(demo.replay_storage_key)

    def run_upgrade(self) -> str | None:
        return upgrade_stale_replays(
            run_parse=lambda _path, on_tick=None: parsed_match(),
            session_factory=self.Session,
            force=True,
            check_interval_seconds=0,
        )

    # -- parse completion -------------------------------------------------------------

    def test_a_finished_parse_is_published_after_its_commit(self) -> None:
        demo_id = self.upload()
        seen: list[tuple[str | None, str | None]] = []
        self.redis.publish.side_effect = lambda channel, payload: seen.append(self.committed_state(payload))

        self.parse(demo_id)

        self.redis.publish.assert_called_once_with(CHANNEL, demo_id)
        status, key = seen[0]
        self.assertEqual(status, "completed")
        self.assertTrue(key and key.startswith("artifact://"))

    def test_a_failed_publish_does_not_fail_the_parse(self) -> None:
        demo_id = self.upload()
        self.redis.publish.side_effect = ConnectionError("redis://user:secret@redis:6379 refused")

        with self.assertLogs(warmer_module.logger, level="WARNING") as logs:
            self.parse(demo_id)
            announce_replay_ready(demo_id)  # still failing: no second warning

        self.assertEqual(self.committed_state(demo_id)[0], "completed")
        self.assertEqual(len(logs.records), 1)
        self.assertNotIn("secret", logs.output[0])

    def test_no_publish_when_the_parse_commit_fails(self) -> None:
        demo_id = self.upload()
        replay = normalize_parser_output(demo_id, parsed_match())
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
            with patch.object(db, "commit", side_effect=RuntimeError("database went away")):
                with self.assertRaises(RuntimeError):
                    self.internal(db).complete_parse_job(demo, job, replay, [])
        self.redis.publish.assert_not_called()
        self.assertNotEqual(self.committed_state(demo_id)[0], "completed")

    def test_no_publish_for_a_demo_deleted_before_its_parse_completed(self) -> None:
        demo_id = self.upload()
        replay = normalize_parser_output(demo_id, parsed_match())
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
            with self.Session() as other:
                self.assertTrue(DeletionService(other, artifact_store=self.store).delete_demo(
                    settings.dev_user_id, demo_id,
                ))
            self.assertFalse(self.internal(db).complete_parse_job(demo, job, replay, []))
        self.redis.publish.assert_not_called()

    # -- replay upgrade -----------------------------------------------------------------

    def test_an_upgrade_swap_is_published_after_its_commit(self) -> None:
        demo_id = self.upload()
        self.parse(demo_id)
        old_key = self.make_old(demo_id)
        self.redis.publish.reset_mock()
        seen: list[tuple[str | None, str | None]] = []
        self.redis.publish.side_effect = lambda channel, payload: seen.append(self.committed_state(payload))

        self.assertEqual(self.run_upgrade(), "upgraded")

        self.redis.publish.assert_called_once_with(CHANNEL, demo_id)
        status, key = seen[0]
        self.assertEqual(status, "completed")
        self.assertNotEqual(key, old_key)
        with self.Session() as db:
            replay = self.internal(db).load_replay_blob(db.get(Demo, demo_id))
            assert replay is not None
            self.assertEqual(replay["contractVersion"], REPLAY_CONTRACT_VERSION)

    def test_a_failed_publish_does_not_fail_the_upgrade(self) -> None:
        demo_id = self.upload()
        self.parse(demo_id)
        old_key = self.make_old(demo_id)
        self.redis.publish.side_effect = ConnectionError("down")

        with self.assertLogs(warmer_module.logger, level="WARNING"):
            self.assertEqual(self.run_upgrade(), "upgraded")
        self.assertNotEqual(self.committed_state(demo_id)[1], old_key)

    def test_an_upgrade_that_lost_its_race_publishes_nothing(self) -> None:
        demo_id = self.upload()
        self.parse(demo_id)
        self.make_old(demo_id)
        self.redis.publish.reset_mock()
        with self.Session() as db:
            service = self.internal(db)
            claim = service.claim_replay_upgrade(demo_id)
            assert claim is not None
            outcome = service.complete_replay_upgrade(
                claim,
                expected_replay_key="artifact://someone-else-moved-it",
                replay=normalize_parser_output(demo_id, parsed_match()),
            )
        self.assertEqual(outcome, "changed")
        self.redis.publish.assert_not_called()

    # -- video writes and process wiring ------------------------------------------------

    def test_a_worker_side_video_write_is_published(self) -> None:
        demo_id = self.upload()
        self.parse(demo_id)
        self.redis.publish.reset_mock()
        with self.Session() as db:
            service = self.internal(db)
            demo = db.get(Demo, demo_id)
            current = service.get_video_status(demo)
            service.update_replay_video(demo, {**current, "status": "failed", "source": "rendered", "url": None})
        self.redis.publish.assert_called_once_with(CHANNEL, demo_id)

    def test_a_running_warmer_takes_the_announcement_instead_of_redis(self) -> None:
        warmer = ReplayWarmer(session_factory=self.Session, channel="")
        with patch.object(warmer_module, "_running", warmer):
            announce_replay_ready("demo-local")
            announce_replay_ready("../not-an-id")
        self.assertEqual(warmer.pending(), ["demo-local"])
        self.redis.publish.assert_not_called()

    def test_warming_off_installs_no_publisher(self) -> None:
        uninstall_replay_ready_publisher()
        self.swap_settings(replay_warm_enabled=False)
        self.assertFalse(install_replay_ready_publisher(self.redis))
        announce_replay_ready("demo-x")
        self.redis.publish.assert_not_called()

    def test_api_startup_starts_the_warmer_and_never_fails_on_it(self) -> None:
        startup_settings = SimpleNamespace(
            validate_runtime_configuration=lambda: None,
            max_demo_upload_bytes=settings.max_demo_upload_bytes,
            upload_chunk_bytes=settings.upload_chunk_bytes,
            artifact_quarantine_ttl_seconds=settings.artifact_quarantine_ttl_seconds,
            artifact_storage_backend="s3",
        )
        quiet = [
            patch.object(main_module, "settings", startup_settings),
            patch.object(main_module, "init_db"),
            patch.object(main_module, "artifact_store_from_settings"),
            patch.object(main_module, "ArtifactIntakeService"),
            patch.object(main_module, "SessionLocal"),
            patch.object(main_module, "DeletionService"),
            patch.object(main_module, "prune_upload_ledger"),
        ]
        for item in quiet:
            item.start()
        try:
            with patch.object(main_module, "start_replay_warmer") as start:
                main_module.on_startup()
            start.assert_called_once_with()
            with patch.object(main_module, "start_replay_warmer", side_effect=RuntimeError("boom")), self.assertLogs(
                main_module.logger, level="WARNING"
            ):
                main_module.on_startup()
        finally:
            for item in reversed(quiet):
                item.stop()

    def test_shutdown_stops_the_warmer(self) -> None:
        with patch.object(main_module, "on_startup"), patch.object(main_module, "stop_replay_warmer") as stop:
            with TestClient(main_module.app):
                stop.assert_not_called()
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
