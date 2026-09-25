"""Production-only upload quotas: per-owner daily and in-flight caps plus a global parse cap.

Counts come from the demos table. It is authoritative where the Redis queue is
not: that queue also carries render jobs and deliberate re-sends, and Redis has
no persistence here. Every check is a no-op outside AUTH_MODE=production, and a
limit of 0 turns that limit off. Callers that queue a demo run the authoritative
check and their commit inside `parse_admission`, so concurrent requests cannot
all pass on the same count.
"""

import math
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.core.config import Settings, settings
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
        # Archived demos still count: archiving must not refill the quota.
        recent = (
            self.db.query(Demo.created_at)
            .filter(
                Demo.owner_id == owner_id,
                Demo.created_at > now - UPLOAD_QUOTA_WINDOW,
            )
            .order_by(Demo.created_at.desc())
            .limit(limit)
            .all()
        )
        if len(recent) < limit:
            return
        # The owner is back under the limit once the limit-th newest upload
        # leaves the window; with exactly `limit` uploads that is the oldest.
        blocking = _as_utc(recent[-1][0])
        seconds = (blocking + UPLOAD_QUOTA_WINDOW - now).total_seconds()
        raise UploadQuotaExceeded(
            429,
            "upload_daily_limit",
            "Daily upload limit reached. Try again later.",
            max(1, min(math.ceil(seconds), 86_400)),
        )

    def _active_demo_count(self, owner_id: str | None = None) -> int:
        query = self.db.query(func.count(Demo.id)).filter(
            Demo.status.in_(ACTIVE_DEMO_STATUSES)
        )
        if owner_id is not None:
            query = query.filter(Demo.owner_id == owner_id)
        return int(query.scalar() or 0)


@contextmanager
def parse_admission(db: Session, runtime_settings: Settings = settings) -> Iterator[None]:
    """Serialize a parse-capacity count with the commit that makes a demo active.

    Wrap the authoritative quota check and that commit, and nothing that can
    wait on the network (artifact checks, queue dispatch): every other upload
    and retry in the process waits on this lock meanwhile. A process-wide lock
    covers this API process; on PostgreSQL a transaction-scoped advisory lock
    taken in `db` also covers other API processes until the commit (or the
    rollback on failure) ends the transaction. That relies on READ COMMITTED,
    the default, so the count sees every demo admitted before the lock. The
    block must end its transaction before it exits. Quotas are production-only,
    so elsewhere this takes no lock at all.
    """
    if runtime_settings.auth_mode != "production":
        yield
        return
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
    anything, and `parse_admission` rolls the transaction back.
    """
    with parse_admission(db, runtime_settings):
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


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive; the stored value is UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
