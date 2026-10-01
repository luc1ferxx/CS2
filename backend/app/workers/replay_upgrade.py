"""Backstop that re-parses completed demos whose replay predates the current contract.

Demos parsed before a contract change (v2: live player states and utility
trajectories) keep their old replay, and the review page degrades quietly for
them. This pass upgrades them off the worker's idle tick, one demo per pass: the
stored `.dem` is re-parsed through the same parse child process as an upload,
under the same wall-clock and memory limits, and
`DemoService.complete_replay_upgrade` swaps the result in with one commit and
without a status change; the coaching suggestions (and the verdicts on them)
are left as they are (see services/demo_service/replay_upgrade.py for the
state it keeps and the compare-and-set rules).

Real work always wins: the pass only starts when the queue is empty, and a
message arriving while the child runs stops it and hands the attempt back
uncounted. A match deleted meanwhile stops it too, without a failure. A failed
upgrade keeps the old replay, records a compact error code (no messages, no
stack traces) and is retried later with a doubling delay, up to
`settings.replay_upgrade_max_attempts`; attempts a dead worker never finished
count towards that limit too.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.demo import Demo
from app.models.job import DemoJob
from app.parser.demo_parser import DemoParserError
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import replay_contract_is_current
from app.services.artifact_binding import AcceptedArtifactError
from app.services.demo_service import DemoGoneError, DemoService, ReplayUpgradeClaim
from app.services.storage import ArtifactStoreError, StorageKeyError

REPLAY_UPGRADE_INTERVAL_SECONDS = 30
# Demos looked at per pass when the first ones cannot be claimed (another worker
# took them); only one is ever re-parsed.
REPLAY_UPGRADE_CANDIDATES = 3
# How often a running upgrade checks for waiting work and for its demo.
REPLAY_UPGRADE_CHECK_SECONDS = 15

ParseRunner = Callable[..., dict[str, Any]]
SessionFactory = Callable[[], AbstractContextManager[Session]]

_last_pass = 0.0


class _Yielded(Exception):
    """A queue message is waiting; the upgrade gives the worker back."""


class _DemoDeleted(Exception):
    """The demo vanished while its upgrade ran."""


def queue_has_work(queue: Any) -> bool:
    """Whether any message waits on the worker's queue; unknown counts as yes."""
    try:
        return int(queue.redis.llen(queue.queue_name) or 0) > 0
    except Exception:
        return True


def upgrade_stale_replays(
    *,
    run_parse: ParseRunner,
    should_yield: Callable[[], bool] = lambda: False,
    on_tick: Callable[[], None] | None = None,
    session_factory: SessionFactory = SessionLocal,
    force: bool = False,
    check_interval_seconds: float = REPLAY_UPGRADE_CHECK_SECONDS,
) -> str | None:
    """One bounded pass: upgrade at most one demo; returns what happened to it.

    `run_parse(source_path, on_tick=...)` is the worker's parse-child runner
    (injected: this module is imported by the worker). `on_tick` keeps the
    lease and heartbeat fresh while the child runs. Rate-limited like the other
    idle-tick backstops; `force` skips the limiter for tests.
    """
    global _last_pass

    if not settings.replay_upgrade_enabled:
        return None
    now = time.monotonic()
    if not force and now - _last_pass < REPLAY_UPGRADE_INTERVAL_SECONDS:
        return None
    _last_pass = now
    if should_yield():
        return None

    with session_factory() as db:
        service = DemoService.for_internal(db)
        claim: ReplayUpgradeClaim | None = None
        for demo_id in service.demo_ids_due_for_replay_upgrade(limit=REPLAY_UPGRADE_CANDIDATES):
            claim = service.claim_replay_upgrade(demo_id)
            if claim is not None:
                break
        if claim is None:
            return None
        outcome = _upgrade(
            db,
            service,
            claim,
            run_parse=run_parse,
            should_yield=should_yield,
            on_tick=on_tick,
            check_interval_seconds=check_interval_seconds,
        )
    print(f"Replay upgrade for demo {claim.demo_id}: {outcome}", flush=True)
    return outcome


def _upgrade(
    db: Session,
    service: DemoService,
    claim: ReplayUpgradeClaim,
    *,
    run_parse: ParseRunner,
    should_yield: Callable[[], bool],
    on_tick: Callable[[], None] | None,
    check_interval_seconds: float,
) -> str:
    try:
        demo = db.query(Demo).filter(Demo.id == claim.demo_id).one_or_none()
        job = db.query(DemoJob).filter(DemoJob.id == claim.job_id).one_or_none()
        if demo is None or job is None:
            return "gone"
        expected_replay_key = demo.replay_storage_key

        stored = service.load_replay_blob(demo)
        if stored is None:
            return _failed(service, claim, "REPLAY_UNREADABLE")
        version = stored.get("contractVersion")
        if replay_contract_is_current(version):
            service.mark_replay_upgrade_current(claim, str(version))
            return "current"
        # Render status and manual MP4 calibration live in the replay; keep only
        # that section, not the whole old replay, in memory across the parse.
        video = stored.get("video")
        del stored
    except Exception:
        db.rollback()
        if service.demo_missing(claim.demo_id):
            return "gone"
        return _failed(service, claim, "REPLAY_UNREADABLE")

    bind = db.get_bind()
    tick = _checking_tick(
        lambda: _demo_exists(bind, claim.demo_id),
        should_yield,
        on_tick,
        interval_seconds=check_interval_seconds,
    )
    try:
        with service.materialized_source_demo(demo, job) as source_path:
            # End the read transaction: nothing is held while the child runs.
            db.rollback()
            parsed = run_parse(source_path, on_tick=tick)
    except _Yielded:
        db.rollback()
        service.release_replay_upgrade(claim)
        return "yielded"
    except _DemoDeleted:
        db.rollback()
        return "gone"
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        db.rollback()
        if service.demo_missing(claim.demo_id):
            return "gone"
        return _failed(service, claim, _parse_error_code(exc))

    try:
        replay = normalize_parser_output(claim.demo_id, parsed)
        if isinstance(video, dict):
            replay["video"] = video
    except Exception:
        return _failed(service, claim, "NORMALIZATION_FAILED")

    team_names = parsed.get("teamNames")
    try:
        outcome = service.complete_replay_upgrade(
            claim,
            expected_replay_key=expected_replay_key,
            replay=replay,
            team_names=team_names if isinstance(team_names, dict) else None,
        )
    except DemoGoneError:
        return "gone"
    except Exception:
        return _failed(service, claim, "REPLAY_WRITE_FAILED")
    if outcome == "changed":
        # The demo moved on meanwhile (a video update rewrote the replay, a
        # retry, another worker): not this demo's fault, so not an attempt.
        service.release_replay_upgrade(claim)
    return outcome


def _failed(service: DemoService, claim: ReplayUpgradeClaim, error_code: str) -> str:
    try:
        service.record_replay_upgrade_failure(claim, error_code)
    except Exception:
        # The claim already counted the attempt and holds the demo until the
        # parse timeout has passed; the next pass after that retries it.
        pass
    return f"failed:{error_code}"


def _parse_error_code(exc: BaseException) -> str:
    if isinstance(exc, DemoParserError):
        return exc.error_code
    if isinstance(exc, (OSError, StorageKeyError, ArtifactStoreError, AcceptedArtifactError)):
        return "STORAGE_READ_FAILED"
    return "PARSER_UNEXPECTED"


def _demo_exists(bind: Any, demo_id: str) -> bool:
    # A short session of its own; the upgrade's session stays idle meanwhile.
    with Session(bind=bind) as check_db:
        return check_db.query(Demo.id).filter(Demo.id == demo_id).first() is not None


def _checking_tick(
    demo_exists: Callable[[], bool],
    should_yield: Callable[[], bool],
    on_tick: Callable[[], None] | None,
    *,
    interval_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], None]:
    """The parse tick, raising to stop the child when work waits or the demo is gone.

    The parse runner kills its child when the tick raises. A failed check
    never stops the upgrade: an unknown answer is neither work nor a deletion.
    """
    last_check = clock()

    def tick() -> None:
        nonlocal last_check
        if on_tick is not None:
            on_tick()
        now = clock()
        if now - last_check < interval_seconds:
            return
        last_check = now
        if should_yield():
            raise _Yielded
        try:
            exists = demo_exists()
        except Exception:
            return
        if not exists:
            raise _DemoDeleted

    return tick


def reset_upgrade_state() -> None:
    """Forget the rate limit (tests)."""
    global _last_pass
    _last_pass = 0.0
