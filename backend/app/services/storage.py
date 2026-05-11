from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from app.core.config import settings


class StorageKeyError(ValueError):
    pass


class StorageWriteError(RuntimeError):
    pass


class LocalStorageService:
    CATEGORIES = {"uploads", "replays", "summaries", "videos"}

    def __init__(
        self,
        artifact_root: Path,
        category_roots: Mapping[str, Path] | None = None,
    ):
        self.artifact_root = artifact_root
        self.category_roots = {
            category: (category_roots[category] if category_roots and category in category_roots else artifact_root / category)
            for category in self.CATEGORIES
        }

    @classmethod
    def from_settings(cls) -> "LocalStorageService":
        return cls(
            settings.artifact_storage_root,
            {
                "uploads": settings.demo_upload_storage_dir,
                "replays": settings.replay_storage_dir,
                "summaries": settings.summary_storage_dir,
                "videos": settings.video_storage_dir,
            },
        )

    def key(self, category: str, *segments: str) -> str:
        self._validate_category(category)
        if not segments:
            raise StorageKeyError("Storage key requires at least one path segment")
        safe_segments = [self._validate_segment(segment) for segment in segments]
        return f"local://{category}/{'/'.join(safe_segments)}"

    def demo_upload_key(self, demo_id: str, filename: str) -> str:
        return self.key("uploads", demo_id, filename)

    def replay_key(self, demo_id: str) -> str:
        return self.key("replays", f"{demo_id}.json")

    def summary_key(self, demo_id: str, filename: str) -> str:
        return self.key("summaries", demo_id, filename)

    def video_key(self, demo_id: str, filename: str) -> str:
        return self.key("videos", demo_id, filename)

    def path_for_key(self, storage_key: str) -> Path:
        category, segments = self._parse_key(storage_key)
        root = self.category_roots[category]
        path = root.joinpath(*segments)
        self._ensure_within_root(path, root)
        return path

    def exists(self, storage_key: str) -> bool:
        return self.path_for_key(storage_key).exists()

    def read_bytes(self, storage_key: str) -> bytes:
        return self.path_for_key(storage_key).read_bytes()

    def write_bytes(self, storage_key: str, data: bytes) -> Path:
        path = self.path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read_json(self, storage_key: str) -> dict[str, Any]:
        return json.loads(self.path_for_key(storage_key).read_text(encoding="utf-8"))

    def write_json(self, storage_key: str, payload: dict[str, Any]) -> Path:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.write_bytes(storage_key, data)

    async def write_upload_stream(
        self,
        storage_key: str,
        upload: Any,
        *,
        max_bytes: int,
        chunk_size: int,
    ) -> int:
        path = self.path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        size_bytes = 0
        try:
            with path.open("wb") as handle:
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise StorageWriteError("Artifact exceeds configured size limit")
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return size_bytes

    def delete(self, storage_key: str) -> None:
        self.path_for_key(storage_key).unlink(missing_ok=True)

    def media_url(self, storage_key: str) -> str:
        category, segments = self._parse_key(storage_key)
        if category != "videos":
            raise StorageKeyError("Only video artifacts can produce media URLs")
        return f"/media/videos/{'/'.join(segments)}"

    def storage_key_from_media_url(self, url: str) -> str:
        prefix = "/media/videos/"
        if not url.startswith(prefix):
            raise StorageKeyError("videoUrl must use /media/videos/... for local storage")
        segments = [segment for segment in url[len(prefix) :].split("/") if segment]
        return self.key("videos", *segments)

    def media_url_for_local_path(self, local_path: Path) -> str:
        if not local_path.is_absolute():
            raise StorageKeyError("localMediaPath must be /media/videos/... or an absolute path")
        video_root = self.category_roots["videos"]
        try:
            relative_path = local_path.resolve().relative_to(video_root.resolve())
        except ValueError as exc:
            raise StorageKeyError("localMediaPath must be inside video storage") from exc
        return f"/media/videos/{relative_path.as_posix()}"

    def _parse_key(self, storage_key: str) -> tuple[str, list[str]]:
        parsed = urlparse(storage_key)
        if parsed.scheme != "local" or not parsed.netloc:
            raise StorageKeyError("Storage key must use local://<category>/...")
        category = parsed.netloc
        self._validate_category(category)
        segments = [self._validate_segment(segment) for segment in parsed.path.split("/") if segment]
        if not segments:
            raise StorageKeyError("Storage key requires at least one path segment")
        return category, segments

    def _validate_category(self, category: str) -> None:
        if category not in self.CATEGORIES:
            raise StorageKeyError(f"Unsupported storage category: {category}")

    def _validate_segment(self, segment: str) -> str:
        value = str(segment).strip()
        if not value or value in {".", ".."}:
            raise StorageKeyError("Storage key path segment cannot be blank or relative")
        if "/" in value or "\\" in value or "\x00" in value:
            raise StorageKeyError("Storage key path segment cannot contain path separators")
        return value

    def _ensure_within_root(self, path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise StorageKeyError("Storage path escaped its configured root") from exc
