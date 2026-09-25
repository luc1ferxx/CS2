from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse

import httpx

from app.core.config import Settings, _is_individual_steam_id, settings
from app.core.redis import get_redis_client
from app.services.auth_service import (
    AuthenticationError,
    _decode_redis_value,
    _hashed_key,
    _is_valid_opaque_value,
    _safe_return_to,
)

OPENID_NAMESPACE = "http://specs.openid.net/auth/2.0"
OPENID_IDENTIFIER_SELECT = f"{OPENID_NAMESPACE}/identifier_select"
OPENID_SIGNON_TYPE = f"{OPENID_NAMESPACE}/signon"
STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
STEAM_CLAIMED_ID_PREFIX = "https://steamcommunity.com/openid/id/"
STEAM_PLAYER_SUMMARIES_ENDPOINT = (
    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
)
REQUIRED_SIGNED_FIELDS = frozenset(
    {
        "op_endpoint",
        "claimed_id",
        "identity",
        "return_to",
        "response_nonce",
        "assoc_handle",
    }
)
MAX_OPENID_CALLBACK_BYTES = 32 * 1024
MAX_OPENID_FIELD_BYTES = 8 * 1024
MAX_OPENID_DISCOVERY_BYTES = 64 * 1024
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
AUTH_LOGIN_RATE_LIMIT = 30
AUTH_CALLBACK_RATE_LIMIT = 60
AUTH_RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if count > tonumber(ARGV[2]) then
  return 0
end
return 1
"""


@dataclass(frozen=True)
class SteamLoginStart:
    authorization_url: str
    state: str


@dataclass(frozen=True)
class VerifiedSteamLogin:
    steam_id: str
    display_name: str | None
    avatar_url: str | None
    max_age: int
    return_to: str


class AuthenticationRateLimitError(AuthenticationError):
    pass


class SteamAuthService:
    def __init__(
        self,
        runtime_settings: Settings,
        redis_client: object,
        *,
        http_client: object = httpx,
        clock: object = time.time,
    ):
        self.settings = runtime_settings
        self.redis = redis_client
        self.http = http_client
        self.clock = clock

    def enforce_request_limit(self, scope: str, client_id: str) -> None:
        limit = {
            "login": AUTH_LOGIN_RATE_LIMIT,
            "callback": AUTH_CALLBACK_RATE_LIMIT,
        }.get(scope)
        if limit is None:
            raise ValueError("Unknown Steam authentication rate-limit scope")
        window = int(self.clock()) // AUTH_RATE_LIMIT_WINDOW_SECONDS
        client_digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()
        key = f"auth:steam:rate:{scope}:{window}:{client_digest}"
        allowed = self.redis.eval(
            AUTH_RATE_LIMIT_SCRIPT,
            1,
            key,
            AUTH_RATE_LIMIT_WINDOW_SECONDS + 1,
            limit,
        )
        if int(allowed) != 1:
            raise AuthenticationRateLimitError("Steam sign-in rate limit exceeded")

    def begin_login(self, return_to: str | None) -> SteamLoginStart:
        safe_return_to = _safe_return_to(return_to)
        realm = self.settings.steam_openid_realm
        for _ in range(3):
            state = secrets.token_urlsafe(32)
            callback_url = _callback_url_with_state(
                self.settings.steam_openid_callback_url,
                state,
            )
            reserved = self.redis.set(
                _hashed_key("auth:steam:login", state),
                json.dumps(
                    {
                        "callbackUrl": callback_url,
                        "realm": realm,
                        "returnTo": safe_return_to,
                    },
                    separators=(",", ":"),
                ),
                ex=self.settings.auth_login_ttl_seconds,
                nx=True,
            )
            if reserved:
                break
        else:
            raise AuthenticationError("Steam sign-in could not be started")
        query = urlencode(
            {
                "openid.ns": OPENID_NAMESPACE,
                "openid.mode": "checkid_setup",
                "openid.return_to": callback_url,
                "openid.realm": realm,
                "openid.identity": OPENID_IDENTIFIER_SELECT,
                "openid.claimed_id": OPENID_IDENTIFIER_SELECT,
            }
        )
        return SteamLoginStart(
            authorization_url=f"{STEAM_OPENID_ENDPOINT}?{query}",
            state=state,
        )

    def complete_login(
        self,
        query_items: Iterable[tuple[str, str]],
        state_cookie: str | None,
    ) -> VerifiedSteamLogin:
        fields = _unique_callback_fields(query_items)
        state = fields.get("state")
        if (
            not _is_valid_opaque_value(state)
            or not _is_valid_opaque_value(state_cookie)
            or not secrets.compare_digest(state, state_cookie)
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")

        raw_attempt = self.redis.getdel(_hashed_key("auth:steam:login", state))
        if not raw_attempt:
            raise AuthenticationError("Steam OpenID callback could not be verified")
        try:
            attempt = json.loads(_decode_redis_value(raw_attempt))
            expected_callback_url = str(attempt["callbackUrl"])
            expected_realm = str(attempt["realm"])
            return_to = _safe_return_to(str(attempt["returnTo"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError(
                "Steam OpenID callback could not be verified"
            ) from exc
        if (
            expected_callback_url
            != _callback_url_with_state(self.settings.steam_openid_callback_url, state)
            or expected_realm != self.settings.steam_openid_realm
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")

        steam_id, response_nonce = self._validate_assertion(
            fields,
            expected_callback_url=expected_callback_url,
        )
        self._verify_claimed_id_discovery(f"{STEAM_CLAIMED_ID_PREFIX}{steam_id}")
        self._verify_with_steam(fields)
        self._reserve_response_nonce(response_nonce)
        display_name, avatar_url = self._fetch_profile(steam_id)
        return VerifiedSteamLogin(
            steam_id=steam_id,
            display_name=display_name,
            avatar_url=avatar_url,
            max_age=self.settings.auth_session_ttl_seconds,
            return_to=return_to,
        )

    def _validate_assertion(
        self,
        fields: dict[str, str],
        *,
        expected_callback_url: str,
    ) -> tuple[str, str]:
        required_values = {
            "openid.ns": OPENID_NAMESPACE,
            "openid.mode": "id_res",
            "openid.op_endpoint": STEAM_OPENID_ENDPOINT,
            "openid.return_to": expected_callback_url,
        }
        if any(fields.get(key) != value for key, value in required_values.items()):
            raise AuthenticationError("Steam OpenID callback could not be verified")

        claimed_id = fields.get("openid.claimed_id", "")
        if fields.get("openid.identity") != claimed_id:
            raise AuthenticationError("Steam OpenID callback could not be verified")
        claimed_match = re.fullmatch(
            rf"{re.escape(STEAM_CLAIMED_ID_PREFIX)}([1-9][0-9]{{16}})",
            claimed_id,
        )
        if claimed_match is None or not _is_individual_steam_id(claimed_match.group(1)):
            raise AuthenticationError("Steam OpenID callback could not be verified")

        signed_fields = fields.get("openid.signed", "").split(",")
        if (
            not signed_fields
            or any(not field or field.strip() != field for field in signed_fields)
            or any(
                re.fullmatch(r"[A-Za-z0-9_.-]+", field) is None
                for field in signed_fields
            )
            or len(set(signed_fields)) != len(signed_fields)
            or not REQUIRED_SIGNED_FIELDS.issubset(signed_fields)
            or any(f"openid.{field}" not in fields for field in signed_fields)
            or not fields.get("openid.sig")
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")

        response_nonce = fields.get("openid.response_nonce", "")
        self._validate_nonce_freshness(response_nonce)
        return claimed_match.group(1), response_nonce

    def _verify_with_steam(self, fields: dict[str, str]) -> None:
        verification_form = {
            key: value for key, value in fields.items() if key.startswith("openid.")
        }
        verification_form["openid.mode"] = "check_authentication"
        try:
            response = self.http.post(
                STEAM_OPENID_ENDPOINT,
                data=verification_form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5.0,
                follow_redirects=False,
            )
            response.raise_for_status()
            verification = _parse_key_value_response(response.text)
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(
                "Steam OpenID callback could not be verified"
            ) from exc
        if (
            verification.get("ns") != OPENID_NAMESPACE
            or verification.get("is_valid") != "true"
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")

    def _verify_claimed_id_discovery(self, claimed_id: str) -> None:
        try:
            with self.http.stream(
                "GET",
                claimed_id,
                headers={"Accept": "application/xrds+xml"},
                timeout=5.0,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type.split(";", 1)[0].strip() != "application/xrds+xml":
                    raise AuthenticationError(
                        "Steam OpenID callback could not be verified"
                    )
                chunks: list[bytes] = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_OPENID_DISCOVERY_BYTES:
                        raise AuthenticationError(
                            "Steam OpenID callback could not be verified"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
                if not body:
                    raise AuthenticationError(
                        "Steam OpenID callback could not be verified"
                    )
            root = ElementTree.fromstring(body)
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(
                "Steam OpenID callback could not be verified"
            ) from exc

        if _xml_local_name(root.tag) != "XRDS":
            raise AuthenticationError("Steam OpenID callback could not be verified")
        matching_services = 0
        for service in root.iter():
            if _xml_local_name(service.tag) != "Service":
                continue
            types = [
                (element.text or "").strip()
                for element in service
                if _xml_local_name(element.tag) == "Type"
            ]
            if OPENID_SIGNON_TYPE not in types:
                continue
            uris = [
                (element.text or "").strip()
                for element in service
                if _xml_local_name(element.tag) == "URI"
            ]
            local_ids = [
                (element.text or "").strip()
                for element in service
                if _xml_local_name(element.tag) in {"LocalID", "Delegate"}
            ]
            if uris != [STEAM_OPENID_ENDPOINT] or (
                local_ids and local_ids != [claimed_id]
            ):
                raise AuthenticationError(
                    "Steam OpenID callback could not be verified"
                )
            matching_services += 1
        if matching_services != 1:
            raise AuthenticationError("Steam OpenID callback could not be verified")

    def _validate_nonce_freshness(self, response_nonce: str) -> None:
        match = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)[!-~]*",
            response_nonce,
        )
        if match is None or len(response_nonce) > 255:
            raise AuthenticationError("Steam OpenID callback could not be verified")
        try:
            issued_at = datetime.strptime(
                match.group(1), "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
        except ValueError as exc:
            raise AuthenticationError(
                "Steam OpenID callback could not be verified"
            ) from exc
        age_seconds = self.clock() - issued_at.timestamp()
        if (
            age_seconds < -self.settings.auth_clock_skew_seconds
            or age_seconds > self.settings.steam_openid_nonce_ttl_seconds
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")

    def _reserve_response_nonce(self, response_nonce: str) -> None:
        reserved = self.redis.set(
            _hashed_key("auth:steam:nonce", response_nonce),
            "1",
            ex=self.settings.steam_openid_nonce_ttl_seconds,
            nx=True,
        )
        if not reserved:
            raise AuthenticationError("Steam OpenID callback could not be verified")

    def _fetch_profile(self, steam_id: str) -> tuple[str | None, str | None]:
        if not self.settings.steam_web_api_key:
            return None, None
        try:
            response = self.http.get(
                STEAM_PLAYER_SUMMARIES_ENDPOINT,
                params={
                    "key": self.settings.steam_web_api_key,
                    "steamids": steam_id,
                },
                timeout=5.0,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            players = payload["response"]["players"]
            if not isinstance(players, list) or len(players) != 1:
                return None, None
            player = players[0]
            if not isinstance(player, dict) or str(player.get("steamid")) != steam_id:
                return None, None
            display_name = _safe_display_name(player.get("personaname"))
            avatar_url = _safe_avatar_url(player.get("avatarfull"))
            return display_name, avatar_url
        except Exception:
            return None, None


def get_steam_auth_service() -> SteamAuthService:
    return SteamAuthService(settings, get_redis_client())


def _callback_url_with_state(callback_url: str, state: str) -> str:
    return f"{callback_url}?{urlencode({'state': state})}"


def _unique_callback_fields(
    query_items: Iterable[tuple[str, str]],
) -> dict[str, str]:
    fields: dict[str, str] = {}
    total_bytes = 0
    for key, value in query_items:
        if key in fields or (
            key != "state"
            and re.fullmatch(r"openid\.[A-Za-z0-9_.-]+", key) is None
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")
        field_bytes = len(key.encode("utf-8")) + len(value.encode("utf-8"))
        total_bytes += field_bytes
        if (
            not key
            or len(key) > 128
            or field_bytes > MAX_OPENID_FIELD_BYTES
            or total_bytes > MAX_OPENID_CALLBACK_BYTES
        ):
            raise AuthenticationError("Steam OpenID callback could not be verified")
        fields[key] = value
    return fields


def _parse_key_value_response(value: str) -> dict[str, str]:
    if len(value.encode("utf-8")) > 4_096:
        raise AuthenticationError("Steam OpenID callback could not be verified")
    parsed: dict[str, str] = {}
    for line in value.splitlines():
        if not line:
            continue
        key, separator, field_value = line.partition(":")
        if not separator or not key or key in parsed or "\n" in field_value:
            raise AuthenticationError("Steam OpenID callback could not be verified")
        parsed[key] = field_value
    return parsed


def _xml_local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _safe_display_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:128] or None


def _safe_avatar_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 512:
        return None
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname or ""
        _ = parsed.port  # accessing .port is what raises ValueError on a malformed port
    except ValueError:
        return None
    trusted_host = (
        hostname == "steamcdn-a.akamaihd.net"
        or hostname == "avatars.steamstatic.com"
        or hostname.endswith(".steamstatic.com")
    )
    if (
        parsed.scheme != "https"
        or not trusted_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    return value
