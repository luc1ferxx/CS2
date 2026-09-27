"""The render worker's artifact boundary: the pinned .dem source it downloads and the MP4 output it uploads, binds, and that is then served as the private render video."""

import hashlib
import json
import math
from collections.abc import Iterator
from typing import Any

from app.core.config import settings
from app.models.demo import Demo
from app.models.job import DemoJob
from app.schemas.demo import RenderWorkerResult
from app.services.artifact_binding import (
    AcceptedArtifactError,
    AcceptedArtifactSnapshot,
    VerifiedAcceptedArtifact,
    head_accepted_video,
    parse_source_artifact_snapshot,
    verify_accepted_artifact,
)
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import (
    _job_metadata,
    _metadata_json,
    _optional_float,
    _optional_int,
    _optional_str,
    _render_output_reference,
    _required_int,
)
from app.services.demo_service.constants import RENDER_CLIP_JOB_TYPE
from app.services.demo_service.errors import DemoGoneError
from app.services.demo_service.gone import job_gone_raises, row_identity, rows_missing
from app.services.demo_service.video_registry import PrivateVideoHandle, _AcceptedVideoHandle, _PrivateArtifactStat
from app.services.storage import ArtifactStoreError
from app.services.upload_service import StoredVideoUpload


class RenderWorkerMedia(ServiceComponent):
    """Reached as ``DemoService.worker_media``."""

    def render_source_snapshot(self, job: DemoJob) -> AcceptedArtifactSnapshot:
        try:
            snapshot = parse_source_artifact_snapshot(_job_metadata(job).get("sourceArtifact"))
            if (
                job.job_type != RENDER_CLIP_JOB_TYPE
                or snapshot.reference != job.demo.source_storage_key
                or snapshot.reference != _job_metadata(job).get("demoStorageKey")
            ):
                raise ValueError
            self.artifact_store.require_binding(
                snapshot.reference,
                owner_id=job.demo.owner_id,
                demo_id=job.demo_id,
                kind="source",
                state="accepted",
            )
            return snapshot
        except (AcceptedArtifactError, ArtifactStoreError, ValueError):
            raise ValueError("Accepted render source binding is invalid") from None

    @job_gone_raises
    def open_render_source(self, job: DemoJob) -> tuple[Any, AcceptedArtifactSnapshot]:
        if job.status != "rendering":
            raise ValueError("Render source download requires a rendering job")
        snapshot = self.render_source_snapshot(job)
        opened = None
        try:
            verified = verify_accepted_artifact(
                self.artifact_store,
                snapshot.reference,
                owner_id=job.demo.owner_id,
                demo_id=job.demo_id,
                kind="source",
                snapshot=snapshot,
                max_bytes=settings.max_demo_upload_bytes,
            )
            opened = self.artifact_store.read_range(
                snapshot.reference,
                expected_generation=snapshot.generation,
            )
            if (
                AcceptedArtifactSnapshot.from_metadata(opened.metadata) != verified.snapshot
                or opened.start != 0
                or opened.length != snapshot.size_bytes
                or opened.total_size != snapshot.size_bytes
            ):
                raise ValueError
            return opened, snapshot
        except (AcceptedArtifactError, ArtifactStoreError, ValueError):
            if opened is not None:
                opened.close()
            raise ValueError("Accepted render source is not available") from None

    @staticmethod
    def stream_render_source(opened: Any, snapshot: AcceptedArtifactSnapshot) -> Iterator[bytes]:
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in opened.iter_chunks(settings.upload_chunk_bytes):
                size += len(chunk)
                if size > snapshot.size_bytes:
                    raise ValueError("Accepted render source integrity check failed")
                digest.update(chunk)
                yield chunk
            if size != snapshot.size_bytes or digest.hexdigest() != snapshot.sha256:
                raise ValueError("Accepted render source integrity check failed")
        finally:
            opened.close()

    def bind_render_worker_media(
        self,
        job: DemoJob,
        stored_video: StoredVideoUpload,
    ) -> AcceptedArtifactSnapshot:
        """Bind an uploaded MP4 to its rendering job.

        The upload is already stored when this runs. If the match was deleted
        while the body streamed in, or while binding, the MP4 belongs to
        nothing: it is removed here and DemoGoneError answers the worker 404.
        """
        job_id = row_identity(job)
        try:
            return self._bind_render_worker_media(job, stored_video)
        except Exception as exc:
            if isinstance(exc, DemoGoneError) or rows_missing(self.db, job_id=job_id):
                self._service.delete_artifact_safely(
                    stored_video.storage_key,
                    expected_generation=stored_video.generation,
                )
                raise DemoGoneError("Render job was deleted") from None
            raise

    def _bind_render_worker_media(
        self,
        job: DemoJob,
        stored_video: StoredVideoUpload,
    ) -> AcceptedArtifactSnapshot:
        # Serialize with completion so a late upload cannot replace a saved clip.
        self.db.refresh(job, with_for_update=True)
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs accept render worker media")
        if job.status != "rendering":
            raise ValueError("Render worker media requires a rendering job")
        if not stored_video.storage_key.startswith("artifact://"):
            raise ValueError("Render worker media must use accepted artifact storage")

        previous = self.job_output_artifact(job)
        try:
            verified = head_accepted_video(
                self.artifact_store,
                stored_video.storage_key,
                owner_id=job.demo.owner_id,
                demo_id=job.demo_id,
                max_bytes=settings.max_video_upload_bytes,
            )
        except AcceptedArtifactError:
            self._service.delete_artifact_safely(
                stored_video.storage_key,
                expected_generation=stored_video.generation,
            )
            raise ValueError("Render worker media artifact is invalid") from None

        snapshot = verified.snapshot
        if (
            stored_video.generation != snapshot.generation
            or stored_video.size_bytes != snapshot.size_bytes
            or stored_video.sha256 != snapshot.sha256
        ):
            self._service.delete_artifact_safely(
                stored_video.storage_key,
                expected_generation=snapshot.generation,
            )
            raise ValueError("Render worker media artifact is invalid")

        metadata = _job_metadata(job)
        metadata["outputArtifact"] = snapshot.as_dict()
        job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            if not self._render_output_reference_is_bound(
                job.id,
                snapshot.reference,
            ):
                self._service.delete_artifact_safely(
                    stored_video.storage_key,
                    expected_generation=snapshot.generation,
                )
            raise
        self.db.refresh(job)

        if previous is not None and previous.reference != snapshot.reference:
            self._service.delete_artifact_safely(
                previous.reference,
                expected_generation=previous.generation,
            )
        return snapshot

    def _render_output_reference_is_bound(
        self,
        job_id: str,
        reference: str,
    ) -> bool:
        try:
            metadata_json = (
                self.db.query(DemoJob.metadata_json)
                .filter(DemoJob.id == job_id)
                .scalar()
            )
            metadata = json.loads(metadata_json or "{}")
            snapshot = AcceptedArtifactSnapshot.from_mapping(
                metadata.get("outputArtifact")
            )
        except AcceptedArtifactError:
            return False
        except Exception:
            # Preserve the object until reconciliation if commit outcome is unknown.
            return True
        return snapshot.reference == reference

    def job_output_artifact(self, job: DemoJob) -> AcceptedArtifactSnapshot | None:
        payload = _job_metadata(job).get("outputArtifact")
        if payload is None:
            return None
        try:
            snapshot = AcceptedArtifactSnapshot.from_mapping(payload)
            self.artifact_store.require_binding(
                snapshot.reference,
                owner_id=job.demo.owner_id,
                demo_id=job.demo_id,
                kind="video",
                state="accepted",
            )
        except (AcceptedArtifactError, ArtifactStoreError, ValueError):
            raise ValueError("Render worker media binding is invalid") from None
        return snapshot

    def _verified_render_job_output(self, job: DemoJob) -> VerifiedAcceptedArtifact | None:
        if (
            job.job_type != RENDER_CLIP_JOB_TYPE
            or job.status != "completed"
            or job.demo is None
            or (self.owner_id is not None and job.demo.owner_id != self.owner_id)
        ):
            return None
        try:
            snapshot = self.job_output_artifact(job)
            if snapshot is None:
                return None
            return head_accepted_video(
                self.artifact_store, snapshot.reference,
                owner_id=job.demo.owner_id, demo_id=job.demo_id,
                snapshot=snapshot, max_bytes=settings.max_video_upload_bytes,
            )
        except (AcceptedArtifactError, ArtifactStoreError, ValueError, OSError):
            return None

    def public_render_job_video(
        self, job: DemoJob, *, current_video: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        verified = self._verified_render_job_output(job)
        if verified is None:
            return None
        metadata = _job_metadata(job)
        calibration = metadata.get("completedVideo")
        if not isinstance(calibration, dict):
            # Older successful jobs kept calibration only in the active replay video.
            current = current_video if current_video is not None else self._service.replay.get_video_status(job.demo)
            if (
                current.get("status") != "ready"
                or current.get("renderJobId") != job.id
                or current.get("storageKey") != verified.snapshot.reference
                or any(current.get(key) != metadata.get(key) for key in ("tickStart", "tickEnd", "tickRate"))
            ):
                return None
            calibration = current
        duration = _optional_float(calibration.get("durationSeconds"))
        origin = _optional_float(calibration.get("timeOriginSeconds"))
        start, end, rate = (
            _optional_int(metadata.get(key)) for key in ("tickStart", "tickEnd", "tickRate")
        )
        if (
            duration is None or origin is None
            or not math.isfinite(duration) or not math.isfinite(origin)
            or duration <= 0 or origin < 0 or origin >= duration
            or start is None or end is None or rate is None or rate <= 0 or end <= start
        ):
            return None
        return {
            "status": "ready", "source": "rendered",
            "url": f"/demos/{job.demo_id}/render/jobs/{job.id}/media/video",
            "durationSeconds": duration, "timeOriginSeconds": origin,
            "tickStart": start, "tickEnd": end, "tickRate": rate,
            "povSteamId": _optional_str(metadata.get("povSteamId")),
            "renderJobId": job.id, "errorCode": None, "errorMessage": None,
        }

    def open_private_render_video(
        self, demo: Demo, job_id: str
    ) -> tuple[PrivateVideoHandle, Any] | None:
        job = (
            self.db.query(DemoJob)
            .filter(DemoJob.id == job_id, DemoJob.demo_id == demo.id)
            .one_or_none()
        )
        if job is None:
            return None
        verified = self._verified_render_job_output(job)
        if verified is None:
            return None
        return (
            _AcceptedVideoHandle(
                store=self.artifact_store, reference=verified.snapshot.reference,
                owner_id=demo.owner_id, demo_id=demo.id, verified=verified,
            ),
            _PrivateArtifactStat(verified.snapshot.size_bytes),
        )

    def retain_current_render_video(self, demo: Demo, video: Any) -> None:
        if not isinstance(video, dict) or video.get("source") != "rendered":
            return
        job_id = _optional_str(video.get("renderJobId"))
        if job_id is None:
            return
        job = self.db.query(DemoJob).filter(
            DemoJob.id == job_id, DemoJob.demo_id == demo.id,
        ).one_or_none()
        if job is None or "completedVideo" in _job_metadata(job):
            return
        public_video = self.public_render_job_video(job)
        if public_video is not None:
            metadata = _job_metadata(job)
            metadata["completedVideo"] = {
                key: public_video[key] for key in ("durationSeconds", "timeOriginSeconds")
            }
            job.metadata_json = _metadata_json(metadata)

    def completed_render_retains_video(self, demo: Demo, reference: str) -> bool:
        jobs = self.db.query(DemoJob).filter(
            DemoJob.demo_id == demo.id, DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
            DemoJob.status == "completed",
        ).all()
        return any(
            isinstance(output := _job_metadata(job).get("outputArtifact"), dict)
            and output.get("reference") == reference
            for job in jobs
        )

    def completed_render_clip_video(
        self,
        job: DemoJob,
        result: RenderWorkerResult,
    ) -> dict[str, Any]:
        metadata = _job_metadata(job)
        if result.tickRate <= 0:
            raise ValueError("tickRate must be greater than zero")
        if result.tickEnd <= result.tickStart:
            raise ValueError("tickEnd must be greater than tickStart")
        if result.durationSeconds < 0:
            raise ValueError("durationSeconds must be zero or greater")
        if result.timeOriginSeconds < 0:
            raise ValueError("timeOriginSeconds must be zero or greater")
        if (
            result.tickStart != _required_int(metadata, "tickStart")
            or result.tickEnd != _required_int(metadata, "tickEnd")
            or result.tickRate != _required_int(metadata, "tickRate")
        ):
            raise ValueError("completed render output does not match the requested clip")

        demo = job.demo
        video_reference = self._render_output_reference_for_job(job, result)
        if video_reference is None:
            raise ValueError("completed render output must include videoUrl or localMediaPath")
        if (
            not video_reference["storageKey"].startswith("artifact://")
            and self.storage.video_path_for_demo(
                demo.id,
                video_reference["storageKey"],
            )
            is None
        ):
            raise ValueError("completed render output must belong to the requested demo")

        return {
            "status": "ready",
            "url": video_reference["url"],
            "storageKey": video_reference["storageKey"],
            "durationSeconds": result.durationSeconds,
            "tickStart": result.tickStart,
            "tickEnd": result.tickEnd,
            "tickRate": result.tickRate,
            "source": "rendered",
            "povSteamId": _optional_str(metadata.get("povSteamId")),
            "renderJobId": job.id,
            "errorCode": None,
            "errorMessage": None,
            "timeOriginSeconds": result.timeOriginSeconds,
        }

    def _render_output_reference_for_job(
        self,
        job: DemoJob,
        result: RenderWorkerResult,
    ) -> dict[str, str] | None:
        demo = job.demo
        storage_key = _optional_str(result.storageKey)
        if storage_key and storage_key.startswith("artifact://"):
            expected_url = f"/demos/{demo.id}/media/video"
            if result.videoUrl and result.videoUrl != expected_url:
                raise ValueError("videoUrl does not match storageKey")
            snapshot = self.job_output_artifact(job)
            if snapshot is None or snapshot.reference != storage_key:
                raise ValueError("completed render output is not bound to this job")
            try:
                head_accepted_video(
                    self.artifact_store,
                    storage_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    snapshot=snapshot,
                    max_bytes=settings.max_video_upload_bytes,
                )
            except AcceptedArtifactError:
                raise ValueError(
                    "completed render output must belong to the requested demo"
                ) from None
            return {"url": expected_url, "storageKey": storage_key}
        if settings.artifact_storage_backend != "local":
            raise ValueError("completed render output must use accepted artifact storage")
        return _render_output_reference(result, self.storage)
