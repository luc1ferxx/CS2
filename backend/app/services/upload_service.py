from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings

ALLOWED_DEMO_UPLOAD_EXTENSIONS = {".dem", ".zip"}
ALLOWED_VIDEO_UPLOAD_EXTENSIONS = {".mp4"}
MAX_DEMO_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


class DemoUploadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class StoredDemoUpload:
    original_filename: str
    stored_path: Path
    size_bytes: int
    extension: str


@dataclass(frozen=True)
class StoredVideoUpload:
    original_filename: str
    stored_path: Path
    size_bytes: int
    extension: str
    url: str


def validate_demo_upload(filename: str, size_bytes: int) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_DEMO_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_DEMO_UPLOAD_EXTENSIONS))
        raise DemoUploadValidationError(f"Only {allowed} uploads are supported")
    if size_bytes <= 0:
        raise DemoUploadValidationError("Uploaded demo is empty")
    if size_bytes > MAX_DEMO_UPLOAD_BYTES:
        raise DemoUploadValidationError("Uploaded demo exceeds the 1 GiB size limit")
    return extension


def validate_video_upload(filename: str, size_bytes: int) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_VIDEO_UPLOAD_EXTENSIONS:
        raise DemoUploadValidationError("Only .mp4 video uploads are supported")
    if size_bytes <= 0:
        raise DemoUploadValidationError("Uploaded video is empty")
    if size_bytes > MAX_VIDEO_UPLOAD_BYTES:
        raise DemoUploadValidationError("Uploaded video exceeds the 2 GiB size limit")
    return extension


def safe_upload_filename(filename: str) -> str:
    basename = Path(filename).name.strip() or "demo.dem"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", basename)


def demo_upload_path(demo_id: str, filename: str) -> Path:
    return settings.demo_upload_storage_dir / demo_id / safe_upload_filename(filename)


def video_upload_path(demo_id: str, filename: str) -> Path:
    return settings.video_storage_dir / demo_id / safe_upload_filename(filename)


async def store_demo_upload(demo_id: str, upload: Any) -> StoredDemoUpload:
    original_filename = safe_upload_filename(upload.filename or "demo.dem")
    target_path = demo_upload_path(demo_id, original_filename)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    size_bytes = 0
    with target_path.open("wb") as handle:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            size_bytes += len(chunk)
            if size_bytes > MAX_DEMO_UPLOAD_BYTES:
                handle.close()
                target_path.unlink(missing_ok=True)
                validate_demo_upload(original_filename, size_bytes)
            handle.write(chunk)

    extension = validate_demo_upload(original_filename, size_bytes)
    return StoredDemoUpload(
        original_filename=original_filename,
        stored_path=target_path,
        size_bytes=size_bytes,
        extension=extension,
    )


async def store_video_upload(demo_id: str, upload: Any) -> StoredVideoUpload:
    original_filename = safe_upload_filename(upload.filename or "video.mp4")
    validate_video_upload(original_filename, 1)
    stored_filename = f"{uuid.uuid4().hex}_{original_filename}"
    target_path = video_upload_path(demo_id, stored_filename)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    size_bytes = 0
    try:
        with target_path.open("wb") as handle:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_VIDEO_UPLOAD_BYTES:
                    handle.close()
                    target_path.unlink(missing_ok=True)
                    validate_video_upload(original_filename, size_bytes)
                handle.write(chunk)

        extension = validate_video_upload(original_filename, size_bytes)
    except DemoUploadValidationError:
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        raise
    except Exception:
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        raise

    return StoredVideoUpload(
        original_filename=original_filename,
        stored_path=target_path,
        size_bytes=size_bytes,
        extension=extension,
        url=f"/media/videos/{demo_id}/{target_path.name}",
    )
