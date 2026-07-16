from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AdapterConfigError(ValueError):
    pass


class RenderWorkerClient(Protocol):
    def upload_media(self, job_id: str, media_path: Path) -> "UploadedMedia":
        ...

    def post_result(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class AdapterResult:
    action: str
    job_id: str | None
    message: str
    manifest_path: Path | None = None
    callback_payload: dict[str, Any] | None = None
    workspace_path: Path | None = None
    output_path: Path | None = None


@dataclass(frozen=True)
class UploadedMedia:
    video_url: str
    storage_key: str


def completed_payload(
    manifest: dict[str, Any],
    video_url: str,
    storage_key: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "completed",
        "videoUrl": video_url,
        "storageKey": storage_key,
        "localMediaPath": None,
        "tickStart": int(manifest["tickStart"]),
        "tickEnd": int(manifest["tickEnd"]),
        "tickRate": int(manifest["tickRate"]),
        "timeOriginSeconds": 0,
        "durationSeconds": clip_duration_seconds(manifest),
        "errorMessage": None,
    }


def failed_payload(manifest: dict[str, Any], error_message: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "videoUrl": None,
        "storageKey": None,
        "localMediaPath": None,
        "tickStart": int(manifest["tickStart"]),
        "tickEnd": int(manifest["tickEnd"]),
        "tickRate": int(manifest["tickRate"]),
        "timeOriginSeconds": 0,
        "durationSeconds": clip_duration_seconds(manifest),
        "errorMessage": error_message,
    }


def clip_duration_seconds(manifest: dict[str, Any]) -> float:
    tick_rate = max(1, int(manifest["tickRate"]))
    return round((int(manifest["tickEnd"]) - int(manifest["tickStart"])) / tick_rate, 3)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_api_media_url_path(path: Path | None) -> bool:
    return path is not None and str(path).startswith("/media/videos/")
