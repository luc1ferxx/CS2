from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class DemoJob(Base):
    __tablename__ = "demo_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    demo_id: Mapped[str] = mapped_column(ForeignKey("demos.id"), index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # When this row last entered "queued" -- which is NOT created_at. A requeue
    # (reclaim, or a person clicking retry) reuses the row and leaves created_at
    # on the original creation, so a sweep that aged a queued row by created_at
    # would judge every retried clip by how old the clip is rather than by how
    # long it has been waiting, and kill the retry before a worker could claim
    # it. Every DemoJob is created "queued", so the default covers new rows and
    # only the requeue paths have to restamp it.
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=True
    )

    demo = relationship("Demo", back_populates="jobs")
