from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


STEAM_CONNECTION_STATUSES = (
    "connected",
    "syncing",
    "caught_up",
    "retry_wait",
    "authorization_required",
    "error",
)
STEAM_MATCH_STATUSES = (
    "discovered",
    "demo_pending",
    "downloading",
    "parsing",
    "ready",
    "unavailable",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SteamConnection(Base):
    __tablename__ = "steam_connections"
    __table_args__ = (
        UniqueConstraint("owner_id", name="uq_steam_connections_owner_id"),
        UniqueConstraint("steam_id64", name="uq_steam_connections_steam_id64"),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_steam_connections_id_owner_id",
        ),
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{status}'" for status in STEAM_CONNECTION_STATUSES)
            + ")",
            name="ck_steam_connections_status",
        ),
        CheckConstraint(
            "length(game_auth_code_nonce) = 12",
            name="ck_steam_connections_game_auth_nonce_length",
        ),
        CheckConstraint(
            "length(game_auth_code_ciphertext) >= 16",
            name="ck_steam_connections_game_auth_ciphertext_length",
        ),
        CheckConstraint(
            "length(known_code_nonce) = 12",
            name="ck_steam_connections_known_code_nonce_length",
        ),
        CheckConstraint(
            "length(known_code_ciphertext) >= 16",
            name="ck_steam_connections_known_code_ciphertext_length",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_steam_connections_consecutive_failures",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("accounts.owner_id", ondelete="CASCADE"),
        nullable=False,
    )
    steam_id64: Mapped[str] = mapped_column(String(17), nullable=False)
    game_auth_code_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    game_auth_code_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    known_code_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    known_code_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="connected", nullable=False)
    sync_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_sync_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    matches: Mapped[list["SteamMatch"]] = relationship(
        "SteamMatch",
        back_populates="connection",
        cascade="all, delete-orphan",
    )


class SteamMatch(Base):
    __tablename__ = "steam_matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connection_id", "owner_id"),
            ("steam_connections.id", "steam_connections.owner_id"),
            ondelete="CASCADE",
            name="fk_steam_matches_connection_owner",
        ),
        UniqueConstraint(
            "owner_id",
            "share_code_hash",
            name="uq_steam_matches_owner_share_code_hash",
        ),
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{status}'" for status in STEAM_MATCH_STATUSES)
            + ")",
            name="ck_steam_matches_status",
        ),
        CheckConstraint(
            "length(share_code_hash) = 64",
            name="ck_steam_matches_share_code_hash_length",
        ),
        CheckConstraint(
            "length(share_code_nonce) = 12",
            name="ck_steam_matches_share_code_nonce_length",
        ),
        CheckConstraint(
            "length(share_code_ciphertext) >= 16",
            name="ck_steam_matches_share_code_ciphertext_length",
        ),
        Index("ix_steam_matches_owner_discovered_at", "owner_id", "discovered_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String(36),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("accounts.owner_id", ondelete="CASCADE"),
        nullable=False,
    )
    share_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    share_code_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    share_code_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="discovered", nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    connection: Mapped[SteamConnection] = relationship(
        "SteamConnection",
        back_populates="matches",
    )
