from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.core.auth import require_trusted_origin
from app.services.auth_service import (
    MAX_RETURN_TO_LENGTH,
    AuthenticationError,
    AuthService,
    get_auth_service,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login(
    return_to: Annotated[str | None, Query(max_length=MAX_RETURN_TO_LENGTH)] = None,
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    if service.settings.auth_mode != "production":
        raise HTTPException(status_code=409, detail="Production sign-in is not enabled")
    started = service.begin_login(return_to)
    response = RedirectResponse(started.authorization_url, status_code=302)
    response.set_cookie(
        service.settings.auth_state_cookie_name,
        started.state,
        max_age=service.settings.auth_login_ttl_seconds,
        httponly=True,
        secure=service.settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/oidc/callback")
def callback(
    request: Request,
    code: str,
    state: str,
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    try:
        grant = service.complete_login(
            code,
            state,
            request.cookies.get(service.settings.auth_state_cookie_name),
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="OIDC callback could not be verified") from exc
    frontend_callback = (
        f"{service.settings.frontend_public_url}/auth/callback?"
        f"{urlencode({'return_to': grant.return_to})}"
    )
    response = RedirectResponse(frontend_callback, status_code=303)
    response.delete_cookie(
        service.settings.auth_state_cookie_name,
        path="/",
        secure=service.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        service.settings.auth_session_cookie_name,
        grant.session_token,
        max_age=grant.max_age,
        httponly=True,
        secure=service.settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/session")
def session(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> dict[str, bool]:
    if service.settings.auth_mode in {"development", "test"}:
        return {"authenticated": True}
    owner_id = service.resolve_session(
        request.cookies.get(service.settings.auth_session_cookie_name)
    )
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"authenticated": True}


@router.post("/logout", status_code=204, dependencies=[Depends(require_trusted_origin)])
def logout(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> Response:
    service.revoke_session(
        request.cookies.get(service.settings.auth_session_cookie_name)
    )
    response = Response(status_code=204)
    response.delete_cookie(
        service.settings.auth_session_cookie_name,
        path="/",
        secure=service.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
