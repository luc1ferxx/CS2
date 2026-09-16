from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CoachingEvent(Base):
    __tablename__ = "coaching_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    demo_id: Mapped[str] = mapped_column(ForeignKey("demos.id"), index=True, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    player_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tick_start: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    tick_end: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    structured_context_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    demo = relationship("Demo", back_populates="coaching_events")
