import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.database import get_db
from app.services.demo_service import DemoService

router = APIRouter(tags=["replay"])


@router.get(
    "/demos/{demo_id}/replay",
    response_class=Response,
    responses={200: {"description": "The replay contract.", "content": {"application/json": {}}}},
)
def get_replay(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> Response:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Replay is not ready")

    replay = service.public_replay(demo)
    if replay is None:
        raise HTTPException(status_code=404, detail="Replay blob not found")
    # Serialized once, compactly: a replay is tens of MB of plain JSON, and a
    # response-model pass over it roughly doubles the time to first byte.
    return Response(
        content=json.dumps(replay, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        media_type="application/json",
    )
