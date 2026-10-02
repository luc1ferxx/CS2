"""Recomputing a completed demo's coaching suggestions under the current rules, in place.

Suggestions are stored when a demo is parsed, by the rules of that day. When
the analyzer's output changes, `COACHING_RULES_VERSION` (app/analysis/version.py)
is bumped, and the worker's idle-tick backstop (`app/workers/coaching_recompute.py`)
re-runs the analyzer on each older demo's stored replay, one demo at a time;
this component is the database side of it. Like the replay upgrade it is
invisible in the library: the demo stays `completed` the whole time (no job row,
no status, `completed_at` or `updated_at` change, no upload ledger entry, no new
artifact), and one short commit replaces the demo's coaching events and
`coaching_event_count`. Verdicts are never touched. They are keyed by the
deterministic event id, so a suggestion the new rules emit again keeps its
verdict, and a verdict on one they no longer emit stays in `coaching_feedback`
(no foreign key), neither shown nor counted, until its match or account is
deleted -- or until a later version emits that id again.

State lives in the metadata of the demo's latest parse job (a completed
`real_parse`; mock suggestions are not analyzer output and are never
recomputed), never in a new column:

* `coachingRulesVersion` -- the rules version of the stored suggestions.
  `complete_parse_job` writes it for every new parse; demos analyzed before it
  existed have none and count as older. The replay upgrade keeps it (it does
  not run the analyzer), and a demo the replay upgrade still owes a current
  replay waits for it, so it is recomputed once, on that replay.
* `coachingRecompute` -- `{rulesVersion, attempts, nextAttemptAt,
  lastErrorCode, lastFailedAt, exhausted}` while a recompute is owed. A claim
  counts the attempt before the analyzer runs (it runs in the worker process,
  so a crash there still spends it) and pushes `nextAttemptAt` out, which keeps
  a second worker off the demo. A failure keeps the old suggestions and sets the
  retry time (doubling per attempt); after `coaching_recompute_max_attempts` the
  demo keeps its older suggestions. Attempts that never recorded a failure (a
  worker killed mid-recompute) count too: a claim that finds them all spent
  marks the demo exhausted instead. The state belongs to the rules version it
  names, so the next bump starts with a fresh budget.

Every write is a compare-and-set on the job's metadata string, and the swap also
requires the demo to be completed, on the replay the analyzer read and with the
same latest parse job, so a deletion, a user retry, a replay rewrite or another
worker in between wins and nothing is resurrected. Lock order is job, then
demo -- the order a deletion takes them -- and no storage I/O happens inside.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, insert, update

from app.analysis.version import COACHING_RULES_VERSION
from app.core.config import settings
from app.models.coaching import CoachingEvent
from app.models.demo import Demo
from app.models.job import DemoJob
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import _aware_datetime, _job_metadata, _metadata_json, _parse_datetime
from app.services.demo_service.constants import PARSE_JOB_TYPES
from app.services.demo_service.errors import DemoGoneError
from app.services.demo_service.gone import rows_missing

VERSION_KEY = "coachingRulesVersion"
STATE_KEY = "coachingRecompute"
# How long a claim keeps other workers off the demo. The analyzer takes about a
# second per match; a worker that died mid-recompute leaves the demo due again
# after this.
CLAIM_HOLD_SECONDS = 600
# The reason recorded when every attempt was spent without one being recorded.
ABANDONED_ERROR_CODE = "RECOMPUTE_ABANDONED"
# The coaching_events columns an analyzer event fills (demo_id comes from the
# claim, created_at from the column default).
_EVENT_COLUMNS = (
    "id",
    "round_number",
    "player_id",
    "player_name",
    "tick_start",
    "tick_end",
    "category",
    "severity",
    "title",
    "message",
    "structured_context_json",
    "confidence",
)

RecomputeOutcome = Literal["recomputed", "gone", "changed"]


def retry_delay_seconds(attempts: int) -> int:
    base = max(1, settings.coaching_recompute_retry_seconds)
    return base * 2 ** max(0, min(attempts - 1, 10))


def coaching_event_rows(events: Iterable[Mapping[str, Any]], demo_id: str) -> list[dict[str, Any]]:
    """The analyzer's events as `demo_id`'s coaching_events rows; ValueError for an unusable one.

    Checked before the swap so a malformed or duplicated event fails the
    attempt instead of the commit.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("coaching event is not a mapping")
        row = {column: event.get(column) for column in _EVENT_COLUMNS}
        if any(value is None for value in row.values()) or not isinstance(row["structured_context_json"], dict):
            raise ValueError("coaching event is incomplete")
        event_id = str(row["id"])
        if event_id in seen:
            raise ValueError("coaching event id is not unique")
        seen.add(event_id)
        rows.append({**row, "id": event_id, "demo_id": demo_id})
    return rows


@dataclass(frozen=True)
class CoachingRecomputeClaim:
    demo_id: str
    job_id: str
    attempt: int
    # The job metadata before and after the claim: the compare-and-set tokens
    # for releasing the claim and for every later write.
    previous_metadata_json: str
    metadata_json: str


def _recompute_state(metadata: Mapping[str, Any]) -> dict[str, Any]:
    state = metadata.get(STATE_KEY)
    if not isinstance(state, Mapping) or state.get("rulesVersion") != COACHING_RULES_VERSION:
        # None yet, or left by an older rules version: this version starts afresh.
        return {}
    return dict(state)


def _attempts(state: Mapping[str, Any]) -> int:
    value = state.get("attempts")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _needs_recompute(metadata: Mapping[str, Any]) -> bool:
    return (
        metadata.get(VERSION_KEY) != COACHING_RULES_VERSION
        and _recompute_state(metadata).get("exhausted") is not True
    )


def _due(metadata: Mapping[str, Any], now: datetime) -> bool:
    raw = _recompute_state(metadata).get("nextAttemptAt")
    next_attempt = _parse_datetime(raw if isinstance(raw, str) else None)
    return next_attempt is None or _aware_datetime(next_attempt) <= now


def _latest(jobs: Iterable[DemoJob]) -> DemoJob | None:
    # The order of ParseLifecycle.latest_parse_job: newest created_at, then id.
    best: DemoJob | None = None
    for job in jobs:
        if best is None or (_aware_datetime(job.created_at), str(job.id)) > (
            _aware_datetime(best.created_at),
            str(best.id),
        ):
            best = job
    return best


class CoachingRecompute(ServiceComponent):
    """Reached as ``DemoService.recompute``."""

    def demo_ids_due(self, *, limit: int, now: datetime | None = None) -> list[str]:
        """Completed real-parse demos whose suggestions predate the current rules and whose retry is due.

        Non-archived first, newest first. The SQL only narrows the search to
        demos none of whose parse jobs carries the current marker; the latest
        parse job decides. Exhaustion is checked here rather than in SQL: the
        replay upgrade's state can hold the same `"exhausted":true` text.
        """
        moment = _aware_datetime(now)
        marker = f'"{VERSION_KEY}":"{COACHING_RULES_VERSION}"'

        def real_parse_job(*conditions: Any) -> Any:
            return (
                self.db.query(DemoJob.id)
                .filter(DemoJob.demo_id == Demo.id, DemoJob.job_type == "real_parse", *conditions)
                .exists()
            )

        demos = (
            self.db.query(Demo)
            .filter(
                Demo.status == "completed",
                real_parse_job(),
                ~real_parse_job(DemoJob.metadata_json.contains(marker, autoescape=True)),
            )
            .order_by(Demo.archived.asc(), Demo.created_at.desc(), Demo.id.asc())
            .all()
        )
        if not demos:
            return []
        jobs: dict[str, list[DemoJob]] = {}
        for job in (
            self.db.query(DemoJob)
            .filter(DemoJob.demo_id.in_([demo.id for demo in demos]), DemoJob.job_type.in_(PARSE_JOB_TYPES))
            .all()
        ):
            jobs.setdefault(str(job.demo_id), []).append(job)
        due: list[str] = []
        for demo in demos:
            if self._eligible(demo, _latest(jobs.get(str(demo.id), [])), moment):
                due.append(str(demo.id))
                if len(due) >= limit:
                    break
        return due

    def claim(self, demo_id: str, *, now: datetime | None = None) -> CoachingRecomputeClaim | None:
        """Count one attempt and reserve the demo for this worker; None when it is not due."""
        moment = _aware_datetime(now)
        demo = self.db.query(Demo).filter(Demo.id == demo_id).one_or_none()
        job = self._service.parse.latest_parse_job(demo) if demo is not None else None
        if demo is None or job is None or not self._eligible(demo, job, moment):
            self.db.rollback()
            return None
        previous = job.metadata_json or "{}"
        metadata = _job_metadata(job)
        state = _recompute_state(metadata)
        state["rulesVersion"] = COACHING_RULES_VERSION
        job_id = str(job.id)
        if _attempts(state) >= max(1, settings.coaching_recompute_max_attempts):
            # Every attempt was claimed but the last one never recorded how it
            # ended (the worker died): give up like a recorded last failure.
            state.update(
                exhausted=True,
                lastErrorCode=ABANDONED_ERROR_CODE,
                lastFailedAt=moment.isoformat(),
            )
            self._swap_metadata(job_id, previous, _metadata_json({**metadata, STATE_KEY: state}))
            return None
        attempt = _attempts(state) + 1
        state.update(
            attempts=attempt,
            nextAttemptAt=(moment + timedelta(seconds=CLAIM_HOLD_SECONDS)).isoformat(),
        )
        claimed_json = _metadata_json({**metadata, STATE_KEY: state})
        if not self._swap_metadata(job_id, previous, claimed_json):
            return None
        return CoachingRecomputeClaim(
            demo_id=demo_id,
            job_id=job_id,
            attempt=attempt,
            previous_metadata_json=previous,
            metadata_json=claimed_json,
        )

    def release(self, claim: CoachingRecomputeClaim) -> bool:
        """Give the attempt back (the worker yielded to real work, or the demo moved meanwhile).

        When something else rewrote the job's metadata after the claim, only the
        recompute state is put back, and only while it still is this claim's.
        """
        if self._swap_metadata(claim.job_id, claim.metadata_json, claim.previous_metadata_json):
            return True
        row = self.db.query(DemoJob.metadata_json).filter(DemoJob.id == claim.job_id).one_or_none()
        self.db.rollback()
        if row is None or row[0] is None:
            return False
        current_json = str(row[0])
        current = _loads(current_json)
        if current.get(STATE_KEY) != _loads(claim.metadata_json).get(STATE_KEY):
            return False
        previous_state = _loads(claim.previous_metadata_json).get(STATE_KEY)
        if previous_state is None:
            current.pop(STATE_KEY, None)
        else:
            current[STATE_KEY] = previous_state
        return self._swap_metadata(claim.job_id, current_json, _metadata_json(current))

    def record_failure(
        self,
        claim: CoachingRecomputeClaim,
        error_code: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Keep the old suggestions, note a compact reason, schedule the retry or give up."""
        moment = _aware_datetime(now)
        metadata = _loads(claim.metadata_json)
        state = _recompute_state(metadata)
        state.update(
            rulesVersion=COACHING_RULES_VERSION,
            attempts=claim.attempt,
            lastErrorCode=error_code[:64],
            lastFailedAt=moment.isoformat(),
            nextAttemptAt=(moment + timedelta(seconds=retry_delay_seconds(claim.attempt))).isoformat(),
        )
        if claim.attempt >= max(1, settings.coaching_recompute_max_attempts):
            state["exhausted"] = True
        metadata[STATE_KEY] = state
        return self._swap_metadata(claim.job_id, claim.metadata_json, _metadata_json(metadata))

    def complete(
        self,
        claim: CoachingRecomputeClaim,
        *,
        expected_replay_key: str | None,
        events: list[dict[str, Any]],
    ) -> RecomputeOutcome:
        """Replace the demo's suggestions with `events` (rows from `coaching_event_rows`).

        "changed" means the demo moved on while the analyzer ran (another
        replay, another status, another parse job or claim): nothing is
        written. "gone" means it was deleted. Raises for failures with the rows
        still present, after rolling back.
        """
        demo_id, job_id = claim.demo_id, claim.job_id
        outcome: RecomputeOutcome = "changed"
        try:
            job = (
                self.db.query(DemoJob)
                .filter(DemoJob.id == job_id, DemoJob.demo_id == demo_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            demo = (
                self.db.query(Demo)
                .filter(Demo.id == demo_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if demo is None:
                outcome = "gone"
            elif (
                job is not None
                and demo.status == "completed"
                and demo.replay_storage_key == expected_replay_key
                and job.status == "completed"
                and job.metadata_json == claim.metadata_json
                and _row_id(self._service.parse.latest_parse_job(demo)) == job_id
            ):
                self.db.execute(
                    delete(CoachingEvent)
                    .where(CoachingEvent.demo_id == demo_id)
                    .execution_options(synchronize_session=False)
                )
                if events:
                    self.db.execute(insert(CoachingEvent), events)
                self.db.execute(
                    update(Demo)
                    .where(Demo.id == demo_id)
                    .values(
                        coaching_event_count=len(events),
                        # Derived data, not a user-visible change.
                        updated_at=Demo.updated_at,
                    )
                    .execution_options(synchronize_session=False)
                )
                metadata = _loads(claim.metadata_json)
                metadata.pop(STATE_KEY, None)
                metadata[VERSION_KEY] = COACHING_RULES_VERSION
                job.metadata_json = _metadata_json(metadata)
                self.db.commit()
                outcome = "recomputed"
            if outcome != "recomputed":
                self.db.rollback()
        except BaseException as exc:
            self.db.rollback()
            if isinstance(exc, Exception) and (
                isinstance(exc, DemoGoneError) or rows_missing(self.db, demo_id=demo_id)
            ):
                return "gone"
            raise
        return outcome

    def _eligible(self, demo: Demo, job: DemoJob | None, now: datetime) -> bool:
        if demo.status != "completed" or job is None:
            return False
        if job.job_type != "real_parse" or job.status != "completed":
            return False
        metadata = _job_metadata(job)
        if not _needs_recompute(metadata) or not _due(metadata, now):
            return False
        # Recompute once, on the current replay: wait while the background
        # replay upgrade still owes this demo one (pending or retrying).
        return not self._service.upgrade.replay_upgrade_pending(
            demo, job, has_source=bool(demo.source_storage_key)
        )

    def _swap_metadata(self, job_id: str, expected: str, replacement: str) -> bool:
        try:
            result = self.db.execute(
                update(DemoJob)
                .where(DemoJob.id == job_id, DemoJob.metadata_json == expected)
                .values(metadata_json=replacement)
                .execution_options(synchronize_session=False)
            )
            swapped = getattr(result, "rowcount", 0) == 1
            if swapped:
                self.db.commit()
            else:
                self.db.rollback()
            return swapped
        except Exception:
            self.db.rollback()
            raise


def _row_id(job: DemoJob | None) -> str | None:
    return str(job.id) if job is not None else None


def _loads(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
