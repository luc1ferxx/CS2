from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.config import settings
from app.core.database import get_db
from app.schemas.demo import (
    DemoListItem,
    DemoStatus,
    DemoUpdate,
    RenderClipRequest,
    RenderJobManifest,
    RenderJobCreated,
    RenderJobStatus,
    RenderWorkerResult,
    RenderWorkerResultAccepted,
    RenderWorkerMediaUpload,
    ReplayVideoStatus,
    VideoCalibrationUpdate,
)
from app.services.demo_service import DemoService
from app.services.upload_service import DemoUploadValidationError, store_video_upload

router = APIRouter(tags=["demos"])


def require_render_worker_token(
    x_render_worker_token: str | None = Header(default=None, alias="X-Render-Worker-Token"),
) -> None:
    if x_render_worker_token != settings.render_worker_token:
        raise HTTPException(status_code=401, detail="Invalid render worker token")


@router.get("/demos", response_model=list[DemoListItem])
def list_demos(
    search: str | None = None,
    status: str | None = None,
    map_name: str | None = Query(default=None, alias="map"),
    sort: str = "recent",
    order: str | None = None,
    include_archived: bool = Query(default=False, alias="includeArchived"),
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> list[DemoListItem]:
    return DemoService(db, owner_id=owner_id).list_demos(
        search=search,
        status=status,
        map_name=map_name,
        sort=sort,
        order=order,
        include_archived=include_archived,
    )


@router.patch("/demos/{demo_id}", response_model=DemoListItem)
def update_demo(
    demo_id: str,
    update: DemoUpdate,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoListItem:
    service = DemoService(db, owner_id=owner_id)
    try:
        demo = service.update_demo(demo_id, name=update.name, archived=update.archived)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return service.demo_list_item(demo)


@router.post("/demos/{demo_id}/archive", response_model=DemoListItem)
def archive_demo(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoListItem:
    service = DemoService(db, owner_id=owner_id)
    demo = service.archive_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return service.demo_list_item(demo)


@router.post("/demos/{demo_id}/parse/retry", response_model=DemoListItem)
def retry_demo_parse(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoListItem:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    try:
        return service.retry_parse_job(demo)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def render_job_created_response(
    job_status: RenderJobStatus,
    video: ReplayVideoStatus,
) -> RenderJobCreated:
    return RenderJobCreated(**job_status.model_dump(), video=video)


@router.get("/demos/{demo_id}/status", response_model=DemoStatus)
def get_demo_status(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoStatus:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return service.demo_status(demo)


@router.get("/demos/{demo_id}/video", response_model=ReplayVideoStatus)
def get_demo_video(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> ReplayVideoStatus:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return ReplayVideoStatus.model_validate(service.get_video_status(demo))


@router.post("/demos/{demo_id}/video/upload", response_model=ReplayVideoStatus)
async def upload_demo_video(
    demo_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> ReplayVideoStatus:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Demo parse must complete before video upload")

    try:
        stored_video = await store_video_upload(demo.id, file)
        video = service.attach_manual_video(demo, stored_video)
    except DemoUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ReplayVideoStatus.model_validate(video)


@router.post("/demos/{demo_id}/video/calibration", response_model=ReplayVideoStatus)
def update_demo_video_calibration(
    demo_id: str,
    calibration: VideoCalibrationUpdate,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> ReplayVideoStatus:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")

    try:
        video = service.update_video_calibration(
            demo,
            duration_seconds=calibration.durationSeconds,
            tick_start=calibration.tickStart,
            tick_end=calibration.tickEnd,
            tick_rate=calibration.tickRate,
            time_origin_seconds=calibration.timeOriginSeconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ReplayVideoStatus.model_validate(video)


@router.post("/demos/{demo_id}/render/mock", response_model=RenderJobCreated, status_code=201)
def create_mock_render_job(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> RenderJobCreated:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Demo parse must complete before rendering")

    try:
        job = service.create_mock_render_job(demo)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job_status = service.render_job_status(job)
    return render_job_created_response(
        job_status,
        ReplayVideoStatus.model_validate(service.get_video_status(demo)),
    )


@router.post("/demos/{demo_id}/render/clip", response_model=RenderJobCreated, status_code=201)
def create_render_clip_job(
    demo_id: str,
    request: RenderClipRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> RenderJobCreated:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Demo parse must complete before rendering")

    try:
        job = service.create_render_clip_job(demo, request)
    except ValueError as exc:
        status_code = 409 if "Replay blob" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    job_status = service.render_job_status(job)
    return render_job_created_response(
        job_status,
        ReplayVideoStatus.model_validate(service.get_video_status(demo)),
    )


@router.get("/demos/{demo_id}/render/jobs", response_model=list[RenderJobStatus])
def list_render_clip_jobs(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> list[RenderJobStatus]:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    return service.list_render_clip_jobs(demo)


@router.get(
    "/render-worker/jobs/next",
    response_model=RenderJobManifest,
    tags=["render-worker"],
)
def get_next_render_worker_manifest(
    claim: bool = True,
    _: None = Depends(require_render_worker_token),
    db: Session = Depends(get_db),
) -> RenderJobManifest | Response:
    service = DemoService(db)
    job = service.next_render_clip_job()
    if job is None:
        return Response(status_code=204)

    try:
        if claim:
            job = service.claim_render_clip_job(job)
        return service.render_job_manifest(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/render-worker/jobs/{job_id}/manifest",
    response_model=RenderJobManifest,
    tags=["render-worker"],
)
def get_render_worker_manifest(
    job_id: str,
    claim: bool = True,
    _: None = Depends(require_render_worker_token),
    db: Session = Depends(get_db),
) -> RenderJobManifest:
    service = DemoService(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")

    try:
        if claim:
            job = service.claim_render_clip_job(job)
        return service.render_job_manifest(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/render-worker/jobs/{job_id}/media",
    response_model=RenderWorkerMediaUpload,
    tags=["render-worker"],
)
async def upload_render_worker_media(
    job_id: str,
    file: UploadFile = File(...),
    _: None = Depends(require_render_worker_token),
    db: Session = Depends(get_db),
) -> RenderWorkerMediaUpload:
    service = DemoService(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")

    try:
        stored_video = await store_video_upload(job.demo_id, file)
    except DemoUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RenderWorkerMediaUpload(
        jobId=job.id,
        demoId=job.demo_id,
        videoUrl=stored_video.url,
        storageKey=stored_video.storage_key,
        originalFilename=stored_video.original_filename,
        sizeBytes=stored_video.size_bytes,
    )


@router.post(
    "/render-worker/jobs/{job_id}/result",
    response_model=RenderWorkerResultAccepted,
    tags=["render-worker"],
)
def apply_render_worker_result(
    job_id: str,
    result: RenderWorkerResult,
    _: None = Depends(require_render_worker_token),
    db: Session = Depends(get_db),
) -> RenderWorkerResultAccepted:
    service = DemoService(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")

    try:
        video = service.apply_render_worker_result(job, result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RenderWorkerResultAccepted(
        job=service.render_job_status(job),
        video=ReplayVideoStatus.model_validate(video),
    )
