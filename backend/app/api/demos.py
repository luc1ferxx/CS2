import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.auth import get_current_owner_id
from app.core.config import settings
from app.core.database import get_db
from app.core.features import require_dev_tools, require_render_clips
from app.core.redis import get_redis_client
from app.schemas.demo import (
    DemoListItem,
    DemoStatus,
    DemoUpdate,
    RenderClipRequest,
    RenderJobCreated,
    RenderJobManifest,
    RenderJobStatus,
    RenderWorkerMediaUpload,
    RenderWorkerResult,
    RenderWorkerResultAccepted,
    RenderWorkerStatus,
    ReplayVideoStatus,
    VideoCalibrationUpdate,
)
from app.services.demo_service import (
    RENDER_CLIP_IDLE_RECLAIM_SECONDS,
    DemoDispatchError,
    DemoGoneError,
    DemoService,
    ReplayBlobUnavailableError,
)
from app.services.diagnostics import render_worker_availability, write_render_worker_heartbeat
from app.services.upload_quota import UploadQuotaExceeded, retry_admission
from app.services.upload_service import DemoUploadValidationError, store_video_artifact

logger = logging.getLogger(__name__)

router = APIRouter(tags=["demos"])


def require_render_worker_token(
    x_render_worker_token: str | None = Header(default=None, alias="X-Render-Worker-Token"),
    redis_client: Any = Depends(get_redis_client),
) -> None:
    if not x_render_worker_token or not secrets.compare_digest(
        x_render_worker_token,
        settings.render_worker_token,
    ):
        raise HTTPException(status_code=401, detail="Invalid render worker token")
    # Every render-worker route funnels through here, so one write covers the
    # whole surface. The heartbeat is an observability signal: a Redis outage
    # must never turn a healthy render worker's request into a failure.
    try:
        write_render_worker_heartbeat(redis_client)
    except Exception:
        pass


def _reclaim_orphaned_render_clip_jobs(service: DemoService) -> None:
    """Free jobs a dead render worker left on "rendering" before handing out work.

    The caller of this route is a render worker asking for something to do, so
    it is not rendering anything right now. With a single consumer that makes
    every "rendering" row an orphan, and the request itself is the proof -- no
    heartbeat lookup needed. RENDER_CLIP_IDLE_RECLAIM_SECONDS still protects a
    job claimed moments ago against clock skew.

    Best-effort on purpose: a reclaim that raises must not 500 the poll loop,
    or one bad row would stall every render on the deployment.
    """
    if not settings.render_clip_single_consumer:
        return
    try:
        service.reclaim_stale_render_clip_jobs(
            older_than_seconds=RENDER_CLIP_IDLE_RECLAIM_SECONDS,
        )
    except Exception:
        logger.exception("Failed to reclaim orphaned render_clip jobs")


# A demo deleted mid-request surfaces as whatever the operation tripped over
# first: an SQLAlchemy error on a vanished row, or a 400/409 about a missing
# replay or artifact. Each of these answers 404 once the row is confirmed gone.
_GONE_MASKING_STATUSES = frozenset({400, 409})


@contextmanager
def deleted_demo_is_404(service: DemoService, demo_id: str) -> Iterator[None]:
    """Answer 404, never a 500 or a misleading 409, for a demo deleted mid-request."""
    try:
        yield
    except DemoGoneError:
        raise HTTPException(status_code=404, detail="Demo not found") from None
    except HTTPException as exc:
        if exc.status_code in _GONE_MASKING_STATUSES and service.demo_missing(demo_id):
            raise HTTPException(status_code=404, detail="Demo not found") from None
        raise
    except Exception:
        if service.demo_missing(demo_id):
            raise HTTPException(status_code=404, detail="Demo not found") from None
        raise


@contextmanager
def deleted_render_job_is_404(service: DemoService, job_id: str) -> Iterator[None]:
    """The render-worker twin: a job whose match was deleted is uniformly 404.

    The runner treats 404 as terminal (no failed callback, no retry), which is
    exactly right for work nobody is waiting for any more.
    """
    try:
        yield
    except DemoGoneError:
        raise HTTPException(status_code=404, detail="Render clip job not found") from None
    except HTTPException as exc:
        if exc.status_code in _GONE_MASKING_STATUSES and service.render_job_missing(job_id):
            raise HTTPException(status_code=404, detail="Render clip job not found") from None
        raise
    except Exception:
        if service.render_job_missing(job_id):
            raise HTTPException(status_code=404, detail="Render clip job not found") from None
        raise


def _require_render_job(service: DemoService, job_id: str) -> None:
    """Re-check a render job right before answering with what was read for it.

    The job and its demo are loaded once per request; a match deleted after
    that must not still get its manifest or source handed to the renderer.
    """
    if service.render_job_missing(job_id):
        raise HTTPException(status_code=404, detail="Render clip job not found")


def render_worker_status_response(db: Session, redis_client: Any) -> RenderWorkerStatus:
    availability = render_worker_availability(db, redis_client)
    return RenderWorkerStatus(
        mode=availability["mode"],
        required=availability["required"],
        connected=availability["connected"],
        status=availability["status"],
        last_seen_at=availability["lastSeenAt"],
        age_seconds=availability["ageSeconds"],
        busy_rendering=availability["busyRendering"],
    )


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
    with deleted_demo_is_404(service, demo_id):
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
    with deleted_demo_is_404(service, demo_id):
        demo = service.archive_demo(demo_id)
        if demo is None:
            raise HTTPException(status_code=404, detail="Demo not found")
        return service.demo_list_item(demo)


@router.post("/demos/{demo_id}/parse/retry", response_model=DemoListItem)
def retry_demo_parse(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoListItem | JSONResponse:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    with deleted_demo_is_404(service, demo_id):
        try:
            # Eligibility (409) is checked first; the in-flight count and the
            # commit that queues the demo then share one admission, which also
            # locks the demo (a deletion that won the race is a 404), and
            # dispatch runs after.
            return service.retry_parse_job(
                demo,
                admission=lambda: retry_admission(db, owner_id),
            )
        except UploadQuotaExceeded as exc:
            return exc.to_response()
        except DemoDispatchError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


def render_job_created_response(
    job_status: RenderJobStatus,
    video: ReplayVideoStatus,
    render_worker: RenderWorkerStatus | None = None,
) -> RenderJobCreated:
    return RenderJobCreated(
        **job_status.model_dump(exclude={"video"}),
        video=job_status.video or video,
        render_worker=render_worker,
    )


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
    with deleted_demo_is_404(service, demo_id):
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
    with deleted_demo_is_404(service, demo_id):
        return ReplayVideoStatus.model_validate(service.public_video_status(demo))


@router.post(
    "/demos/{demo_id}/video/upload",
    response_model=ReplayVideoStatus,
    dependencies=[Depends(require_dev_tools)],
)
def upload_demo_video(
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

    with deleted_demo_is_404(service, demo_id):
        try:
            stored_video = store_video_artifact(
                owner_id=demo.owner_id,
                demo_id=demo.id,
                upload=file,
                store=service.artifact_store,
                max_bytes=settings.max_video_upload_bytes,
                chunk_size=settings.upload_chunk_bytes,
            )
            service.attach_manual_video(demo, stored_video)
        except DemoUploadValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return ReplayVideoStatus.model_validate(service.public_video_status(demo))


@router.post(
    "/demos/{demo_id}/video/calibration",
    response_model=ReplayVideoStatus,
    dependencies=[Depends(require_dev_tools)],
)
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

    with deleted_demo_is_404(service, demo_id):
        try:
            service.update_video_calibration(
                demo,
                duration_seconds=calibration.durationSeconds,
                tick_start=calibration.tickStart,
                tick_end=calibration.tickEnd,
                tick_rate=calibration.tickRate,
                time_origin_seconds=calibration.timeOriginSeconds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return ReplayVideoStatus.model_validate(service.public_video_status(demo))


@router.post(
    "/demos/{demo_id}/render/mock",
    response_model=RenderJobCreated,
    status_code=201,
    dependencies=[Depends(require_dev_tools)],
)
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

    with deleted_demo_is_404(service, demo_id):
        try:
            job = service.create_mock_render_job(demo)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        job_status = service.render_job_status(job)
        return render_job_created_response(
            job_status,
            ReplayVideoStatus.model_validate(service.public_video_status(demo)),
        )


@router.post(
    "/demos/{demo_id}/render/clip",
    response_model=RenderJobCreated,
    status_code=201,
    dependencies=[Depends(require_render_clips)],
)
def create_render_clip_job(
    demo_id: str,
    request: RenderClipRequest,
    db: Session = Depends(get_db),
    redis_client: Any = Depends(get_redis_client),
    owner_id: str = Depends(get_current_owner_id),
) -> RenderJobCreated:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Demo parse must complete before rendering")

    with deleted_demo_is_404(service, demo_id):
        try:
            job = service.create_render_clip_job(demo, request)
        except ReplayBlobUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job_status = service.render_job_status(job)
        return render_job_created_response(
            job_status,
            ReplayVideoStatus.model_validate(service.public_video_status(demo)),
            # The job is durable and stays queued either way, but the caller
            # should learn immediately that nothing will pick it up yet.
            render_worker_status_response(db, redis_client),
        )


@router.post(
    "/demos/{demo_id}/render/jobs/{job_id}/retry",
    response_model=RenderJobCreated,
    dependencies=[Depends(require_render_clips)],
)
def retry_render_clip_job(
    demo_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    redis_client: Any = Depends(get_redis_client),
    owner_id: str = Depends(get_current_owner_id),
) -> RenderJobCreated:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")

    with deleted_demo_is_404(service, demo_id):
        try:
            job = service.retry_render_clip_job(demo, job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=404, detail="Render job not found")

        job_status = service.render_job_status(job)
        return render_job_created_response(
            job_status,
            ReplayVideoStatus.model_validate(service.public_video_status(demo)),
            # Same as creating a job: the row is durable, but the caller should
            # learn immediately whether anything is around to pick it up.
            render_worker_status_response(db, redis_client),
        )


@router.get("/render/worker", response_model=RenderWorkerStatus)
def get_render_worker_status(
    db: Session = Depends(get_db),
    redis_client: Any = Depends(get_redis_client),
    _: str = Depends(get_current_owner_id),
) -> RenderWorkerStatus:
    # The render worker is a single global process, not a per-demo resource, so
    # this is owner-authenticated but not demo-scoped.
    return render_worker_status_response(db, redis_client)


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
    with deleted_demo_is_404(service, demo_id):
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
    service = DemoService.for_internal(db)
    _reclaim_orphaned_render_clip_jobs(service)
    job = service.next_render_clip_job()
    if job is None:
        return Response(status_code=204)

    job_id = job.id
    try:
        with deleted_render_job_is_404(service, job_id):
            try:
                if claim:
                    job = service.claim_render_clip_job(job)
                manifest = service.render_job_manifest(job)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            _require_render_job(service, job_id)
            return manifest
    except HTTPException as exc:
        if exc.status_code == 404:
            # The match was deleted between the lookup and the claim. The
            # worker asked for "any job", not this one: nothing to hand out.
            return Response(status_code=204)
        raise


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
    service = DemoService.for_internal(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")

    with deleted_render_job_is_404(service, job_id):
        try:
            if claim:
                job = service.claim_render_clip_job(job)
            manifest = service.render_job_manifest(job)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _require_render_job(service, job_id)
        return manifest


@router.get("/render-worker/jobs/{job_id}/source", tags=["render-worker"])
def download_render_worker_source(
    job_id: str,
    _: None = Depends(require_render_worker_token),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    service = DemoService.for_internal(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")
    with deleted_render_job_is_404(service, job_id):
        try:
            opened, snapshot = service.open_render_source(job)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        try:
            # After opening, not before: a deletion that committed by now must
            # not hand its .dem out of storage its purge has not reached yet.
            _require_render_job(service, job_id)
        except BaseException:
            opened.close()
            raise
    return StreamingResponse(
        service.stream_render_source(opened, snapshot),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(snapshot.size_bytes),
            "Content-Disposition": 'attachment; filename="source.dem"',
            "X-Content-SHA256": snapshot.sha256,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(opened.close),
    )


@router.post(
    "/render-worker/jobs/{job_id}/media",
    response_model=RenderWorkerMediaUpload,
    tags=["render-worker"],
)
def upload_render_worker_media(
    job_id: str,
    file: UploadFile = File(...),
    _: None = Depends(require_render_worker_token),
    db: Session = Depends(get_db),
) -> RenderWorkerMediaUpload:
    service = DemoService.for_internal(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")
    if job.status != "rendering":
        raise HTTPException(
            status_code=409,
            detail="Render worker media requires a rendering job",
        )

    owner_id, demo_id = job.demo.owner_id, job.demo_id
    with deleted_render_job_is_404(service, job_id):
        try:
            stored_video = store_video_artifact(
                owner_id=owner_id,
                demo_id=demo_id,
                upload=file,
                store=service.artifact_store,
                max_bytes=settings.max_video_upload_bytes,
                chunk_size=settings.upload_chunk_bytes,
            )
            # Removes the stored MP4 itself when the match was deleted meanwhile.
            service.bind_render_worker_media(job, stored_video)
        except DemoUploadValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RenderWorkerMediaUpload(
        jobId=job_id,
        demoId=demo_id,
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
    service = DemoService.for_internal(db)
    job = service.get_render_clip_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render clip job not found")

    with deleted_render_job_is_404(service, job_id):
        try:
            video = service.apply_render_worker_result(job, result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return RenderWorkerResultAccepted(
            job=service.render_job_status(job),
            video=ReplayVideoStatus.model_validate(video),
        )
