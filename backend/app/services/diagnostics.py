from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.demo import Demo
from app.models.job import DemoJob
from app.services.demo_service import RENDER_CLIP_JOB_TYPE, DemoService
from app.services.storage import (
    ArtifactStore,
    ArtifactStoreError,
    LocalStorageService,
    StorageKeyError,
    artifact_store_from_settings,
)


WORKER_HEARTBEAT_KEY = "cs2-demo-coach:worker:heartbeat"
WORKER_HEARTBEAT_TTL_SECONDS = 120
WORKER_HEARTBEAT_ALIVE_SECONDS = 90
MAX_RECENT_FAILURES = 10
MAX_MESSAGE_LENGTH = 240

_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w:])/(?:Users|private|var|tmp|data|Volumes)(?:/[^\s,;:)\"'\]]+)+"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def write_worker_heartbeat(redis_client: Any, *, now: datetime | None = None) -> None:
    seen_at = _aware_datetime(now or utc_now())
    redis_client.setex(
        WORKER_HEARTBEAT_KEY,
        WORKER_HEARTBEAT_TTL_SECONDS,
        json.dumps({"lastSeenAt": seen_at.isoformat()}, separators=(",", ":")),
    )


def read_worker_heartbeat(
    redis_client: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = redis_client.get(WORKER_HEARTBEAT_KEY)
    if payload is None:
        return {"alive": False, "lastSeenAt": None, "ageSeconds": None}
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")

    try:
        body = json.loads(str(payload))
    except json.JSONDecodeError:
        return {"alive": False, "lastSeenAt": None, "ageSeconds": None}

    last_seen = _parse_datetime(_optional_str(body.get("lastSeenAt")))
    if last_seen is None:
        return {"alive": False, "lastSeenAt": None, "ageSeconds": None}

    current = _aware_datetime(now or utc_now())
    age_seconds = max(0, int((current - last_seen).total_seconds()))
    return {
        "alive": age_seconds <= WORKER_HEARTBEAT_ALIVE_SECONDS,
        "lastSeenAt": last_seen.isoformat(),
        "ageSeconds": age_seconds,
    }


def build_system_diagnostics(
    db: Session,
    redis_client: Any,
    *,
    storage: LocalStorageService | ArtifactStore | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware_datetime(now or utc_now())
    database = _database_status(db)
    redis = _redis_status(redis_client)
    storage_status = _storage_status(storage or artifact_store_from_settings())
    worker_dependencies = _worker_dependency_status()

    jobs = {
        "counts": _job_counts(db) if database["ok"] else {},
        "recentFailures": _recent_failed_jobs(db) if database["ok"] else [],
    }
    worker = _worker_status(redis_client, current, database_ok=database["ok"], db=db)
    dependencies = {
        "database": database,
        "redis": redis,
        "storage": storage_status,
        "worker": worker_dependencies,
    }

    return {
        "status": "ok" if all(item["ok"] for item in dependencies.values()) else "degraded",
        "api": {
            "ok": True,
            "name": "CS2 Demo AI Coach Mock API",
            "version": "0.1.0",
        },
        "dependencies": dependencies,
        "worker": worker,
        "renderWorker": _render_worker_status(db) if database["ok"] else _unknown_render_worker_status(),
        "jobs": jobs,
        "generatedAt": current.isoformat(),
    }


def build_demo_diagnostics(service: DemoService, demo: Demo) -> dict[str, Any]:
    ingestion = service.demo_ingestion_status(demo)
    parse_job = service.latest_parse_job(demo)
    render_job = _latest_render_clip_job(service.db, demo.id)
    replay = _safe_load_replay(service, demo)
    video = _safe_video_status(service, demo)

    return {
        "demoId": demo.id,
        "status": demo.status,
        "mapName": demo.map_name,
        "updatedAt": _iso_datetime(demo.updated_at),
        "parse": {
            **ingestion.model_dump(mode="json"),
            "lastStatusTransitionAt": _last_job_transition_at(parse_job),
        },
        "storage": {
            "sourceDemo": _artifact_status(
                service,
                _source_storage_key_for_diagnostics(service, demo),
                demo=demo,
                kind="source",
            ),
            "replay": _artifact_status(
                service,
                getattr(demo, "replay_storage_key", None),
                demo=demo,
                kind="replay",
            ),
        },
        "video": {
            "status": _optional_str(video.get("status")),
            "source": _optional_str(video.get("source")),
            "urlAvailable": bool(video.get("url")),
            "errorMessage": compact_safe_message(_optional_str(video.get("errorMessage"))),
        },
        "render": _render_job_summary(service, render_job),
        "map": _map_diagnostics(replay, demo.map_name),
    }


def compact_safe_message(message: str | None, *, max_length: int = MAX_MESSAGE_LENGTH) -> str | None:
    if not message:
        return None
    first_line = next((line.strip() for line in str(message).splitlines() if line.strip()), "")
    if not first_line:
        return None
    if first_line.startswith("Traceback"):
        return "Job failed"
    compact = _ABSOLUTE_PATH_RE.sub("[path]", first_line)
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def _database_status(db: Session) -> dict[str, bool]:
    try:
        db.execute(text("select 1"))
        return {"ok": True}
    except Exception:
        return {"ok": False}


def _redis_status(redis_client: Any) -> dict[str, bool]:
    try:
        return {"ok": bool(redis_client.ping())}
    except Exception:
        return {"ok": False}


def _storage_status(storage: LocalStorageService | ArtifactStore) -> dict[str, Any]:
    if not isinstance(storage, LocalStorageService):
        return {
            "ok": True,
            "backend": settings.artifact_storage_backend,
            "private": True,
        }
    categories: dict[str, bool] = {}
    for category in sorted(storage.CATEGORIES):
        try:
            root = storage.category_roots[category]
            root.mkdir(parents=True, exist_ok=True)
            categories[category] = root.exists() and root.is_dir()
        except Exception:
            categories[category] = False
    return {"ok": all(categories.values()), "categories": categories}


def _worker_dependency_status() -> dict[str, Any]:
    queue_configured = bool(settings.redis_queue_name and settings.redis_url)
    token_configured = bool(settings.render_worker_token)
    max_render_seconds_ok = settings.max_render_clip_seconds > 0
    return {
        "ok": queue_configured and token_configured and max_render_seconds_ok,
        "queueConfigured": queue_configured,
        "renderWorkerTokenConfigured": token_configured,
        "maxRenderClipSecondsConfigured": max_render_seconds_ok,
    }


def _worker_status(
    redis_client: Any,
    now: datetime,
    *,
    database_ok: bool,
    db: Session,
) -> dict[str, Any]:
    try:
        queue_length = redis_client.llen(settings.redis_queue_name)
    except Exception:
        queue_length = None
    try:
        heartbeat = read_worker_heartbeat(redis_client, now=now)
    except Exception:
        heartbeat = {"alive": False, "lastSeenAt": None, "ageSeconds": None}

    return {
        "queueName": settings.redis_queue_name,
        "queueLength": queue_length,
        "heartbeat": heartbeat,
        "lastJobActivityAt": _latest_job_transition_at(db) if database_ok else None,
    }


def _job_counts(db: Session) -> dict[str, dict[str, int]]:
    rows = (
        db.query(DemoJob.job_type, DemoJob.status, func.count(DemoJob.id))
        .group_by(DemoJob.job_type, DemoJob.status)
        .all()
    )
    counts: dict[str, dict[str, int]] = {}
    for job_type, status, count in rows:
        counts.setdefault(str(job_type), {})[str(status)] = int(count)
    return counts


def _recent_failed_jobs(db: Session) -> list[dict[str, Any]]:
    jobs = (
        db.query(DemoJob)
        .filter(DemoJob.status == "failed")
        .order_by(desc(DemoJob.finished_at), desc(DemoJob.created_at))
        .limit(MAX_RECENT_FAILURES)
        .all()
    )
    return [_failed_job_summary(job) for job in jobs]


def _failed_job_summary(job: DemoJob) -> dict[str, Any]:
    metadata = _job_metadata(job)
    failure = metadata.get("failure") if isinstance(metadata.get("failure"), dict) else {}
    message = compact_safe_message(
        _optional_str(failure.get("message"))
        or job.error_message
        or "Job failed"
    )
    return {
        "jobId": job.id,
        "demoId": job.demo_id,
        "jobType": job.job_type,
        "status": job.status,
        "attempts": int(job.attempts or 0),
        "errorCode": _optional_str(failure.get("errorCode")) or _fallback_error_code(job),
        "message": message,
        "createdAt": _iso_datetime(job.created_at),
        "startedAt": _iso_datetime(job.started_at),
        "finishedAt": _iso_datetime(job.finished_at),
    }


def _render_worker_status(db: Session) -> dict[str, Any]:
    latest = (
        db.query(DemoJob)
        .filter(DemoJob.job_type == RENDER_CLIP_JOB_TYPE)
        .order_by(desc(DemoJob.created_at))
        .first()
    )
    if latest is None:
        return _unknown_render_worker_status()
    if latest.status == "completed":
        return {
            "status": "recent_activity",
            "connected": True,
            "lastJobId": latest.id,
            "lastStatus": latest.status,
            "lastActivityAt": _last_job_transition_at(latest),
        }
    if latest.status == "failed" and "GPU worker not connected" in (latest.error_message or ""):
        return {
            "status": "not_connected",
            "connected": False,
            "lastJobId": latest.id,
            "lastStatus": latest.status,
            "lastActivityAt": _last_job_transition_at(latest),
            "reason": "Latest render_clip job reached the local no-GPU fallback.",
        }
    return {
        "status": "unknown",
        "connected": None,
        "lastJobId": latest.id,
        "lastStatus": latest.status,
        "lastActivityAt": _last_job_transition_at(latest),
    }


def _unknown_render_worker_status() -> dict[str, Any]:
    return {"status": "unknown", "connected": None}


def _artifact_status(
    service: DemoService,
    storage_key: str | None,
    *,
    demo: Demo,
    kind: str,
) -> dict[str, Any]:
    if not storage_key:
        return {"keyPresent": False, "artifactPresent": None}
    if storage_key.startswith("artifact://"):
        try:
            service.artifact_store.require_binding(
                storage_key,
                owner_id=demo.owner_id,
                demo_id=demo.id,
                kind=kind,
                state="accepted",
            )
            exists = service.artifact_store.head(storage_key) is not None
        except ArtifactStoreError:
            exists = False
        return {"keyPresent": True, "artifactPresent": exists}
    try:
        if kind == "source" and not service.storage.upload_key_belongs_to_demo(
            demo.id,
            storage_key,
        ):
            return {"keyPresent": True, "artifactPresent": False}
        if kind == "replay" and not service.storage.replay_key_belongs_to_demo(
            demo.id,
            storage_key,
        ):
            return {"keyPresent": True, "artifactPresent": False}
        exists = service.storage.exists(storage_key)
    except (OSError, StorageKeyError):
        exists = False
    return {"keyPresent": True, "artifactPresent": exists}


def _source_storage_key_for_diagnostics(service: DemoService, demo: Demo) -> str | None:
    if getattr(demo, "source_storage_key", None):
        return demo.source_storage_key
    return None


def _safe_load_replay(service: DemoService, demo: Demo) -> dict[str, Any] | None:
    try:
        replay = service.load_replay_blob(demo)
    except (OSError, ValueError, json.JSONDecodeError, StorageKeyError):
        return None
    return replay if isinstance(replay, dict) else None


def _safe_video_status(service: DemoService, demo: Demo) -> dict[str, Any]:
    try:
        video = service.get_video_status(demo)
    except (OSError, ValueError, json.JSONDecodeError, StorageKeyError):
        return {"status": "unknown", "source": "unknown", "url": None, "errorMessage": None}
    return video if isinstance(video, dict) else {}


def _render_job_summary(service: DemoService, job: DemoJob | None) -> dict[str, Any]:
    if job is None:
        return {"latestJob": None}
    status = service.render_job_status(job)
    return {
        "latestJob": {
            "jobId": status.job_id,
            "status": status.status,
            "videoStatus": status.video_status,
            "tickStart": status.tick_start,
            "tickEnd": status.tick_end,
            "tickRate": status.tick_rate,
            "durationSeconds": status.duration_seconds,
            "roundNumber": status.round_number,
            "errorMessage": compact_safe_message(status.error_message),
            "createdAt": status.created_at.isoformat() if status.created_at else None,
            "startedAt": status.started_at.isoformat() if status.started_at else None,
            "finishedAt": status.finished_at.isoformat() if status.finished_at else None,
        }
    }


def _map_diagnostics(replay: dict[str, Any] | None, fallback_map_name: str | None) -> dict[str, Any]:
    metadata = replay.get("mapMetadata") if isinstance(replay, dict) else None
    if not isinstance(metadata, dict):
        return {
            "mapName": fallback_map_name or "unknown",
            "calibration": "unavailable",
            "confidence": None,
        }
    calibrated = metadata.get("calibrated")
    return {
        "mapName": _optional_str(metadata.get("mapName")) or fallback_map_name or "unknown",
        "displayName": _optional_str(metadata.get("displayName")),
        "calibration": "calibrated" if calibrated is True else "fallback",
        "confidence": _optional_str(metadata.get("confidence")),
    }


def _latest_render_clip_job(db: Session, demo_id: str) -> DemoJob | None:
    return (
        db.query(DemoJob)
        .filter(DemoJob.demo_id == demo_id, DemoJob.job_type == RENDER_CLIP_JOB_TYPE)
        .order_by(desc(DemoJob.created_at))
        .first()
    )


def _latest_job_transition_at(db: Session) -> str | None:
    latest = db.query(DemoJob).order_by(desc(DemoJob.created_at)).first()
    return _last_job_transition_at(latest)


def _last_job_transition_at(job: DemoJob | None) -> str | None:
    if job is None:
        return None
    value = job.finished_at or job.started_at or job.created_at
    return _iso_datetime(value)


def _fallback_error_code(job: DemoJob) -> str:
    if job.job_type in {"real_parse", "mock_parse"}:
        return "PARSER_FAILED"
    if job.job_type == RENDER_CLIP_JOB_TYPE:
        return "RENDER_CLIP_FAILED"
    return "JOB_FAILED"


def _job_metadata(job: DemoJob) -> dict[str, Any]:
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_datetime(parsed)


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware_datetime(value).isoformat()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
