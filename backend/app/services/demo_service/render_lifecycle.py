"""The render job state machine: mock and render_clip creation, claim/next/list/get, status and worker manifest, result/failure, retry, and the stale-claim and never-claimed sweeps."""

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, or_, update

from app.core.config import settings
from app.models.demo import Demo
from app.models.job import DemoJob
from app.schemas.demo import (
    RenderClipRequest,
    RenderJobManifest,
    RenderJobStatus,
    RenderWorkerResult,
    ReplayVideoStatus,
)
from app.services.artifact_binding import AcceptedArtifactError
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import (
    _aware_datetime,
    _compact_render_clip_metadata,
    _job_metadata,
    _metadata_json,
    _optional_float,
    _optional_int,
    _optional_str,
    _required_int,
    utc_now,
)
from app.services.demo_service.constants import (
    RENDER_CLIP_DEFAULT_PRESET,
    RENDER_CLIP_JOB_TYPE,
    RENDER_CLIP_MAX_ATTEMPTS,
    RENDER_FAILED_ERROR_CODE,
    RENDER_FAILED_PUBLIC_MESSAGE,
    RENDER_QUEUE_TIMED_OUT_ERROR_CODE,
    RENDER_TIMED_OUT_ERROR_CODE,
    RENDER_WORKER_MANIFEST_VERSION,
    UNCLAIMED_RENDER_CLIP_STATUSES,
)
from app.services.demo_service.projection import _public_render_failure

logger = logging.getLogger(__name__)


class RenderLifecycle(ServiceComponent):
    """Reached as ``DemoService.render``."""

    def create_mock_render_job(self, demo: Demo) -> DemoJob:
        self._service.replay.require_replay_blob(demo)

        job_id = str(uuid.uuid4())
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type="mock_render",
            status="queued",
            attempts=0,
        )
        pending = self._service.replay.prepare_replay_video_update(
            demo,
            {
                **self._service.replay.get_video_status(demo),
                "status": "queued",
                "source": "rendered",
                "url": None,
                "errorMessage": None,
            },
        )
        self.db.add(job)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        self._service.replay.finish_replay_update(pending)

        self._service.queue_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo.id}),
        )
        return job

    def create_render_clip_job(self, demo: Demo, request: RenderClipRequest) -> DemoJob:
        # PostgreSQL serializes matching requests before either can create a job.
        query = self.db.query(Demo).filter(Demo.id == demo.id)
        if self.owner_id is not None:
            query = query.filter(Demo.owner_id == self.owner_id)
        locked = query.populate_existing().with_for_update().one_or_none()
        if locked is None or locked.status != "completed":
            raise ValueError("Demo parse must complete before rendering")
        demo = locked
        replay = self._service.replay.require_replay_blob(demo)

        if request.tickRate <= 0:
            raise ValueError("tickRate must be greater than zero")
        if request.tickEnd <= request.tickStart:
            raise ValueError("tickEnd must be greater than tickStart")

        max_duration_seconds = settings.max_render_clip_seconds
        duration_seconds = (request.tickEnd - request.tickStart) / request.tickRate
        if duration_seconds > max_duration_seconds:
            raise ValueError(
                f"Render clip duration must be {max_duration_seconds} seconds or less"
            )

        request = self._resolve_render_pov(replay, request)
        source_key = self._service.ingest.source_demo_storage_key(demo)
        source_snapshot = None
        if source_key.startswith("artifact://"):
            parse_job = self._service.parse.latest_parse_job(demo)
            if parse_job is None:
                raise ValueError("Accepted render source is not available")
            try:
                source_snapshot = self._service.ingest.verify_source_artifact(demo, parse_job).snapshot
            except (AcceptedArtifactError, AttributeError):
                raise ValueError("Accepted render source is not available") from None
        if settings.render_worker_mode == "external":
            if source_snapshot is None:
                raise ValueError("External rendering requires an accepted .dem upload")
            if request.povSteamId is None:
                raise ValueError("Select a player with a Steam ID before rendering")
            if request.tickRate != replay.get("tickRate"):
                raise ValueError("Render tickRate must match the parsed replay")
            rounds: Any = replay.get("rounds", [])
            round_ends = [
                value for item in rounds
                if isinstance(item, dict)
                and isinstance(value := item.get("endTick"), int)
            ]
            if request.tickStart < 0 or (round_ends and request.tickEnd > max(round_ends)):
                raise ValueError("Render tick range must stay within the parsed replay")

        replay_storage_key = (
            getattr(demo, "replay_storage_key", None) or self._service.replay.replay_blob_key(demo.id)
        )
        metadata = _compact_render_clip_metadata(
            request,
            duration_seconds=duration_seconds,
            max_duration_seconds=max_duration_seconds,
            demo_storage_key=source_key,
            replay_storage_key=replay_storage_key,
        )
        if source_snapshot is not None:
            metadata["sourceArtifact"] = source_snapshot.as_dict()
        existing = self._reusable_render_clip_job(demo, metadata)
        if existing is not None:
            self.db.commit()
            return existing

        job_id = str(uuid.uuid4())
        _, pending = self._service.replay.prepare_render_clip_video_status(demo, "queued", None)
        metadata["replayStorageKey"] = (
            getattr(demo, "replay_storage_key", None) or self._service.replay.replay_blob_key(demo.id)
        )
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type=RENDER_CLIP_JOB_TYPE,
            status="queued",
            attempts=0,
            metadata_json=json.dumps(metadata, separators=(",", ":")),
        )
        self.db.add(job)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        if pending is not None:
            self._service.replay.finish_replay_update(pending)
        self.db.refresh(job)

        if settings.render_worker_mode == "fallback":
            self._service.queue_client().lpush(
                settings.redis_queue_name,
                json.dumps(
                    {
                        "job_id": job_id,
                        "demo_id": demo.id,
                        "job_type": RENDER_CLIP_JOB_TYPE,
                    }
                ),
            )
        return job

    def _reusable_render_clip_job(
        self, demo: Demo, requested: dict[str, Any]
    ) -> DemoJob | None:
        identity_fields = (
            "demoStorageKey", "sourceArtifact", "playerId", "povSteamId",
            "tickStart", "tickEnd", "tickRate", "renderPreset",
        )
        jobs = (
            self.db.query(DemoJob)
            .filter(
                DemoJob.demo_id == demo.id,
                DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
                DemoJob.status.in_(("queued", "pending", "rendering", "completed")),
            )
            .order_by(desc(DemoJob.created_at))
            .all()
        )
        for job in jobs:
            metadata = _job_metadata(job)
            if any(metadata.get(key) != requested.get(key) for key in identity_fields):
                continue
            if job.status != "completed" or self._service.worker_media.public_render_job_video(job) is not None:
                return job
        return None

    def _resolve_render_pov(
        self, replay: dict[str, Any], request: RenderClipRequest
    ) -> RenderClipRequest:
        if request.playerId is None and request.povSteamId is None:
            return request
        players = [player for player in replay.get("players", []) if isinstance(player, dict)]

        def steam_id(player: dict[str, Any]) -> str | None:
            value = player.get("steamId") or player.get("id")
            if isinstance(value, str) and len(value) == 17 and value.isascii() and value.isdecimal():
                return value
            return None

        matches = [
            player for player in players
            if (request.playerId is None or player.get("id") == request.playerId)
            and (request.povSteamId is None or steam_id(player) == request.povSteamId)
        ]
        if len(matches) != 1:
            raise ValueError("Render player and POV must identify one player in the replay")
        selected = matches[0]
        return request.model_copy(update={"playerId": selected["id"], "povSteamId": steam_id(selected)})

    def transition_mock_render_job(
        self,
        job: DemoJob,
        *,
        job_status: str,
        video_status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if job.job_type != "mock_render":
            raise ValueError("Only mock_render jobs use mock transitions")
        pending = self._service.replay.prepare_replay_video_update(
            job.demo,
            {
                **self._service.replay.get_video_status(job.demo),
                "status": video_status,
                "source": "rendered",
                "url": None,
                "errorCode": error_code,
                "errorMessage": error_message,
            },
        )
        job.status = job_status
        if job.started_at is None:
            job.started_at = utc_now()
        if job_status in {"completed", "failed"}:
            job.finished_at = utc_now()
        job.error_message = error_message if job_status == "failed" else None
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        self._service.replay.finish_replay_update(pending)
        self.db.refresh(job)
        return pending.video

    def list_render_clip_jobs(self, demo: Demo) -> list[RenderJobStatus]:
        jobs = (
            self.db.query(DemoJob)
            .filter(DemoJob.demo_id == demo.id, DemoJob.job_type == RENDER_CLIP_JOB_TYPE)
            .order_by(desc(DemoJob.created_at))
            .all()
        )
        if not jobs:
            return []
        current_video = self._service.replay.get_video_status(demo)
        demo_video = self._service.replay.public_video_status(demo, internal_video=current_video)
        return [
            self.render_job_status(job, demo_video=demo_video, current_video=current_video)
            for job in jobs
        ]

    def claim_render_clip_job(self, job: DemoJob) -> DemoJob:
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs can be claimed by render workers")
        if job.status in {"completed", "failed"}:
            raise ValueError(f"Render job is already {job.status}")
        claimed = self.db.execute(
            update(DemoJob)
            .where(DemoJob.id == job.id, DemoJob.status.in_(("queued", "pending")))
            .values(
                status="rendering",
                attempts=DemoJob.attempts + 1,
                started_at=func.coalesce(DemoJob.started_at, utc_now()),
                finished_at=None,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            raise ValueError("Render job is already claimed or finished")
        self.db.refresh(job)
        _, pending = self._service.replay.prepare_render_clip_video_status(
            job.demo,
            "rendering",
            None,
        )
        metadata = _job_metadata(job)
        metadata["replayStorageKey"] = (
            getattr(job.demo, "replay_storage_key", None)
            or self._service.replay.replay_blob_key(job.demo_id)
        )
        job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        if pending is not None:
            self._service.replay.finish_replay_update(pending)
        self.db.refresh(job)
        return job

    def latest_render_clip_job(self, demo: Demo) -> DemoJob | None:
        return (
            self.db.query(DemoJob)
            .filter(DemoJob.demo_id == demo.id, DemoJob.job_type == RENDER_CLIP_JOB_TYPE)
            .order_by(desc(DemoJob.created_at))
            .first()
        )

    def get_render_clip_job(self, job_id: str) -> DemoJob | None:
        job = (
            self.db.query(DemoJob)
            .filter(DemoJob.id == job_id, DemoJob.job_type == RENDER_CLIP_JOB_TYPE)
            .one_or_none()
        )
        if job is None or job.demo is None:
            return None
        return job

    def next_render_clip_job(self, statuses: tuple[str, ...] = ("queued", "pending")) -> DemoJob | None:
        return (
            self.db.query(DemoJob)
            .join(Demo, DemoJob.demo_id == Demo.id)
            .filter(
                DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
                DemoJob.status.in_(statuses),
            )
            .order_by(DemoJob.created_at.asc())
            .first()
        )

    def _stale_render_clip_jobs(self, cutoff: datetime, limit: int) -> list[DemoJob]:
        return (
            self.db.query(DemoJob)
            # The join keeps orphan rows out: the reclaim has to reconcile the
            # demo's video status, which needs a demo to reconcile against.
            .join(Demo, DemoJob.demo_id == Demo.id)
            .filter(
                DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
                DemoJob.status == "rendering",
                # A claim always stamps started_at, so "rendering" without one is
                # already broken data. Treating it as stale makes the sweep
                # self-healing instead of leaving the row wedged forever.
                or_(DemoJob.started_at <= cutoff, DemoJob.started_at.is_(None)),
            )
            .order_by(DemoJob.started_at.asc())
            .limit(limit)
            .all()
        )

    def _unclaimed_render_clip_jobs(self, cutoff: datetime, limit: int) -> list[DemoJob]:
        # created_at is the fallback, not the measure: it is only right for rows
        # written before queued_at existed, which by definition were never
        # requeued. See the column's comment in app/models/job.py.
        queued_since = func.coalesce(DemoJob.queued_at, DemoJob.created_at)
        return (
            self.db.query(DemoJob)
            # Same reason as the rendering sweep: failing the job reconciles the
            # demo's video status, which needs a demo to reconcile against.
            .join(Demo, DemoJob.demo_id == Demo.id)
            .filter(
                DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
                DemoJob.status.in_(UNCLAIMED_RENDER_CLIP_STATUSES),
                queued_since <= cutoff,
            )
            .order_by(queued_since.asc())
            .limit(limit)
            .all()
        )

    def fail_unclaimed_render_clip_jobs(
        self,
        *,
        older_than_seconds: int,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[str]:
        """Fail render_clip jobs that have waited on the queue with no worker.

        The rendering sweep next door covers a worker that died holding a job.
        This covers the opposite and more common case: no worker ever claimed it
        because none was running. Nothing else in the system touches such a row.
        next_render_clip_job keeps offering it, forever; retry_render_clip_job
        refuses it because it is not "failed"; create_render_clip_job hands the
        same row back to anyone asking for that clip again; and the frontend's
        isActive() reads it as in flight. So the demo page shows "generating"
        with no way out short of a hand-written UPDATE -- the render-side twin of
        the parse dead end PARSE_ABANDONED exists to break.

        Failing is what restores the way out: "failed" is retryable, is not
        reused by create_render_clip_job, and carries copy that names the actual
        problem. Attempts are deliberately untouched -- the clip never ran, so it
        has not earned a strike, and a retry starts from a full budget.

        Staleness is wall-clock only. It must NOT consult
        diagnostics.render_worker_availability: a deployment whose renderer is
        simply off is precisely the case this has to resolve, so gating on the
        renderer looking alive would skip every job it exists for.
        """
        moment = _aware_datetime(now)
        cutoff = moment - timedelta(seconds=older_than_seconds)
        failed: list[str] = []
        for job in self._unclaimed_render_clip_jobs(cutoff, limit):
            # Re-read under a row lock before committing to the failure. A
            # renderer can claim the job between the query above and here, and
            # fail_render_clip_job accepts a "rendering" row -- so without this
            # check the sweep would kill a render that is actually running. The
            # lock is what makes the check and the write one decision: a
            # concurrent claim_render_clip_job blocks on it rather than slipping
            # in behind it.
            self.db.refresh(job, with_for_update=True)
            if job.status not in UNCLAIMED_RENDER_CLIP_STATUSES:
                self.db.rollback()
                continue
            try:
                self.fail_render_clip_job(
                    job,
                    f"No render worker claimed this job within {older_than_seconds}s.",
                    error_code=RENDER_QUEUE_TIMED_OUT_ERROR_CODE,
                )
            except ValueError:
                # Reached the terminal states the guard above cannot see, so the
                # row is already resolved. One such race must not cost the rest
                # of the batch.
                continue
            failed.append(str(job.id))
        return failed

    def reclaim_stale_render_clip_jobs(
        self,
        *,
        older_than_seconds: int,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[str]:
        """Return render_clip jobs abandoned mid-render to the queue.

        A worker that dies between claim_render_clip_job and its result call
        leaves the row on "rendering", where no poller can see it. This puts it
        back on "queued" -- and rolls the demo's replay video status back with
        it, in the same commit, so the two never disagree.

        Jobs that have already burned RENDER_CLIP_MAX_ATTEMPTS claims fail
        instead, on the assumption that the clip itself is what keeps killing
        the renderer.

        Staleness is judged purely from started_at and the caller's window. It
        must NOT consult diagnostics.render_worker_availability, which counts a
        rendering job as proof the worker is alive -- using it here to decide
        whether that same job is dead would be circular.
        """
        moment = _aware_datetime(now)
        cutoff = moment - timedelta(seconds=older_than_seconds)
        reclaimed: list[str] = []
        for job in self._stale_render_clip_jobs(cutoff, limit):
            if job.attempts >= RENDER_CLIP_MAX_ATTEMPTS:
                try:
                    self.fail_render_clip_job(
                        job,
                        f"Render did not finish within {older_than_seconds}s "
                        f"after {job.attempts} attempts.",
                        error_code=RENDER_TIMED_OUT_ERROR_CODE,
                    )
                except ValueError:
                    # The worker reported in between the query and the refresh
                    # above. Its result wins, exactly as in the requeue path --
                    # and one such race must not cost the rest of the batch.
                    continue
                reclaimed.append(str(job.id))
                continue
            if self._requeue_render_clip_job(job):
                reclaimed.append(str(job.id))
        return reclaimed

    def retry_render_clip_job(self, demo: Demo, job_id: str) -> DemoJob | None:
        """Put a failed render_clip job back on the queue instead of cloning it.

        The frontend's retry used to call create_render_clip_job, which leaves
        the failed row behind and starts a fresh one. Reusing the row keeps one
        clip to one job, and the renderer re-reads the source on its next claim,
        so a failure caused by a since-repaired artifact gets another look.
        """
        job = (
            self.db.query(DemoJob)
            .filter(
                DemoJob.id == job_id,
                DemoJob.demo_id == demo.id,
                DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
            )
            .one_or_none()
        )
        if job is None:
            return None
        if job.status != "failed":
            raise ValueError("Only failed render jobs can be retried")
        if not self._requeue_render_clip_job(job, from_status="failed", reset_attempts=True):
            # Something moved the row off "failed" between the read and the
            # update; whatever wrote it is newer than this request.
            raise ValueError("Only failed render jobs can be retried")

        if settings.render_worker_mode == "fallback":
            # External mode polls the durable DB queue, so requeueing is enough.
            # Fallback mode is driven by Redis and would never see the job again.
            self._service.queue_client().lpush(
                settings.redis_queue_name,
                json.dumps(
                    {
                        "job_id": str(job.id),
                        "demo_id": demo.id,
                        "job_type": RENDER_CLIP_JOB_TYPE,
                    }
                ),
            )
        return job

    def _requeue_render_clip_job(
        self,
        job: DemoJob,
        *,
        from_status: str = "rendering",
        reset_attempts: bool = False,
    ) -> bool:
        _, pending = self._service.replay.prepare_render_clip_video_status(job.demo, "queued", None)
        values: dict[str, Any] = {
            "status": "queued",
            # claim_render_clip_job stamps started_at through a coalesce, so
            # it only ever writes the FIRST claim's timestamp. Keeping the
            # old value here would leave the requeued job permanently older
            # than any cutoff -- reclaimed again the instant it is reclaimed.
            "started_at": None,
            # The row is entering the queue now, whatever created_at says. Without
            # this a retry of an old clip is already past the queue timeout the
            # moment it is queued, and fail_unclaimed_render_clip_jobs would kill
            # it before a worker could claim it.
            "queued_at": utc_now(),
            "finished_at": None,
            "error_message": None,
        }
        if reset_attempts:
            # A person asking for a retry is starting the attempt budget over.
            # A job that already burned RENDER_CLIP_MAX_ATTEMPTS would otherwise
            # be failed again by the very next sweep, without being rendered.
            values["attempts"] = 0
        requeued = self.db.execute(
            update(DemoJob)
            .where(
                DemoJob.id == job.id,
                DemoJob.job_type == RENDER_CLIP_JOB_TYPE,
                DemoJob.status == from_status,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if requeued.rowcount != 1:
            # The worker came back and finished the job between the query above
            # and this update. Its result wins; drop the replay blob we staged.
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            return False
        if pending is not None:
            metadata = _job_metadata(job)
            metadata["replayStorageKey"] = pending.next_reference
            job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        if pending is not None:
            self._service.replay.finish_replay_update(pending)
        self.db.refresh(job)
        logger.info(
            "Requeued render_clip job %s for demo %s from %s (attempt %s of %s)",
            job.id,
            job.demo_id,
            from_status,
            job.attempts,
            RENDER_CLIP_MAX_ATTEMPTS,
        )
        return True

    def render_job_status(
        self, job: DemoJob, *,
        demo_video: dict[str, Any] | None = None,
        current_video: dict[str, Any] | None = None,
    ) -> RenderJobStatus:
        metadata = _job_metadata(job)
        public_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in {"demoStorageKey", "replayStorageKey", "outputArtifact", "sourceArtifact", "completedVideo"}
        }
        video = demo_video if demo_video is not None else (
            self._service.replay.public_video_status(job.demo) if job.demo is not None else {}
        )
        error_code, error_message = _public_render_failure(
            job.status,
            None,
            job.error_message,
        )
        render_video = self._service.worker_media.public_render_job_video(job, current_video=current_video)
        return RenderJobStatus(
            job_id=job.id,
            demo_id=job.demo_id,
            job_type=job.job_type,
            status=job.status,
            source=_optional_str(video.get("source")) or "unknown",
            video_status=_optional_str(video.get("status")),
            video=ReplayVideoStatus.model_validate(render_video) if render_video is not None else None,
            tick_start=_optional_int(metadata.get("tickStart")),
            tick_end=_optional_int(metadata.get("tickEnd")),
            tick_rate=_optional_int(metadata.get("tickRate")),
            duration_seconds=_optional_float(metadata.get("durationSeconds")),
            event_id=_optional_str(metadata.get("eventId")),
            player_id=_optional_str(metadata.get("playerId")),
            pov_steam_id=_optional_str(metadata.get("povSteamId")),
            round_number=_optional_int(metadata.get("roundNumber")),
            render_preset=_optional_str(metadata.get("renderPreset")),
            metadata=public_metadata,
            error_code=error_code,
            error_message=error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    def render_job_manifest(self, job: DemoJob) -> RenderJobManifest:
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs have render worker manifests")

        demo = job.demo
        metadata = _job_metadata(job)
        demo_storage_key = (
            _optional_str(metadata.get("demoStorageKey"))
            or self._service.ingest.source_demo_storage_key(demo)
        )
        source_snapshot = self._service.worker_media.render_source_snapshot(job) if metadata.get("sourceArtifact") else None
        return RenderJobManifest(
            manifestVersion=RENDER_WORKER_MANIFEST_VERSION,
            jobId=job.id,
            demoId=demo.id,
            jobType=job.job_type,
            status=job.status,
            demoFilePath="",
            demoStorageKey=demo_storage_key,
            demoDownloadPath=f"/render-worker/jobs/{job.id}/source" if source_snapshot else None,
            sourceSizeBytes=source_snapshot.size_bytes if source_snapshot else None,
            sourceSha256=source_snapshot.sha256 if source_snapshot else None,
            replayStorageKey=getattr(demo, "replay_storage_key", None)
            or _optional_str(metadata.get("replayStorageKey")),
            originalFilename=demo.original_filename,
            mapName=demo.map_name,
            eventId=_optional_str(metadata.get("eventId")),
            playerId=_optional_str(metadata.get("playerId")),
            povSteamId=_optional_str(metadata.get("povSteamId")),
            tickStart=_required_int(metadata, "tickStart"),
            tickEnd=_required_int(metadata, "tickEnd"),
            tickRate=_required_int(metadata, "tickRate"),
            roundNumber=_optional_int(metadata.get("roundNumber")),
            renderPreset=_optional_str(metadata.get("renderPreset")) or RENDER_CLIP_DEFAULT_PRESET,
        )

    def apply_render_worker_result(
        self,
        job: DemoJob,
        result: RenderWorkerResult,
    ) -> dict[str, Any]:
        self.db.refresh(job, with_for_update=True)
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs accept render worker results")
        if job.status in {"completed", "failed"}:
            raise ValueError(f"Render job is already {job.status}")
        if job.status != "rendering":
            raise ValueError("Render worker result requires a rendering job")

        normalized_status = result.status.lower()
        if normalized_status == "failed":
            logger.warning(
                "Render worker job %s reported %s",
                job.id,
                RENDER_FAILED_ERROR_CODE,
            )
            return self.fail_render_clip_job(
                job,
                RENDER_FAILED_PUBLIC_MESSAGE,
                error_code=RENDER_FAILED_ERROR_CODE,
            )
        if normalized_status not in {"completed", "ready"}:
            raise ValueError("status must be completed or failed")

        self.db.refresh(job.demo, with_for_update=True)
        video = self._service.worker_media.completed_render_clip_video(job, result)
        pending = self._service.replay.prepare_replay_video_update(job.demo, video)
        video = pending.video
        job.status = "completed"
        job.error_message = None
        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = utc_now()
        metadata = _job_metadata(job)
        metadata["replayStorageKey"] = pending.next_reference
        metadata["completedVideo"] = {
            key: video[key] for key in ("durationSeconds", "timeOriginSeconds")
        }
        job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        self._service.replay.finish_replay_update(pending)
        self.db.refresh(job)
        return video

    def fail_render_clip_job(
        self,
        job: DemoJob,
        error_message: str,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        self.db.refresh(job, with_for_update=True)
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs can enter render failure")
        if job.status in {"completed", "failed"}:
            raise ValueError(f"Render job is already {job.status}")

        output_artifact = self._service.worker_media.job_output_artifact(job)
        video, pending = self._service.replay.prepare_render_clip_video_status(
            job.demo,
            "failed",
            error_message,
            error_code=error_code,
        )
        job.status = "failed"
        job.error_message = _public_render_failure(
            "failed",
            error_code,
            error_message,
        )[1]
        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = utc_now()
        metadata = _job_metadata(job)
        metadata.pop("outputArtifact", None)
        if pending is not None:
            metadata["replayStorageKey"] = pending.next_reference
        job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._service.replay.abort_replay_update(pending)
            raise
        if pending is not None:
            self._service.replay.finish_replay_update(pending)
        if output_artifact is not None:
            self._service.delete_artifact_safely(
                output_artifact.reference,
                expected_generation=output_artifact.generation,
            )
        self.db.refresh(job)
        return video
