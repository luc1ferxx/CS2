"""Small pure helpers shared by the DemoService components: job metadata, datetimes, compact copy and typed reads of loose dicts."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.job import DemoJob
from app.schemas.demo import RenderClipRequest, RenderWorkerResult
from app.services.demo_service.constants import RENDER_CLIP_DEFAULT_PRESET
from app.services.storage import LocalStorageService, StorageKeyError


def utc_now() -> datetime:
    return datetime.now(UTC)


def _int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _ingestion_phase(status: str) -> str:
    if status == "queued":
        return "uploaded"
    if status == "completed":
        return "ready"
    return status


def _compact_failure_message(message: str | None, *, max_length: int = 240) -> str:
    first_line = next((line.strip() for line in (message or "").splitlines() if line.strip()), "")
    compact = first_line or "Parser failed"
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def _metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, separators=(",", ":"))


def _aware_datetime(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_datetime(parsed)


def _positive_int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    parsed = int(value)
    return parsed if parsed > 0 else default


def _compact_render_clip_metadata(
    request: RenderClipRequest,
    *,
    duration_seconds: float,
    max_duration_seconds: int,
    demo_storage_key: str,
    replay_storage_key: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "tickStart": request.tickStart,
        "tickEnd": request.tickEnd,
        "tickRate": request.tickRate,
        "durationSeconds": round(duration_seconds, 3),
        "maxDurationSeconds": max_duration_seconds,
        "demoStorageKey": demo_storage_key,
        "replayStorageKey": replay_storage_key,
        "renderPreset": request.renderPreset or RENDER_CLIP_DEFAULT_PRESET,
    }
    optional_fields = {
        "eventId": request.eventId,
        "playerId": request.playerId,
        "povSteamId": request.povSteamId,
        "roundNumber": request.roundNumber,
    }
    for key, value in optional_fields.items():
        if value is not None:
            metadata[key] = value
    return metadata


def _job_metadata(job: DemoJob) -> dict[str, Any]:
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _render_output_reference(
    result: RenderWorkerResult,
    storage: LocalStorageService,
) -> dict[str, str] | None:
    if result.storageKey:
        video_url = storage.media_url(result.storageKey)
        if result.videoUrl and result.videoUrl != video_url:
            raise ValueError("videoUrl does not match storageKey")
        return {"url": video_url, "storageKey": result.storageKey}

    if result.videoUrl:
        storage_key = storage.storage_key_from_media_url(result.videoUrl)
        return {"url": result.videoUrl, "storageKey": storage_key}

    if not result.localMediaPath:
        return None

    if result.localMediaPath.startswith("/media/videos/"):
        storage_key = storage.storage_key_from_media_url(result.localMediaPath)
        return {"url": result.localMediaPath, "storageKey": storage_key}

    local_path = Path(result.localMediaPath)
    try:
        video_url = storage.media_url_for_local_path(local_path)
        storage_key = storage.storage_key_from_media_url(video_url)
    except StorageKeyError as exc:
        raise ValueError(str(exc)) from exc
    return {"url": video_url, "storageKey": storage_key}


def _required_int(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    if value is None:
        raise ValueError(f"Render job metadata is missing {key}")
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
