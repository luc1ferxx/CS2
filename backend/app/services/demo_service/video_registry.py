"""Private video delivery and the dev/QA manual MP4 bridge: availability, owner-scoped open, attach, and tick calibration."""

import os
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings
from app.models.demo import Demo
from app.services.artifact_binding import (
    AcceptedArtifactError,
    VerifiedAcceptedArtifact,
    head_accepted_video,
    open_accepted_video_range,
)
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import _optional_str
from app.services.storage import ArtifactStore, StorageKeyError
from app.services.upload_service import StoredVideoUpload


class PrivateVideoHandle(Protocol):
    """What private media delivery needs from an opened video: a plain file
    handle from the legacy local adapter or an `_AcceptedVideoHandle`."""

    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def read(self, size: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


class _PrivateArtifactStat:
    def __init__(self, size_bytes: int):
        self.st_size = size_bytes


class _AcceptedVideoHandle:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        reference: str,
        owner_id: str,
        demo_id: str,
        verified: VerifiedAcceptedArtifact,
    ):
        self.store = store
        self.reference = reference
        self.owner_id = owner_id
        self.demo_id = demo_id
        self.verified = verified
        self._opened: Any | None = None
        self._closed = False

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if self._closed or whence != os.SEEK_SET or offset < 0:
            raise OSError("Private artifact seek is invalid")
        self._reopen(offset)
        return offset

    def _reopen(self, offset: int) -> Any:
        if self._opened is not None:
            self._opened.close()
        self._opened = open_accepted_video_range(
            self.store,
            self.reference,
            owner_id=self.owner_id,
            demo_id=self.demo_id,
            start=offset,
            end_inclusive=self.verified.snapshot.size_bytes - 1,
            verified=self.verified,
            max_bytes=settings.max_video_upload_bytes,
        )
        return self._opened

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise OSError("Private artifact read is closed")
        opened = self._opened if self._opened is not None else self._reopen(0)
        return opened.read(size)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._opened is not None:
                self._opened.close()


class VideoRegistry(ServiceComponent):
    """Reached as ``DemoService.video``."""

    def private_video_available(self, demo: Demo) -> bool:
        storage_key = self._private_video_storage_key(demo)
        if storage_key is None:
            return False
        if storage_key.startswith("artifact://"):
            try:
                head_accepted_video(
                    self.artifact_store,
                    storage_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    max_bytes=settings.max_video_upload_bytes,
                )
                return True
            except AcceptedArtifactError:
                return False
        return self.storage.video_path_for_demo(demo.id, storage_key) is not None

    def get_private_video_path(self, demo: Demo) -> Path | None:
        storage_key = self._private_video_storage_key(demo)
        if storage_key is None:
            return None
        if storage_key.startswith("artifact://"):
            return None
        return self.storage.video_path_for_demo(demo.id, storage_key)

    def open_private_video(
        self,
        demo: Demo,
    ) -> tuple[PrivateVideoHandle, Any] | None:
        storage_key = self._private_video_storage_key(demo)
        if storage_key is None:
            return None
        if storage_key.startswith("artifact://"):
            try:
                verified = head_accepted_video(
                    self.artifact_store,
                    storage_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    max_bytes=settings.max_video_upload_bytes,
                )
            except AcceptedArtifactError:
                return None
            return (
                _AcceptedVideoHandle(
                    store=self.artifact_store,
                    reference=storage_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    verified=verified,
                ),
                _PrivateArtifactStat(verified.snapshot.size_bytes),
            )
        return self.storage.open_video_for_demo(demo.id, storage_key)

    def _private_video_storage_key(self, demo: Demo) -> str | None:
        video = self._service.replay.get_video_status(demo)
        if video.get("status") != "ready":
            return None

        storage_key = _optional_str(video.get("storageKey"))
        if storage_key is None:
            media_url = _optional_str(video.get("url"))
            if media_url is None:
                return None
            try:
                storage_key = self.storage.storage_key_from_media_url(media_url)
            except StorageKeyError:
                return None
        return storage_key

    def attach_manual_video(self, demo: Demo, stored_video: StoredVideoUpload) -> dict[str, Any]:
        current_video = self._service.replay.get_video_status(demo)
        previous_reference = _optional_str(current_video.get("storageKey"))
        try:
            updated = self._service.replay.update_replay_video(
                demo,
                {
                    **current_video,
                    "status": "ready",
                    "url": stored_video.url,
                    "storageKey": stored_video.storage_key,
                    "source": "manual_upload",
                    "errorMessage": None,
                },
            )
        except BaseException:
            if stored_video.storage_key.startswith("artifact://"):
                self._service.delete_artifact_safely(
                    stored_video.storage_key,
                    expected_generation=stored_video.generation,
                )
            raise
        if (
            previous_reference
            and previous_reference.startswith("artifact://")
            and previous_reference != stored_video.storage_key
            and not self._service.worker_media.completed_render_retains_video(demo, previous_reference)
        ):
            self._service.delete_artifact_safely(previous_reference)
        return updated

    def update_video_calibration(
        self,
        demo: Demo,
        *,
        duration_seconds: float | None = None,
        tick_start: int | None = None,
        tick_end: int | None = None,
        tick_rate: int | None = None,
        time_origin_seconds: float | None = None,
    ) -> dict[str, Any]:
        current_video = self._service.replay.get_video_status(demo)
        next_video = {**current_video}

        if duration_seconds is not None:
            if duration_seconds < 0:
                raise ValueError("durationSeconds must be zero or greater")
            next_video["durationSeconds"] = duration_seconds
        if tick_start is not None:
            next_video["tickStart"] = tick_start
        if tick_end is not None:
            next_video["tickEnd"] = tick_end
        if tick_rate is not None:
            if tick_rate <= 0:
                raise ValueError("tickRate must be greater than zero")
            next_video["tickRate"] = tick_rate
        if time_origin_seconds is not None:
            if time_origin_seconds < 0:
                raise ValueError("timeOriginSeconds must be zero or greater")
            next_video["timeOriginSeconds"] = time_origin_seconds

        if int(next_video["tickEnd"]) < int(next_video["tickStart"]):
            raise ValueError("tickEnd must be greater than or equal to tickStart")
        return self._service.replay.update_replay_video(demo, next_video)
