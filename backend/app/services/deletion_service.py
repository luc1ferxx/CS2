"""Hard deletion of one demo or a whole account, and the durable storage purge behind it.

The protocol (docs/data_deletion_v1.md):

1. One short database transaction deletes the rows and inserts a
   `DeletionTask` (the outbox) naming what storage still has to go. There is
   no storage I/O inside it: a delete holds row locks that parse retries and
   uploads may be queued behind.
   * Demo: lock `demo_jobs WHERE demo_id ORDER BY id`, then the owner's demo
     row, read the storage keys under that lock, then unlink the Steam match
     and delete coaching_feedback, coaching_events, demo_jobs and demos in
     that order (Postgres has no ON DELETE for the jobs and events, and the
     SQLite tests may run without foreign keys, so nothing relies on cascades).
   * Account (production only): lock the account row, write the owner session
     revocation marker (so every device is signed out even if the commit is
     slow), delete the first batch of demos through the same per-demo step,
     then the owner's feedback, Steam matches and connection, identities and
     account, plus one owner-wide task with `cutoff_at = now`.
   Deadlocks and serialization failures retry the whole transaction.
2. After the commit the request makes one immediate, best-effort purge pass.
3. The parse worker's idle tick (and the API once at startup) drains due
   tasks: exact references first, then a sweep of every state and kind under
   the demo's (or the owner's) prefix. A task is removed only once
   `final_sweep_after` has passed and a sweep found nothing -- the window
   covers a parse or render that was still running and writes late.

Creators are fenced separately: `account_exists_for_write` is taken inside the
upload and Steam-import commits, so a racing commit either lands before the
account delete (and is deleted with it) or finds no account.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.account import Account, ExternalIdentity
from app.models.coaching import CoachingEvent, CoachingFeedback
from app.models.deletion import DeletionTask
from app.models.demo import Demo
from app.models.job import DemoJob
from app.models.steam import SteamConnection, SteamMatch
from app.services.demo_service.constants import RENDER_CLIP_STALE_AFTER_SECONDS
from app.services.demo_service.replay_response_cache import replay_response_cache
from app.services.storage import (
    ArtifactReferenceError,
    ArtifactStore,
    ArtifactStoreError,
    LocalStorageService,
    artifact_store_from_settings,
)

logger = logging.getLogger(__name__)

# Whole-transaction attempts for a delete that hit a deadlock or serialization failure.
DELETE_TRANSACTION_ATTEMPTS = 3
# The final sweep waits out anything that may still write for a deleted demo:
# a parse is reclaimed after PARSE_RECLAIM_AFTER_SECONDS (1800 s) and a render
# clip after RENDER_CLIP_STALE_AFTER_SECONDS (1800 s).
MIN_FINAL_SWEEP_DELAY_SECONDS = 35 * 60
FINAL_SWEEP_MARGIN_SECONDS = 5 * 60
# How long a claimed task is hidden from other drainers while one pass runs.
TASK_LEASE = timedelta(minutes=10)
# After the final sweep still found objects, confirm with another pass this soon.
RESWEEP_DELAY = timedelta(seconds=60)
MAX_RETRY_DELAY_SECONDS = 3600
# Demos deleted inside the account transaction; the owner task deletes the rest.
ACCOUNT_DEMO_BATCH = 50
# Demo rows one owner-task pass deletes before handing back to the drain.
OWNER_TASK_DEMO_LIMIT = 200
# Tasks one drain call processes; each pass lists 20 prefixes.
DRAIN_BATCH = 5
MAX_ARTIFACT_REFS = 500
_REF_PREFIXES = ("artifact://", "local://")
_RETRYABLE_SQLSTATES = {"40P01", "40001"}

# Idle-tick rate limits (monotonic seconds).
DRAIN_INTERVAL_SECONDS = 60
QUARANTINE_CLEANUP_INTERVAL_SECONDS = 3600

_T = TypeVar("_T")


def utc_now() -> datetime:
    return datetime.now(UTC)


class AccountDeletedError(Exception):
    """The session's account no longer exists; a creator must not commit for it."""

    code = "account_deleted"
    message = "This account was deleted."


class _NothingToDelete(Exception):
    pass


def final_sweep_delay(runtime_settings: Settings = settings) -> timedelta:
    return timedelta(
        seconds=max(
            MIN_FINAL_SWEEP_DELAY_SECONDS,
            int(runtime_settings.parse_reclaim_after_seconds) + FINAL_SWEEP_MARGIN_SECONDS,
            RENDER_CLIP_STALE_AFTER_SECONDS + FINAL_SWEEP_MARGIN_SECONDS,
        )
    )


def account_exists_for_write(
    db: Session,
    owner_id: str,
    runtime_settings: Settings = settings,
) -> bool:
    """The production creators' fence, taken inside the commit transaction.

    `FOR KEY SHARE` on Postgres: concurrent creators do not block each other,
    but an account delete (`FOR UPDATE`) waits for them, or they wait for it
    and then find the row gone. SQLite ignores the lock clause. Outside
    production there are no account rows to check.
    """
    if runtime_settings.auth_mode != "production":
        return True
    statement = (
        select(Account.owner_id)
        .where(Account.owner_id == owner_id)
        .with_for_update(read=True, key_share=True)
    )
    return db.execute(statement).first() is not None


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive; the stored value is UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_retryable_conflict(exc: DBAPIError) -> bool:
    original = getattr(exc, "orig", None)
    code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
    if code in _RETRYABLE_SQLSTATES:
        return True
    return isinstance(exc, OperationalError) and "database is locked" in str(original)


def _safe_error(exc: BaseException) -> str:
    # Storage errors carry deliberately safe messages; anything else is named
    # by type only, so no path, key or credential reaches the table.
    if isinstance(exc, ArtifactStoreError):
        return f"{type(exc).__name__}: {str(exc)[:160]}"[:255]
    return type(exc).__name__[:255]


def _collect_refs(values: Iterable[Any]) -> set[str]:
    found: set[str] = set()

    def walk(value: Any, depth: int) -> None:
        if depth > 8 or len(found) >= MAX_ARTIFACT_REFS:
            return
        if isinstance(value, str):
            if value.startswith(_REF_PREFIXES) and len(value) <= 1024:
                found.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    for value in values:
        walk(value, 0)
    return found


def _job_metadata_refs(raw_metadata: Iterable[str | None]) -> set[str]:
    parsed: list[Any] = []
    for raw in raw_metadata:
        if not raw:
            continue
        try:
            parsed.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return _collect_refs(parsed)


@dataclass(frozen=True)
class _TaskSnapshot:
    id: str
    owner_id: str
    demo_id: str | None
    artifact_refs: tuple[str, ...]
    cutoff_at: datetime | None
    final_sweep_after: datetime
    attempts: int


class DeletionService:
    def __init__(
        self,
        db: Session,
        *,
        artifact_store: ArtifactStore | None = None,
        legacy_storage: LocalStorageService | None = None,
        runtime_settings: Settings = settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db = db
        self._artifact_store = artifact_store
        self._legacy_storage = legacy_storage
        self.settings = runtime_settings
        self.clock = clock

    @property
    def artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            self._artifact_store = artifact_store_from_settings()
        return self._artifact_store

    @property
    def legacy_storage(self) -> LocalStorageService:
        if self._legacy_storage is None:
            self._legacy_storage = LocalStorageService.from_settings()
        return self._legacy_storage

    def _now(self) -> datetime:
        return _as_utc(self.clock())

    # -- demo -------------------------------------------------------------------

    def delete_demo(self, owner_id: str, demo_id: str) -> bool:
        """Hard-delete one of the owner's demos. False when it is not theirs or already gone."""

        def transaction() -> str:
            now = self._now()
            task_id = self._delete_demo_rows(
                owner_id,
                demo_id,
                now=now,
                next_attempt_at=now + TASK_LEASE,
            )
            if task_id is None:
                raise _NothingToDelete
            return task_id

        task_id = self._run_transaction(transaction)
        if task_id is None:
            return False
        # Committed: drop this process's cached replay responses of the demo.
        replay_response_cache.evict_demo(demo_id)
        # This request holds the task's lease; the drain takes over if it dies.
        self.run_task(task_id)
        return True

    def _delete_demo_rows(
        self,
        owner_id: str,
        demo_id: str,
        *,
        now: datetime,
        next_attempt_at: datetime,
    ) -> str | None:
        """Delete one demo's rows and add its outbox task, inside the caller's transaction."""
        db = self.db
        owned = db.execute(
            select(Demo.id).where(Demo.id == demo_id, Demo.owner_id == owner_id)
        ).first()
        if owned is None:
            return None
        # Jobs first, then the demo: render claims and results lock job then
        # demo, so this is the order least likely to deadlock (and a deadlock
        # retries the whole transaction).
        db.execute(
            select(DemoJob.id)
            .where(DemoJob.demo_id == demo_id)
            .order_by(DemoJob.id)
            .with_for_update()
        ).all()
        locked = db.execute(
            select(Demo.source_storage_key, Demo.replay_storage_key)
            .where(Demo.id == demo_id, Demo.owner_id == owner_id)
            .with_for_update()
        ).first()
        if locked is None:
            return None
        # Read under the demo lock: a parse commit or replay rewrite that won
        # the lock has already moved these keys forward.
        job_metadata = db.execute(
            select(DemoJob.metadata_json).where(DemoJob.demo_id == demo_id)
        ).scalars().all()
        refs = _collect_refs([locked.source_storage_key, locked.replay_storage_key])
        refs |= _job_metadata_refs(job_metadata)

        db.execute(
            update(SteamMatch)
            .where(SteamMatch.demo_id == demo_id, SteamMatch.owner_id == owner_id)
            .values(
                demo_id=None,
                status="discovered",
                import_run_id=None,
                import_lease_expires_at=None,
                import_attempts=0,
                provider_id=None,
                last_import_error_code=None,
                last_import_error_message=None,
                import_started_at=None,
                import_completed_at=None,
                parser_dispatched_at=None,
                parser_dispatched_job_id=None,
                map_name=None,
                duration_seconds=None,
                ct_round_wins=None,
                t_round_wins=None,
                players_json=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        for model in (CoachingFeedback, CoachingEvent, DemoJob):
            db.execute(
                delete(model)
                .where(model.demo_id == demo_id)
                .execution_options(synchronize_session=False)
            )
        deleted = db.execute(
            delete(Demo)
            .where(Demo.id == demo_id, Demo.owner_id == owner_id)
            .execution_options(synchronize_session=False)
        )
        if getattr(deleted, "rowcount", 1) == 0:
            # A concurrent delete won (SQLite has no row locks to make us wait);
            # it inserted the task, so there is nothing left for this one.
            return None
        task_id = str(uuid.uuid4())
        db.add(
            DeletionTask(
                id=task_id,
                owner_id=owner_id,
                demo_id=demo_id,
                artifact_refs=sorted(refs)[:MAX_ARTIFACT_REFS],
                cutoff_at=None,
                final_sweep_after=now + final_sweep_delay(self.settings),
                next_attempt_at=next_attempt_at,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
        )
        return task_id

    # -- account ----------------------------------------------------------------

    def delete_account(
        self,
        owner_id: str,
        *,
        revoke_sessions: Callable[[str], object],
    ) -> bool:
        """Delete an account and everything it owns. False when there is no such account.

        `revoke_sessions` writes the owner revocation marker; it runs after the
        account row is locked and before the commit, and a failure there aborts
        the deletion (a deleted account must never keep a live session).
        """

        def transaction() -> str:
            db = self.db
            account = db.execute(
                select(Account.owner_id)
                .where(Account.owner_id == owner_id)
                .with_for_update()
            ).first()
            if account is None:
                raise _NothingToDelete
            revoke_sessions(owner_id)
            now = self._now()
            final_sweep_after = now + final_sweep_delay(self.settings)
            demo_ids = db.execute(
                select(Demo.id)
                .where(Demo.owner_id == owner_id)
                .order_by(Demo.created_at, Demo.id)
                .limit(ACCOUNT_DEMO_BATCH)
            ).scalars().all()
            for demo_id in demo_ids:
                # The owner-wide sweep below purges these objects now; each
                # demo's own task only waits for the late-writer final sweep.
                self._delete_demo_rows(
                    owner_id,
                    demo_id,
                    now=now,
                    next_attempt_at=final_sweep_after,
                )
            # Explicit deletes: the Steam rows cascade from accounts only on
            # Postgres, and feedback has no foreign key to accounts at all.
            for model in (CoachingFeedback, SteamMatch, SteamConnection, ExternalIdentity, Account):
                db.execute(
                    delete(model)
                    .where(model.owner_id == owner_id)
                    .execution_options(synchronize_session=False)
                )
            task_id = str(uuid.uuid4())
            db.add(
                DeletionTask(
                    id=task_id,
                    owner_id=owner_id,
                    demo_id=None,
                    artifact_refs=[],
                    cutoff_at=now,
                    final_sweep_after=final_sweep_after,
                    next_attempt_at=now + TASK_LEASE,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            return task_id

        task_id = self._run_transaction(transaction)
        if task_id is None:
            return False
        # Committed: drop this process's cached replay responses of every demo
        # of the owner, including those the owner task deletes later.
        replay_response_cache.evict_owner(owner_id)
        self.run_task(task_id)
        return True

    # -- transactions -----------------------------------------------------------

    def _run_transaction(self, operation: Callable[[], _T]) -> _T | None:
        """Run and commit one delete transaction; None when there was nothing to delete."""
        for attempt in range(1, DELETE_TRANSACTION_ATTEMPTS + 1):
            try:
                result = operation()
                self.db.commit()
                return result
            except _NothingToDelete:
                self.db.rollback()
                return None
            except DBAPIError as exc:
                self.db.rollback()
                if attempt < DELETE_TRANSACTION_ATTEMPTS and _is_retryable_conflict(exc):
                    time.sleep(0.05 * attempt)
                    continue
                raise
            except BaseException:
                self.db.rollback()
                raise
        return None  # pragma: no cover - the loop always returns or raises

    # -- outbox -----------------------------------------------------------------

    def drain_due_tasks(self, *, limit: int = DRAIN_BATCH) -> int:
        """Run one pass for each due task (up to `limit`); never raises for a task's own failure."""
        task_ids = self._claim_due_task_ids(limit)
        for task_id in task_ids:
            self.run_task(task_id)
        return len(task_ids)

    def _claim_due_task_ids(self, limit: int) -> list[str]:
        now = self._now()
        statement = (
            select(DeletionTask.id)
            .where(DeletionTask.next_attempt_at <= now)
            .order_by(DeletionTask.next_attempt_at, DeletionTask.id)
            .limit(limit)
        )
        if self.db.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        try:
            task_ids = list(self.db.execute(statement).scalars().all())
            if task_ids:
                self.db.execute(
                    update(DeletionTask)
                    .where(DeletionTask.id.in_(task_ids))
                    .values(next_attempt_at=now + TASK_LEASE, updated_at=now)
                    .execution_options(synchronize_session=False)
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return task_ids

    def run_task(self, task_id: str) -> None:
        """One purge pass for a task this caller holds the lease on. Never raises."""
        snapshot: _TaskSnapshot | None = None
        try:
            snapshot = self._load_task(task_id)
            if snapshot is None:
                return
            found, complete = self._purge_pass(snapshot)
            now = self._now()
            if complete and found == 0 and now >= snapshot.final_sweep_after:
                self.db.execute(
                    delete(DeletionTask)
                    .where(DeletionTask.id == task_id)
                    .execution_options(synchronize_session=False)
                )
            else:
                if not complete:
                    next_attempt_at = now
                elif now < snapshot.final_sweep_after:
                    next_attempt_at = snapshot.final_sweep_after
                else:
                    next_attempt_at = now + RESWEEP_DELAY
                self.db.execute(
                    update(DeletionTask)
                    .where(DeletionTask.id == task_id)
                    .values(next_attempt_at=next_attempt_at, last_error=None, updated_at=now)
                    .execution_options(synchronize_session=False)
                )
            self.db.commit()
        except Exception as exc:
            self._record_failure(task_id, snapshot, exc)

    def _load_task(self, task_id: str) -> _TaskSnapshot | None:
        task = self.db.execute(
            select(DeletionTask).where(DeletionTask.id == task_id)
        ).scalar_one_or_none()
        if task is None:
            self.db.rollback()
            return None
        refs = task.artifact_refs if isinstance(task.artifact_refs, list) else []
        snapshot = _TaskSnapshot(
            id=task.id,
            owner_id=task.owner_id,
            demo_id=task.demo_id,
            artifact_refs=tuple(str(ref) for ref in refs if isinstance(ref, str)),
            cutoff_at=_as_utc(task.cutoff_at) if task.cutoff_at is not None else None,
            final_sweep_after=_as_utc(task.final_sweep_after),
            attempts=int(task.attempts or 0),
        )
        # End the read before any storage I/O: nothing is held while purging.
        self.db.commit()
        return snapshot

    def _record_failure(
        self,
        task_id: str,
        snapshot: _TaskSnapshot | None,
        exc: BaseException,
    ) -> None:
        logger.warning("Deletion task %s pass failed: %s", task_id, type(exc).__name__)
        try:
            self.db.rollback()
            attempts = (snapshot.attempts if snapshot is not None else 0) + 1
            now = self._now()
            delay = min(60 * 2 ** min(attempts - 1, 10), MAX_RETRY_DELAY_SECONDS)
            self.db.execute(
                update(DeletionTask)
                .where(DeletionTask.id == task_id)
                .values(
                    attempts=DeletionTask.attempts + 1,
                    last_error=_safe_error(exc),
                    next_attempt_at=now + timedelta(seconds=delay),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            self.db.commit()
        except Exception:
            # The lease expires on its own and a later drain retries.
            try:
                self.db.rollback()
            except Exception:
                pass

    def _purge_pass(self, task: _TaskSnapshot) -> tuple[int, bool]:
        """Delete what the task names; returns (objects or rows found, listing complete)."""
        found = 0
        complete = True
        cutoff = None
        if task.demo_id is None:
            cutoff = self._owner_sweep_cutoff(task)
            deleted_rows, rows_complete = self._delete_owner_demos(task, cutoff)
            found += deleted_rows
            complete = complete and rows_complete
        self._purge_exact_refs(task)
        result = self.artifact_store.purge_prefix(
            owner_id=task.owner_id,
            demo_id=task.demo_id,
            created_before=cutoff,
        )
        found += result.found
        return found, complete and result.complete

    def _owner_sweep_cutoff(self, task: _TaskSnapshot) -> datetime | None:
        """How far back an owner-wide sweep reaches.

        `cutoff_at` spares an account re-created under the same owner id (OIDC
        ids are derived from the subject). Once the late-writer window is over
        and no account holds the id, everything under it belonged to the
        deleted account -- including an object written after the cutoff whose
        own cleanup failed, such as an upload fenced at its commit whose
        best-effort discard did not go through, or a crash between promote and
        discard. Those are swept up to now, so the task never finishes over
        them. Before the window ends the cutoff stays: an upload that was
        streaming during the delete is still refused at its fence, not broken
        halfway by the sweep.
        """
        if task.cutoff_at is None:
            return None
        now = self._now()
        if now < task.final_sweep_after:
            return task.cutoff_at
        account = self.db.execute(
            select(Account.owner_id).where(Account.owner_id == task.owner_id)
        ).first()
        self.db.commit()
        return task.cutoff_at if account is not None else now

    def _delete_owner_demos(self, task: _TaskSnapshot, cutoff: datetime | None) -> tuple[int, bool]:
        """Demos of a deleted account the account transaction did not reach (or that raced it)."""
        deleted = 0
        while deleted < OWNER_TASK_DEMO_LIMIT:
            query = select(Demo.id).where(Demo.owner_id == task.owner_id)
            if cutoff is not None:
                query = query.where(
                    or_(Demo.created_at <= cutoff, Demo.created_at.is_(None))
                )
            demo_ids = self.db.execute(
                query.order_by(Demo.created_at, Demo.id).limit(ACCOUNT_DEMO_BATCH)
            ).scalars().all()
            self.db.commit()
            if not demo_ids:
                return deleted, True
            progressed = False
            for demo_id in demo_ids:

                def transaction(demo_id: str = demo_id) -> str:
                    now = self._now()
                    task_id = self._delete_demo_rows(
                        task.owner_id,
                        demo_id,
                        now=now,
                        next_attempt_at=now + final_sweep_delay(self.settings),
                    )
                    if task_id is None:
                        raise _NothingToDelete
                    return task_id

                if self._run_transaction(transaction) is not None:
                    deleted += 1
                    progressed = True
            if not progressed:
                return deleted, True
        return deleted, False

    def _purge_exact_refs(self, task: _TaskSnapshot) -> None:
        artifact_refs: list[str] = []
        for ref in task.artifact_refs:
            if ref.startswith("artifact://"):
                try:
                    parsed = self.artifact_store.parse_reference(ref)
                except (ArtifactReferenceError, ValueError):
                    continue
                # Only what the task is scoped to: a malformed row must never
                # purge another owner's (or another demo's) object.
                if parsed.owner_id != task.owner_id or (
                    task.demo_id is not None and parsed.demo_id != task.demo_id
                ):
                    continue
                artifact_refs.append(ref)
            elif ref.startswith("local://") and self.settings.auth_mode != "production":
                self.legacy_storage.purge_key(ref)
        if artifact_refs:
            self.artifact_store.purge_references(artifact_refs)


# -- idle-tick maintenance ------------------------------------------------------

_last_drain = 0.0
_last_quarantine_cleanup = 0.0


def drain_deletion_outbox(
    *,
    force: bool = False,
    session_factory: Callable[[], Session] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> int:
    """The worker idle tick's outbox drain: at most every DRAIN_INTERVAL_SECONDS."""
    global _last_drain

    now = time.monotonic()
    if not force and now - _last_drain < DRAIN_INTERVAL_SECONDS:
        return 0
    _last_drain = now
    factory = session_factory or _default_session_factory()
    with factory() as db:
        processed = DeletionService(db, artifact_store=artifact_store).drain_due_tasks()
    if processed:
        logger.info("Ran %s deletion task pass(es)", processed)
    return processed


def run_hourly_storage_maintenance(
    *,
    force: bool = False,
    session_factory: Callable[[], Session] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> int:
    """Hourly: aborted-upload quarantine cleanup (older than its 1 h TTL) and the upload ledger prune.

    Before this ran on the worker, quarantine objects of a crashed upload
    waited for the next API restart.
    """
    global _last_quarantine_cleanup

    now = time.monotonic()
    if not force and now - _last_quarantine_cleanup < QUARANTINE_CLEANUP_INTERVAL_SECONDS:
        return 0
    _last_quarantine_cleanup = now
    from app.services.artifact_intake import ArtifactIntakePolicy, ArtifactIntakeService
    from app.services.upload_quota import prune_upload_ledger

    # Independent chores: one failing (storage down, database down) must not
    # skip the other. The first failure is re-raised for the caller to log.
    failure: Exception | None = None
    cleaned = 0
    try:
        store = artifact_store or artifact_store_from_settings()
        cleaned = ArtifactIntakeService(
            store,
            policy=ArtifactIntakePolicy(
                max_source_bytes=settings.max_demo_upload_bytes,
                stream_chunk_bytes=settings.upload_chunk_bytes,
                quarantine_ttl_seconds=settings.artifact_quarantine_ttl_seconds,
            ),
        ).cleanup_abandoned()
    except Exception as exc:
        failure = exc
    try:
        factory = session_factory or _default_session_factory()
        with factory() as db:
            prune_upload_ledger(db)
    except Exception as exc:
        failure = failure or exc
    if failure is not None:
        raise failure
    return cleaned


def _default_session_factory() -> Callable[[], Session]:
    from app.core.database import SessionLocal

    return SessionLocal
