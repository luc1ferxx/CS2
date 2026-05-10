from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.database import get_db
from app.schemas.coaching import CoachingEventOut
from app.services.demo_service import DemoService

router = APIRouter(tags=["coaching"])


@router.get("/demos/{demo_id}/coaching", response_model=list[CoachingEventOut])
def get_coaching_events(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> list[CoachingEventOut]:
    service = DemoService(db, owner_id=owner_id)
    if service.get_demo(demo_id) is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return service.list_coaching_events(demo_id)
