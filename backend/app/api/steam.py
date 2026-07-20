import json
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id, require_trusted_origin
from app.core.config import settings
from app.core.database import get_db
from app.core.redis import get_redis_client
from app.models.steam import SteamMatch
from app.schemas.steam import (
    SteamConnectionCredentials,
    SteamConnectionView,
    SteamMatchView,
    SteamSyncResult,
)
from app.services.steam_match_service import (
    SteamAuthorizationRequiredError,
    SteamConnectionConflictError,
    SteamConnectionRequiredError,
    SteamIdentityRequiredError,
    SteamMatchService,
    SteamMatchSyncConfigurationError,
    SteamSyncInProgressError,
    SteamSyncRetryError,
    SteamUpstreamProtocolError,
)
from app.services.steam_demo_import_service import (
    SteamDemoImportFailedError,
    SteamDemoImportInProgressError,
    SteamDemoImportNotFoundError,
    SteamDemoImportService,
    SteamDemoSourceUnavailableError,
    steam_match_import_retryable,
    steam_match_parser_dispatch_retryable,
    utc_now as steam_import_utc_now,
)
from app.services.demo_source_provider import demo_source_provider_from_settings
from app.services.secure_demo_downloader import secure_demo_downloader_from_settings
from app.services.steam_demo_download_limiter import (
    steam_demo_download_limiter_from_settings,
)
from app.services.steam_sync_rate_limit import (
    SteamSyncRateLimitError,
    SteamSyncRateLimiter,
    SteamSyncRateLimitUnavailableError,
)


class SensitiveSteamRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, StarletteResponse]]:
        original_handler = super().get_route_handler()

        async def sanitized_handler(request: Request) -> StarletteResponse:
            try:
                return await original_handler(request)
            except RequestValidationError:
                # Pydantic's default 422 body includes the rejected input value.
                # Steam authorization inputs must never be reflected by the API.
                raise HTTPException(
                    status_code=422,
                    detail="Steam request validation failed",
                ) from None

        return sanitized_handler


router = APIRouter(
    prefix="/steam",
    tags=["steam"],
    route_class=SensitiveSteamRoute,
)


def get_steam_match_service(
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> SteamMatchService:
    return SteamMatchService(
        db,
        owner_id=owner_id,
        sync_rate_limiter=SteamSyncRateLimiter(get_redis_client()),
    )


def get_steam_demo_import_service(
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> SteamDemoImportService:
    provider = demo_source_provider_from_settings(settings)
    if not provider.available:
        return SteamDemoImportService(
            db,
            owner_id=owner_id,
            provider=provider,
        )
    redis_client = get_redis_client()
    return SteamDemoImportService(
        db,
        owner_id=owner_id,
        provider=provider,
        downloader=secure_demo_downloader_from_settings(settings),
        download_limiter=steam_demo_download_limiter_from_settings(
            redis_client,
            settings,
        ),
        redis_client=redis_client,
    )


@router.get("/connection", response_model=SteamConnectionView)
def get_connection(
    service: SteamMatchService = Depends(get_steam_match_service),
) -> SteamConnectionView:
    return _connection_view(service)


@router.post(
    "/connection/credentials",
    response_model=SteamConnectionView,
    dependencies=[Depends(require_trusted_origin)],
)
def save_connection_credentials(
    credentials: SteamConnectionCredentials,
    service: SteamMatchService = Depends(get_steam_match_service),
) -> SteamConnectionView:
    try:
        service.save_credentials(
            game_auth_code=credentials.game_auth_code,
            initial_match_sharing_code=credentials.initial_match_sharing_code,
        )
    except SteamIdentityRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SteamConnectionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SteamSyncInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SteamMatchSyncConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _connection_view(service)


@router.delete(
    "/connection",
    status_code=204,
    dependencies=[Depends(require_trusted_origin)],
)
def delete_connection(
    service: SteamMatchService = Depends(get_steam_match_service),
) -> Response:
    service.delete_connection()
    return Response(status_code=204)


@router.post(
    "/sync",
    response_model=SteamSyncResult,
    dependencies=[Depends(require_trusted_origin)],
)
def sync_matches(
    service: SteamMatchService = Depends(get_steam_match_service),
) -> SteamSyncResult:
    try:
        result = service.sync_now()
    except SteamConnectionRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SteamSyncInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SteamAuthorizationRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.error_code,
                "message": str(exc),
            },
        ) from exc
    except SteamSyncRetryError as exc:
        local_status = 429 if exc.upstream_status == 429 else 503
        raise HTTPException(
            status_code=local_status,
            detail="Steam match-history sync is temporarily unavailable",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except SteamSyncRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="Steam match-history sync rate limit exceeded",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except SteamSyncRateLimitUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Steam match-history sync is temporarily unavailable",
        ) from exc
    except SteamMatchSyncConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SteamUpstreamProtocolError as exc:
        raise HTTPException(
            status_code=502,
            detail="Steam returned an invalid match-history response",
        ) from exc
    return SteamSyncResult(
        discovered_count=result.discovered_count,
        caught_up=result.caught_up,
        limit_reached=result.limit_reached,
        status=result.status,
    )


@router.get("/matches", response_model=list[SteamMatchView])
def list_matches(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    service: SteamMatchService = Depends(get_steam_match_service),
) -> list[SteamMatchView]:
    return [
        _match_view(match, db=service.db)
        for match in service.list_matches(limit=limit)
    ]


@router.post(
    "/matches/{match_id}/import",
    response_model=SteamMatchView,
    dependencies=[Depends(require_trusted_origin)],
)
def import_match_demo(
    match_id: Annotated[
        str,
        Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
    service: SteamDemoImportService = Depends(get_steam_demo_import_service),
) -> SteamMatchView:
    try:
        match = service.import_match(match_id)
    except SteamDemoImportNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Steam match was not found") from exc
    except SteamDemoSourceUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
                "manualUploadSupported": exc.manual_upload_supported,
            },
        ) from exc
    except SteamDemoImportInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "demo_import_in_progress",
                "message": str(exc),
                "manualUploadSupported": True,
            },
        ) from exc
    except SteamDemoImportFailedError as exc:
        unavailable_codes = {
            "credential_decryption_failed",
            "demo_bind_failed",
            "demo_download_capacity_full",
            "demo_download_limiter_unavailable",
            "downloader_not_configured",
            "download_limiter_not_configured",
            "parser_dispatch_unavailable",
            "parser_dispatch_state_invalid",
            "parser_dispatch_state_update_failed",
            "provider_failed",
        }
        raise HTTPException(
            status_code=503 if exc.code in unavailable_codes else 502,
            detail={
                "code": exc.code,
                "message": str(exc),
                "manualUploadSupported": True,
            },
        ) from exc
    return _match_view(match, db=service.db)


def _connection_view(service: SteamMatchService) -> SteamConnectionView:
    provider = demo_source_provider_from_settings(service.settings)
    connection = service.get_connection()
    if connection is None:
        return SteamConnectionView(
            connected=False,
            status="disconnected",
            credentials_configured=False,
            scheduled_sync_enabled=False,
            demo_import_available=provider.available,
            demo_source_provider=provider.provider_id,
        )
    return SteamConnectionView(
        connected=True,
        status=connection.status,
        credentials_configured=True,
        scheduled_sync_enabled=service.settings.steam_scheduled_sync_enabled,
        last_sync_started_at=connection.last_sync_started_at,
        last_sync_completed_at=connection.last_sync_completed_at,
        next_retry_at=connection.next_retry_at,
        last_error_code=connection.last_error_code,
        last_error_message=connection.last_error_message,
        demo_import_available=provider.available,
        demo_source_provider=provider.provider_id,
    )


def _match_view(match: object, *, db: Session | None = None) -> SteamMatchView:
    ready = getattr(match, "status", None) == "ready" and bool(
        getattr(match, "demo_id", None)
    )
    return SteamMatchView(
        id=getattr(match, "id"),
        status=getattr(match, "status"),
        discovered_at=getattr(match, "discovered_at"),
        updated_at=getattr(match, "updated_at"),
        demo_id=getattr(match, "demo_id", None),
        provider_id=getattr(match, "provider_id", None),
        map_name=getattr(match, "map_name", None) if ready else None,
        duration_seconds=(
            getattr(match, "duration_seconds", None) if ready else None
        ),
        ct_round_wins=getattr(match, "ct_round_wins", None) if ready else None,
        t_round_wins=getattr(match, "t_round_wins", None) if ready else None,
        players=(
            _safe_player_names(getattr(match, "players_json", None))
            if ready
            else None
        ),
        import_error_code=getattr(match, "last_import_error_code", None),
        import_error_message=getattr(match, "last_import_error_message", None),
        import_retryable=(
            steam_match_import_retryable(
                match,
                now=steam_import_utc_now(),
            )
            if isinstance(match, SteamMatch)
            else False
        ),
        parser_dispatch_pending=(
            steam_match_parser_dispatch_retryable(
                db,
                match,
                now=steam_import_utc_now(),
            )
            if db is not None and isinstance(match, SteamMatch)
            else False
        ),
    )


def _safe_player_names(raw_players: object) -> list[str] | None:
    if raw_players is None:
        return None
    try:
        players = json.loads(str(raw_players))
    except (TypeError, ValueError):
        return None
    if not isinstance(players, list):
        return None
    names: list[str] = []
    for player in players:
        if not isinstance(player, dict) or not isinstance(player.get("name"), str):
            continue
        name = "".join(
            character
            for character in player["name"]
            if character.isprintable()
        ).strip()
        name = " ".join(name.split())[:64]
        if name and name not in names:
            names.append(name)
        if len(names) >= 20:
            break
    return names
