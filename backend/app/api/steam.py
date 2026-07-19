from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id, require_trusted_origin
from app.core.database import get_db
from app.core.redis import get_redis_client
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
        SteamMatchView(
            id=match.id,
            status=match.status,
            discovered_at=match.discovered_at,
            updated_at=match.updated_at,
            demo_id=None,
        )
        for match in service.list_matches(limit=limit)
    ]


def _connection_view(service: SteamMatchService) -> SteamConnectionView:
    connection = service.get_connection()
    if connection is None:
        return SteamConnectionView(
            connected=False,
            status="disconnected",
            credentials_configured=False,
            scheduled_sync_enabled=False,
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
    )
