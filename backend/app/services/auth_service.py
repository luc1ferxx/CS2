from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from app.core.config import Settings, settings
from app.core.redis import get_redis_client

MAX_RETURN_TO_LENGTH = 2048
# An owner revocation marker outlives every session it can revoke plus clock
# skew: renewal never carries a session past AUTH_SESSION_MAX_AGE_SECONDS from
# its sign-in, which production validation caps at 86400 s.
OWNER_REVOCATION_TTL_SECONDS = 86_400 + 300


@dataclass(frozen=True)
class LoginStart:
    authorization_url: str
    state: str


@dataclass(frozen=True)
class SessionGrant:
    session_token: str
    max_age: int
    return_to: str


@dataclass(frozen=True)
class ActiveSession:
    """A session record that passed every check in `resolve_session`."""

    owner_id: str
    expires_at: int
    # Milliseconds; None for records written before issuedAt existed.
    issued_at_ms: int | None
    record: dict[str, object]


@dataclass(frozen=True)
class VerifiedLogin:
    provider: str
    subject: str
    preferred_owner_id: str
    max_age: int
    return_to: str


class AuthenticationError(RuntimeError):
    pass


class AuthService:
    def __init__(
        self,
        runtime_settings: Settings,
        redis_client: object,
        *,
        http_client: object = httpx,
    ):
        self.settings = runtime_settings
        self.redis = redis_client
        self.http = http_client

    def begin_login(self, return_to: str | None) -> LoginStart:
        safe_return_to = _safe_return_to(return_to)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        self.redis.setex(
            _hashed_key("auth:login", state),
            self.settings.auth_login_ttl_seconds,
            json.dumps(
                {
                    "nonce": nonce,
                    "codeVerifier": verifier,
                    "returnTo": safe_return_to,
                },
                separators=(",", ":"),
            ),
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.oidc_client_id,
                "redirect_uri": self.settings.oidc_redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return LoginStart(
            authorization_url=f"{self.settings.oidc_authorization_endpoint}?{query}",
            state=state,
        )

    def complete_login(self, code: str, state: str, state_cookie: str | None) -> SessionGrant:
        verified = self.verify_login(code, state, state_cookie)
        return self.create_session(
            verified.preferred_owner_id,
            max_age=verified.max_age,
            return_to=verified.return_to,
        )

    def verify_login(
        self,
        code: str,
        state: str,
        state_cookie: str | None,
    ) -> VerifiedLogin:
        if (
            not code
            or len(code) > 4096
            or not _is_valid_opaque_value(state)
            or not _is_valid_opaque_value(state_cookie)
        ):
            raise AuthenticationError("OIDC callback could not be verified")
        if not secrets.compare_digest(state, state_cookie):
            raise AuthenticationError("OIDC callback could not be verified")

        raw_attempt = self.redis.getdel(_hashed_key("auth:login", state))
        if not raw_attempt:
            raise AuthenticationError("OIDC callback could not be verified")
        try:
            attempt = json.loads(_decode_redis_value(raw_attempt))
            verifier = str(attempt["codeVerifier"])
            nonce = str(attempt["nonce"])
            return_to = _safe_return_to(str(attempt["returnTo"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError("OIDC callback could not be verified") from exc

        token_form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "client_id": self.settings.oidc_client_id,
            "code_verifier": verifier,
        }
        if self.settings.oidc_client_secret:
            token_form["client_secret"] = self.settings.oidc_client_secret
        try:
            token_response = self.http.post(
                self.settings.oidc_token_endpoint,
                data=token_form,
                timeout=5.0,
            )
            token_response.raise_for_status()
            id_token = token_response.json().get("id_token")
            if not isinstance(id_token, str) or not id_token:
                raise AuthenticationError("OIDC callback could not be verified")
            jwks_response = self.http.get(self.settings.oidc_jwks_url, timeout=5.0)
            jwks_response.raise_for_status()
            claims = self._verified_claims(id_token, jwks_response.json(), nonce)
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError("OIDC callback could not be verified") from exc

        issuer = str(claims["iss"])
        subject = str(claims["sub"])
        owner_id = derive_owner_id(issuer, subject)
        now = int(time.time())
        max_age = min(self.settings.auth_session_ttl_seconds, int(claims["exp"]) - now)
        if max_age <= 0:
            raise AuthenticationError("OIDC callback could not be verified")
        return VerifiedLogin(
            provider=oidc_provider_key(issuer),
            subject=subject,
            preferred_owner_id=owner_id,
            max_age=max_age,
            return_to=return_to,
        )

    def create_session(
        self,
        owner_id: str,
        *,
        max_age: int | None = None,
        return_to: str = "/dashboard",
        steam_id: str | None = None,
    ) -> SessionGrant:
        if not owner_id.startswith("owner_v1_") or len(owner_id) > 64:
            raise AuthenticationError("Verified account owner is invalid")
        bounded_max_age = min(
            max_age if max_age is not None else self.settings.auth_session_ttl_seconds,
            self.settings.auth_session_ttl_seconds,
        )
        if bounded_max_age <= 0:
            raise AuthenticationError("Verified session lifetime is invalid")
        now_seconds = time.time()
        now = int(now_seconds)
        session_token = secrets.token_urlsafe(32)
        record: dict[str, str | int] = {
            "ownerId": owner_id,
            "expiresAt": now + bounded_max_age,
            # Milliseconds; compared with an owner revocation marker.
            "issuedAt": int(now_seconds * 1000),
        }
        if steam_id is not None:
            record["steamId"] = steam_id
        self.redis.setex(
            _hashed_key("auth:session", session_token),
            bounded_max_age,
            json.dumps(record, separators=(",", ":")),
        )
        return SessionGrant(
            session_token=session_token,
            max_age=bounded_max_age,
            return_to=_safe_return_to(return_to),
        )

    def resolve_session(self, session_token: str | None) -> str | None:
        session = self.resolve_active_session(session_token)
        return session.owner_id if session is not None else None

    def resolve_active_session(self, session_token: str | None) -> ActiveSession | None:
        if not _is_valid_opaque_value(session_token):
            return None
        raw_session = self.redis.get(_hashed_key("auth:session", session_token))
        if not raw_session:
            return None
        try:
            session = json.loads(_decode_redis_value(raw_session))
            owner_id = str(session["ownerId"])
            expires_at = int(session["expiresAt"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            expires_at <= int(time.time())
            or not owner_id.startswith("owner_v1_")
            or not self._steam_session_still_invited(session.get("steamId"))
            or self._revoked_by_owner_marker(owner_id, session.get("issuedAt"))
        ):
            self.revoke_session(session_token)
            return None
        issued_at = session.get("issuedAt")
        return ActiveSession(
            owner_id=owner_id,
            expires_at=expires_at,
            issued_at_ms=issued_at if isinstance(issued_at, int) and not isinstance(issued_at, bool) else None,
            record=session,
        )

    def renew_session(self, session_token: str | None, session: ActiveSession) -> int | None:
        """Slide the idle window of a session resolved in this request.

        Renews only once less than half of AUTH_SESSION_TTL_SECONDS is left,
        and never past AUTH_SESSION_MAX_AGE_SECONDS after sign-in. `issuedAt`
        is kept, so an owner revocation marker still ends the session.
        `SET ... XX` writes only while the key still exists: a logout or
        revocation that deleted it meanwhile is never undone. Returns the new
        lifetime in seconds (the cookie's Max-Age), or None when not renewed.
        """
        if not _is_valid_opaque_value(session_token) or session.issued_at_ms is None:
            return None
        idle_seconds = self.settings.auth_session_ttl_seconds
        now_seconds = time.time()
        now = int(now_seconds)
        if (session.expires_at - now) * 2 >= idle_seconds:
            return None
        age_seconds = max(0.0, now_seconds - session.issued_at_ms / 1000)
        lifetime = min(idle_seconds, int(self.settings.auth_session_max_age_seconds - age_seconds))
        if now + lifetime <= session.expires_at:
            return None
        # Account deletion may have revoked every session of this owner while
        # the request ran (renewals are rare, so this read is cheap).
        if self._revoked_by_owner_marker(session.owner_id, session.issued_at_ms):
            return None
        record = {**session.record, "expiresAt": now + lifetime}
        stored = self.redis.set(
            _hashed_key("auth:session", session_token),
            json.dumps(record, separators=(",", ":")),
            ex=lifetime,
            xx=True,
        )
        return lifetime if stored else None

    def revoke_session(self, session_token: str | None) -> None:
        if _is_valid_opaque_value(session_token):
            self.redis.delete(_hashed_key("auth:session", session_token))

    def revoke_owner_sessions(self, owner_id: str) -> int:
        """End every session of this owner, on every device, as of now.

        Sessions are keyed by token with no owner index, so this writes one
        marker instead: `resolve_session` rejects any session of the owner
        issued at or before it. Sessions created later (a new sign-in) have a
        later `issuedAt` and are unaffected. Returns the marker (ms).
        """
        marker = int(time.time() * 1000)
        self.redis.setex(
            _owner_revocation_key(owner_id),
            OWNER_REVOCATION_TTL_SECONDS,
            str(marker),
        )
        return marker

    def _revoked_by_owner_marker(self, owner_id: str, issued_at: object) -> bool:
        raw_marker = self.redis.get(_owner_revocation_key(owner_id))
        if not raw_marker:
            return False
        try:
            marker = int(_decode_redis_value(raw_marker))
        except (TypeError, ValueError):
            # An unreadable marker still means "this owner was revoked".
            return True
        # A session from before issuedAt existed cannot prove it is newer.
        if isinstance(issued_at, bool) or not isinstance(issued_at, int):
            return True
        return issued_at <= marker

    def _steam_session_still_invited(self, steam_id: object) -> bool:
        # Re-checked on every resolution so removing a Steam ID from
        # STEAM_LOGIN_ALLOWLIST ends its live sessions. Records without a
        # steamId (created before the allowlist) fail closed.
        if (
            self.settings.auth_mode != "production"
            or self.settings.auth_provider != "steam"
            or self.settings.steam_login_allowlist is None
        ):
            return True
        return isinstance(steam_id, str) and self.settings.steam_login_allowed(steam_id)

    def _verified_claims(
        self,
        id_token: str,
        jwks: object,
        expected_nonce: str,
    ) -> dict[str, object]:
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise AuthenticationError("OIDC callback could not be verified")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("OIDC callback could not be verified") from exc
        algorithm = header.get("alg")
        if (
            algorithm not in self.settings.oidc_allowed_algorithms
            or not isinstance(header.get("kid"), str)
        ):
            raise AuthenticationError("OIDC callback could not be verified")
        matching_keys = [key for key in jwks["keys"] if key.get("kid") == header["kid"]]
        if len(matching_keys) != 1:
            raise AuthenticationError("OIDC callback could not be verified")
        try:
            public_key = jwt.PyJWK.from_dict(matching_keys[0], algorithm=algorithm).key
            claims = jwt.decode(
                id_token,
                public_key,
                algorithms=[algorithm],
                audience=self.settings.oidc_client_id,
                issuer=self.settings.oidc_issuer,
                options={"require": ["iss", "sub", "aud", "iat", "exp"]},
                leeway=self.settings.auth_clock_skew_seconds,
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthenticationError("OIDC callback could not be verified") from exc
        nonce = claims.get("nonce")
        subject = claims.get("sub")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            raise AuthenticationError("OIDC callback could not be verified")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError("OIDC callback could not be verified")
        if int(claims["iat"]) > int(time.time()) + self.settings.auth_clock_skew_seconds:
            raise AuthenticationError("OIDC callback could not be verified")
        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if authorized_party is not None and authorized_party != self.settings.oidc_client_id:
            raise AuthenticationError("OIDC callback could not be verified")
        if isinstance(audience, list) and len(audience) > 1:
            if authorized_party != self.settings.oidc_client_id:
                raise AuthenticationError("OIDC callback could not be verified")
        return claims


def get_auth_service() -> AuthService:
    return AuthService(settings, get_redis_client())


def derive_owner_id(issuer: str, subject: str) -> str:
    if not issuer.strip() or not subject.strip():
        raise ValueError("Verified issuer and subject must be non-empty")
    digest = hashlib.sha256(f"{issuer}\0{subject}".encode()).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"owner_v1_{encoded}"


def oidc_provider_key(issuer: str) -> str:
    normalized = issuer.strip()
    if not normalized:
        raise ValueError("Verified issuer must be non-empty")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"oidc_{digest[:40]}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _owner_revocation_key(owner_id: str) -> str:
    digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
    return f"auth:owner-revoked:{digest}"


def _hashed_key(prefix: str, opaque_value: str) -> str:
    digest = hashlib.sha256(opaque_value.encode("ascii")).hexdigest()
    return f"{prefix}:{digest}"


def _is_valid_opaque_value(value: str | None) -> bool:
    if not value or len(value) > 128:
        return False
    return all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


def _safe_return_to(return_to: str | None) -> str:
    value = (return_to or "/dashboard").strip()
    parsed = urlparse(value)
    if (
        len(value) > MAX_RETURN_TO_LENGTH
        or parsed.scheme
        or parsed.netloc
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return "/dashboard"
    return value


def _decode_redis_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError("Redis value must be bytes or text")
