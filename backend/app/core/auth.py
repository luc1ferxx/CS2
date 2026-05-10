from typing import Annotated

from fastapi import Header

from app.core.config import settings


DEV_OWNER_HEADER = "X-Dev-User-Id"


def normalize_owner_id(owner_id: str | None = None) -> str:
    normalized = (owner_id or settings.dev_user_id).strip()
    return normalized or settings.dev_user_id


def get_current_owner_id(
    x_dev_user_id: Annotated[str | None, Header(alias=DEV_OWNER_HEADER)] = None,
) -> str:
    return normalize_owner_id(x_dev_user_id)
