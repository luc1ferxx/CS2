from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.demo import DemoListItem, DemoStatus, RenderJobCreated, ReplayVideoStatus
from app.services.demo_service import DemoService

router = APIRouter(tags=["demos"])


@router.get("/demos", response_model=list[DemoListItem])
def list_demos(db: Session = Depends(get_db)) -> list[DemoListItem]:
    return DemoService(db).list_demos()


@router.get("/demos/{demo_id}/status", response_model=DemoStatus)
def get_demo_status(demo_id: str, db: Session = Depends(get_db)) -> DemoStatus:
    demo = DemoService(db).get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return DemoStatus.model_validate(demo)


@router.get("/demos/{demo_id}/video", response_model=ReplayVideoStatus)
def get_demo_video(demo_id: str, db: Session = Depends(get_db)) -> ReplayVideoStatus:
    service = DemoService(db)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return ReplayVideoStatus.model_validate(service.get_video_status(demo))


@router.post("/demos/{demo_id}/render/mock", response_model=RenderJobCreated, status_code=201)
def create_mock_render_job(
    demo_id: str,
    db: Session = Depends(get_db),
) -> RenderJobCreated:
    service = DemoService(db)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Demo parse must complete before rendering")

    try:
        job = service.create_mock_render_job(demo)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RenderJobCreated(
        job_id=job.id,
        demo_id=demo.id,
        job_type=job.job_type,
        status=job.status,
        video=ReplayVideoStatus.model_validate(service.get_video_status(demo)),
    )
