import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings, settings
from app.core.upload_slots import is_upload_part_request
from app.services.auth_service import ActiveSession, AuthService, get_auth_service

DEV_OWNER_HEADER = "X-Dev-User-Id"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

logger = logging.getLogger(__name__)


class SessionCsrfMiddleware(BaseHTTPMiddleware):
    """Production only: resolves the session cookie for the owner dependency and slides its idle window.

    Writes need a valid session and an exact trusted Origin (401/403 here);
    reads that carry the cookie are resolved too, so `get_current_owner_id`
    reuses the result and a session kept in use by polling is renewed. The
    one renewal per request happens here, after the route has answered.
    """

    def __init__(
        self,
        app: ASGIApp,
        runtime_settings: Settings = settings,
        auth_service_factory: Callable[[], AuthService] = get_auth_service,
    ):
        super().__init__(app)
        self.settings = runtime_settings
        self.auth_service_factory = auth_service_factory

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        service: AuthService | None = None
        session: ActiveSession | None = None
        session_token: str | None = None
        if (
            self.settings.auth_mode == "production"
            and not request.url.path.startswith("/render-worker/")
            # A chunked-upload part authenticates with its session's upload
            # token, never the cookie; the route checks the token and Origin.
            # Matched on the raw scope path the router sees: `url.path` goes
            # through urlsplit, which silently drops tabs and newlines.
            and not is_upload_part_request(request.method, request.scope.get("path", ""))
        ):
            session_token = request.cookies.get(self.settings.auth_session_cookie_name)
            if request.method not in SAFE_METHODS:
                service = self.auth_service_factory()
                session = service.resolve_active_session(session_token)
                if session is None:
                    return _protect_browser_response(request, JSONResponse(
                        status_code=401,
                        content={"detail": "Authentication required"},
                        headers={"WWW-Authenticate": "Session"},
                    ))
                request.state.authenticated_owner_id = session.owner_id
                if request.headers.get("origin") not in self.settings.runtime_cors_origins:
                    return _protect_browser_response(request, JSONResponse(
                        status_code=403,
                        content={"detail": "Untrusted request origin"},
                    ))
            elif session_token:
                # Off the event loop, like the owner dependency that used to do it for reads.
                service = self.auth_service_factory()
                session = await run_in_threadpool(_resolve_for_read, service, session_token)
                if session is not None:
                    request.state.authenticated_owner_id = session.owner_id
        response = await call_next(request)
        if (
            service is not None
            and session is not None
            and session_token is not None
            # Sign-in, logout and account deletion set or clear this cookie
            # themselves; their answer stands.
            and not _sets_cookie(response, self.settings.auth_session_cookie_name)
        ):
            lifetime = await run_in_threadpool(_renew_quietly, service, session_token, session)
            if lifetime is not None:
                set_session_cookie(response, self.settings, session_token, max_age=lifetime)
        return _protect_browser_response(request, response)


def _resolve_for_read(service: AuthService, session_token: str) -> ActiveSession | None:
    try:
        return service.resolve_active_session(session_token)
    except Exception:
        # A read decides nothing here: the route's owner dependency resolves
        # again and fails exactly as it did before.
        return None


def _renew_quietly(service: AuthService, session_token: str, session: ActiveSession) -> int | None:
    try:
        return service.renew_session(session_token, session)
    except Exception:
        # The session keeps what it has left; the next request tries again.
        logger.warning("Session renewal was unavailable")
        return None


def _sets_cookie(response: Response, cookie_name: str) -> bool:
    prefix = f"{cookie_name}="
    return any(value.startswith(prefix) for value in response.headers.getlist("set-cookie"))


def set_session_cookie(response: Response, runtime_settings: Settings, session_token: str, *, max_age: int) -> None:
    """The session cookie, with the same attributes on sign-in and on every renewal."""
    response.set_cookie(
        runtime_settings.auth_session_cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=runtime_settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def normalize_owner_id(owner_id: str | None = None) -> str:
    fallback = settings.dev_user_id if settings.auth_mode in {"development", "test"} else ""
    normalized = (owner_id or fallback).strip()
    if not normalized:
        raise ValueError("An explicit owner id is required")
    if len(normalized) > 64:
        raise ValueError("owner id must be 64 characters or fewer")
    return normalized


def get_current_owner_id(
    request: Request,
    x_dev_user_id: Annotated[str | None, Header(alias=DEV_OWNER_HEADER)] = None,
    service: AuthService = Depends(get_auth_service),
) -> str:
    mode = service.settings.auth_mode
    if mode in {"development", "test"}:
        normalized = (x_dev_user_id or service.settings.dev_user_id).strip()
        if not normalized or len(normalized) > 64:
            raise HTTPException(status_code=400, detail="Invalid development owner id")
        return normalized
    if mode != "production":
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    owner_id = getattr(request.state, "authenticated_owner_id", None)
    if owner_id is None:
        owner_id = service.resolve_session(
            request.cookies.get(service.settings.auth_session_cookie_name)
        )
    if owner_id is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return owner_id


def require_trusted_origin(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> None:
    if service.settings.auth_mode != "production":
        return
    owner_id = getattr(request.state, "authenticated_owner_id", None)
    if owner_id is None:
        owner_id = service.resolve_session(
            request.cookies.get(service.settings.auth_session_cookie_name)
        )
    if owner_id is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    origin = request.headers.get("origin")
    if origin not in service.settings.runtime_cors_origins:
        raise HTTPException(status_code=403, detail="Untrusted request origin")


def _protect_browser_response(request: Request, response: Response) -> Response:
    if not _is_private_browser_path(request.url.path):
        return response
    response.headers["Cache-Control"] = "private, no-store"
    vary = {
        value.strip()
        for value in response.headers.get("Vary", "").split(",")
        if value.strip()
    }
    vary.update({"Cookie", "Origin"})
    response.headers["Vary"] = ", ".join(sorted(vary, key=str.lower))
    return response


def _is_private_browser_path(path: str) -> bool:
    return (
        path in ("/demos", "/diagnostics")
        or path.startswith(("/demos/", "/uploads/", "/auth/", "/steam/"))
    )
