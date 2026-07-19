from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Account, ExternalIdentity


@dataclass(frozen=True)
class AccountIdentity:
    owner_id: str
    display_name: str | None
    avatar_url: str | None
    provider: str


class AccountConflictError(RuntimeError):
    pass


class AccountService:
    def __init__(self, db: Session):
        self.db = db

    def resolve_or_create_identity(
        self,
        *,
        provider: str,
        subject: str,
        preferred_owner_id: str | None = None,
        display_name: str | None = None,
        avatar_url: str | None = None,
    ) -> AccountIdentity:
        normalized_provider = _normalize_identity_value(provider, "provider", 96)
        normalized_subject = _normalize_identity_value(subject, "subject", 255)
        owner_id = (
            _validate_account_owner_id(preferred_owner_id)
            if preferred_owner_id is not None
            else None
        )
        safe_display_name = _normalize_optional_text(display_name, 128)
        safe_avatar_url = _normalize_optional_text(avatar_url, 512)

        try:
            result = self._resolve_or_create(
                provider=normalized_provider,
                subject=normalized_subject,
                preferred_owner_id=owner_id,
                display_name=safe_display_name,
                avatar_url=safe_avatar_url,
            )
            self.db.commit()
            return result
        except AccountConflictError:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            existing = self._find_identity(normalized_provider, normalized_subject)
            if existing is None or (
                owner_id is not None and existing.owner_id != owner_id
            ):
                raise AccountConflictError(
                    "External identity is already bound to another account"
                ) from exc
            account = self.db.get(Account, existing.owner_id)
            if account is None:
                raise AccountConflictError(
                    "External identity is not attached to a valid account"
                ) from exc
            return _account_identity(account, existing.provider)

    def get_account(self, owner_id: str) -> AccountIdentity | None:
        account = self.db.get(Account, owner_id)
        if account is None:
            return None
        identity = (
            self.db.query(ExternalIdentity)
            .filter(ExternalIdentity.owner_id == owner_id)
            .order_by(
                (ExternalIdentity.provider != "steam").asc(),
                ExternalIdentity.created_at.asc(),
            )
            .first()
        )
        if identity is None:
            return None
        return _account_identity(account, identity.provider)

    def _resolve_or_create(
        self,
        *,
        provider: str,
        subject: str,
        preferred_owner_id: str | None,
        display_name: str | None,
        avatar_url: str | None,
    ) -> AccountIdentity:
        now = datetime.now(timezone.utc)
        identity = self._find_identity(provider, subject)
        if identity is not None:
            if preferred_owner_id is not None and identity.owner_id != preferred_owner_id:
                raise AccountConflictError(
                    "External identity is already bound to another account"
                )
            account = self.db.get(Account, identity.owner_id)
            if account is None:
                raise AccountConflictError(
                    "External identity is not attached to a valid account"
                )
            identity.last_login_at = now
            _update_profile(account, display_name, avatar_url, now)
            self.db.flush()
            return _account_identity(account, identity.provider)

        owner_id = preferred_owner_id or generate_owner_id()
        account = self.db.get(Account, owner_id)
        if account is None:
            account = Account(
                owner_id=owner_id,
                display_name=display_name,
                avatar_url=avatar_url,
                created_at=now,
                updated_at=now,
            )
            self.db.add(account)
        else:
            _update_profile(account, display_name, avatar_url, now)

        owner_provider_identity = (
            self.db.query(ExternalIdentity)
            .filter(
                ExternalIdentity.owner_id == owner_id,
                ExternalIdentity.provider == provider,
            )
            .one_or_none()
        )
        if owner_provider_identity is not None:
            raise AccountConflictError(
                "Account already has a different identity for this provider"
            )

        identity = ExternalIdentity(
            id=str(uuid.uuid4()),
            provider=provider,
            subject=subject,
            owner_id=owner_id,
            created_at=now,
            last_login_at=now,
        )
        self.db.add(identity)
        self.db.flush()
        return _account_identity(account, provider)

    def _find_identity(self, provider: str, subject: str) -> ExternalIdentity | None:
        return (
            self.db.query(ExternalIdentity)
            .filter(
                ExternalIdentity.provider == provider,
                ExternalIdentity.subject == subject,
            )
            .one_or_none()
        )


def generate_owner_id() -> str:
    owner_id = f"owner_v1_{secrets.token_urlsafe(32)}"
    return _validate_account_owner_id(owner_id)


def provider_label(provider: str) -> str:
    return "oidc" if provider.startswith("oidc_") else provider


def _validate_account_owner_id(owner_id: str | None) -> str:
    value = (owner_id or "").strip()
    if not value.startswith("owner_v1_") or len(value) > 64:
        raise ValueError("Account owner id must be a bounded opaque owner_v1 value")
    return value


def _normalize_identity_value(value: str, label: str, max_length: int) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"External identity {label} is invalid")
    return normalized


def _normalize_optional_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized[:max_length]


def _update_profile(
    account: Account,
    display_name: str | None,
    avatar_url: str | None,
    now: datetime,
) -> None:
    changed = False
    if display_name is not None and display_name != account.display_name:
        account.display_name = display_name
        changed = True
    if avatar_url is not None and avatar_url != account.avatar_url:
        account.avatar_url = avatar_url
        changed = True
    if changed:
        account.updated_at = now


def _account_identity(account: Account, provider: str) -> AccountIdentity:
    return AccountIdentity(
        owner_id=account.owner_id,
        display_name=account.display_name,
        avatar_url=account.avatar_url,
        provider=provider,
    )
