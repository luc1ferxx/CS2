from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import Settings, settings
from app.services.auth_service import AuthService, get_auth_service


DEV_OWNER_HEADER = "X-Dev-User-Id"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class SessionCsrfMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
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
        if (
            self.settings.auth_mode == "production"
            and request.method not in SAFE_METHODS
            and not request.url.path.startswith("/render-worker/")
        ):
            session_token = request.cookies.get(self.settings.auth_session_cookie_name)
            if session_token:
                owner_id = self.auth_service_factory().resolve_session(session_token)
                if owner_id is None:
                    return _protect_browser_response(request, JSONResponse(
                        status_code=401,
                        content={"detail": "Authentication required"},
                        headers={"WWW-Authenticate": "Session"},
                    ))
                request.state.authenticated_owner_id = owner_id
                if request.headers.get("origin") not in self.settings.runtime_cors_origins:
                    return _protect_browser_response(request, JSONResponse(
                        status_code=403,
                        content={"detail": "Untrusted request origin"},
                    ))
        response = await call_next(request)
        return _protect_browser_response(request, response)


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
        path == "/demos"
        or path.startswith("/demos/")
        or path.startswith("/uploads/")
        or path.startswith("/auth/")
        or path == "/diagnostics"
    )
