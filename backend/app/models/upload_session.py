"""Chunked .dem upload sessions (S22): one row per resumable upload until `complete` creates the demo.

The table is created by the versioned migration in app/migrations/runner.py
(`create_all` only fills it in for tests that skip migrations), so a change here
needs a matching migration. Which parts arrived is not stored here: the staging
directory (`app.services.storage.UploadStagingStore`) is the source of truth.
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

UPLOAD_SESSION_STATES = ("open", "completing", "completed", "failed")
# States that hold the owner's single active-upload slot (`active_owner_id`).
UPLOAD_SESSION_ACTIVE_STATES = ("open", "completing")


def utc_now() -> datetime:
    return datetime.now(UTC)


class UploadSession(Base):
    """One resumable upload, scoped to `owner_id`.

    Database constraints (names as in the migration), so a write that breaks
    the state machine fails with IntegrityError instead of leaving bad rows:

    - ck_upload_sessions_state: state IN ('open', 'completing', 'completed', 'failed')
    - ck_upload_sessions_active_owner:
        (state IN ('open', 'completing') AND active_owner_id IS NOT NULL
         AND active_owner_id = owner_id)
        OR (state IN ('completed', 'failed') AND active_owner_id IS NULL)
      i.e. every UPDATE to completed/failed must also set active_owner_id = NULL,
      and going back to open keeps it.
    - uq_upload_sessions_active_owner_id: UNIQUE (active_owner_id), NULLs never
      collide; this is the "one open/completing upload per owner" rule, so a
      second insert for the same owner raises IntegrityError.
    - ck_upload_sessions_completing: state != 'completing'
        OR (pending_demo_id IS NOT NULL AND lease_until IS NOT NULL)
    - ck_upload_sessions_completed: state != 'completed' OR demo_id IS NOT NULL
    - ck_upload_sessions_failed: state != 'failed' OR error_code IS NOT NULL
    - ck_upload_sessions_sizes: file_size > 0 AND part_size > 0 AND part_count > 0
    - ck_upload_sessions_token: length(token_sha256) = 64

    There is no foreign key to accounts or demos: deletion removes these rows
    explicitly (docs/data_deletion_v1.md). `token_sha256` is the hex sha256 of
    the per-session upload token; the token itself is never stored.
    """

    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint("active_owner_id", name="uq_upload_sessions_active_owner_id"),
        CheckConstraint(
            "state IN ('open', 'completing', 'completed', 'failed')",
            name="ck_upload_sessions_state",
        ),
        CheckConstraint(
            "(state IN ('open', 'completing') AND active_owner_id IS NOT NULL "
            "AND active_owner_id = owner_id) "
            "OR (state IN ('completed', 'failed') AND active_owner_id IS NULL)",
            name="ck_upload_sessions_active_owner",
        ),
        CheckConstraint(
            "state != 'completing' OR (pending_demo_id IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_upload_sessions_completing",
        ),
        CheckConstraint(
            "state != 'completed' OR demo_id IS NOT NULL",
            name="ck_upload_sessions_completed",
        ),
        CheckConstraint(
            "state != 'failed' OR error_code IS NOT NULL",
            name="ck_upload_sessions_failed",
        ),
        CheckConstraint(
            "file_size > 0 AND part_size > 0 AND part_count > 0",
            name="ck_upload_sessions_sizes",
        ),
        CheckConstraint(
            "length(token_sha256) = 64",
            name="ck_upload_sessions_token",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    active_owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pending_demo_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    demo_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
