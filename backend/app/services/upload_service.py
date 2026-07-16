from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.storage import (
    ArtifactReference,
    ArtifactStore,
    ArtifactStoreError,
    ArtifactTooLargeError,
    LocalStorageService,
    StorageWriteError,
)

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
    storage_key: str
    stored_path: Path
    size_bytes: int
    extension: str


@dataclass(frozen=True)
class StoredVideoUpload:
    original_filename: str
    storage_key: str
    stored_path: Path | None
    size_bytes: int
    extension: str
    url: str
    generation: str | None = None
    sha256: str | None = None


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
    return re.sub(r"[^A-Za-z0-9._-]+", "_", basename)[:255]


def demo_upload_path(demo_id: str, filename: str) -> Path:
    storage = LocalStorageService.from_settings()
    return storage.path_for_key(demo_upload_key(demo_id, filename))


def video_upload_path(demo_id: str, filename: str) -> Path:
    storage = LocalStorageService.from_settings()
    return storage.path_for_key(video_upload_key(demo_id, filename))


def demo_upload_key(demo_id: str, filename: str) -> str:
    return LocalStorageService.from_settings().demo_upload_key(
        demo_id,
        safe_upload_filename(filename),
    )


def video_upload_key(demo_id: str, filename: str) -> str:
    return LocalStorageService.from_settings().video_key(
        demo_id,
        safe_upload_filename(filename),
    )


async def store_demo_upload(demo_id: str, upload: Any) -> StoredDemoUpload:
    original_filename = safe_upload_filename(upload.filename or "demo.dem")
    validate_demo_upload(original_filename, 1)
    storage = LocalStorageService.from_settings()
    storage_key = storage.demo_upload_key(demo_id, original_filename)

    try:
        size_bytes = await storage.write_upload_stream(
            storage_key,
            upload,
            max_bytes=MAX_DEMO_UPLOAD_BYTES,
            chunk_size=UPLOAD_CHUNK_BYTES,
        )
        extension = validate_demo_upload(original_filename, size_bytes)
    except StorageWriteError as exc:
        raise DemoUploadValidationError("Uploaded demo exceeds the 1 GiB size limit") from exc
    except DemoUploadValidationError:
        storage.delete(storage_key)
        raise

    return StoredDemoUpload(
        original_filename=original_filename,
        storage_key=storage_key,
        stored_path=storage.path_for_key(storage_key),
        size_bytes=size_bytes,
        extension=extension,
    )


async def store_video_upload(demo_id: str, upload: Any) -> StoredVideoUpload:
    original_filename = safe_upload_filename(upload.filename or "video.mp4")
    validate_video_upload(original_filename, 1)
    stored_filename = f"{uuid.uuid4().hex}_{original_filename}"
    storage = LocalStorageService.from_settings()
    storage_key = storage.video_key(demo_id, stored_filename)

    try:
        size_bytes = await storage.write_upload_stream(
            storage_key,
            upload,
            max_bytes=MAX_VIDEO_UPLOAD_BYTES,
            chunk_size=UPLOAD_CHUNK_BYTES,
        )
        extension = validate_video_upload(original_filename, size_bytes)
    except StorageWriteError as exc:
        raise DemoUploadValidationError("Uploaded video exceeds the 2 GiB size limit") from exc
    except DemoUploadValidationError:
        storage.delete(storage_key)
        raise
    except Exception:
        storage.delete(storage_key)
        raise

    return StoredVideoUpload(
        original_filename=original_filename,
        storage_key=storage_key,
        stored_path=storage.path_for_key(storage_key),
        size_bytes=size_bytes,
        extension=extension,
        url=storage.media_url(storage_key),
    )


def store_video_artifact(
    *,
    owner_id: str,
    demo_id: str,
    upload: Any,
    store: ArtifactStore,
    max_bytes: int = MAX_VIDEO_UPLOAD_BYTES,
    chunk_size: int = UPLOAD_CHUNK_BYTES,
) -> StoredVideoUpload:
    original_filename = safe_upload_filename(upload.filename or "video.mp4")
    validate_video_upload(original_filename, 1)
    stream = getattr(upload, "file", None)
    if stream is None or not hasattr(stream, "read"):
        raise DemoUploadValidationError("Video upload could not be read")
    try:
        stream.seek(0)
    except (AttributeError, OSError):
        raise DemoUploadValidationError("Video upload could not be read") from None

    quarantine: str | None = None
    accepted_reference: str | None = None
    try:
        quarantine = store.new_reference(
            owner_id=owner_id,
            demo_id=demo_id,
            kind="video",
            state="quarantine",
        )
        written = store.write_stream(
            quarantine,
            stream,
            max_bytes=max_bytes,
            chunk_size=chunk_size,
            content_type="video/mp4",
            policy_version="artifact_store_v1",
        )
        extension = validate_video_upload(original_filename, written.size_bytes)
        stream.close()
        promoted = store.promote(
            quarantine,
            expected_generation=written.generation,
            expected_size=written.size_bytes,
            expected_sha256=written.sha256,
        )
        accepted_reference = promoted.reference
        found = store.head(promoted.reference)
        if found != promoted:
            raise ArtifactStoreError("Video artifact verification failed")
    except ArtifactTooLargeError:
        _delete_artifact_candidate(store, quarantine, accepted_reference)
        raise DemoUploadValidationError("Uploaded video exceeds the 2 GiB size limit") from None
    except DemoUploadValidationError:
        _delete_artifact_candidate(store, quarantine, accepted_reference)
        raise
    except Exception:
        _delete_artifact_candidate(store, quarantine, accepted_reference)
        raise DemoUploadValidationError("Video artifact could not be stored") from None

    return StoredVideoUpload(
        original_filename=original_filename,
        storage_key=promoted.reference,
        stored_path=None,
        size_bytes=promoted.size_bytes,
        extension=extension,
        url=f"/demos/{demo_id}/media/video",
        generation=promoted.generation,
        sha256=promoted.sha256,
    )


def _delete_artifact_candidate(
    store: ArtifactStore,
    quarantine_reference: str | None,
    accepted_reference: str | None,
) -> None:
    candidates: list[str] = []
    if accepted_reference:
        candidates.append(accepted_reference)
    if quarantine_reference is not None:
        try:
            parsed = store.parse_reference(quarantine_reference)
            candidates.append(
                ArtifactReference(
                    owner_id=parsed.owner_id,
                    demo_id=parsed.demo_id,
                    kind=parsed.kind,
                    state="accepted",
                    artifact_id=parsed.artifact_id,
                ).to_uri()
            )
        except Exception:
            pass
        candidates.append(quarantine_reference)
    for reference in dict.fromkeys(candidates):
        try:
            store.delete(reference)
        except Exception:
            pass
