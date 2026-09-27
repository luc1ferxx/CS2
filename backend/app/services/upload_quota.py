"""Production-only upload quotas: per-owner daily and in-flight caps plus a global parse cap.

The in-flight caps count active rows of the demos table. It is authoritative
where the Redis queue is not: that queue also carries render jobs and
deliberate re-sends, and Redis has no persistence here. The daily cap counts
the content-free `upload_ledger` instead, written in the same transaction as
every demo creation (upload and Steam import) and pruned after 24 hours: a
demo row can be hard-deleted, and deleting must not refill the quota. Every
check is a no-op outside AUTH_MODE=production, and a limit of 0 turns that
limit off. Callers that queue a demo run the authoritative check and their
commit inside `parse_admission`, so concurrent requests cannot all pass on the
same count.
"""

import math
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.core.config import Settings, settings
from app.models.deletion import UploadLedger
from app.models.demo import Demo, utc_now
from app.services.demo_service.constants import ACTIVE_DEMO_STATUSES

UPLOAD_QUOTA_WINDOW = timedelta(hours=24)
PARSE_CAPACITY_RETRY_AFTER_SECONDS = 60
PARSE_ADMISSION_LOCK_ID = 7_302_202_609_250_001

_parse_admission_lock = threading.Lock()


class UploadQuotaExceeded(Exception):
    # Deliberately not a ValueError: the parse-retry route maps ValueError to 409.
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retry_after_seconds: int,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds

    def response_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryAfterSeconds": self.retry_after_seconds,
        }

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content={"detail": self.response_detail()},
            headers={
                "Cache-Control": "private, no-store",
                "Retry-After": str(self.retry_after_seconds),
            },
        )


@dataclass(frozen=True)
class UploadQuotaSnapshot:
    """What the dashboard shows before an upload: None limits mean the limit is off."""

    daily_limit: int | None
    daily_used: int
    daily_reset_seconds: int | None
    active_limit: int | None
    active_count: int


class UploadQuotaService:
    def __init__(
        self,
        db: Session,
        runtime_settings: Settings = settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db = db
        self.settings = runtime_settings
        self.clock = clock

    def check_new_upload(self, owner_id: str) -> None:
        if self.settings.auth_mode != "production":
            return
        # The upload route re-checks with its prepared demo pending in this
        # session; that demo must not count against its own admission.
        with self.db.no_autoflush:
            self._check_parse_capacity(owner_id)
            self._check_daily_uploads(owner_id)

    def snapshot(self, owner_id: str) -> UploadQuotaSnapshot:
        """The owner's counts against the limits `check_new_upload` enforces.

        Advisory only: the upload route re-checks authoritatively. The global
        parse cap is shared by every owner and is not reported here.
        """
        active_count = self._active_demo_count(owner_id)
        if self.settings.auth_mode != "production":
            return UploadQuotaSnapshot(None, 0, None, None, active_count)
        daily_limit = self.settings.demo_upload_daily_limit or None
        daily_used = 0
        daily_reset_seconds: int | None = None
        if daily_limit:
            now = self.clock()
            recent = self._recent_uploads(owner_id, now, daily_limit)
            daily_used = len(recent)
            if daily_used >= daily_limit:
                daily_reset_seconds = _seconds_until_slot_frees(recent[-1][0], now)
        return UploadQuotaSnapshot(
            daily_limit=daily_limit,
            daily_used=daily_used,
            daily_reset_seconds=daily_reset_seconds,
            active_limit=self.settings.demo_active_parse_limit or None,
            active_count=active_count,
        )

    def check_parse_retry(self, owner_id: str) -> None:
        # A retry reuses its demo row, so only the in-flight caps apply.
        if self.settings.auth_mode != "production":
            return
        self._check_parse_capacity(owner_id)

    def _check_parse_capacity(self, owner_id: str) -> None:
        global_limit = self.settings.parse_queue_global_limit
        if global_limit and self._active_demo_count() >= global_limit:
            raise UploadQuotaExceeded(
                503,
                "parse_queue_full",
                "The parse queue is full. Try again in a few minutes.",
                PARSE_CAPACITY_RETRY_AFTER_SECONDS,
            )
        active_limit = self.settings.demo_active_parse_limit
        if active_limit and self._active_demo_count(owner_id) >= active_limit:
            raise UploadQuotaExceeded(
                429,
                "active_parse_limit",
                "Too many of your demos are still processing. Try again once one finishes.",
                PARSE_CAPACITY_RETRY_AFTER_SECONDS,
            )

    def _check_daily_uploads(self, owner_id: str) -> None:
        limit = self.settings.demo_upload_daily_limit
        if not limit:
            return
        now = self.clock()
        recent = self._recent_uploads(owner_id, now, limit)
        if len(recent) < limit:
            return
        raise UploadQuotaExceeded(
            429,
            "upload_daily_limit",
            "Daily upload limit reached. Try again later.",
            _seconds_until_slot_frees(recent[-1][0], now),
        )

    def _recent_uploads(self, owner_id: str, now: datetime, limit: int) -> list[Any]:
        # The ledger, not demo rows: archiving or deleting a demo must not
        # refill the quota.
        return (
            self.db.query(UploadLedger.created_at)
            .filter(
                UploadLedger.owner_id == owner_id,
                UploadLedger.created_at > now - UPLOAD_QUOTA_WINDOW,
            )
            .order_by(UploadLedger.created_at.desc())
            .limit(limit)
            .all()
        )

    def _active_demo_count(self, owner_id: str | None = None) -> int:
        query = self.db.query(func.count(Demo.id)).filter(
            Demo.status.in_(ACTIVE_DEMO_STATUSES)
        )
        if owner_id is not None:
            query = query.filter(Demo.owner_id == owner_id)
        return int(query.scalar() or 0)


@contextmanager
def parse_admission(
    db: Session,
    runtime_settings: Settings = settings,
    *,
    serialize_without_limits: bool = False,
) -> Iterator[None]:
    """Serialize a parse-capacity count with the commit that makes a demo active.

    Wrap the authoritative quota check and that commit, and nothing that can
    wait on the network (artifact checks, queue dispatch) or need another
    pooled connection once the commit has released this one (a reload, a
    cleanup query): every other upload and retry in the process waits on this
    lock meanwhile. A process-wide lock covers this API process; on PostgreSQL
    a transaction-scoped advisory lock taken in `db` also covers other API
    processes until the commit (or the rollback on failure) ends the
    transaction. That relies on READ COMMITTED, the default, so the count sees
    every demo admitted before the lock. The block must end its transaction
    before it exits. Quotas are production-only, so elsewhere this takes no
    lock at all, and with every limit off there is no count to serialize
    unless the caller asks (`serialize_without_limits`).
    """
    if runtime_settings.auth_mode != "production":
        yield
        return
    if not serialize_without_limits and not _any_parse_limit(runtime_settings):
        yield
        return
    # Check out this session's connection before waiting: the requests queued
    # on the lock hold theirs, so a holder that needed a checkout (the advisory
    # lock, the count, the commit's flush) could wait out the pool behind them.
    db.connection()
    with _parse_admission_lock:
        try:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": PARSE_ADMISSION_LOCK_ID},
                )
            yield
        except BaseException:
            db.rollback()
            raise


@contextmanager
def retry_admission(
    db: Session,
    owner_id: str,
    runtime_settings: Settings = settings,
) -> Iterator[None]:
    """The admission a parse retry commits under: the in-flight caps, checked inside it.

    A refusal raises UploadQuotaExceeded on entry, before the retry has changed
    anything, and `parse_admission` rolls the transaction back. The lock is
    taken even with every limit off: the retry's active-job re-read relies on
    it to queue a demo once when the same retry arrives twice.
    """
    with parse_admission(db, runtime_settings, serialize_without_limits=True):
        UploadQuotaService(db, runtime_settings).check_parse_retry(owner_id)
        yield


def upload_quota_precheck(
    session_factory: Callable[[], Session],
    runtime_settings: Settings = settings,
) -> Callable[[str], UploadQuotaExceeded | None]:
    """Build the pre-body check MultipartRequestLimitMiddleware runs for /uploads/demo."""

    def precheck(owner_id: str) -> UploadQuotaExceeded | None:
        db = session_factory()
        try:
            UploadQuotaService(db, runtime_settings).check_new_upload(owner_id)
        except UploadQuotaExceeded as exc:
            return exc
        finally:
            db.close()
        return None

    return precheck


def record_upload(db: Session, owner_id: str, *, now: datetime | None = None) -> UploadLedger:
    """Add the ledger row for one demo creation to the caller's transaction (commit is the caller's)."""
    entry = UploadLedger(id=str(uuid.uuid4()), owner_id=owner_id, created_at=now or utc_now())
    db.add(entry)
    return entry


def prune_upload_ledger(db: Session, *, now: datetime | None = None) -> int:
    """Drop ledger rows older than the quota window; they no longer count for anything."""
    cutoff = (now or utc_now()) - UPLOAD_QUOTA_WINDOW
    try:
        result = db.execute(
            delete(UploadLedger)
            .where(UploadLedger.created_at <= cutoff)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return int(getattr(result, "rowcount", 0) or 0)


def _any_parse_limit(runtime_settings: Settings) -> bool:
    return bool(
        runtime_settings.demo_upload_daily_limit
        or runtime_settings.demo_active_parse_limit
        or runtime_settings.parse_queue_global_limit
    )


def _seconds_until_slot_frees(blocking_created_at: datetime, now: datetime) -> int:
    # The owner is back under the limit once the limit-th newest upload
    # leaves the window; with exactly `limit` uploads that is the oldest.
    seconds = (_as_utc(blocking_created_at) + UPLOAD_QUOTA_WINDOW - now).total_seconds()
    return max(1, min(math.ceil(seconds), 86_400))


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive; the stored value is UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
