"""Backstop that recomputes the coaching suggestions of demos analyzed by older rules.

Suggestions are stored at parse time. When the analyzer's output changes,
COACHING_RULES_VERSION (app/analysis/version.py) is bumped, and this pass brings
older demos up to date off the worker's idle tick, one demo per pass: the stored
replay is read through the storage package, the analyzer runs on it in this
process (about a second for a full match), and
`DemoService.complete_coaching_recompute` swaps the suggestions in with one short
commit and without a status change (see services/demo_service/coaching_recompute.py
for the state it keeps, the compare-and-set rules and what happens to verdicts).
A demo the replay upgrade still owes a current replay waits for it, so it is
recomputed once, on that replay.

Real work always wins: the pass only starts when the queue is empty, and a
message waiting once the replay is loaded hands the attempt back uncounted. A
match deleted meanwhile stops it without a failure. A failed recompute keeps the
old suggestions, records a compact error code (no messages, no stack traces) and
is retried later with a doubling delay, up to
`settings.coaching_recompute_max_attempts`; attempts a dead worker never finished
count towards that limit too.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.analyzer import analyze_replay
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.demo import Demo
from app.services.demo_service import CoachingRecomputeClaim, DemoGoneError, DemoService, coaching_event_rows

COACHING_RECOMPUTE_INTERVAL_SECONDS = 30
# Demos looked at per pass when the first ones cannot be claimed (another worker
# took them); only one is ever recomputed.
COACHING_RECOMPUTE_CANDIDATES = 3

Analyzer = Callable[[dict[str, Any]], list[dict[str, Any]]]
SessionFactory = Callable[[], AbstractContextManager[Session]]

_last_pass = 0.0


def recompute_stale_coaching(
    *,
    should_yield: Callable[[], bool] = lambda: False,
    on_tick: Callable[[], None] | None = None,
    session_factory: SessionFactory = SessionLocal,
    analyze: Analyzer = analyze_replay,
    force: bool = False,
) -> str | None:
    """One bounded pass: recompute at most one demo; returns what happened to it.

    `on_tick` keeps the worker's lease and heartbeat fresh between the steps.
    `analyze` is the analyzer (injected for tests). Rate-limited like the other
    idle-tick backstops; `force` skips the limiter for tests.
    """
    global _last_pass

    if not settings.coaching_recompute_enabled:
        return None
    now = time.monotonic()
    if not force and now - _last_pass < COACHING_RECOMPUTE_INTERVAL_SECONDS:
        return None
    _last_pass = now
    if should_yield():
        return None

    with session_factory() as db:
        service = DemoService.for_internal(db)
        claim: CoachingRecomputeClaim | None = None
        for demo_id in service.demo_ids_due_for_coaching_recompute(limit=COACHING_RECOMPUTE_CANDIDATES):
            claim = service.claim_coaching_recompute(demo_id)
            if claim is not None:
                break
        if claim is None:
            return None
        outcome = _recompute(
            db,
            service,
            claim,
            analyze=analyze,
            should_yield=should_yield,
            on_tick=on_tick or _no_tick,
        )
    print(f"Coaching recompute for demo {claim.demo_id}: {outcome}", flush=True)
    return outcome


def _recompute(
    db: Session,
    service: DemoService,
    claim: CoachingRecomputeClaim,
    *,
    analyze: Analyzer,
    should_yield: Callable[[], bool],
    on_tick: Callable[[], None],
) -> str:
    on_tick()
    try:
        demo = db.query(Demo).filter(Demo.id == claim.demo_id).one_or_none()
        if demo is None:
            return "gone"
        expected_replay_key = demo.replay_storage_key
        # End the read transaction first: nothing is held while the replay is
        # read and analyzed. The detached row keeps the fields the read needs.
        db.expunge(demo)
        db.rollback()
        stored = service.load_replay_blob(demo)
    except Exception:
        db.rollback()
        if service.demo_missing(claim.demo_id):
            return "gone"
        return _failed(service, claim, "REPLAY_UNREADABLE")
    if stored is None:
        if service.demo_missing(claim.demo_id):
            return "gone"
        return _failed(service, claim, "REPLAY_UNREADABLE")
    if stored.get("demoId") != claim.demo_id:
        return _failed(service, claim, "REPLAY_ID_MISMATCH")

    on_tick()
    if should_yield():
        del stored
        service.release_coaching_recompute(claim)
        return "yielded"
    try:
        rows = coaching_event_rows(analyze(stored), claim.demo_id)
    except Exception:
        return _failed(service, claim, "ANALYZE_FAILED")
    finally:
        # A full match's replay is tens of MB in memory; drop it before the write.
        del stored

    on_tick()
    try:
        outcome = service.complete_coaching_recompute(
            claim,
            expected_replay_key=expected_replay_key,
            events=rows,
        )
    except DemoGoneError:
        return "gone"
    except Exception:
        return _failed(service, claim, "RECOMPUTE_WRITE_FAILED")
    if outcome == "changed":
        # The demo moved on meanwhile (a replay rewrite, a retry, another
        # worker): not this demo's fault, so not an attempt.
        service.release_coaching_recompute(claim)
    return outcome


def _failed(service: DemoService, claim: CoachingRecomputeClaim, error_code: str) -> str:
    try:
        service.record_coaching_recompute_failure(claim, error_code)
    except Exception:
        # The claim already counted the attempt and holds the demo for
        # CLAIM_HOLD_SECONDS; the next pass after that retries it.
        pass
    return f"failed:{error_code}"


def _no_tick() -> None:
    """No lease or heartbeat to keep (direct calls and tests)."""


def reset_recompute_state() -> None:
    """Forget the rate limit (tests)."""
    global _last_pass
    _last_pass = 0.0
