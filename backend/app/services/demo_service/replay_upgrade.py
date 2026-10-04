"""Upgrading a completed demo's stored replay to the current contract, in place.

A demo parsed before the current replay contract keeps its old replay until it
is re-parsed. The worker's idle-tick backstop (`app/workers/replay_upgrade.py`)
re-parses such demos from their stored `.dem`, one at a time, and this component
is the database side of it. Unlike a user's retry it is invisible in the
library: the demo stays `completed` the whole time (no job row, no status or
`completed_at`/`updated_at` change, no upload ledger entry, no admission lock),
and one commit swaps the replay, `round_count`, the map/tick fields and
`match_summary`. The coaching events are left exactly as they are: the analyzer
does not read the v2 fields, and re-running it would replace suggestions made by
older rules and hide the verdicts on them. The replay's `video` section (render
status, manual MP4 calibration) is carried over by the caller. The old replay
artifact is deleted after that commit through the storage package.

State lives in the metadata of the demo's latest parse job (a completed
`real_parse`), never in a new column:

* `replayContractVersion` -- the contract of the replay that job's demo holds.
  `complete_parse_job` writes it for every new parse; demos parsed before it
  existed have none and count as older.
* `replayUpgrade` -- `{attempts, nextAttemptAt, lastErrorCode, lastFailedAt,
  exhausted}` while an upgrade is owed. A claim counts the attempt before the
  parse starts (so a crashed worker still spends it) and pushes
  `nextAttemptAt` past the parse timeout, which keeps a second worker off the
  demo. A failure sets the retry time (doubling per attempt); after
  `replay_upgrade_max_attempts` the demo is left on its old replay for good.
  Attempts that never recorded a failure (a worker killed mid-upgrade) count
  too: a claim that finds them all spent marks the demo exhausted instead.

Every write is a compare-and-set on the job's metadata string, and the swap
also requires the demo to be completed and still on the replay the caller
read, so a deletion, a user retry, a video update or another worker in between
wins and nothing is resurrected. Lock order is job, then demo -- the order a
deletion takes them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import update

from app.core.config import settings
from app.models.demo import Demo
from app.models.job import DemoJob
from app.parser.replay_contract import REPLAY_CONTRACT_VERSION, replay_contract_is_current
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import _aware_datetime, _job_metadata, _metadata_json, _parse_datetime
from app.services.demo_service.constants import PARSE_JOB_TYPES
from app.services.demo_service.errors import DemoGoneError
from app.services.demo_service.gone import rows_missing
from app.services.demo_service.parse_lifecycle import _match_summary_or_none
from app.services.demo_service.replay_warmer import announce_replay_ready

logger = logging.getLogger(__name__)

VERSION_KEY = "replayContractVersion"
STATE_KEY = "replayUpgrade"
# A claim keeps other workers off the demo for at least this long past the parse
# timeout; a worker that died mid-upgrade leaves the demo due again after it.
CLAIM_GRACE_SECONDS = 300
# The reason recorded when every attempt was spent without one being recorded.
ABANDONED_ERROR_CODE = "UPGRADE_ABANDONED"

UpgradeOutcome = Literal["upgraded", "gone", "changed"]


def retry_delay_seconds(attempts: int) -> int:
    base = max(1, settings.replay_upgrade_retry_seconds)
    return base * 2 ** max(0, min(attempts - 1, 10))


@dataclass(frozen=True)
class ReplayUpgradeClaim:
    demo_id: str
    job_id: str
    attempt: int
    # The job metadata before and after the claim: the compare-and-set tokens
    # for releasing the claim and for every later write.
    previous_metadata_json: str
    metadata_json: str


def _upgrade_state(metadata: Mapping[str, Any]) -> dict[str, Any]:
    state = metadata.get(STATE_KEY)
    return dict(state) if isinstance(state, Mapping) else {}


def _attempts(state: Mapping[str, Any]) -> int:
    value = state.get("attempts")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _needs_upgrade(metadata: Mapping[str, Any]) -> bool:
    return not replay_contract_is_current(metadata.get(VERSION_KEY)) and _upgrade_state(metadata).get("exhausted") is not True


def _due(metadata: Mapping[str, Any], now: datetime) -> bool:
    next_attempt = _parse_datetime(_str_or_none(_upgrade_state(metadata).get("nextAttemptAt")))
    return next_attempt is None or _aware_datetime(next_attempt) <= now


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _latest(jobs: Iterable[tuple[Any, ...]]) -> tuple[Any, ...] | None:
    # The order of ParseLifecycle.latest_parse_job: newest created_at, then id.
    best: tuple[Any, ...] | None = None
    for row in jobs:
        if best is None or (_aware_datetime(row[4]), str(row[0])) > (_aware_datetime(best[4]), str(best[0])):
            best = row
    return best


class ReplayUpgrade(ServiceComponent):
    """Reached as ``DemoService.upgrade``."""

    def replay_upgrade_pending(self, demo: Demo, job: DemoJob | None, *, has_source: bool) -> bool:
        """Whether the background re-parse still owes this completed demo a current replay."""
        if not settings.replay_upgrade_enabled or not has_source or demo.status != "completed":
            return False
        if job is None or job.job_type != "real_parse" or job.status != "completed":
            return False
        return _needs_upgrade(_job_metadata(job))

    def demo_ids_due(self, *, limit: int, now: datetime | None = None) -> list[str]:
        """Completed uploaded demos whose replay is older than the contract and whose retry is due.

        Non-archived first, newest first. The SQL only narrows the search to
        uploaded demos none of whose parse jobs carries the current version
        marker; the latest parse job decides. Exhaustion is checked here rather
        than in SQL: the coaching recompute's state can hold the same
        `"exhausted":true` text.
        """
        moment = _aware_datetime(now)
        marker = f'"{VERSION_KEY}":"{REPLAY_CONTRACT_VERSION}"'

        def real_parse_job(*conditions: Any) -> Any:
            return (
                self.db.query(DemoJob.id)
                .filter(DemoJob.demo_id == Demo.id, DemoJob.job_type == "real_parse", *conditions)
                .exists()
            )

        demo_ids = [
            str(row.id)
            for row in self.db.query(Demo.id)
            .filter(
                Demo.status == "completed",
                Demo.source_storage_key.isnot(None),
                real_parse_job(),
                ~real_parse_job(DemoJob.metadata_json.contains(marker, autoescape=True)),
            )
            .order_by(Demo.archived.asc(), Demo.created_at.desc(), Demo.id.asc())
            .all()
        ]
        if not demo_ids:
            return []
        jobs: dict[str, list[tuple[Any, ...]]] = {}
        for row in (
            self.db.query(
                DemoJob.id,
                DemoJob.demo_id,
                DemoJob.job_type,
                DemoJob.status,
                DemoJob.created_at,
                DemoJob.metadata_json,
            )
            .filter(DemoJob.demo_id.in_(demo_ids), DemoJob.job_type.in_(PARSE_JOB_TYPES))
            .all()
        ):
            jobs.setdefault(str(row[1]), []).append(tuple(row))
        due: list[str] = []
        for demo_id in demo_ids:
            latest = _latest(jobs.get(demo_id, []))
            if latest is None or latest[2] != "real_parse" or latest[3] != "completed":
                continue
            metadata = _loads(latest[5])
            if _needs_upgrade(metadata) and _due(metadata, moment):
                due.append(demo_id)
                if len(due) >= limit:
                    break
        return due

    def claim(self, demo_id: str, *, now: datetime | None = None) -> ReplayUpgradeClaim | None:
        """Count one attempt and reserve the demo for this worker; None when it is not due."""
        moment = _aware_datetime(now)
        demo = self.db.query(Demo).filter(Demo.id == demo_id).one_or_none()
        if demo is None or demo.status != "completed" or not demo.source_storage_key:
            self.db.rollback()
            return None
        job = self._service.parse.latest_parse_job(demo)
        if job is None or job.job_type != "real_parse" or job.status != "completed":
            self.db.rollback()
            return None
        previous = job.metadata_json or "{}"
        metadata = _job_metadata(job)
        if not _needs_upgrade(metadata) or not _due(metadata, moment):
            self.db.rollback()
            return None
        state = _upgrade_state(metadata)
        job_id = str(job.id)
        if _attempts(state) >= max(1, settings.replay_upgrade_max_attempts):
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
        hold_seconds = max(
            retry_delay_seconds(attempt),
            settings.parse_timeout_seconds + CLAIM_GRACE_SECONDS,
        )
        state.update(
            attempts=attempt,
            nextAttemptAt=(moment + timedelta(seconds=hold_seconds)).isoformat(),
        )
        claimed_json = _metadata_json({**metadata, STATE_KEY: state})
        if not self._swap_metadata(job_id, previous, claimed_json):
            return None
        return ReplayUpgradeClaim(
            demo_id=demo_id,
            job_id=job_id,
            attempt=attempt,
            previous_metadata_json=previous,
            metadata_json=claimed_json,
        )

    def release(self, claim: ReplayUpgradeClaim) -> bool:
        """Give the attempt back (the worker yielded to real work, or the replay moved meanwhile)."""
        return self._swap_metadata(claim.job_id, claim.metadata_json, claim.previous_metadata_json)

    def mark_current(self, claim: ReplayUpgradeClaim, version: str) -> bool:
        """The stored replay already is current: record that and drop the upgrade state."""
        metadata = _loads(claim.metadata_json)
        metadata.pop(STATE_KEY, None)
        metadata[VERSION_KEY] = version
        return self._swap_metadata(claim.job_id, claim.metadata_json, _metadata_json(metadata))

    def record_failure(
        self,
        claim: ReplayUpgradeClaim,
        error_code: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Keep the old replay, note a compact reason, schedule the retry or give up."""
        moment = _aware_datetime(now)
        metadata = _loads(claim.metadata_json)
        state = _upgrade_state(metadata)
        exhausted = claim.attempt >= max(1, settings.replay_upgrade_max_attempts)
        state.update(
            attempts=claim.attempt,
            lastErrorCode=error_code[:64],
            lastFailedAt=moment.isoformat(),
            nextAttemptAt=(moment + timedelta(seconds=retry_delay_seconds(claim.attempt))).isoformat(),
        )
        if exhausted:
            state["exhausted"] = True
        metadata[STATE_KEY] = state
        return self._swap_metadata(claim.job_id, claim.metadata_json, _metadata_json(metadata))

    def complete(
        self,
        claim: ReplayUpgradeClaim,
        *,
        expected_replay_key: str | None,
        replay: dict[str, Any],
        team_names: Mapping[str, Any] | None = None,
    ) -> UpgradeOutcome:
        """Swap in the re-parsed replay without touching the demo's status or coaching.

        "changed" means the demo moved on while the parse ran (another replay,
        another status, another claim): nothing is written and the new blob is
        removed. "gone" means it was deleted. Raises for failures with the rows
        still present, after removing the new blob.
        """
        demo_id, job_id = claim.demo_id, claim.job_id
        new_key = self._service.replay.write_replay_blob(demo_id, replay)
        outcome: UpgradeOutcome = "changed"
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
            if job is None or demo is None:
                outcome = "gone" if demo is None else "changed"
            elif (
                demo.status == "completed"
                and demo.replay_storage_key == expected_replay_key
                and job.status == "completed"
                and job.metadata_json == claim.metadata_json
                and _row_id(self._service.parse.latest_parse_job(demo)) == job_id
            ):
                self.db.execute(
                    update(Demo)
                    .where(Demo.id == demo_id)
                    .values(
                        replay_storage_key=new_key,
                        map_name=replay["mapName"],
                        tick_rate=replay["tickRate"],
                        round_count=len(replay["rounds"]),
                        match_summary=_match_summary_or_none(replay, team_names),
                        # Derived data, not a user-visible change.
                        updated_at=Demo.updated_at,
                    )
                    .execution_options(synchronize_session=False)
                )
                metadata = _loads(claim.metadata_json)
                metadata.pop(STATE_KEY, None)
                metadata[VERSION_KEY] = replay.get("contractVersion")
                job.metadata_json = _metadata_json(metadata)
                self.db.commit()
                outcome = "upgraded"
            if outcome != "upgraded":
                self.db.rollback()
        except BaseException as exc:
            self.db.rollback()
            self._service.delete_artifact_safely(new_key)
            if isinstance(exc, Exception) and (
                isinstance(exc, DemoGoneError) or rows_missing(self.db, demo_id=demo_id)
            ):
                return "gone"
            raise
        if outcome != "upgraded":
            self._service.delete_artifact_safely(new_key)
            return outcome
        self._delete_previous_replay(demo_id, expected_replay_key, new_key)
        # Committed: let the API warm the new replay's response (replay_warmer.py).
        announce_replay_ready(demo_id)
        return outcome

    def _delete_previous_replay(self, demo_id: str, previous: str | None, current: str) -> None:
        if previous and previous.startswith("artifact://"):
            if previous != current:
                self._service.delete_artifact_safely(previous)
            return
        # A legacy local replay: the stored key, or the default one a demo
        # without a key was read from.
        legacy = previous or self._service.replay.replay_blob_key(demo_id)
        try:
            if self.storage.replay_key_belongs_to_demo(demo_id, legacy):
                self.storage.purge_key(legacy)
        except Exception:
            logger.warning("old replay of demo %s could not be removed", demo_id)

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
