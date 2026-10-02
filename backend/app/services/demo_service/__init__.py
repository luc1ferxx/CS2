"""Owner-scoped demo operations behind one entry point.

`DemoService` is a facade: it owns the database session, the owner context and
the artifact stores, and delegates every operation to one of eleven
components, each a single responsibility in its own module:

    library       demo_library.py         list/search, read, rename, archive, status
    ingest        demo_ingest.py          mock/real intake, parse dispatch, source artifact
    parse         parse_lifecycle.py      parse job state machine and stale-job recovery
    render        render_lifecycle.py     render job state machine and sweeps
    worker_media  render_worker_media.py  worker source download, output upload/binding
    replay        replay_blob.py          replay contract artifact and its video section
    video         video_registry.py       private video delivery and the manual MP4 bridge
    coaching      coaching_review.py      coaching suggestions and the owner's verdicts on them
    summary       match_summary.py        stored match summary (score, team names) and its backfill
    upgrade       replay_upgrade.py       in-place upgrade of an older replay to the current contract
    recompute     coaching_recompute.py   in-place recompute of older suggestions under the current rules

Rules of the composition:

* The facade is the public API. API routers, the worker and other services call
  `DemoService` methods and never a component directly.
* Components keep no state; they reach the session and stores through the
  facade (see `_component.ServiceComponent`) and call each other's seams through
  it as `self._service.<component>.<method>`. Those seams are public on the
  component but deliberately absent from the facade, so the coupling between
  responsibilities is explicit and greppable without widening the API.
* `queue_client()` is the one place the Redis client is resolved, through this
  module's `get_redis_client`, so tests can keep patching
  `app.services.demo_service.get_redis_client`.
* A match can be deleted in any state, so a write to a demo or job loaded
  earlier can find the row gone. Such operations raise `DemoGoneError` (a
  LookupError, never caught as ValueError: the API answers 404), and the parse
  transitions return False instead; `gone.py` holds the shared detection.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import redis
from sqlalchemy.orm import Session

from app.core.auth import normalize_owner_id
from app.core.redis import get_redis_client
from app.models.demo import Demo
from app.models.job import DemoJob
from app.schemas.coaching import CoachingEventOut, CoachingFeedbackOut, CoachingFeedbackSummary
from app.schemas.demo import (
    DemoIngestionStatus,
    DemoListItem,
    DemoStatus,
    RenderClipRequest,
    RenderJobManifest,
    RenderJobStatus,
    RenderWorkerResult,
)
from app.services.artifact_binding import AcceptedArtifactSnapshot, VerifiedAcceptedArtifact
from app.services.demo_service._helpers import utc_now
from app.services.demo_service.coaching_recompute import (
    CoachingRecompute,
    CoachingRecomputeClaim,
    RecomputeOutcome,
    coaching_event_rows,
)
from app.services.demo_service.coaching_review import CoachingReview
from app.services.demo_service.constants import (
    ACTIVE_DEMO_STATUSES,
    ACTIVE_PARSE_JOB_STATUSES,
    DEMO_STATUS_ORDER,
    DEMO_STATUS_SEARCH_ALIASES,
    PARSE_ABANDONED_ERROR_CODE,
    PARSE_ABANDONED_MESSAGE,
    PARSE_JOB_TYPES,
    RENDER_CLIP_DEFAULT_PRESET,
    RENDER_CLIP_IDLE_RECLAIM_SECONDS,
    RENDER_CLIP_JOB_TYPE,
    RENDER_CLIP_MAX_ATTEMPTS,
    RENDER_CLIP_NOT_CONNECTED_ERROR,
    RENDER_CLIP_STALE_AFTER_SECONDS,
    RENDER_FAILED_ERROR_CODE,
    RENDER_FAILED_PUBLIC_MESSAGE,
    RENDER_QUEUE_TIMED_OUT_ERROR_CODE,
    RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE,
    RENDER_TIMED_OUT_ERROR_CODE,
    RENDER_TIMED_OUT_PUBLIC_MESSAGE,
    RENDER_WORKER_MANIFEST_VERSION,
    RENDER_WORKER_UNAVAILABLE_ERROR_CODE,
    REPLAY_ARTIFACT_MISSING_ERROR_CODE,
    REPLAY_ARTIFACT_MISSING_MESSAGE,
    STALE_PARSE_AFTER_SECONDS,
    STEAM_MATCH_PARSE_FAILED_MESSAGE,
    STEAM_MATCH_PLAYER_ID_LIMIT,
    STEAM_MATCH_PLAYER_LIMIT,
    STEAM_MATCH_PLAYER_NAME_LIMIT,
    UNCLAIMED_RENDER_CLIP_STATUSES,
)
from app.services.demo_service.demo_ingest import DemoIngest, PreparedRealDemo, QueueClient
from app.services.demo_service.demo_library import DemoLibrary
from app.services.demo_service.errors import (
    DemoArtifactBindError,
    DemoDispatchError,
    DemoGoneError,
    ReplayBlobUnavailableError,
)
from app.services.demo_service.gone import rows_missing
from app.services.demo_service.match_summary import MatchSummaries, TeamNamesReader
from app.services.demo_service.parse_lifecycle import ParseLifecycle
from app.services.demo_service.render_lifecycle import RenderLifecycle
from app.services.demo_service.render_worker_media import RenderWorkerMedia
from app.services.demo_service.replay_blob import ReplayBlob
from app.services.demo_service.replay_upgrade import ReplayUpgrade, ReplayUpgradeClaim, UpgradeOutcome
from app.services.demo_service.video_registry import PrivateVideoHandle, VideoRegistry
from app.services.storage import ArtifactStore, LocalStorageService, artifact_store_from_settings
from app.services.upload_service import StoredVideoUpload


class DemoService:
    def __init__(
        self,
        db: Session,
        owner_id: str | None = None,
        storage: LocalStorageService | None = None,
        artifact_store: ArtifactStore | None = None,
        *,
        internal: bool = False,
    ):
        self.db = db
        self.owner_id = None if internal else normalize_owner_id(owner_id)
        self._storage = storage
        self._artifact_store = artifact_store
        self.library = DemoLibrary(self)
        self.ingest = DemoIngest(self)
        self.parse = ParseLifecycle(self)
        self.render = RenderLifecycle(self)
        self.worker_media = RenderWorkerMedia(self)
        self.replay = ReplayBlob(self)
        self.video = VideoRegistry(self)
        self.coaching = CoachingReview(self)
        self.summary = MatchSummaries(self)
        self.upgrade = ReplayUpgrade(self)
        self.recompute = CoachingRecompute(self)

    @classmethod
    def for_internal(
        cls,
        db: Session,
        storage: LocalStorageService | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> "DemoService":
        return cls(
            db,
            storage=storage,
            artifact_store=artifact_store,
            internal=True,
        )

    def require_owner_id(self) -> str:
        if self.owner_id is None:
            raise RuntimeError("Owner-scoped operations require an explicit owner context")
        return self.owner_id

    def queue_client(self) -> redis.Redis:
        """The Redis client used to enqueue jobs; resolved on every call (see module docstring)."""
        return get_redis_client()

    @property
    def storage(self) -> LocalStorageService:
        return self._storage or LocalStorageService.from_settings()

    @property
    def artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            self._artifact_store = artifact_store_from_settings()
        return self._artifact_store

    def delete_artifact_safely(
        self,
        reference: str,
        *,
        expected_generation: str | None = None,
    ) -> None:
        try:
            self.artifact_store.delete(
                reference,
                expected_generation=expected_generation,
            )
        except Exception:
            pass

    # -- Rows deleted mid-request (gone.py) ---------------------------------------
    def demo_missing(self, demo_id: str) -> bool:
        """Roll back, then report whether the demo row no longer exists."""
        return rows_missing(self.db, demo_id=demo_id)

    def render_job_missing(self, job_id: str) -> bool:
        """Roll back, then report whether the job (or its demo) no longer exists."""
        return rows_missing(self.db, job_id=job_id)

    # -- DemoLibrary (demo_library.py) --------------------------------------------
    def list_demos(
        self,
        *,
        search: str | None = None,
        status: str | None = None,
        map_name: str | None = None,
        sort: str = "recent",
        order: str | None = None,
        include_archived: bool = False,
    ) -> list[DemoListItem]:
        return self.library.list_demos(
            search=search,
            status=status,
            map_name=map_name,
            sort=sort,
            order=order,
            include_archived=include_archived,
        )

    def demo_list_item(self, demo: Demo) -> DemoListItem:
        return self.library.demo_list_item(demo)

    def demo_status(self, demo: Demo) -> DemoStatus:
        return self.library.demo_status(demo)

    def get_demo(self, demo_id: str) -> Demo | None:
        return self.library.get_demo(demo_id)

    def update_demo(
        self,
        demo_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
    ) -> Demo | None:
        return self.library.update_demo(demo_id, name=name, archived=archived)

    def archive_demo(self, demo_id: str) -> Demo | None:
        return self.library.archive_demo(demo_id)

    # -- DemoIngest (demo_ingest.py) ----------------------------------------------
    def create_mock_demo(self) -> DemoListItem:
        return self.ingest.create_mock_demo()

    def create_real_demo(self, upload: Any) -> DemoListItem:
        return self.ingest.create_real_demo(upload)

    def prepare_real_demo(
        self,
        *,
        stream: BinaryIO | None,
        filename: str,
        content_type: str | None,
        demo_id: str | None = None,
        job_id: str | None = None,
    ) -> PreparedRealDemo:
        return self.ingest.prepare_real_demo(
            stream=stream,
            filename=filename,
            content_type=content_type,
            demo_id=demo_id,
            job_id=job_id,
        )

    def commit_prepared_real_demo(self, prepared: PreparedRealDemo) -> None:
        return self.ingest.commit_prepared_real_demo(prepared)

    def commit_prepared_real_demo_rows(self) -> None:
        return self.ingest.commit_prepared_real_demo_rows()

    def reload_prepared_real_demo(self, prepared: PreparedRealDemo) -> None:
        return self.ingest.reload_prepared_real_demo(prepared)

    def discard_prepared_real_demo(self, prepared: PreparedRealDemo) -> None:
        return self.ingest.discard_prepared_real_demo(prepared)

    def dispatch_prepared_real_demo(
        self,
        prepared: PreparedRealDemo,
        *,
        redis_client: QueueClient | None = None,
    ) -> None:
        return self.ingest.dispatch_prepared_real_demo(prepared, redis_client=redis_client)

    def dispatch_parse_job(
        self,
        *,
        job_id: str,
        demo_id: str,
        redis_client: QueueClient | None = None,
    ) -> None:
        return self.ingest.dispatch_parse_job(
            job_id=job_id,
            demo_id=demo_id,
            redis_client=redis_client,
        )

    def source_artifact_snapshot(self, job: DemoJob) -> AcceptedArtifactSnapshot:
        return self.ingest.source_artifact_snapshot(job)

    def verify_source_artifact(
        self,
        demo: Demo,
        job: DemoJob,
    ) -> VerifiedAcceptedArtifact:
        return self.ingest.verify_source_artifact(demo, job)

    def source_demo_path(self, demo: Demo) -> Path:
        return self.ingest.source_demo_path(demo)

    def materialized_source_demo(
        self,
        demo: Demo,
        job: DemoJob,
    ) -> AbstractContextManager[Path]:
        return self.ingest.materialized_source_demo(demo, job)

    def source_demo_storage_key(self, demo: Demo) -> str:
        return self.ingest.source_demo_storage_key(demo)

    # -- ParseLifecycle (parse_lifecycle.py) --------------------------------------
    def latest_parse_job(self, demo: Demo) -> DemoJob | None:
        return self.parse.latest_parse_job(demo)

    def demo_ingestion_status(self, demo: Demo) -> DemoIngestionStatus:
        return self.parse.demo_ingestion_status(demo)

    def retry_parse_job(
        self,
        demo: Demo,
        *,
        admission: Callable[[], AbstractContextManager[object]] = nullcontext,
    ) -> DemoListItem:
        return self.parse.retry_parse_job(demo, admission=admission)

    def claim_parse_job(self, demo: Demo, job: DemoJob) -> bool:
        return self.parse.claim_parse_job(demo, job)

    def mark_parse_analyzing(self, demo: Demo, job: DemoJob) -> bool:
        return self.parse.mark_parse_analyzing(demo, job)

    def complete_parse_job(
        self,
        demo: Demo,
        job: DemoJob,
        replay: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        name: str | None = None,
        team_names: Mapping[str, Any] | None = None,
    ) -> bool:
        return self.parse.complete_parse_job(
            demo, job, replay, events, name=name, team_names=team_names,
        )

    def fail_parse_job(
        self,
        demo: Demo,
        job: DemoJob,
        error: str,
        *,
        error_code: str = "PARSER_FAILED",
    ) -> bool:
        return self.parse.fail_parse_job(demo, job, error, error_code=error_code)

    def abandon_parse_job(self, job: DemoJob) -> bool:
        return self.parse.abandon_parse_job(job)

    def recover_parse_job(self, job_id: str, demo_id: str) -> bool:
        return self.parse.recover_parse_job(job_id, demo_id)

    def reclaim_stale_parse_jobs(
        self,
        *,
        older_than_seconds: int,
        redispatch_after_seconds: int,
        now: datetime | None = None,
        limit: int = 20,
        redis_client: Any | None = None,
    ) -> list[str]:
        return self.parse.reclaim_stale_parse_jobs(
            older_than_seconds=older_than_seconds,
            redispatch_after_seconds=redispatch_after_seconds,
            now=now,
            limit=limit,
            redis_client=redis_client,
        )

    # -- RenderLifecycle (render_lifecycle.py) ------------------------------------
    def create_mock_render_job(self, demo: Demo) -> DemoJob:
        return self.render.create_mock_render_job(demo)

    def create_render_clip_job(self, demo: Demo, request: RenderClipRequest) -> DemoJob:
        return self.render.create_render_clip_job(demo, request)

    def transition_mock_render_job(
        self,
        job: DemoJob,
        *,
        job_status: str,
        video_status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return self.render.transition_mock_render_job(
            job,
            job_status=job_status,
            video_status=video_status,
            error_code=error_code,
            error_message=error_message,
        )

    def list_render_clip_jobs(self, demo: Demo) -> list[RenderJobStatus]:
        return self.render.list_render_clip_jobs(demo)

    def claim_render_clip_job(self, job: DemoJob) -> DemoJob:
        return self.render.claim_render_clip_job(job)

    def get_render_clip_job(self, job_id: str) -> DemoJob | None:
        return self.render.get_render_clip_job(job_id)

    def next_render_clip_job(self, statuses: tuple[str, ...] = ("queued", "pending")) -> DemoJob | None:
        return self.render.next_render_clip_job(statuses)

    def fail_unclaimed_render_clip_jobs(
        self,
        *,
        older_than_seconds: int,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[str]:
        return self.render.fail_unclaimed_render_clip_jobs(
            older_than_seconds=older_than_seconds,
            now=now,
            limit=limit,
        )

    def reclaim_stale_render_clip_jobs(
        self,
        *,
        older_than_seconds: int,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[str]:
        return self.render.reclaim_stale_render_clip_jobs(
            older_than_seconds=older_than_seconds,
            now=now,
            limit=limit,
        )

    def retry_render_clip_job(self, demo: Demo, job_id: str) -> DemoJob | None:
        return self.render.retry_render_clip_job(demo, job_id)

    def render_job_status(
        self, job: DemoJob, *,
        demo_video: dict[str, Any] | None = None,
        current_video: dict[str, Any] | None = None,
    ) -> RenderJobStatus:
        return self.render.render_job_status(
            job,
            demo_video=demo_video,
            current_video=current_video,
        )

    def render_job_manifest(self, job: DemoJob) -> RenderJobManifest:
        return self.render.render_job_manifest(job)

    def apply_render_worker_result(
        self,
        job: DemoJob,
        result: RenderWorkerResult,
    ) -> dict[str, Any]:
        return self.render.apply_render_worker_result(job, result)

    def fail_render_clip_job(
        self,
        job: DemoJob,
        error_message: str,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        return self.render.fail_render_clip_job(job, error_message, error_code=error_code)

    # -- RenderWorkerMedia (render_worker_media.py) -------------------------------
    def open_render_source(self, job: DemoJob) -> tuple[Any, AcceptedArtifactSnapshot]:
        return self.worker_media.open_render_source(job)

    @staticmethod
    def stream_render_source(opened: Any, snapshot: AcceptedArtifactSnapshot) -> Iterator[bytes]:
        return RenderWorkerMedia.stream_render_source(opened, snapshot)

    def bind_render_worker_media(
        self,
        job: DemoJob,
        stored_video: StoredVideoUpload,
    ) -> AcceptedArtifactSnapshot:
        return self.worker_media.bind_render_worker_media(job, stored_video)

    def public_render_job_video(
        self, job: DemoJob, *, current_video: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        return self.worker_media.public_render_job_video(job, current_video=current_video)

    def open_private_render_video(
        self, demo: Demo, job_id: str
    ) -> tuple[PrivateVideoHandle, Any] | None:
        return self.worker_media.open_private_render_video(demo, job_id)

    # -- ReplayBlob (replay_blob.py) ----------------------------------------------
    def replay_blob_path(self, demo_id: str) -> Path:
        return self.replay.replay_blob_path(demo_id)

    def replay_blob_key(self, demo_id: str) -> str:
        return self.replay.replay_blob_key(demo_id)

    def write_replay_blob(self, demo_id: str, replay: dict[str, Any]) -> str:
        return self.replay.write_replay_blob(demo_id, replay)

    def replay_artifact_is_missing(self, demo: Demo) -> bool:
        return self.replay.replay_artifact_is_missing(demo)

    def load_replay_blob(self, demo: Demo) -> dict[str, object] | None:
        return self.replay.load_replay_blob(demo)

    def public_replay(self, demo: Demo) -> dict[str, object] | None:
        return self.replay.public_replay(demo)

    def get_video_status(self, demo: Demo) -> dict[str, Any]:
        return self.replay.get_video_status(demo)

    def public_video_status(
        self, demo: Demo, *, internal_video: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.replay.public_video_status(demo, internal_video=internal_video)

    def update_replay_video(self, demo: Demo, video: dict[str, Any]) -> dict[str, Any]:
        return self.replay.update_replay_video(demo, video)

    def update_render_clip_video_status(
        self,
        demo: Demo,
        status: str,
        error_message: str | None,
        *,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        return self.replay.update_render_clip_video_status(
            demo,
            status,
            error_message,
            error_code=error_code,
        )

    # -- VideoRegistry (video_registry.py) ----------------------------------------
    def private_video_available(self, demo: Demo) -> bool:
        return self.video.private_video_available(demo)

    def get_private_video_path(self, demo: Demo) -> Path | None:
        return self.video.get_private_video_path(demo)

    def open_private_video(
        self,
        demo: Demo,
    ) -> tuple[PrivateVideoHandle, Any] | None:
        return self.video.open_private_video(demo)

    def attach_manual_video(self, demo: Demo, stored_video: StoredVideoUpload) -> dict[str, Any]:
        return self.video.attach_manual_video(demo, stored_video)

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
        return self.video.update_video_calibration(
            demo,
            duration_seconds=duration_seconds,
            tick_start=tick_start,
            tick_end=tick_end,
            tick_rate=tick_rate,
            time_origin_seconds=time_origin_seconds,
        )

    # -- CoachingReview (coaching_review.py) --------------------------------------
    def list_coaching_events(self, demo_id: str) -> list[CoachingEventOut]:
        return self.coaching.list_coaching_events(demo_id)

    def save_coaching_feedback(
        self,
        demo: Demo,
        event_id: str,
        *,
        verdict: str,
        note: str | None = None,
    ) -> CoachingFeedbackOut | None:
        return self.coaching.save_coaching_feedback(demo, event_id, verdict=verdict, note=note)

    def clear_coaching_feedback(self, demo: Demo, event_id: str) -> bool:
        return self.coaching.clear_coaching_feedback(demo, event_id)

    def coaching_feedback_summary(self, demo_id: str | None = None) -> CoachingFeedbackSummary:
        return self.coaching.coaching_feedback_summary(demo_id)

    # -- MatchSummaries (match_summary.py) ----------------------------------------
    def demo_ids_missing_match_summary(
        self,
        *,
        limit: int,
        exclude: Iterable[str] = (),
    ) -> list[str]:
        return self.summary.demo_ids_missing_summary(limit=limit, exclude=exclude)

    def backfill_match_summary(self, demo_id: str, read_team_names: TeamNamesReader) -> bool:
        return self.summary.backfill_match_summary(demo_id, read_team_names)

    # -- ReplayUpgrade (replay_upgrade.py) ----------------------------------------
    def demo_ids_due_for_replay_upgrade(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> list[str]:
        return self.upgrade.demo_ids_due(limit=limit, now=now)

    def claim_replay_upgrade(
        self,
        demo_id: str,
        *,
        now: datetime | None = None,
    ) -> ReplayUpgradeClaim | None:
        return self.upgrade.claim(demo_id, now=now)

    def release_replay_upgrade(self, claim: ReplayUpgradeClaim) -> bool:
        return self.upgrade.release(claim)

    def mark_replay_upgrade_current(self, claim: ReplayUpgradeClaim, version: str) -> bool:
        return self.upgrade.mark_current(claim, version)

    def record_replay_upgrade_failure(
        self,
        claim: ReplayUpgradeClaim,
        error_code: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.upgrade.record_failure(claim, error_code, now=now)

    def complete_replay_upgrade(
        self,
        claim: ReplayUpgradeClaim,
        *,
        expected_replay_key: str | None,
        replay: dict[str, Any],
        team_names: Mapping[str, Any] | None = None,
    ) -> UpgradeOutcome:
        return self.upgrade.complete(
            claim,
            expected_replay_key=expected_replay_key,
            replay=replay,
            team_names=team_names,
        )

    # -- CoachingRecompute (coaching_recompute.py) --------------------------------
    def demo_ids_due_for_coaching_recompute(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> list[str]:
        return self.recompute.demo_ids_due(limit=limit, now=now)

    def claim_coaching_recompute(
        self,
        demo_id: str,
        *,
        now: datetime | None = None,
    ) -> CoachingRecomputeClaim | None:
        return self.recompute.claim(demo_id, now=now)

    def release_coaching_recompute(self, claim: CoachingRecomputeClaim) -> bool:
        return self.recompute.release(claim)

    def record_coaching_recompute_failure(
        self,
        claim: CoachingRecomputeClaim,
        error_code: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.recompute.record_failure(claim, error_code, now=now)

    def complete_coaching_recompute(
        self,
        claim: CoachingRecomputeClaim,
        *,
        expected_replay_key: str | None,
        events: list[dict[str, Any]],
    ) -> RecomputeOutcome:
        return self.recompute.complete(
            claim,
            expected_replay_key=expected_replay_key,
            events=events,
        )

__all__ = [
    "ACTIVE_DEMO_STATUSES",
    "ACTIVE_PARSE_JOB_STATUSES",
    "DEMO_STATUS_ORDER",
    "DEMO_STATUS_SEARCH_ALIASES",
    "PARSE_ABANDONED_ERROR_CODE",
    "PARSE_ABANDONED_MESSAGE",
    "PARSE_JOB_TYPES",
    "RENDER_CLIP_DEFAULT_PRESET",
    "RENDER_CLIP_IDLE_RECLAIM_SECONDS",
    "RENDER_CLIP_JOB_TYPE",
    "RENDER_CLIP_MAX_ATTEMPTS",
    "RENDER_CLIP_NOT_CONNECTED_ERROR",
    "RENDER_CLIP_STALE_AFTER_SECONDS",
    "RENDER_FAILED_ERROR_CODE",
    "RENDER_FAILED_PUBLIC_MESSAGE",
    "RENDER_QUEUE_TIMED_OUT_ERROR_CODE",
    "RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE",
    "RENDER_TIMED_OUT_ERROR_CODE",
    "RENDER_TIMED_OUT_PUBLIC_MESSAGE",
    "RENDER_WORKER_MANIFEST_VERSION",
    "RENDER_WORKER_UNAVAILABLE_ERROR_CODE",
    "REPLAY_ARTIFACT_MISSING_ERROR_CODE",
    "REPLAY_ARTIFACT_MISSING_MESSAGE",
    "STALE_PARSE_AFTER_SECONDS",
    "STEAM_MATCH_PARSE_FAILED_MESSAGE",
    "STEAM_MATCH_PLAYER_ID_LIMIT",
    "STEAM_MATCH_PLAYER_LIMIT",
    "STEAM_MATCH_PLAYER_NAME_LIMIT",
    "UNCLAIMED_RENDER_CLIP_STATUSES",
    "CoachingRecomputeClaim",
    "DemoArtifactBindError",
    "DemoDispatchError",
    "DemoGoneError",
    "DemoService",
    "PreparedRealDemo",
    "PrivateVideoHandle",
    "QueueClient",
    "ReplayBlobUnavailableError",
    "ReplayUpgradeClaim",
    "coaching_event_rows",
    "get_redis_client",
    "utc_now",
]
