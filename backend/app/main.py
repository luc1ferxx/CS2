from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import auth, coaching, demos, diagnostics, private_media, replay, uploads
from app.core.config import settings
from app.core.auth import SessionCsrfMiddleware
from app.core.database import SessionLocal, init_db
from app.core.redis import get_redis_client


app = FastAPI(title="CS2 Demo AI Coach Mock API", version="0.1.0")

app.add_middleware(SessionCsrfMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.runtime_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(demos.router)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(replay.router)
app.include_router(coaching.router)
app.include_router(diagnostics.router)
app.include_router(private_media.router)


@app.on_event("startup")
def on_startup() -> None:
    settings.validate_runtime_configuration()
    init_db()
    for storage_dir in (
        settings.replay_storage_dir,
        settings.demo_upload_storage_dir,
        settings.video_storage_dir,
        settings.summary_storage_dir,
    ):
        storage_dir.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict[str, object]:
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

    return {"status": "ok" if db_ok and redis_ok and worker_dependencies_ok else "degraded"}
