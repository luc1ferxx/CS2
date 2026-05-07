from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.demo_service import DemoService

router = APIRouter(tags=["replay"])


@router.get("/demos/{demo_id}/replay")
def get_replay(demo_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    service = DemoService(db)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Replay is not ready")

    replay = service.load_replay_blob(demo)
    if replay is None:
        raise HTTPException(status_code=404, detail="Replay blob not found")
    return replay
