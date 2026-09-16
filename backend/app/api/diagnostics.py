from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.config import settings
from app.core.database import get_db
from app.core.redis import get_redis_client
from app.services.demo_service import DemoService
from app.services.diagnostics import (
    build_demo_diagnostics,
    build_system_diagnostics,
    read_worker_heartbeat,
    write_worker_heartbeat,
)

router = APIRouter(tags=["diagnostics"])


@router.get("/diagnostics")
def get_diagnostics(db: Session = Depends(get_db)) -> dict[str, Any]:
    if settings.auth_mode == "production":
        raise HTTPException(status_code=404, detail="Not found")
    return build_system_diagnostics(db, get_redis_client())


@router.get("/demos/{demo_id}/diagnostics")
def get_demo_diagnostics(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> dict[str, Any]:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return build_demo_diagnostics(service, demo)


__all__ = [
    "read_worker_heartbeat",
    "router",
    "write_worker_heartbeat",
]
