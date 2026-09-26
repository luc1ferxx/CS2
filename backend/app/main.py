import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, coaching, demos, diagnostics, private_media, replay, steam, uploads
from app.core.access_log import (
    install_auth_callback_access_log_redaction,
    suppress_outbound_http_request_logging,
)
from app.core.auth import SessionCsrfMiddleware
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.features import api_docs_kwargs
from app.core.json_compression import JsonGzipMiddleware
from app.core.redis import get_redis_client
from app.core.request_limits import (
    MultipartRequestLimitMiddleware,
    SensitiveJsonRequestLimitMiddleware,
)
from app.services.artifact_intake import ArtifactIntakeError, ArtifactIntakePolicy, ArtifactIntakeService
from app.services.storage import artifact_store_from_settings
from app.services.upload_quota import upload_quota_precheck

logger = logging.getLogger(__name__)


def on_startup() -> None:
    settings.validate_runtime_configuration()
    init_db()
    store = artifact_store_from_settings()
    try:
        ArtifactIntakeService(
            store,
            policy=ArtifactIntakePolicy(
                max_source_bytes=settings.max_demo_upload_bytes,
                stream_chunk_bytes=settings.upload_chunk_bytes,
                quarantine_ttl_seconds=settings.artifact_quarantine_ttl_seconds,
            ),
        ).cleanup_abandoned()
    except ArtifactIntakeError:
        logger.warning("Artifact quarantine cleanup was unavailable during startup")
    if settings.artifact_storage_backend == "local":
        for storage_dir in (
            settings.replay_storage_dir,
            settings.demo_upload_storage_dir,
            settings.video_storage_dir,
            settings.summary_storage_dir,
        ):
            storage_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Once per process, before the first request. This is the lifespan form
    # of the deprecated @app.on_event("startup") hook; the tasks themselves
    # stay in on_startup() so they remain a plain function to call and patch.
    on_startup()
    yield


app = FastAPI(
    title="CS2 Demo AI Coach Mock API",
    version="0.1.0",
    lifespan=lifespan,
    **api_docs_kwargs(settings),
)
install_auth_callback_access_log_redaction()
suppress_outbound_http_request_logging()

# JSON only: media Range responses and streamed downloads pass through untouched.
# Added first so it sits innermost, next to the routes: SessionCsrfMiddleware is
# a BaseHTTPMiddleware, and anything outside it sees every body re-streamed in chunks.
app.add_middleware(JsonGzipMiddleware, minimum_size=1024, compresslevel=5)
multipart_envelope_overhead = 8 * 1024 * 1024
app.add_middleware(
    MultipartRequestLimitMiddleware,
    demo_envelope_limit_bytes=settings.max_demo_upload_bytes + multipart_envelope_overhead,
    video_envelope_limit_bytes=settings.max_video_upload_bytes + multipart_envelope_overhead,
    worker_media_envelope_limit_bytes=settings.max_video_upload_bytes
    + multipart_envelope_overhead,
    worker_result_envelope_limit_bytes=64 * 1024,
    render_worker_token=settings.render_worker_token,
    max_concurrent_uploads=1,
    manual_video_upload_enabled=settings.auth_mode != "production",
    demo_upload_precheck=(
        upload_quota_precheck(SessionLocal) if settings.auth_mode == "production" else None
    ),
)
app.add_middleware(SensitiveJsonRequestLimitMiddleware)
app.add_middleware(SessionCsrfMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.runtime_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

app.include_router(demos.router)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(replay.router)
app.include_router(coaching.router)
app.include_router(diagnostics.router)
app.include_router(private_media.router)
app.include_router(steam.router)


@app.get(
    "/health",
    responses={503: {"description": 'A dependency check failed; the body is {"status": "degraded"}.'}},
)
def health() -> JSONResponse:
    db_ok = False
    redis_ok = False

    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        redis_ok = bool(get_redis_client().ping())
    except Exception:
        redis_ok = False

    worker_dependencies_ok = bool(
        settings.redis_queue_name
        and settings.redis_url
        and settings.render_worker_token
        and settings.max_render_clip_seconds > 0
    )

    # The status code carries the verdict so Caddy, uptime checks and deploy
    # scripts can fail on it without parsing the body; the body stays coarse.
    if db_ok and redis_ok and worker_dependencies_ok:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "degraded"}, status_code=503)
