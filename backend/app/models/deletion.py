"""Hard-deletion bookkeeping: the storage purge outbox and the content-free upload ledger.

Both tables are created by the versioned migration in app/migrations/runner.py
(`create_all` only fills them in for tests that skip migrations), so a change
here needs a matching migration.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class DeletionTask(Base):
    """Storage still to purge after rows were hard-deleted.

    Inserted in the same transaction as the row deletes, so a crash between the
    commit and the purge leaves a task instead of silent orphans. `demo_id` NULL
    means a whole account: that task also deletes any of the owner's demo rows
    created at or before `cutoff_at`, and sweeps only objects older than it.
    A task is removed once `final_sweep_after` has passed and a sweep found
    nothing left. `next_attempt_at` schedules and leases the next pass.
    """

    __tablename__ = "deletion_tasks"
    __table_args__ = (Index("ix_deletion_tasks_next_attempt_at", "next_attempt_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    demo_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_sweep_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class UploadLedger(Base):
    """One row per demo creation, kept 24 hours: the daily upload quota counts these.

    Deliberately content-free (no demo id, name or file) and not linked to
    `demos`, so deleting a demo does not refill the quota.
    """

    __tablename__ = "upload_ledger"
    __table_args__ = (Index("ix_upload_ledger_owner_created_at", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
