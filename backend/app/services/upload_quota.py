"""Production-only upload quotas: per-owner daily and in-flight caps plus a global parse cap.

Counts come from the demos table. It is authoritative where the Redis queue is
not: that queue also carries render jobs and deliberate re-sends, and Redis has
no persistence here. Every check is a no-op outside AUTH_MODE=production, and a
limit of 0 turns that limit off.
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.core.config import Settings, settings
from app.models.demo import Demo, utc_now
from app.services.demo_service.constants import ACTIVE_DEMO_STATUSES

UPLOAD_QUOTA_WINDOW = timedelta(hours=24)
PARSE_CAPACITY_RETRY_AFTER_SECONDS = 60


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
