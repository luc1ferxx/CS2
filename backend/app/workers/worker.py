import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.analyzer import analyze_replay
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.redis import get_redis_client
from app.models.demo import Demo
from app.models.job import DemoJob
from app.parser.demo_parser import DemoParserError
from app.parser.normalizer import normalize_parser_output
from app.services.artifact_binding import AcceptedArtifactError
from app.services.deletion_service import drain_deletion_outbox, run_hourly_storage_maintenance
from app.services.demo_service import (
    RENDER_CLIP_JOB_TYPE,
    RENDER_CLIP_NOT_CONNECTED_ERROR,
    RENDER_CLIP_STALE_AFTER_SECONDS,
    RENDER_FAILED_ERROR_CODE,
    RENDER_FAILED_PUBLIC_MESSAGE,
    RENDER_WORKER_UNAVAILABLE_ERROR_CODE,
    DemoGoneError,
    DemoService,
)
from app.services.demo_service.replay_warmer import (
    install_replay_ready_publisher,
    uninstall_replay_ready_publisher,
)
from app.services.diagnostics import write_worker_heartbeat
from app.services.mock_replay_service import build_mock_replay
from app.services.storage import ArtifactStoreError, StorageKeyError
from app.workers.child_process import (
    ChildUsage,
    child_workspace,
    read_child_json,
    read_stderr_tail,
    resolve_child_identity,
    share_private_source,
    spawn_child,
    stderr_capture,
    wait_for_exit,
)
from app.workers.coaching_recompute import recompute_stale_coaching
from app.workers.match_summary_backfill import backfill_match_summaries
from app.workers.parse_child import EXIT_PARSE_ERROR
from app.workers.queue import ParseQueue, new_consumer_id
from app.workers.replay_upgrade import queue_has_work, upgrade_stale_replays

PARSE_CHILD_MODULE = "app.workers.parse_child"
# backend/app/workers/worker.py -> backend/, the directory holding the `app`
# package. The parse child is spawned as `-m app.workers.parse_child`, and it
# gets a fresh sys.path built from its own cwd; pinning the root here keeps it
# importable no matter where the worker was started from.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
PARSER_UNEXPECTED_PUBLIC_MESSAGE = "Unexpected parser error. Retry or upload a different demo."
# How long to wait for a killed parse child to actually go away before giving up
# on it and carrying on. Reaping it is a courtesy; the worker staying responsive
# is the point.
PARSE_KILL_GRACE_SECONDS = 30
# How often a running parse checks that its job still exists. A match deleted
# mid-parse stops its child instead of holding the single worker (and every
# queued upload behind it) for up to PARSE_TIMEOUT_SECONDS.
PARSE_DELETION_CHECK_SECONDS = 15
# What the Rust allocator writes to stderr right before it aborts (SIGABRT) on a
# failed allocation -- which is how RLIMIT_DATA usually ends a demoparser2 parse.
RUST_ALLOCATION_FAILURE_MARKER = "memory allocation of"
PARSE_OUT_OF_MEMORY_PUBLIC_MESSAGE = "This demo needed more memory than the parser is allowed to use."
# One JSON line per finished queue job on stdout (run_worker). Opaque ids and
# numbers only: never a file name, owner, path or message.
JOB_DONE_FIELDS = (
    "event",
    "jobId",
    "type",
    "outcome",
    "errorCode",
    "attempt",
    "sourceBytes",
    "downloadS",
    "parseS",
    "normalizeS",
    "analyzeS",
    "coachingEvents",
    "peakRssMiB",
)


class ParseJobDeletedError(Exception):
    """The job's row vanished while its parse ran: the match was deleted."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_job_report(job_id: str) -> dict[str, Any]:
    """The fields of one `job_done` line, filled in while the job runs."""
    report: dict[str, Any] = dict.fromkeys(JOB_DONE_FIELDS)
    report["event"] = "job_done"
    report["jobId"] = job_id
    return report


def log_job_done(report: dict[str, Any]) -> None:
    print(
        json.dumps({field: report.get(field) for field in JOB_DONE_FIELDS}, separators=(",", ":")),
        flush=True,
    )


def process_job(
    db: Session,
    job_id: str,
    demo_id: str,
    *,
    on_tick: Callable[[], None] | None = None,
    report: dict[str, Any] | None = None,
) -> None:
    report = {} if report is None else report
    demo = db.query(Demo).filter(Demo.id == demo_id).one_or_none()
    job = db.query(DemoJob).filter(DemoJob.id == job_id).one_or_none()
    if demo is None or job is None or job.demo_id != demo.id:
        report["outcome"] = "skipped"
        return
    report["type"] = job.job_type
    report["attempt"] = job.attempts

    if job.job_type == RENDER_CLIP_JOB_TYPE:
        process_render_clip_job(db, demo, job)
        return
    if job.job_type == "mock_render":
        process_mock_render_job(db, demo, job)
        return
    if job.job_type == "real_parse":
        process_real_parse_job(db, demo, job, on_tick=on_tick, report=report)
        return
    if job.job_type != "mock_parse":
        raise ValueError(f"Unsupported job type: {job.job_type}")

    process_mock_parse_job(db, demo, job)


def process_mock_parse_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService.for_internal(db)
    demo_id = demo.id
    if not service.claim_parse_job(demo, job):
        return

    time.sleep(1.2)

    if not service.mark_parse_analyzing(demo, job):
        return
    time.sleep(1.2)

    replay, events = build_mock_replay(demo_id)
    service.complete_parse_job(demo, job, replay, events)


def run_parse_subprocess(
    source_path: Path,
    *,
    on_tick: Callable[[], None] | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse one demo in a child process and return its output.

    demoparser2 is a Rust extension: a segfault in it kills the process it runs
    in outright, with no exception to catch. Running it here means the worker
    only ever sees an exit code, so one bad demo costs that demo instead of the
    whole loop -- and a hung parse gets a wall-clock ceiling that an in-process
    call could not give it. The child gets an allow-listed environment and, under
    a root worker, an unprivileged account of its own (child_process.py).

    `on_tick` is called every parse_lease_renew_seconds while the child runs.
    The parent is otherwise blocked for the entire parse, which is long enough
    for both the queue lease and the worker heartbeat to lapse.

    `stats`, when given, receives `parseS` (wall seconds) and `peakRssMiB` (the
    child's peak resident memory; None where os.wait4 is unavailable), on
    failure as well as success.
    """
    timeout_seconds = settings.parse_timeout_seconds
    renew_seconds = max(1, settings.parse_lease_renew_seconds)
    identity = resolve_child_identity()
    usage = ChildUsage()
    wall_started = time.perf_counter()

    try:
        with child_workspace("cs2-parse-", identity) as workspace, stderr_capture(workspace) as stderr:
            output_path = workspace / "parsed.json"
            share_private_source(source_path, identity)
            process = spawn_child(
                [
                    sys.executable,
                    "-m",
                    PARSE_CHILD_MODULE,
                    "--source",
                    str(source_path),
                    "--output",
                    str(output_path),
                    "--memory-limit-bytes",
                    str(settings.parse_memory_limit_bytes),
                ],
                package_root=PACKAGE_ROOT,
                identity=identity,
                workspace=workspace,
                stderr=stderr,
            )
            started = time.monotonic()
            try:
                while not wait_for_exit(process, renew_seconds, usage):
                    if on_tick is not None:
                        on_tick()
                    if time.monotonic() - started >= timeout_seconds:
                        raise DemoParserError(
                            f"Parser exceeded {timeout_seconds}s and was stopped",
                            error_code="PARSE_TIMED_OUT",
                            user_message=(
                                "Parsing this demo took too long and was stopped."
                            ),
                        ) from None
            finally:
                if process.poll() is None:
                    process.kill()
                    if not wait_for_exit(process, PARSE_KILL_GRACE_SECONDS, usage):
                        # The kill has been delivered; a child still running after
                        # it is stuck somewhere the signal cannot reach, usually
                        # uninterruptible IO. Waiting on that forever would cost
                        # the worker the very thing the subprocess was meant to
                        # protect.
                        print(
                            f"Parse child {process.pid} did not exit after being killed",
                            flush=True,
                        )

            stderr_tail = read_stderr_tail(stderr)
            _replay_child_stderr(process.returncode, stderr_tail)
            return _parse_child_result(process.returncode, output_path, stderr_tail)
    finally:
        if stats is not None:
            stats["parseS"] = round(time.perf_counter() - wall_started, 2)
            stats["peakRssMiB"] = usage.peak_rss_mib


def _replay_child_stderr(return_code: int | None, stderr_tail: str) -> None:
    """Put the end of the child's stderr into the worker log (worker-side only).

    Never into a job's failure message: that becomes the demo's user-visible
    error, and stderr can name local paths.
    """
    if not stderr_tail.strip():
        return
    print(
        f"Parse child exited with {return_code}; end of its stderr:\n{stderr_tail.rstrip()}",
        flush=True,
    )


def _parse_child_result(
    return_code: int,
    output_path: Path,
    stderr_tail: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        loaded = read_child_json(output_path)
        body = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        body = {}

    if return_code == 0 and body.get("ok") is True:
        parsed = body.get("parsed")
        if isinstance(parsed, dict):
            return parsed
        raise DemoParserError(
            "Parser returned no usable output",
            error_code="PARSER_UNEXPECTED",
            user_message=PARSER_UNEXPECTED_PUBLIC_MESSAGE,
        )

    if return_code == EXIT_PARSE_ERROR and body.get("ok") is False:
        # The child classified its own failure; keep that classification rather
        # than flattening every parser complaint into one code. The raw message
        # stays worker-side -- it can name the demo's path, and an unclassified
        # failure has no vetted wording to show a user.
        raise DemoParserError(
            str(body.get("message") or "Parser reported a failure"),
            error_code=str(body.get("errorCode") or "PARSER_UNEXPECTED"),
            user_message=str(body.get("userMessage") or PARSER_UNEXPECTED_PUBLIC_MESSAGE),
        )

    if return_code < 0:
        # Killed by a signal. SIGKILL is what the container OOM killer sends, so
        # it reads as a memory problem rather than a crash. SIGABRT right after
        # the Rust allocator's "memory allocation of N bytes failed" is
        # RLIMIT_DATA refusing an allocation: also memory, and retrying cannot
        # help. Anything else is the native crash this whole subprocess exists
        # to contain.
        signal_number = -return_code
        if signal_number == 9:
            raise DemoParserError(
                "Parser process was killed, most likely for exceeding memory",
                error_code="PARSE_OUT_OF_MEMORY",
                user_message=PARSE_OUT_OF_MEMORY_PUBLIC_MESSAGE,
            )
        if signal_number == 6 and RUST_ALLOCATION_FAILURE_MARKER in stderr_tail:
            raise DemoParserError(
                "Parser aborted on a failed memory allocation",
                error_code="PARSE_OUT_OF_MEMORY",
                user_message=PARSE_OUT_OF_MEMORY_PUBLIC_MESSAGE,
            )
        raise DemoParserError(
            f"Parser process died on signal {signal_number}",
            error_code="PARSER_CRASHED",
            user_message="The parser crashed while reading this demo.",
        )

    raise DemoParserError(
        f"Parser process exited with code {return_code}",
        error_code="PARSER_UNEXPECTED",
        user_message=PARSER_UNEXPECTED_PUBLIC_MESSAGE,
    )


def parse_job_exists(db: Session, job_id: str) -> bool:
    """Whether the job row is still there, read on a short session of its own.

    The parse's own session sits idle while the child runs; a separate one
    keeps this from holding anything open across the whole parse.
    """
    with Session(bind=db.get_bind()) as check_db:
        return check_db.query(DemoJob.id).filter(DemoJob.id == job_id).first() is not None


def deletion_aware_tick(
    job_exists: Callable[[], bool],
    on_tick: Callable[[], None] | None = None,
    *,
    interval_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], None]:
    """Wrap a parse's tick so it raises ParseJobDeletedError once the job is gone.

    run_parse_subprocess kills the child when its tick raises, and the
    temporary copy of the .dem goes with its workspace. A failed check never
    stops a parse: an unknown answer is not a deletion.
    """
    interval = PARSE_DELETION_CHECK_SECONDS if interval_seconds is None else interval_seconds
    last_check = clock()

    def tick() -> None:
        nonlocal last_check
        if on_tick is not None:
            on_tick()
        now = clock()
        if now - last_check < interval:
            return
        last_check = now
        try:
            exists = job_exists()
        except Exception:
            return
        if not exists:
            raise ParseJobDeletedError

    return tick


def process_real_parse_job(
    db: Session,
    demo: Demo,
    job: DemoJob,
    *,
    on_tick: Callable[[], None] | None = None,
    report: dict[str, Any] | None = None,
) -> None:
    """Parse, normalize, analyze and store one uploaded demo.

    `report` (the run loop's `job_done` line) receives the outcome, error code,
    attempt, source size and per-step timings as they become known.
    """
    report = {} if report is None else report
    service = DemoService.for_internal(db)
    # Read once while the rows are known to be loaded: the match can be
    # deleted at any point from here on, and each transition below reports
    # that by returning False instead of raising.
    demo_id, job_id = demo.id, job.id

    if not service.claim_parse_job(demo, job):
        report["outcome"] = "skipped"
        return
    report["attempt"] = job.attempts

    tick = deletion_aware_tick(lambda: parse_job_exists(db, job_id), on_tick)
    parse_stats: dict[str, Any] = {}
    try:
        download_started = time.perf_counter()
        with service.materialized_source_demo(demo, job) as source_path:
            report["downloadS"] = _seconds_since(download_started)
            report["sourceBytes"] = _file_size(source_path)
            parsed = run_parse_subprocess(source_path, on_tick=tick, stats=parse_stats)
    except ParseJobDeletedError:
        # The child is already stopped and its copy of the .dem removed.
        report.update(_parse_stats_fields(parse_stats), outcome="deleted")
        print(f"Worker job {job_id} stopped: demo deleted during parse", flush=True)
        try:
            db.rollback()
        except Exception:
            pass
        return
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report.update(_parse_stats_fields(parse_stats))
        _log_job_failure(job_id, "parse", exc)
        _fail_classified_parse_job(
            service, demo, job, exc, phase="parse", parse_stats=parse_stats, report=report,
        )
        return
    report.update(_parse_stats_fields(parse_stats))

    if not service.mark_parse_analyzing(demo, job):
        report["outcome"] = "deleted"
        return

    try:
        step_started = time.perf_counter()
        replay = normalize_parser_output(demo_id, parsed)
        report["normalizeS"] = _seconds_since(step_started)
        step_started = time.perf_counter()
        events = analyze_replay(replay)
        report["analyzeS"] = _seconds_since(step_started)
    except Exception as exc:
        _log_job_failure(job_id, "normalization", exc)
        _fail_classified_parse_job(
            service, demo, job, exc, phase="normalization", parse_stats=parse_stats, report=report,
        )
        return

    team_names = parsed.get("teamNames")
    report["coachingEvents"] = len(events)
    stored = service.complete_parse_job(
        demo,
        job,
        replay,
        events,
        team_names=team_names if isinstance(team_names, dict) else None,
        parse_stats=parse_stats,
    )
    report["outcome"] = "completed" if stored else "deleted"


def _parse_stats_fields(parse_stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "parseS": parse_stats.get("parseS"),
        "peakRssMiB": parse_stats.get("peakRssMiB"),
    }


def _seconds_since(started: float) -> float:
    return round(time.perf_counter() - started, 2)


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def process_mock_render_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService.for_internal(db)

    try:
        job.attempts += 1
        service.transition_mock_render_job(
            job,
            job_status="processing",
            video_status="rendering",
        )

        time.sleep(1.4)

        service.transition_mock_render_job(
            job,
            job_status="completed",
            video_status="ready",
        )
    except DemoGoneError:
        # The match was deleted mid-render; there is nothing left to update.
        return


def process_render_clip_job(db: Session, demo: Demo, job: DemoJob) -> None:
    # An external renderer polls the durable DB queue; stale Redis deliveries
    # must not claim or fail its jobs after the mode is enabled.
    if settings.render_worker_mode == "external":
        return
    service = DemoService.for_internal(db)

    if job.status != "queued":
        return

    if service.load_replay_blob(demo) is None:
        raise ValueError("Replay blob is not ready")

    try:
        try:
            service.claim_render_clip_job(job)
        except ValueError:
            # Another worker may have claimed the job since the initial read.
            db.refresh(job)
            if job.status != "queued":
                return
            raise
        service.fail_render_clip_job(
            job,
            RENDER_CLIP_NOT_CONNECTED_ERROR,
            error_code=RENDER_WORKER_UNAVAILABLE_ERROR_CODE,
        )
    except DemoGoneError:
        # The match was deleted while this job waited; nothing to fail.
        return


def fail_job(db: Session, job_id: str, demo_id: str, error: Any) -> None:
    # Whatever failed may have left the session mid-transaction -- a flush
    # that hit a row deleted underneath it leaves it unusable until this.
    try:
        db.rollback()
    except Exception:
        pass
    demo = db.query(Demo).filter(Demo.id == demo_id).one_or_none()
    job = db.query(DemoJob).filter(DemoJob.id == job_id).one_or_none()
    if demo is None or job is None or job.demo_id != demo.id:
        # Deleted (or never paired): nothing to mark failed.
        return
    if demo is not None and job is not None and job.job_type == "mock_render":
        try:
            service = DemoService.for_internal(db)
            service.transition_mock_render_job(
                job,
                job_status="failed",
                video_status="failed",
                error_code=RENDER_FAILED_ERROR_CODE,
                error_message=RENDER_FAILED_PUBLIC_MESSAGE,
            )
        except Exception:
            _log_job_failure(job.id, "mock-render-failure-update", error)
        return
    if demo is not None and job is not None and job.job_type == RENDER_CLIP_JOB_TYPE:
        try:
            service = DemoService.for_internal(db)
            service.fail_render_clip_job(
                job,
                RENDER_FAILED_PUBLIC_MESSAGE,
                error_code=RENDER_FAILED_ERROR_CODE,
            )
        except Exception:
            _log_job_failure(job.id, "render-clip-failure-update", error)
        return
    if demo is not None and job is not None and job.job_type in {"real_parse", "mock_parse"}:
        failure = _parse_failure_for_exception(error, phase="parse")
        DemoService.for_internal(db).fail_parse_job(
            demo,
            job,
            failure["message"],
            error_code=failure["errorCode"],
        )
        return
    if demo is not None:
        demo.status = "failed"
        demo.error_message = "Background job failed. Retry the operation."
    if job is not None:
        job.status = "failed"
        job.error_message = (
            RENDER_FAILED_PUBLIC_MESSAGE
            if job.job_type in {"mock_render", RENDER_CLIP_JOB_TYPE}
            else "Background job failed. Retry the operation."
        )
        job.finished_at = utc_now()
    try:
        db.commit()
    except Exception:
        db.rollback()
        if db.query(Demo.id).filter(Demo.id == demo_id).first() is None:
            return
        raise


RENDER_CLIP_SWEEP_INTERVAL_SECONDS = 60
_last_render_clip_sweep = 0.0


def sweep_stale_render_clip_jobs(*, force: bool = False) -> list[str]:
    """Backstop for render_clip jobs whose worker never came back.

    The fast path in GET /render-worker/jobs/next only fires when a render
    worker is actually polling. This one has to work when none ever will --
    the machine is off, the process is gone for good -- so it runs off the
    worker's idle brpop tick and judges staleness on wall-clock age alone.

    Rate-limited because that tick is every 5 seconds and the sweep is a
    query; `force` skips the limiter for tests.
    """
    global _last_render_clip_sweep

    now = time.monotonic()
    if not force and now - _last_render_clip_sweep < RENDER_CLIP_SWEEP_INTERVAL_SECONDS:
        return []
    _last_render_clip_sweep = now

    with SessionLocal() as db:
        reclaimed = DemoService.for_internal(db).reclaim_stale_render_clip_jobs(
            older_than_seconds=RENDER_CLIP_STALE_AFTER_SECONDS,
        )
    if reclaimed:
        print(
            f"Reclaimed {len(reclaimed)} stale render_clip job(s): {', '.join(reclaimed)}",
            flush=True,
        )
    return reclaimed


RENDER_CLIP_QUEUE_SWEEP_INTERVAL_SECONDS = 60
_last_render_clip_queue_sweep = 0.0


def sweep_unclaimed_render_clip_jobs(*, force: bool = False) -> list[str]:
    """Backstop for render_clip jobs no worker ever claimed.

    Deliberately separate from sweep_stale_render_clip_jobs: that one hands a
    job back to the queue, this one takes it off the queue, and conflating them
    behind one threshold would make a crashed render and an absent renderer
    resolve the same way. Both run off the same idle tick.

    Rate-limited for the same reason as its neighbour; `force` skips the
    limiter for tests.
    """
    global _last_render_clip_queue_sweep

    now = time.monotonic()
    if (
        not force
        and now - _last_render_clip_queue_sweep < RENDER_CLIP_QUEUE_SWEEP_INTERVAL_SECONDS
    ):
        return []
    _last_render_clip_queue_sweep = now

    with SessionLocal() as db:
        abandoned = DemoService.for_internal(db).fail_unclaimed_render_clip_jobs(
            older_than_seconds=settings.render_clip_queue_timeout_seconds,
        )
    if abandoned:
        print(
            f"Failed {len(abandoned)} unclaimed render_clip job(s): {', '.join(abandoned)}",
            flush=True,
        )
    return abandoned


PARSE_SWEEP_INTERVAL_SECONDS = 60
_last_parse_sweep = 0.0


def sweep_stale_parse_jobs(
    *,
    force: bool = False,
    redis_client: Any | None = None,
) -> list[str]:
    """Backstop for parse jobs the queue can no longer account for.

    The reaper in app.workers.queue is the fast path, but it can only see what
    Redis still remembers. Redis runs without persistence here, so a restart
    drops every lease and every in-flight message at once and leaves the rows
    they described stranded on "processing". This pass judges those rows on
    wall-clock age instead, which is the one signal that outlives Redis.
    """
    global _last_parse_sweep

    now = time.monotonic()
    if not force and now - _last_parse_sweep < PARSE_SWEEP_INTERVAL_SECONDS:
        return []
    _last_parse_sweep = now

    with SessionLocal() as db:
        reclaimed = DemoService.for_internal(db).reclaim_stale_parse_jobs(
            older_than_seconds=settings.parse_reclaim_after_seconds,
            redispatch_after_seconds=settings.parse_redispatch_after_seconds,
            redis_client=redis_client,
        )
    if reclaimed:
        print(
            f"Reclaimed {len(reclaimed)} stale parse job(s): {', '.join(reclaimed)}",
            flush=True,
        )
    return reclaimed


def reap_orphaned_parse_messages(queue: ParseQueue) -> int:
    """Return messages held by workers whose lease has lapsed.

    Moving the message back is only half of it: claim_parse_job accepts a job
    only from "queued", so a redelivery aimed at a row still reading
    "processing" would be swallowed and the task would vanish a second time.
    recover_parse_job rolls the row back first, and says no when the job has
    burned its attempts and should be failed instead of retried.
    """

    def on_orphan(message: dict[str, Any]) -> bool:
        job_id = message.get("job_id")
        demo_id = message.get("demo_id")
        if not job_id or not demo_id:
            return False
        with SessionLocal() as db:
            return DemoService.for_internal(db).recover_parse_job(str(job_id), str(demo_id))

    recovered = queue.reap(on_orphan=on_orphan)
    if recovered:
        print(f"Recovered {recovered} in-flight message(s) from a dead worker", flush=True)
    return recovered


def _run_backstop(name: str, action: Callable[[], Any]) -> None:
    """Run a recovery pass without letting its failure take the worker down."""
    try:
        action()
    except Exception as exc:
        _log_job_failure(name, "sweep", exc)


def run_worker() -> None:
    settings.validate_worker_runtime_configuration()
    # Resolved now so a missing PARSE_CHILD_USER account stops the worker at
    # startup instead of failing every parse.
    child_identity = resolve_child_identity()
    print(
        "Parse child runs as "
        + (f"user {child_identity.name}" if child_identity else "the worker's own user"),
        flush=True,
    )
    init_db()
    redis_client = get_redis_client()
    queue = ParseQueue(
        redis_client,
        queue_name=settings.redis_queue_name,
        consumer_id=new_consumer_id(),
        lease_ttl_seconds=settings.parse_lease_ttl_seconds,
    )
    queue.register()
    print(
        f"Worker {queue.consumer_id} listening on Redis queue: {settings.redis_queue_name}",
        flush=True,
    )
    # Finished parses, replay upgrades and video writes in this process are
    # announced to the API's replay warmer (replay_warmer.py); never a warmer here.
    install_replay_ready_publisher(redis_client)

    def tick() -> None:
        """Stay visibly alive while a long parse blocks this loop.

        Both the lease and the heartbeat lapse in well under the time a large
        demo takes to parse. Without this the queue would hand the job to
        another worker mid-parse and /diagnostics would report a busy worker as
        offline -- the parse-side twin of the render worker's busy grace period.
        """
        queue.renew_lease()
        write_worker_heartbeat(redis_client)

    # A previous process may have died holding messages, and Redis may have
    # restarted out from under every lease it was tracking. Run both recovery
    # passes before taking new work so that restarting the worker is enough to
    # unstick whatever the last one left behind.
    _run_backstop("startup-parse-reap", lambda: reap_orphaned_parse_messages(queue))
    _run_backstop(
        "startup-parse-sweep",
        lambda: sweep_stale_parse_jobs(force=True, redis_client=redis_client),
    )
    # A render_clip job left on "queued" is waiting on a render worker, not on
    # this one, so a restart here is no reason to keep waiting -- and if the
    # renderer has been off for the whole window, the wait is already over.
    _run_backstop(
        "startup-render-clip-queue-sweep",
        lambda: sweep_unclaimed_render_clip_jobs(force=True),
    )

    try:
        while True:
            tick()
            payload = queue.reserve(timeout=5)
            tick()
            if payload is None:
                # An idle tick is the one moment this loop has nothing better to
                # do. A failed sweep must never take the whole worker down.
                _run_backstop("render-clip-sweep", sweep_stale_render_clip_jobs)
                _run_backstop("render-clip-queue-sweep", sweep_unclaimed_render_clip_jobs)
                _run_backstop("parse-reap", lambda: reap_orphaned_parse_messages(queue))
                _run_backstop(
                    "parse-sweep",
                    lambda: sweep_stale_parse_jobs(redis_client=redis_client),
                )
                # Storage purges owed by hard deletes (at most every 60 s), and
                # hourly quarantine cleanup + upload ledger prune.
                _run_backstop("deletion-drain", drain_deletion_outbox)
                _run_backstop("storage-maintenance", run_hourly_storage_maintenance)
                # Demos completed before match summaries existed (every 30 s,
                # a few demos per pass; see match_summary_backfill).
                _run_backstop(
                    "match-summary-backfill",
                    lambda: backfill_match_summaries(on_tick=tick),
                )
                # Completed demos whose replay predates the current contract:
                # one re-parse per pass, stopped as soon as a message waits.
                _run_backstop(
                    "replay-upgrade",
                    lambda: upgrade_stale_replays(
                        run_parse=run_parse_subprocess,
                        should_yield=lambda: queue_has_work(queue),
                        on_tick=tick,
                    ),
                )
                # Completed demos whose suggestions predate the current rules:
                # one recompute from the stored replay per pass, after the
                # replay upgrade so a demo is recomputed on its current replay.
                _run_backstop(
                    "coaching-recompute",
                    lambda: recompute_stale_coaching(
                        should_yield=lambda: queue_has_work(queue),
                        on_tick=tick,
                    ),
                )
                continue

            try:
                message = json.loads(payload)
                job_id = str(message["job_id"])
                demo_id = str(message["demo_id"])
            except (KeyError, TypeError, ValueError) as exc:
                # Nothing downstream can act on a message this damaged, and
                # keeping it would block this consumer's processing list from
                # ever draining.
                _log_job_failure("unreadable-message", "decode", exc)
                queue.release(payload)
                continue

            report = new_job_report(job_id)
            with SessionLocal() as db:
                try:
                    process_job(db, job_id, demo_id, on_tick=tick, report=report)
                    if report["outcome"] is None:
                        report["outcome"] = "completed"
                    print(f"Completed job {job_id} for demo {demo_id}", flush=True)
                except Exception as exc:
                    report["outcome"] = "failed"
                    report["errorCode"] = _job_error_code(exc)
                    _log_job_failure(job_id, "process", exc)
                    try:
                        fail_job(db, job_id, demo_id, exc)
                    except Exception as fail_exc:
                        # Recording the failure failed too (the database may be
                        # briefly unreachable). The sweeps reconcile the row
                        # later; losing the whole worker over it helps nobody.
                        _log_job_failure(job_id, "failure-update", fail_exc)
                finally:
                    # Released only now: for everything between reserve() and
                    # here, the message is still in this consumer's processing
                    # list, which is what lets another worker recover it if this
                    # process dies mid-job.
                    queue.release(payload)
                    write_worker_heartbeat(redis_client)
                    log_job_done(report)
    finally:
        # Only reached on a graceful exit. A hard kill leaves the lease to
        # expire, which is exactly the signal the reaper looks for.
        uninstall_replay_ready_publisher()
        queue.unregister()


def _fail_classified_parse_job(
    service: DemoService,
    demo: Demo,
    job: DemoJob,
    exc: BaseException,
    *,
    phase: str,
    parse_stats: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> None:
    failure = _parse_failure_for_exception(exc, phase=phase)
    recorded = service.fail_parse_job(
        demo,
        job,
        failure["message"],
        error_code=failure["errorCode"],
        parse_stats=parse_stats,
    )
    if report is not None:
        report["outcome"] = "failed" if recorded else "deleted"
        report["errorCode"] = failure["errorCode"]


def _parse_failure_for_exception(error: Any, *, phase: str) -> dict[str, str]:
    if isinstance(error, DemoParserError):
        return {
            "errorCode": error.error_code,
            "message": error.user_message,
        }

    if isinstance(error, (OSError, StorageKeyError, ArtifactStoreError, AcceptedArtifactError)):
        return {
            "errorCode": "STORAGE_READ_FAILED",
            "message": "Uploaded demo artifact could not be read from storage.",
        }

    if phase == "normalization":
        return {
            "errorCode": "NORMALIZATION_FAILED",
            "message": "Parser output could not be normalized for replay review.",
        }

    return {
        "errorCode": "PARSER_UNEXPECTED",
        "message": PARSER_UNEXPECTED_PUBLIC_MESSAGE,
    }


def _job_error_code(error: BaseException) -> str:
    """A `job_done` errorCode: the parser's classification, else the exception type."""
    if isinstance(error, DemoParserError):
        return error.error_code
    return type(error).__name__


def _log_job_failure(job_id: str, phase: str, error: BaseException) -> None:
    print(
        f"Worker job {job_id} failed during {phase}: {type(error).__name__}",
        flush=True,
    )


if __name__ == "__main__":
    run_worker()
