from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.account import Account
from app.models.steam import SteamConnection, SteamMatch
from app.services.account_service import AccountService
from app.services.steam_credentials import (
    SteamCredentialCipher,
    SteamCredentialError,
)
from app.services.steam_sync_rate_limit import (
    SteamSyncRateLimitError,
    SteamSyncRateLimiter,
)


STEAM_MATCH_HISTORY_ENDPOINT = (
    "https://api.steampowered.com/ICSGOPlayers_730/"
    "GetNextMatchSharingCode/v1"
)
MATCH_SHARING_CODE_PATTERN = re.compile(
    r"CSGO(?:-[A-Za-z0-9]{5}){5}\Z"
)
GAME_AUTH_CODE_PATTERN = re.compile(
    r"[A-Za-z0-9]{4,8}(?:-[A-Za-z0-9]{4,8}){2,4}\Z"
)
SYNC_LEASE_SECONDS = 5 * 60
MAX_UPSTREAM_RESPONSE_BYTES = 16 * 1024


class SteamMatchSyncError(RuntimeError):
    pass


class SteamMatchSyncConfigurationError(SteamMatchSyncError):
    pass


class SteamIdentityRequiredError(SteamMatchSyncError):
    pass


class SteamConnectionRequiredError(SteamMatchSyncError):
    pass


class SteamConnectionConflictError(SteamMatchSyncError):
    pass


class SteamSyncInProgressError(SteamMatchSyncError):
    pass


class SteamAuthorizationRequiredError(SteamMatchSyncError):
    def __init__(self, error_code: str):
        super().__init__("Steam match-history authorization must be updated")
        self.error_code = error_code


class SteamSyncRetryError(SteamMatchSyncError):
    def __init__(self, upstream_status: int, retry_after_seconds: int):
        super().__init__("Steam match-history sync is temporarily unavailable")
        self.upstream_status = upstream_status
        self.retry_after_seconds = retry_after_seconds


class SteamUpstreamProtocolError(SteamMatchSyncError):
    pass


@dataclass(frozen=True)
class SteamSyncOutcome:
    discovered_count: int
    caught_up: bool
    limit_reached: bool
    status: str


@dataclass(frozen=True)
class SteamHttpResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    def json(self) -> object:
        return json.loads(self.content)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_game_auth_code(value: str) -> str:
    normalized = value.strip()
    if GAME_AUTH_CODE_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Game Authentication Code format is invalid")
    return normalized


def validate_match_sharing_code(value: str) -> str:
    normalized = value.strip()
    if MATCH_SHARING_CODE_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Match Sharing Code format is invalid")
    return normalized


class SteamMatchService:
    def __init__(
        self,
        db: Session,
        *,
        owner_id: str,
        runtime_settings: Settings = settings,
        cipher: SteamCredentialCipher | None = None,
        sync_rate_limiter: SteamSyncRateLimiter | None = None,
        http_client: object = httpx,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.db = db
        self.owner_id = owner_id
        self.settings = runtime_settings
        self._cipher = cipher
        self.sync_rate_limiter = sync_rate_limiter
        self.http = http_client
        self.clock = clock

    @property
    def cipher(self) -> SteamCredentialCipher:
        if self._cipher is None:
            try:
                self._cipher = SteamCredentialCipher(self.settings)
            except (SteamCredentialError, ValueError) as exc:
                raise SteamMatchSyncConfigurationError(
                    "Steam credential encryption is not configured"
                ) from exc
        return self._cipher

    def get_connection(self) -> SteamConnection | None:
        return self._connection_query().one_or_none()

    def save_credentials(
        self,
        *,
        game_auth_code: str,
        initial_match_sharing_code: str,
    ) -> SteamConnection:
        normalized_auth_code = validate_game_auth_code(game_auth_code)
        normalized_known_code = validate_match_sharing_code(
            initial_match_sharing_code
        )
        if not self._lock_owner_account():
            raise SteamIdentityRequiredError(
                "A Steam identity must be linked before match history can be connected"
            )
        steam_id64 = AccountService(self.db).get_external_subject(
            self.owner_id,
            "steam",
        )
        if steam_id64 is None:
            raise SteamIdentityRequiredError(
                "A Steam identity must be linked before match history can be connected"
            )

        now = self.clock()
        connection = self._connection_query(for_update=True).one_or_none()
        if connection is None:
            connection = SteamConnection(
                id=str(uuid.uuid4()),
                owner_id=self.owner_id,
                steam_id64=steam_id64,
                status="connected",
                consecutive_failures=0,
                created_at=now,
                updated_at=now,
            )
            self.db.add(connection)
        elif connection.steam_id64 != steam_id64:
            raise SteamConnectionConflictError(
                "Steam connection belongs to a different linked identity"
            )
        elif self._has_active_sync_lease(connection, now):
            raise SteamSyncInProgressError("Steam match-history sync is already running")

        encrypted_auth_code = self.cipher.encrypt(
            normalized_auth_code,
            owner_id=self.owner_id,
            purpose="game-auth-code",
            record_id=connection.id,
        )
        encrypted_known_code = self.cipher.encrypt(
            normalized_known_code,
            owner_id=self.owner_id,
            purpose="known-match-code",
            record_id=connection.id,
        )
        connection.game_auth_code_ciphertext = encrypted_auth_code.ciphertext
        connection.game_auth_code_nonce = encrypted_auth_code.nonce
        connection.known_code_ciphertext = encrypted_known_code.ciphertext
        connection.known_code_nonce = encrypted_known_code.nonce
        connection.encryption_key_version = encrypted_auth_code.key_version
        connection.status = "connected"
        connection.sync_run_id = None
        connection.consecutive_failures = 0
        connection.next_retry_at = None
        connection.last_error_code = None
        connection.last_error_message = None
        connection.updated_at = now
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise SteamConnectionConflictError(
                "Steam connection changed while authorization was being saved"
            ) from None
        return connection

    def delete_connection(self) -> bool:
        if not self._lock_owner_account():
            return False
        connection = self._connection_query(for_update=True).one_or_none()
        if connection is None:
            return False
        self.db.delete(connection)
        self.db.commit()
        return True

    def list_matches(self, *, limit: int = 50) -> list[SteamMatch]:
        return (
            self.db.query(SteamMatch)
            .filter(SteamMatch.owner_id == self.owner_id)
            .order_by(SteamMatch.discovered_at.desc(), SteamMatch.id.desc())
            .limit(limit)
            .all()
        )

    def sync_now(self) -> SteamSyncOutcome:
        self._validate_sync_configuration()
        now = self.clock()
        connection = self._connection_query().one_or_none()
        if connection is None:
            raise SteamConnectionRequiredError(
                "Steam match history is not connected"
            )
        if connection.next_retry_at is not None and _as_utc(
            connection.next_retry_at
        ) > now:
            retry_after = max(
                1,
                int((_as_utc(connection.next_retry_at) - now).total_seconds()),
            )
            previous_status = (
                429
                if connection.last_error_code
                in {"steam_upstream_429", "steam_sync_rate_limited"}
                else 503
            )
            raise SteamSyncRetryError(previous_status, retry_after)
        if connection.status == "authorization_required":
            raise SteamAuthorizationRequiredError(
                connection.last_error_code or "steam_authorization_invalid"
            )
        if self._has_active_sync_lease(connection, now):
            raise SteamSyncInProgressError("Steam match-history sync is already running")
        if self.sync_rate_limiter is not None:
            try:
                self.sync_rate_limiter.enforce(self.owner_id)
            except SteamSyncRateLimitError as exc:
                self._record_rate_limit_wait(
                    connection.id,
                    now,
                    exc.retry_after_seconds,
                )
                raise

        run_id = str(uuid.uuid4())
        connection_id = connection.id
        self.db.rollback()
        connection = self._claim_sync_run(connection_id, run_id, now)

        try:
            game_auth_code, known_code = self._decrypt_connection_secrets(
                connection
            )
        except (SteamCredentialError, ValueError) as exc:
            self._finish_error(
                connection.id,
                run_id,
                code="credential_decryption_failed",
                message="Stored Steam authorization could not be decrypted.",
            )
            raise SteamMatchSyncConfigurationError(
                "Stored Steam authorization cannot be decrypted"
            ) from exc

        discovered_count = 0
        for _ in range(self.settings.steam_sync_max_matches):
            try:
                response = self._fetch_match_response(
                    steam_id64=connection.steam_id64,
                    game_auth_code=game_auth_code,
                    known_code=known_code,
                )
            except httpx.RequestError:
                retry_after = self._finish_retry(
                    connection.id,
                    run_id,
                    upstream_status=503,
                    response=None,
                )
                raise SteamSyncRetryError(503, retry_after) from None
            except SteamUpstreamProtocolError:
                self._finish_protocol_error(connection.id, run_id)
                raise

            status_code = int(response.status_code)
            if status_code == 200:
                try:
                    next_code = self._next_code(response, expected_na=False)
                except SteamUpstreamProtocolError:
                    self._finish_protocol_error(connection.id, run_id)
                    raise
                if next_code == known_code:
                    self._finish_protocol_error(connection.id, run_id)
                    raise SteamUpstreamProtocolError(
                        "Steam returned a non-advancing match-history cursor"
                    )
                created = self._record_match_and_advance_cursor(
                    connection_id=connection.id,
                    run_id=run_id,
                    next_code=next_code,
                )
                if created:
                    discovered_count += 1
                known_code = next_code
                continue

            if status_code == 202:
                try:
                    self._next_code(response, expected_na=True)
                except SteamUpstreamProtocolError:
                    self._finish_protocol_error(connection.id, run_id)
                    raise
                self._finish_success(
                    connection.id,
                    run_id,
                    status="caught_up",
                )
                return SteamSyncOutcome(
                    discovered_count=discovered_count,
                    caught_up=True,
                    limit_reached=False,
                    status="caught_up",
                )

            if status_code in {403, 412}:
                error_code = (
                    "steam_authorization_invalid"
                    if status_code == 403
                    else "steam_cursor_invalid"
                )
                self._finish_authorization_required(
                    connection.id,
                    run_id,
                    error_code=error_code,
                )
                raise SteamAuthorizationRequiredError(error_code)

            if status_code in {429, 503}:
                retry_after = self._finish_retry(
                    connection.id,
                    run_id,
                    upstream_status=status_code,
                    response=response,
                )
                if status_code == 429 and self.sync_rate_limiter is not None:
                    self.sync_rate_limiter.block_publisher(retry_after)
                raise SteamSyncRetryError(status_code, retry_after)

            self._finish_protocol_error(connection.id, run_id)
            raise SteamUpstreamProtocolError(
                f"Unexpected Steam match-history response status {status_code}"
            )

        self._finish_success(connection.id, run_id, status="connected")
        return SteamSyncOutcome(
            discovered_count=discovered_count,
            caught_up=False,
            limit_reached=True,
            status="connected",
        )

    def _validate_sync_configuration(self) -> None:
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", self.settings.steam_web_api_key):
            raise SteamMatchSyncConfigurationError(
                "Steam Web API access is not configured"
            )
        _ = self.cipher

    def _fetch_match_response(
        self,
        *,
        steam_id64: str,
        game_auth_code: str,
        known_code: str,
    ) -> SteamHttpResponse:
        with self.http.stream(
            "GET",
            STEAM_MATCH_HISTORY_ENDPOINT,
            params={
                "steamid": steam_id64,
                "steamidkey": game_auth_code,
                "knowncode": known_code,
            },
            headers={
                "Accept": "application/json",
                "X-WebAPI-Key": self.settings.steam_web_api_key,
            },
            timeout=self.settings.steam_sync_timeout_seconds,
            follow_redirects=False,
        ) as response:
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if not isinstance(chunk, bytes):
                    raise self._protocol_failure(
                        "Steam response contained invalid bytes"
                    )
                content.extend(chunk)
                if len(content) > MAX_UPSTREAM_RESPONSE_BYTES:
                    raise self._protocol_failure("Steam response was too large")
            return SteamHttpResponse(
                status_code=int(response.status_code),
                headers={str(key): str(value) for key, value in response.headers.items()},
                content=bytes(content),
            )

    def _connection_query(self, *, for_update: bool = False):
        query = self.db.query(SteamConnection).filter(
            SteamConnection.owner_id == self.owner_id
        )
        return query.with_for_update() if for_update else query

    def _lock_owner_account(self) -> bool:
        return (
            self.db.query(Account.owner_id)
            .filter(Account.owner_id == self.owner_id)
            .with_for_update()
            .scalar()
            is not None
        )

    def _claim_sync_run(
        self,
        connection_id: str,
        run_id: str,
        now: datetime,
    ) -> SteamConnection:
        stale_before = now - timedelta(seconds=SYNC_LEASE_SECONDS)
        updated = (
            self.db.query(SteamConnection)
            .filter(
                SteamConnection.id == connection_id,
                SteamConnection.owner_id == self.owner_id,
                SteamConnection.status != "authorization_required",
                or_(
                    SteamConnection.next_retry_at.is_(None),
                    SteamConnection.next_retry_at <= now,
                ),
                or_(
                    SteamConnection.status != "syncing",
                    SteamConnection.sync_run_id.is_(None),
                    SteamConnection.updated_at <= stale_before,
                ),
            )
            .update(
                {
                    SteamConnection.status: "syncing",
                    SteamConnection.sync_run_id: run_id,
                    SteamConnection.last_sync_started_at: now,
                    SteamConnection.next_retry_at: None,
                    SteamConnection.last_error_code: None,
                    SteamConnection.last_error_message: None,
                    SteamConnection.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if updated == 1:
            return self._run_connection(
                connection_id,
                run_id,
                for_update=False,
            )

        self.db.expire_all()
        connection = self._connection_query().one_or_none()
        if connection is None:
            raise SteamConnectionRequiredError(
                "Steam connection was removed while syncing"
            )
        if connection.next_retry_at is not None and _as_utc(
            connection.next_retry_at
        ) > now:
            retry_after = max(
                1,
                int((_as_utc(connection.next_retry_at) - now).total_seconds()),
            )
            upstream_status = (
                429
                if connection.last_error_code
                in {"steam_upstream_429", "steam_sync_rate_limited"}
                else 503
            )
            raise SteamSyncRetryError(upstream_status, retry_after)
        if connection.status == "authorization_required":
            raise SteamAuthorizationRequiredError(
                connection.last_error_code or "steam_authorization_invalid"
            )
        raise SteamSyncInProgressError("Steam match-history sync is already running")

    def _record_rate_limit_wait(
        self,
        connection_id: str,
        now: datetime,
        retry_after_seconds: int,
    ) -> None:
        stale_before = now - timedelta(seconds=SYNC_LEASE_SECONDS)
        retry_after = max(1, min(int(retry_after_seconds), 86_400))
        (
            self.db.query(SteamConnection)
            .filter(
                SteamConnection.id == connection_id,
                SteamConnection.owner_id == self.owner_id,
                SteamConnection.status != "authorization_required",
                or_(
                    SteamConnection.status != "syncing",
                    SteamConnection.sync_run_id.is_(None),
                    SteamConnection.updated_at <= stale_before,
                ),
            )
            .update(
                {
                    SteamConnection.status: "retry_wait",
                    SteamConnection.sync_run_id: None,
                    SteamConnection.next_retry_at: now
                    + timedelta(seconds=retry_after),
                    SteamConnection.last_error_code: "steam_sync_rate_limited",
                    SteamConnection.last_error_message: (
                        "Steam match-history sync is rate-limited; retry later."
                    ),
                    SteamConnection.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()

    def _run_connection(
        self,
        connection_id: str,
        run_id: str,
        *,
        for_update: bool = True,
    ) -> SteamConnection:
        query = self.db.query(SteamConnection).filter(
            SteamConnection.id == connection_id,
            SteamConnection.owner_id == self.owner_id,
            SteamConnection.sync_run_id == run_id,
        )
        if for_update:
            query = query.with_for_update()
        connection = query.one_or_none()
        if connection is None:
            raise SteamConnectionRequiredError(
                "Steam connection was removed while syncing"
            )
        return connection

    def _decrypt_connection_secrets(
        self,
        connection: SteamConnection,
    ) -> tuple[str, str]:
        game_auth_code = self.cipher.decrypt(
            ciphertext=connection.game_auth_code_ciphertext,
            nonce=connection.game_auth_code_nonce,
            key_version=connection.encryption_key_version,
            owner_id=self.owner_id,
            purpose="game-auth-code",
            record_id=connection.id,
        )
        known_code = self.cipher.decrypt(
            ciphertext=connection.known_code_ciphertext,
            nonce=connection.known_code_nonce,
            key_version=connection.encryption_key_version,
            owner_id=self.owner_id,
            purpose="known-match-code",
            record_id=connection.id,
        )
        return game_auth_code, validate_match_sharing_code(known_code)

    def _record_match_and_advance_cursor(
        self,
        *,
        connection_id: str,
        run_id: str,
        next_code: str,
    ) -> bool:
        now = self.clock()
        connection = self._run_connection(connection_id, run_id)
        share_code_hash = self.cipher.fingerprint(next_code)
        match = (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.share_code_hash == share_code_hash,
            )
            .one_or_none()
        )
        created = match is None
        if match is None:
            match_id = str(uuid.uuid4())
            encrypted_code = self.cipher.encrypt(
                next_code,
                owner_id=self.owner_id,
                purpose="match-sharing-code",
                record_id=match_id,
            )
            match = SteamMatch(
                id=match_id,
                connection_id=connection.id,
                owner_id=self.owner_id,
                share_code_hash=share_code_hash,
                share_code_ciphertext=encrypted_code.ciphertext,
                share_code_nonce=encrypted_code.nonce,
                encryption_key_version=encrypted_code.key_version,
                status="discovered",
                discovered_at=now,
                updated_at=now,
            )
            self.db.add(match)

        encrypted_cursor = self.cipher.encrypt(
            next_code,
            owner_id=self.owner_id,
            purpose="known-match-code",
            record_id=connection.id,
        )
        connection.known_code_ciphertext = encrypted_cursor.ciphertext
        connection.known_code_nonce = encrypted_cursor.nonce
        connection.encryption_key_version = encrypted_cursor.key_version
        connection.updated_at = now
        self.db.commit()
        return created

    def _finish_success(
        self,
        connection_id: str,
        run_id: str,
        *,
        status: str,
    ) -> None:
        connection = self._run_connection(connection_id, run_id)
        now = self.clock()
        connection.status = status
        connection.sync_run_id = None
        connection.consecutive_failures = 0
        connection.last_sync_completed_at = now
        connection.next_retry_at = None
        connection.last_error_code = None
        connection.last_error_message = None
        connection.updated_at = now
        self.db.commit()

    def _finish_authorization_required(
        self,
        connection_id: str,
        run_id: str,
        *,
        error_code: str,
    ) -> None:
        connection = self._run_connection(connection_id, run_id)
        now = self.clock()
        connection.status = "authorization_required"
        connection.sync_run_id = None
        connection.last_sync_completed_at = now
        connection.next_retry_at = None
        connection.last_error_code = error_code
        connection.last_error_message = (
            "Steam authorization must be updated before syncing again."
        )
        connection.updated_at = now
        self.db.commit()

    def _finish_retry(
        self,
        connection_id: str,
        run_id: str,
        *,
        upstream_status: int,
        response: object | None,
    ) -> int:
        connection = self._run_connection(connection_id, run_id)
        now = self.clock()
        failure_count = min(connection.consecutive_failures + 1, 31)
        exponential_delay = min(
            self.settings.steam_sync_retry_max_seconds,
            self.settings.steam_sync_retry_base_seconds
            * (2 ** (failure_count - 1)),
        )
        retry_after = _retry_after_seconds(response, now)
        delay = min(
            self.settings.steam_sync_retry_max_seconds,
            max(exponential_delay, retry_after or 0),
        )
        connection.status = "retry_wait"
        connection.sync_run_id = None
        connection.consecutive_failures = failure_count
        connection.last_sync_completed_at = now
        connection.next_retry_at = now + timedelta(seconds=delay)
        connection.last_error_code = f"steam_upstream_{upstream_status}"
        connection.last_error_message = (
            "Steam match history is temporarily unavailable; retry later."
        )
        connection.updated_at = now
        self.db.commit()
        return delay

    def _finish_protocol_error(self, connection_id: str, run_id: str) -> None:
        self._finish_error(
            connection_id,
            run_id,
            code="steam_upstream_invalid_response",
            message="Steam returned an invalid match-history response.",
        )

    def _finish_error(
        self,
        connection_id: str,
        run_id: str,
        *,
        code: str,
        message: str,
    ) -> None:
        connection = self._run_connection(connection_id, run_id)
        now = self.clock()
        connection.status = "error"
        connection.sync_run_id = None
        connection.last_sync_completed_at = now
        connection.next_retry_at = None
        connection.last_error_code = code
        connection.last_error_message = message
        connection.updated_at = now
        self.db.commit()

    def _has_active_sync_lease(
        self,
        connection: SteamConnection,
        now: datetime,
    ) -> bool:
        if connection.status != "syncing" or connection.sync_run_id is None:
            return False
        lease_heartbeat = connection.updated_at or connection.last_sync_started_at
        if lease_heartbeat is None:
            return False
        return (
            now - _as_utc(lease_heartbeat)
        ).total_seconds() < SYNC_LEASE_SECONDS

    def _next_code(self, response: object, *, expected_na: bool) -> str:
        try:
            payload = response.json()
            result = payload["result"]
            next_code = result["nextcode"]
        except (KeyError, TypeError, ValueError):
            raise self._protocol_failure(
                "Steam response did not contain a next code"
            ) from None
        if expected_na:
            if next_code != "n/a":
                raise self._protocol_failure(
                    "Steam caught-up response did not contain n/a"
                )
            return next_code
        try:
            return validate_match_sharing_code(next_code)
        except (TypeError, ValueError):
            raise self._protocol_failure("Steam returned an invalid next code") from None

    def _protocol_failure(self, message: str) -> SteamUpstreamProtocolError:
        return SteamUpstreamProtocolError(message)


def _retry_after_seconds(response: object | None, now: datetime) -> int | None:
    if response is None:
        return None
    headers = getattr(response, "headers", {})
    raw_value = headers.get("Retry-After") if hasattr(headers, "get") else None
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    value = raw_value.strip()
    if value.isascii() and value.isdigit():
        if len(value) > 10:
            return None
        try:
            return max(1, int(value))
        except (ValueError, OverflowError):
            return None
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(1, int((retry_at.astimezone(timezone.utc) - now).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
