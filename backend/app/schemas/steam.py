from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SteamConnectionStatus = Literal[
    "disconnected",
    "connected",
    "syncing",
    "caught_up",
    "retry_wait",
    "authorization_required",
    "error",
]
SteamMatchStatus = Literal[
    "discovered",
    "demo_pending",
    "downloading",
    "parsing",
    "ready",
    "unavailable",
]


class SteamConnectionCredentials(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    game_auth_code: str = Field(min_length=10, max_length=64)
    initial_match_sharing_code: str = Field(min_length=34, max_length=34)

    @field_validator("game_auth_code")
    @classmethod
    def validate_game_auth_code(cls, value: str) -> str:
        from app.services.steam_match_service import validate_game_auth_code

        return validate_game_auth_code(value)

    @field_validator("initial_match_sharing_code")
    @classmethod
    def validate_match_sharing_code(cls, value: str) -> str:
        from app.services.steam_match_service import validate_match_sharing_code

        return validate_match_sharing_code(value)


class SteamConnectionView(BaseModel):
    connected: bool
    status: SteamConnectionStatus
    credentials_configured: bool
    scheduled_sync_enabled: bool
    last_sync_started_at: datetime | None = None
    last_sync_completed_at: datetime | None = None
    next_retry_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    demo_import_available: bool
    demo_source_provider: str
    manual_upload_supported: bool = True


class SteamMatchView(BaseModel):
    id: str
    status: SteamMatchStatus
    source: Literal["steam_match_history"] = "steam_match_history"
    discovered_at: datetime
    updated_at: datetime
    demo_id: str | None = None
    provider_id: str | None = None
    map_name: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)
    ct_round_wins: int | None = Field(default=None, ge=0, le=100)
    t_round_wins: int | None = Field(default=None, ge=0, le=100)
    players: list[str] | None = Field(default=None, max_length=20)
    import_error_code: str | None = None
    import_error_message: str | None = None
    import_retryable: bool = False
    parser_dispatch_pending: bool = False
    manual_upload_supported: bool = True


class SteamSyncResult(BaseModel):
    discovered_count: int = Field(ge=0, le=20)
    caught_up: bool
    limit_reached: bool
    status: SteamConnectionStatus
