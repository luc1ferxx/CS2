"""The replay contract artifact: write/load/public projection, its video section and status, and the prepare/commit/abort transaction every video mutation goes through."""

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.demo import Demo
from app.parser.replay_contract import normalize_replay_contract, normalize_video_identity
from app.services.artifact_binding import AcceptedArtifactError, read_accepted_replay_json, verify_accepted_artifact
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import _int_or_default, _optional_str, _positive_int_or_default
from app.services.demo_service.constants import REPLAY_ARTIFACT_MISSING_MESSAGE
from app.services.demo_service.errors import DemoGoneError, ReplayBlobUnavailableError
from app.services.demo_service.gone import gone_rows_raise, row_identity
from app.services.demo_service.projection import _project_fields, _public_render_failure, _public_replay_contract
from app.services.storage import ArtifactStoreError


@dataclass(frozen=True)
class _PendingReplayUpdate:
    demo_id: str
    video: dict[str, Any]
    previous_reference: str | None
    next_reference: str


class ReplayBlob(ServiceComponent):
    """Reached as ``DemoService.replay``."""

    def replay_blob_path(self, demo_id: str) -> Path:
        return self.storage.path_for_key(self.replay_blob_key(demo_id))

    def replay_blob_key(self, demo_id: str) -> str:
        return self.storage.replay_key(demo_id)

    def write_replay_blob(self, demo_id: str, replay: dict[str, Any]) -> str:
        demo = self.db.query(Demo).filter(Demo.id == demo_id).one_or_none()
        if demo is None:
            # Deleted while the caller was working on it; nothing may be
            # stored under an id whose rows are gone.
            raise DemoGoneError("Demo is not available for replay storage")
        try:
            encoded = json.dumps(
                replay,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("Replay artifact content is invalid") from None
        if len(encoded) > settings.max_replay_artifact_bytes:
            raise ValueError("Replay artifact exceeds the configured size limit")
        reference = self.artifact_store.new_reference(
            owner_id=demo.owner_id,
            demo_id=demo.id,
            kind="replay",
            state="accepted",
        )
        try:
            metadata = self.artifact_store.write_stream(
                reference,
                io.BytesIO(encoded),
                max_bytes=settings.max_replay_artifact_bytes,
                chunk_size=settings.upload_chunk_bytes,
                expected_size=len(encoded),
                content_type="application/json",
                policy_version="artifact_store_v1",
            )
            found = self.artifact_store.head(reference)
            if found != metadata:
                raise ArtifactStoreError("Replay artifact verification failed")
        except Exception:
            self._service.delete_artifact_safely(reference)
            raise ValueError("Replay artifact could not be stored") from None
        return reference

    def replay_artifact_is_missing(self, demo: Demo) -> bool:
        """Report a finished demo whose replay can no longer be read back.

        Metadata only. This runs once per demo on the library listing and a
        replay blob is tens of megabytes, so it must never touch content. Key
        resolution below deliberately mirrors load_replay_blob(): if the two
        disagree the library either offers a retry for a demo that plays fine,
        or hides one for a demo that does not. It stops short of the content
        checks load_replay_blob() makes after reading, so a blob that exists
        but holds the wrong demoId reads as present here -- reporting that
        would cost a full read of every demo on every listing.
        """
        if demo.status != "completed":
            return False
        storage_key = getattr(demo, "replay_storage_key", None) or self.replay_blob_key(demo.id)
        if storage_key.startswith("artifact://"):
            try:
                verify_accepted_artifact(
                    self.artifact_store,
                    storage_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    kind="replay",
                    max_bytes=settings.max_replay_artifact_bytes,
                )
            except (AcceptedArtifactError, ArtifactStoreError, ValueError):
                return True
            return False
        if not self.storage.replay_key_belongs_to_demo(demo.id, storage_key):
            return True
        return not self.storage.exists(storage_key)

    def require_replay_blob(self, demo: Demo) -> dict[str, object]:
        replay = self.load_replay_blob(demo)
        if replay is not None:
            return replay
        if demo.status == "completed":
            # The parse already finished, so this is not a "wait for it" state.
            # Saying "not ready" here sent users back to wait for a job that had
            # long since succeeded; only a re-parse brings the artifact back.
            raise ReplayBlobUnavailableError(REPLAY_ARTIFACT_MISSING_MESSAGE)
        raise ReplayBlobUnavailableError("Replay blob is not ready")

    def load_replay_blob(self, demo: Demo) -> dict[str, object] | None:
        storage_key = getattr(demo, "replay_storage_key", None) or self.replay_blob_key(demo.id)
        if storage_key.startswith("artifact://"):
            try:
                replay = read_accepted_replay_json(
                    self.artifact_store,
                    storage_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    max_bytes=settings.max_replay_artifact_bytes,
                    chunk_size=settings.upload_chunk_bytes,
                )
            except AcceptedArtifactError:
                return None
            return self._with_replay_contract_defaults(replay)
        if not self.storage.replay_key_belongs_to_demo(demo.id, storage_key):
            return None
        if not self.storage.exists(storage_key):
            return None

        replay = self.storage.read_json(storage_key)
        if not isinstance(replay, dict) or _optional_str(replay.get("demoId")) != demo.id:
            return None
        return self._with_replay_contract_defaults(replay)

    def public_replay(self, demo: Demo) -> dict[str, object] | None:
        # One read: a replay is tens of MB and each load re-parses and re-normalizes it.
        replay = self.load_replay_blob(demo)
        if replay is None:
            return None
        return _public_replay_contract(
            replay,
            self.public_video_status(demo, internal_video=_replay_video_section(replay)),
        )

    def get_video_status(self, demo: Demo) -> dict[str, Any]:
        replay = self.load_replay_blob(demo)
        if replay is None:
            return {
                "status": "pending",
                "url": None,
                "durationSeconds": 0,
                "tickStart": 0,
                "tickEnd": 0,
                "tickRate": demo.tick_rate,
                "source": "mock",
                "errorCode": None,
                "errorMessage": None,
                "timeOriginSeconds": 0,
            }
        return _replay_video_section(replay)

    def public_video_status(
        self, demo: Demo, *, internal_video: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if internal_video is None:
            internal_video = self.get_video_status(demo)
        video = _project_fields(
            internal_video,
            (
                "status",
                "durationSeconds",
                "tickStart",
                "tickEnd",
                "tickRate",
                "source",
                "timeOriginSeconds",
            ),
        )
        video.update(normalize_video_identity(internal_video))
        error_code, error_message = _public_render_failure(
            _optional_str(internal_video.get("status")),
            _optional_str(internal_video.get("errorCode")),
            _optional_str(internal_video.get("errorMessage")),
        )
        video["errorCode"] = error_code
        video["errorMessage"] = error_message
        video["url"] = (
            f"/demos/{demo.id}/media/video"
            if self._service.video.private_video_available(demo, video=internal_video)
            else None
        )
        return video

    def public_video_status_for_list(self, demo: Demo) -> dict[str, Any]:
        try:
            return self.public_video_status(demo)
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "status": "unknown",
                "url": None,
                "source": "unknown",
            }

    def update_replay_video(self, demo: Demo, video: dict[str, Any]) -> dict[str, Any]:
        pending = self.prepare_replay_video_update(demo, video)
        self._commit_replay_update(pending)
        return pending.video

    def prepare_replay_video_update(
        self,
        demo: Demo,
        video: dict[str, Any],
    ) -> _PendingReplayUpdate:
        replay = self.require_replay_blob(demo)
        self._service.worker_media.retain_current_render_video(demo, replay.get("video"))
        next_video = self._with_video_contract_defaults(video, replay)
        replay["video"] = next_video
        previous_reference = getattr(demo, "replay_storage_key", None)
        next_reference = self.write_replay_blob(demo.id, replay)
        demo.replay_storage_key = next_reference
        return _PendingReplayUpdate(
            demo_id=demo.id,
            video=next_video,
            previous_reference=previous_reference,
            next_reference=next_reference,
        )

    def _commit_replay_update(self, pending: _PendingReplayUpdate) -> None:
        with gone_rows_raise(self.db, demo_id=pending.demo_id):
            try:
                self.db.commit()
            except BaseException:
                self.db.rollback()
                self._service.delete_artifact_safely(pending.next_reference)
                raise
        self.finish_replay_update(pending)

    def finish_replay_update(self, pending: _PendingReplayUpdate) -> None:
        if (
            pending.previous_reference
            and pending.previous_reference.startswith("artifact://")
            and pending.previous_reference != pending.next_reference
        ):
            self._service.delete_artifact_safely(pending.previous_reference)

    def abort_replay_update(self, pending: _PendingReplayUpdate | None) -> None:
        if pending is None:
            return
        try:
            found = (
                self.db.query(Demo.replay_storage_key)
                .filter(Demo.id == pending.demo_id)
                .scalar()
            )
        except Exception:
            # Preserve the object until reconciliation if commit outcome is unknown.
            return
        if found != pending.next_reference:
            self._service.delete_artifact_safely(pending.next_reference)

    def prepare_render_clip_video_status(
        self,
        demo: Demo,
        status: str,
        error_message: str | None,
        *,
        error_code: str | None = None,
    ) -> tuple[dict[str, Any], _PendingReplayUpdate | None]:
        # A different job may have completed since this worker loaded the demo.
        # Refreshing a row deleted meanwhile raises; that is a DemoGoneError.
        with gone_rows_raise(self.db, demo_id=row_identity(demo)):
            self.db.refresh(demo, with_for_update=True)
        current_video = self.get_video_status(demo)
        if current_video.get("source") == "manual_upload" or (
            current_video.get("status") == "ready"
            and self._service.video.private_video_available(demo, video=current_video)
        ):
            return current_video, None

        public_error_code, public_error_message = _public_render_failure(
            status,
            error_code,
            error_message,
        )
        pending = self.prepare_replay_video_update(
            demo,
            {
                **current_video,
                "status": status,
                "source": "rendered",
                "url": None,
                "errorCode": public_error_code,
                "errorMessage": public_error_message,
            },
        )
        return pending.video, pending

    def update_render_clip_video_status(
        self,
        demo: Demo,
        status: str,
        error_message: str | None,
        *,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        video, pending = self.prepare_render_clip_video_status(
            demo,
            status,
            error_message,
            error_code=error_code,
        )
        if pending is not None:
            self._commit_replay_update(pending)
        return video

    def _with_replay_contract_defaults(self, replay: dict[str, Any]) -> dict[str, Any]:
        return normalize_replay_contract(replay)

    def _with_video_contract_defaults(
        self,
        video: dict[str, Any],
        replay: dict[str, Any],
    ) -> dict[str, Any]:
        rounds = replay.get("rounds", [])
        tick_rate = _positive_int_or_default(video.get("tickRate"), int(replay.get("tickRate", 64)))
        tick_start = _int_or_default(video.get("tickStart"), int(rounds[0]["startTick"]) if rounds else 0)
        tick_end = _int_or_default(video.get("tickEnd"), int(rounds[-1]["endTick"]) if rounds else tick_start)
        duration_seconds = video.get("durationSeconds")
        if duration_seconds is None:
            duration_seconds = round((tick_end - tick_start) / tick_rate, 2)
        time_origin_seconds = float(video.get("timeOriginSeconds", 0) or 0)

        normalized_video = {
            key: value for key, value in video.items()
            if key not in {"povSteamId", "renderJobId"}
        }
        return {
            **normalized_video,
            **normalize_video_identity(video),
            "status": video.get("status", "pending"),
            "url": video.get("url"),
            "durationSeconds": max(0, float(duration_seconds)),
            "tickStart": tick_start,
            "tickEnd": tick_end,
            "tickRate": tick_rate,
            "source": video.get("source", "mock"),
            "errorCode": video.get("errorCode"),
            "errorMessage": video.get("errorMessage"),
            "timeOriginSeconds": max(0, time_origin_seconds),
        }


def _replay_video_section(replay: dict[str, Any]) -> dict[str, Any]:
    video = replay["video"]
    if isinstance(video, dict):
        return video
    raise ValueError("Invalid replay video contract")
