import json
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import require_trusted_origin
from app.core.database import get_db
from app.core.features import feature_capabilities
from app.services.account_service import (
    AccountConflictError,
    AccountService,
    provider_label,
)
from app.services.auth_service import (
    MAX_RETURN_TO_LENGTH,
    AuthenticationError,
    AuthService,
    get_auth_service,
)
from app.services.deletion_service import DeletionService, account_exists_for_write
from app.services.steam_auth_service import (
    AuthenticationRateLimitError,
    SteamAuthService,
    get_steam_auth_service,
)

router = APIRouter(prefix="/auth", tags=["auth"])
NO_REFERRER_HEADERS = {"Referrer-Policy": "no-referrer"}
ACCOUNT_DELETION_CONFIRMATION = "delete-my-account"
_ACCOUNT_DELETION_BODY_LIMIT_BYTES = 4096


@router.get("/login")
def login(
    return_to: Annotated[str | None, Query(max_length=MAX_RETURN_TO_LENGTH)] = None,
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    if (
        service.settings.auth_mode != "production"
        or service.settings.auth_provider != "oidc"
    ):
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
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if (
        service.settings.auth_mode != "production"
        or service.settings.auth_provider != "oidc"
    ):
        raise HTTPException(status_code=409, detail="OIDC sign-in is not enabled")
    try:
        verified = service.verify_login(
            code,
            state,
            request.cookies.get(service.settings.auth_state_cookie_name),
        )
        account = AccountService(db).resolve_or_create_identity(
            provider=verified.provider,
            subject=verified.subject,
            preferred_owner_id=verified.preferred_owner_id,
        )
        service.revoke_session(
            request.cookies.get(service.settings.auth_session_cookie_name)
        )
        grant = service.create_session(
            account.owner_id,
            max_age=verified.max_age,
            return_to=verified.return_to,
        )
        _require_live_account(db, service, account.owner_id, grant.session_token)
    except AccountConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Identity is already bound",
            headers=NO_REFERRER_HEADERS,
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="OIDC callback could not be verified",
            headers=NO_REFERRER_HEADERS,
        ) from exc
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


@router.get("/steam/login")
def steam_login(
    request: Request,
    return_to: Annotated[str | None, Query(max_length=MAX_RETURN_TO_LENGTH)] = None,
    service: SteamAuthService = Depends(get_steam_auth_service),
) -> RedirectResponse:
    if (
        service.settings.auth_mode != "production"
        or service.settings.auth_provider != "steam"
    ):
        raise HTTPException(status_code=409, detail="Steam sign-in is not enabled")
    try:
        service.enforce_request_limit("login", _request_client_id(request))
        started = service.begin_login(return_to)
    except AuthenticationRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many Steam sign-in requests",
            headers=NO_REFERRER_HEADERS,
        ) from exc
    response = RedirectResponse(started.authorization_url, status_code=302)
    response.set_cookie(
        service.settings.steam_auth_state_cookie_name,
        started.state,
        max_age=service.settings.auth_login_ttl_seconds,
        httponly=True,
        secure=service.settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/steam/callback")
def steam_callback(
    request: Request,
    steam_service: SteamAuthService = Depends(get_steam_auth_service),
    auth_service: AuthService = Depends(get_auth_service),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if (
        steam_service.settings.auth_mode != "production"
        or steam_service.settings.auth_provider != "steam"
    ):
        raise HTTPException(status_code=409, detail="Steam sign-in is not enabled")
    try:
        steam_service.enforce_request_limit(
            "callback",
            _request_client_id(request),
        )
        verified = steam_service.complete_login(
            request.query_params.multi_items(),
            request.cookies.get(steam_service.settings.steam_auth_state_cookie_name),
        )
        # Invite-only beta: refuse before any Account/ExternalIdentity row exists.
        if not auth_service.settings.steam_login_allowed(verified.steam_id):
            auth_service.revoke_session(
                request.cookies.get(auth_service.settings.auth_session_cookie_name)
            )
            return _steam_not_invited_response(steam_service, auth_service)
        account = AccountService(db).resolve_or_create_identity(
            provider="steam",
            subject=verified.steam_id,
            display_name=verified.display_name,
            avatar_url=verified.avatar_url,
        )
        auth_service.revoke_session(
            request.cookies.get(auth_service.settings.auth_session_cookie_name)
        )
        grant = auth_service.create_session(
            account.owner_id,
            max_age=verified.max_age,
            return_to=verified.return_to,
            steam_id=verified.steam_id,
        )
        _require_live_account(db, auth_service, account.owner_id, grant.session_token)
    except AuthenticationRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many Steam callback requests",
            headers=NO_REFERRER_HEADERS,
        ) from exc
    except AccountConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Identity is already bound",
            headers=NO_REFERRER_HEADERS,
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="Steam OpenID callback could not be verified",
            headers=NO_REFERRER_HEADERS,
        ) from exc

    frontend_callback = (
        f"{steam_service.settings.frontend_public_url}/auth/callback?"
        f"{urlencode({'return_to': grant.return_to})}"
    )
    response = RedirectResponse(frontend_callback, status_code=303)
    response.delete_cookie(
        steam_service.settings.steam_auth_state_cookie_name,
        path="/",
        secure=steam_service.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        auth_service.settings.auth_session_cookie_name,
        grant.session_token,
        max_age=grant.max_age,
        httponly=True,
        secure=auth_service.settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Referrer-Policy"] = "no-referrer"
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


@router.get("/me")
def me(
    request: Request,
    service: AuthService = Depends(get_auth_service),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if service.settings.auth_mode in {"development", "test"}:
        return {
            "authenticated": True,
            "account": {
                "displayName": "Local development",
                "avatarUrl": None,
                "provider": "development",
            },
            "capabilities": feature_capabilities(service.settings),
        }
    if service.settings.auth_mode != "production":
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    session_token = request.cookies.get(service.settings.auth_session_cookie_name)
    owner_id = service.resolve_session(session_token)
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    account = AccountService(db).get_account(owner_id)
    if account is None:
        service.revoke_session(session_token)
        raise HTTPException(status_code=401, detail="Authentication required")
    public_provider = provider_label(account.provider)
    account_body: dict[str, object] = {
        "displayName": account.display_name
        or ("Steam account" if public_provider == "steam" else "Account"),
        "avatarUrl": account.avatar_url,
        "provider": public_provider,
    }
    if public_provider == "steam":
        # The viewer's own SteamID64, so the review page can find them in a demo.
        account_body["steamId"] = AccountService(db).get_external_subject(
            owner_id, "steam"
        )
    return {
        "authenticated": True,
        "account": account_body,
        "capabilities": feature_capabilities(service.settings),
    }


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


async def _account_deletion_confirmed(request: Request) -> bool:
    """Whether the body is exactly {"confirm": "delete-my-account"} (read here so any other body is a 400, not a 422)."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _ACCOUNT_DELETION_BODY_LIMIT_BYTES:
            return False
    try:
        payload = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("confirm") == ACCOUNT_DELETION_CONFIRMATION


def _account_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
        headers={"Cache-Control": "private, no-store"},
    )


@router.delete("/account", status_code=204, dependencies=[Depends(require_trusted_origin)])
def delete_account(
    request: Request,
    confirmed: bool = Depends(_account_deletion_confirmed),
    service: AuthService = Depends(get_auth_service),
    db: Session = Depends(get_db),
) -> Response:
    """Delete the signed-in account and everything it owns, and end its sessions everywhere."""
    if service.settings.auth_mode != "production":
        # The local dev owner has no account row, and one request must never
        # wipe the local library. Matches can still be deleted one by one.
        return _account_error(
            409,
            "account_deletion_unavailable",
            "Account deletion is only available with Steam sign-in.",
        )
    session_token = request.cookies.get(service.settings.auth_session_cookie_name)
    owner_id = getattr(request.state, "authenticated_owner_id", None) or service.resolve_session(
        session_token
    )
    if owner_id is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    if not confirmed:
        return _account_error(
            400,
            "confirmation_required",
            'Send {"confirm": "delete-my-account"} to delete this account.',
        )
    deleted = DeletionService(db, runtime_settings=service.settings).delete_account(
        owner_id,
        revoke_sessions=service.revoke_owner_sessions,
    )
    if not deleted:
        # No account behind this session any more: it is stale, not a success.
        service.revoke_session(session_token)
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    service.revoke_session(session_token)
    response = Response(status_code=204)
    response.delete_cookie(
        service.settings.auth_session_cookie_name,
        path="/",
        secure=service.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


def _require_live_account(
    db: Session,
    service: AuthService,
    owner_id: str,
    session_token: str,
) -> None:
    """Refuse a session minted while its account was being deleted.

    An account deletion locks the account row, writes the owner revocation
    marker, then commits. A sign-in that resolved the identity before that
    commit could mint its session after the marker, which the marker would not
    catch. This check takes the creators' fence (`FOR KEY SHARE`) after the
    session exists: it waits for a deletion holding the lock and then finds
    the row gone; if it gets the lock first, any later deletion writes its
    marker after this session's issuedAt and revokes it.
    """
    try:
        alive = account_exists_for_write(db, owner_id, service.settings)
    finally:
        db.rollback()
    if not alive:
        service.revoke_session(session_token)
        raise AuthenticationError("The account was deleted during sign-in")


def _steam_not_invited_response(
    steam_service: SteamAuthService,
    auth_service: AuthService,
) -> RedirectResponse:
    response = RedirectResponse(
        f"{steam_service.settings.frontend_public_url}/auth/callback?"
        f"{urlencode({'error': 'not_invited'})}",
        status_code=303,
        headers=NO_REFERRER_HEADERS,
    )
    response.delete_cookie(
        steam_service.settings.steam_auth_state_cookie_name,
        path="/",
        secure=steam_service.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        auth_service.settings.auth_session_cookie_name,
        path="/",
        secure=auth_service.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


def _request_client_id(request: Request) -> str:
    if request.client is None or not request.client.host:
        return "unknown-client"
    return request.client.host
