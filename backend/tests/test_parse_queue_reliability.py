"""The parse queue's recovery behaviour, across all three layers.

A worker that dies holding a job used to leave a dead end no user could get out
of: the message was gone from Redis, the row sat on "processing" forever, and
the retry endpoint refused because a parse was "already active". These tests
cover the three ways that is now unwound -- the reaper for a dead consumer,
database reconciliation for when Redis itself went away, and the attempt ceiling
for a demo that kills the worker every single time.
"""

import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fixtures.demo_jobs import add_demo_with_job, bind_source_artifact
from fixtures.fake_redis import FakeRedis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, settings
from app.core.database import Base
from app.parser.demo_parser import DemoParserError
from app.services.demo_service import (
    PARSE_ABANDONED_ERROR_CODE,
    DemoService,
)
from app.services.diagnostics import read_worker_heartbeat, write_worker_heartbeat
from app.workers.parse_child import EXIT_PARSE_ERROR
from app.workers.queue import CONSUMERS_KEY, ParseQueue, lease_key, processing_key
from app.workers.worker import _parse_child_result, run_parse_subprocess

QUEUE = settings.redis_queue_name


def utc_now() -> datetime:
    return datetime.now(UTC)


class ParseQueueRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.redis = FakeRedis()

    def tearDown(self) -> None:
        self.engine.dispose()

    def reap_with(self, queue: ParseQueue, db) -> int:
        service = DemoService.for_internal(db)
        return queue.reap(
            on_orphan=lambda message: service.recover_parse_job(
                str(message["job_id"]),
                str(message["demo_id"]),
            )
        )

    def test_a_message_held_by_a_dead_worker_is_returned_to_the_queue(self) -> None:
        with self.Session() as db:
            demo, job = add_demo_with_job(db, "demo-reaped", "real_parse")
            service = DemoService.for_internal(db)
            service.dispatch_parse_job(job_id=job.id, demo_id=demo.id, redis_client=self.redis)

            dead = ParseQueue(
                self.redis,
                queue_name=QUEUE,
                consumer_id="dead-worker",
                lease_ttl_seconds=60,
            )
            dead.register()
            payload = dead.reserve(timeout=1)
            self.assertTrue(service.claim_parse_job(demo, job))
            # SIGKILL: the message is never released and the lease is never
            # renewed again, so it simply lapses.
            self.redis.delete(lease_key("dead-worker"))

            live = ParseQueue(
                self.redis,
                queue_name=QUEUE,
                consumer_id="live-worker",
                lease_ttl_seconds=60,
            )
            live.register()
            recovered = self.reap_with(live, db)

            db.refresh(job)
            db.refresh(demo)

            self.assertEqual(recovered, 1)
            self.assertEqual(self.redis.lrange(QUEUE, 0, -1), [payload])
            self.assertEqual(self.redis.llen(processing_key(QUEUE, "dead-worker")), 0)
            # Returning the message is only half of it. claim_parse_job accepts a
            # job from "queued" and nowhere else, so a row left on "processing"
            # would swallow the redelivery and lose the task a second time.
            self.assertEqual(job.status, "queued")
            self.assertEqual(demo.status, "queued")
            self.assertIsNone(job.started_at)

    def test_reaping_leaves_a_live_worker_alone(self) -> None:
        with self.Session() as db:
            demo, job = add_demo_with_job(db, "demo-busy", "real_parse")
            service = DemoService.for_internal(db)
            service.dispatch_parse_job(job_id=job.id, demo_id=demo.id, redis_client=self.redis)

            busy = ParseQueue(
                self.redis,
                queue_name=QUEUE,
                consumer_id="busy-worker",
                lease_ttl_seconds=60,
            )
            busy.register()
            busy.reserve(timeout=1)
            service.claim_parse_job(demo, job)

            other = ParseQueue(
                self.redis,
                queue_name=QUEUE,
                consumer_id="other-worker",
                lease_ttl_seconds=60,
            )
            other.register()
            recovered = self.reap_with(other, db)

            db.refresh(job)

            self.assertEqual(recovered, 0)
            self.assertEqual(self.redis.llen(processing_key(QUEUE, "busy-worker")), 1)
            self.assertEqual(self.redis.llen(QUEUE), 0)
            self.assertEqual(job.status, "processing")

    def test_reconciliation_recovers_a_row_that_redis_no_longer_remembers(self) -> None:
        # Redis runs without persistence here: a restart drops every lease, every
        # processing list and every queued message at once, leaving the reaper
        # with nothing to work from. Wall-clock age against the row is what is
        # left, and it has to be enough.
        with self.Session() as db:
            demo, job = add_demo_with_job(db, "demo-forgotten", "real_parse")
            service = DemoService.for_internal(db)
            service.claim_parse_job(demo, job)
            job.started_at = utc_now() - timedelta(hours=2)
            db.commit()

            reclaimed = service.reclaim_stale_parse_jobs(
                older_than_seconds=1800,
                redispatch_after_seconds=300,
                redis_client=self.redis,
            )

            db.refresh(job)
            db.refresh(demo)

            self.assertEqual(reclaimed, [job.id])
            self.assertEqual(job.status, "queued")
            self.assertEqual(demo.status, "queued")
            self.assertEqual(
                [json.loads(message) for message in self.redis.lrange(QUEUE, 0, -1)],
                [{"job_id": job.id, "demo_id": demo.id}],
            )

    def test_a_live_parse_is_not_reclaimed_out_from_under_itself(self) -> None:
        with self.Session() as db:
            demo, job = add_demo_with_job(db, "demo-still-running", "real_parse")
            service = DemoService.for_internal(db)
            service.claim_parse_job(demo, job)

            reclaimed = service.reclaim_stale_parse_jobs(
                older_than_seconds=settings.parse_reclaim_after_seconds,
                redispatch_after_seconds=settings.parse_redispatch_after_seconds,
                redis_client=self.redis,
            )

            db.refresh(job)

            self.assertEqual(reclaimed, [])
            self.assertEqual(job.status, "processing")
            self.assertEqual(self.redis.llen(QUEUE), 0)

    def test_a_queued_row_whose_message_was_lost_is_dispatched_again(self) -> None:
        with self.Session() as db:
            demo, job = add_demo_with_job(db, "demo-lost-message", "real_parse")
            job.created_at = utc_now() - timedelta(hours=1)
            db.commit()
            service = DemoService.for_internal(db)

            reclaimed = service.reclaim_stale_parse_jobs(
                older_than_seconds=1800,
                redispatch_after_seconds=300,
                redis_client=self.redis,
            )

            self.assertEqual(reclaimed, [job.id])
            self.assertEqual(self.redis.llen(QUEUE), 1)

            # Redispatching without checking whether a message already exists is
            # deliberate: claiming is a compare-and-set, so the duplicate finds
            # the row taken and does nothing at all.
            self.assertTrue(service.claim_parse_job(demo, job))
            self.assertFalse(service.claim_parse_job(demo, job))
            db.refresh(job)
            self.assertEqual(job.attempts, 1)

    def test_a_demo_that_keeps_killing_the_worker_is_failed_instead_of_retried_forever(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            with self.Session() as db:
                demo, job = add_demo_with_job(db, "demo-poison", "real_parse")
                bind_source_artifact(db, demo, job)
                service = DemoService.for_internal(db)
                service.claim_parse_job(demo, job)
                job.attempts = settings.parse_max_attempts
                job.started_at = utc_now() - timedelta(hours=2)
                db.commit()

                reclaimed = service.reclaim_stale_parse_jobs(
                    older_than_seconds=1800,
                    redispatch_after_seconds=300,
                    redis_client=self.redis,
                )

                db.refresh(job)
                db.refresh(demo)
                failure = json.loads(job.metadata_json)["failure"]

                self.assertEqual(reclaimed, [job.id])
                self.assertEqual(job.status, "failed")
                self.assertEqual(demo.status, "failed")
                self.assertEqual(failure["errorCode"], PARSE_ABANDONED_ERROR_CODE)
                # Requeueing forever was never the answer; every claim burns an
                # attempt. Failing the row is what puts the retry back in the
                # user's hands instead of leaving them stuck on "parsing".
                self.assertTrue(service.demo_ingestion_status(demo).retryable)
                self.assertEqual(self.redis.llen(QUEUE), 0)

    def test_an_orphan_of_an_unknown_job_is_dropped_rather_than_cycled(self) -> None:
        self.redis.lpush(QUEUE, json.dumps({"job_id": "gone", "demo_id": "gone"}))
        dead = ParseQueue(
            self.redis,
            queue_name=QUEUE,
            consumer_id="dead-worker",
            lease_ttl_seconds=60,
        )
        dead.register()
        dead.reserve(timeout=1)
        self.redis.delete(lease_key("dead-worker"))

        with self.Session() as db:
            live = ParseQueue(
                self.redis,
                queue_name=QUEUE,
                consumer_id="live-worker",
                lease_ttl_seconds=60,
            )
            live.register()
            recovered = self.reap_with(live, db)

        # The message is accounted for either way -- what matters is that it did
        # not go back on the queue to be delivered to a job that is not there.
        self.assertEqual(recovered, 1)
        self.assertEqual(self.redis.llen(QUEUE), 0)
        self.assertEqual(self.redis.llen(processing_key(QUEUE, "dead-worker")), 0)
        self.assertNotIn("dead-worker", self.redis.smembers(CONSUMERS_KEY))


class ParseSubprocessTest(unittest.TestCase):
    """The parse now runs out of process; only exit codes come back."""

    def test_a_native_crash_is_reported_as_a_crash_not_a_mystery(self) -> None:
        with self.assertRaises(DemoParserError) as raised:
            _parse_child_result(-11, Path("missing.json"))
        self.assertEqual(raised.exception.error_code, "PARSER_CRASHED")

    def test_a_kill_signal_is_reported_as_a_memory_problem(self) -> None:
        with self.assertRaises(DemoParserError) as raised:
            _parse_child_result(-9, Path("missing.json"))
        self.assertEqual(raised.exception.error_code, "PARSE_OUT_OF_MEMORY")

    def test_any_other_nonzero_exit_stays_generic(self) -> None:
        with self.assertRaises(DemoParserError) as raised:
            _parse_child_result(7, Path("missing.json"))
        self.assertEqual(raised.exception.error_code, "PARSER_UNEXPECTED")

    def test_the_childs_own_classification_survives_the_process_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "parsed.json"
            output.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "errorCode": "INVALID_DEMO",
                        "message": f"demoparser2 choked on {directory}/private/source.dem",
                        "userMessage": "This file is not a readable CS2 demo.",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(DemoParserError) as raised:
                _parse_child_result(EXIT_PARSE_ERROR, output)

            self.assertEqual(raised.exception.error_code, "INVALID_DEMO")
            # The raw message names a local path, so only the vetted wording is
            # allowed to reach a user.
            self.assertNotIn(directory, raised.exception.user_message)

    def test_an_unclassified_child_failure_does_not_leak_its_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "parsed.json"
            output.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "errorCode": "PARSER_UNEXPECTED",
                        "message": f"ValueError: {directory}/private/source.dem\ntraceback...",
                        "userMessage": None,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(DemoParserError) as raised:
                _parse_child_result(EXIT_PARSE_ERROR, output)

            self.assertEqual(raised.exception.error_code, "PARSER_UNEXPECTED")
            self.assertNotIn(directory, raised.exception.user_message)
            self.assertNotIn("traceback", raised.exception.user_message.lower())

    def test_a_successful_parse_is_handed_back_whole(self) -> None:
        parsed = {"mapName": "de_dust2", "tickRate": 64}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "parsed.json"
            output.write_text(json.dumps({"ok": True, "parsed": parsed}), encoding="utf-8")
            self.assertEqual(_parse_child_result(0, output), parsed)

    def test_a_parse_that_never_finishes_is_killed_on_the_deadline(self) -> None:
        # This child ignores the kill too, so the grace wait is exercised along
        # with the deadline: a parse stuck below the signal must still hand the
        # worker back rather than block it forever.
        process = FakeProcess(exits_after_polls=None, return_code=0, survives_kill=True)
        ticks = 0

        def on_tick() -> None:
            nonlocal ticks
            ticks += 1

        clock = iter([0.0, 10.0, 20.0])
        with parse_limits(timeout_seconds=15, renew_seconds=10), patch(
            "app.workers.child_process.subprocess.Popen", return_value=process
        ), patch("app.workers.worker.time.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(DemoParserError) as raised:
                run_parse_subprocess(Path("source.dem"), on_tick=on_tick)

        self.assertEqual(raised.exception.error_code, "PARSE_TIMED_OUT")
        self.assertTrue(process.killed)
        self.assertEqual(ticks, 2)

    def test_a_long_parse_keeps_the_worker_visibly_alive(self) -> None:
        # The heartbeat used to be written only around the queue read, so any
        # parse longer than the alive window made /diagnostics report a working
        # worker as offline. The poll loop is what fixes that.
        redis_client = FakeRedis()
        started = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        write_worker_heartbeat(redis_client, now=started)
        queue = ParseQueue(
            redis_client,
            queue_name=QUEUE,
            consumer_id="slow-worker",
            lease_ttl_seconds=60,
        )
        queue.register()
        beats = iter([started + timedelta(seconds=40), started + timedelta(seconds=80)])

        def on_tick() -> None:
            queue.renew_lease()
            write_worker_heartbeat(redis_client, now=next(beats))

        process = FakeProcess(exits_after_polls=3, return_code=0, parsed={"mapName": "de_dust2"})
        clock = iter([0.0, 40.0, 80.0])
        with parse_limits(timeout_seconds=1200, renew_seconds=40), patch(
            "app.workers.child_process.subprocess.Popen", side_effect=process.open
        ), patch("app.workers.worker.time.monotonic", side_effect=lambda: next(clock)):
            parsed = run_parse_subprocess(Path("source.dem"), on_tick=on_tick)

        self.assertEqual(parsed, {"mapName": "de_dust2"})
        self.assertTrue(redis_client.exists(lease_key("slow-worker")))
        # 100 seconds in, past the 90-second alive window, but only 20 seconds
        # since the last tick wrote one.
        heartbeat = read_worker_heartbeat(
            redis_client,
            now=started + timedelta(seconds=100),
        )
        self.assertTrue(heartbeat["alive"])


class ParseChildEnvironmentTest(unittest.TestCase):
    def test_the_parse_child_runs_openblas_single_threaded(self) -> None:
        # numpy's OpenBLAS reserves buffers per core at import and they count
        # against the child's RLIMIT_DATA; the v5 shots pass overran 4 GiB with it.
        captured: dict[str, str] = {}
        process = FakeProcess(exits_after_polls=0, return_code=EXIT_PARSE_ERROR)

        def popen(*args: object, env: dict[str, str], **kwargs: object) -> FakeProcess:
            captured.update(env)
            return process

        with parse_limits(timeout_seconds=15, renew_seconds=10), patch(
            "app.workers.child_process.subprocess.Popen", side_effect=popen
        ), patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("OPENBLAS_NUM_THREADS", None)
            with self.assertRaises(DemoParserError):
                run_parse_subprocess(Path("source.dem"), on_tick=lambda: None)

        self.assertEqual(captured.get("OPENBLAS_NUM_THREADS"), "1")


class ParseQueueConfigurationTest(unittest.TestCase):
    """Two settings that break the parser quietly if they are wrong.

    Both were found the expensive way: a reclaim window under the parse timeout
    re-parses a demo that is still running, and a memory ceiling under the
    parser's startup footprint kills every child during `import` with an
    allocation error that names neither the demo nor the limit. Startup is the
    cheapest place to notice either.
    """

    def settings_with(self, **overrides: object) -> Settings:
        return Settings(auth_mode="development", **overrides)

    def test_the_shipped_defaults_are_accepted(self) -> None:
        self.settings_with().validate_worker_runtime_configuration()

    def test_a_reclaim_window_inside_the_parse_timeout_is_rejected(self) -> None:
        for reclaim in (600, 1200):
            with self.subTest(reclaim=reclaim):
                with self.assertRaisesRegex(RuntimeError, "PARSE_RECLAIM_AFTER_SECONDS"):
                    self.settings_with(
                        parse_timeout_seconds=1200,
                        parse_reclaim_after_seconds=reclaim,
                    ).validate_worker_runtime_configuration()

    def test_a_memory_ceiling_below_the_parser_footprint_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "PARSE_MEMORY_LIMIT_BYTES"):
            self.settings_with(
                parse_memory_limit_bytes=400 * 1024 * 1024
            ).validate_worker_runtime_configuration()

        # 0 is the documented way to turn the cap off, not a ceiling of zero.
        self.settings_with(parse_memory_limit_bytes=0).validate_worker_runtime_configuration()


class FakeProcess:
    """A child process that never really runs.

    `exits_after_polls=None` means it never exits, which is the only way to get
    at the deadline without waiting one out. `survives_kill` goes one further and
    ignores the kill as well, standing in for a child wedged in uninterruptible
    IO -- the case the grace wait in run_parse_subprocess is there for.
    """

    def __init__(
        self,
        *,
        exits_after_polls: int | None,
        return_code: int,
        parsed: dict | None = None,
        survives_kill: bool = False,
    ) -> None:
        self.exits_after_polls = exits_after_polls
        self._return_code = return_code
        self.parsed = parsed
        self.survives_kill = survives_kill
        self.returncode: int | None = None
        self.pid = 4242
        self.polls = 0
        self.killed = False
        self.output_path: Path | None = None

    def open(self, argv: list[str], **_: object) -> "FakeProcess":
        self.output_path = Path(argv[argv.index("--output") + 1])
        return self

    def wait(self, timeout: int | None = None) -> int:
        if self.killed and not self.survives_kill:
            return self._exit()
        self.polls += 1
        if self.exits_after_polls is None or self.polls < self.exits_after_polls:
            raise subprocess.TimeoutExpired(cmd="parse", timeout=timeout or 0)
        return self._exit()

    def _exit(self) -> int:
        if self.output_path is not None and not self.killed:
            self.output_path.write_text(
                json.dumps({"ok": True, "parsed": self.parsed}), encoding="utf-8"
            )
        self.returncode = -9 if self.killed else self._return_code
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


@contextmanager
def parse_limits(*, timeout_seconds: int, renew_seconds: int):
    original_timeout = settings.parse_timeout_seconds
    original_renew = settings.parse_lease_renew_seconds
    object.__setattr__(settings, "parse_timeout_seconds", timeout_seconds)
    object.__setattr__(settings, "parse_lease_renew_seconds", renew_seconds)
    try:
        yield
    finally:
        object.__setattr__(settings, "parse_timeout_seconds", original_timeout)
        object.__setattr__(settings, "parse_lease_renew_seconds", original_renew)


@contextmanager
def storage_dirs(root: Path):
    original_artifact_root = settings.artifact_storage_root
    original_upload_dir = settings.demo_upload_storage_dir
    original_replay_dir = settings.replay_storage_dir
    original_video_dir = settings.video_storage_dir
    object.__setattr__(settings, "artifact_storage_root", root)
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    try:
        yield
    finally:
        object.__setattr__(settings, "artifact_storage_root", original_artifact_root)
        object.__setattr__(settings, "demo_upload_storage_dir", original_upload_dir)
        object.__setattr__(settings, "replay_storage_dir", original_replay_dir)
        object.__setattr__(settings, "video_storage_dir", original_video_dir)


if __name__ == "__main__":
    unittest.main()
