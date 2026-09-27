from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.database import get_db
from app.schemas.coaching import (
    CoachingEventOut,
    CoachingFeedbackIn,
    CoachingFeedbackOut,
    CoachingFeedbackSummary,
)
from app.services.demo_service import DemoGoneError, DemoService

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


@router.put(
    "/demos/{demo_id}/coaching/{event_id}/feedback",
    response_model=CoachingFeedbackOut,
)
def save_coaching_feedback(
    demo_id: str,
    event_id: str,
    feedback: CoachingFeedbackIn,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> CoachingFeedbackOut:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    try:
        saved = service.save_coaching_feedback(demo, event_id, verdict=feedback.verdict, note=feedback.note)
    except DemoGoneError:
        # The match was deleted since the lookup above; its verdicts went with it.
        raise HTTPException(status_code=404, detail="Demo not found") from None
    if saved is None:
        raise HTTPException(status_code=404, detail="Coaching event not found")
    return saved


@router.delete(
    "/demos/{demo_id}/coaching/{event_id}/feedback",
    status_code=204,
    response_class=Response,
)
def clear_coaching_feedback(
    demo_id: str,
    event_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> Response:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    # Idempotent: clearing a verdict that was never given is still "no verdict".
    service.clear_coaching_feedback(demo, event_id)
    return Response(status_code=204)


@router.get("/coaching/feedback/summary", response_model=CoachingFeedbackSummary)
def coaching_feedback_summary(
    demo_id: str | None = None,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> CoachingFeedbackSummary:
    service = DemoService(db, owner_id=owner_id)
    if demo_id is not None and service.get_demo(demo_id) is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return service.coaching_feedback_summary(demo_id)
