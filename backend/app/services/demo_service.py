import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import asc, case, desc, func, or_
from sqlalchemy.orm import Session

from app.core.auth import normalize_owner_id
from app.core.config import settings
from app.core.redis import get_redis_client
from app.models.coaching import CoachingEvent
from app.models.demo import Demo
from app.models.job import DemoJob
from app.parser.replay_contract import normalize_replay_contract
from app.schemas.coaching import CoachingEventOut
from app.schemas.demo import (
    DemoIngestionStatus,
    DemoListItem,
    DemoStatus,
    ParseFailureMetadata,
    RenderClipRequest,
    RenderJobManifest,
    RenderJobStatus,
    RenderWorkerResult,
)
from app.services.storage import LocalStorageService, StorageKeyError
from app.services.upload_service import StoredVideoUpload, demo_upload_key, store_demo_upload


RENDER_CLIP_JOB_TYPE = "render_clip"
PARSE_JOB_TYPES = ("real_parse", "mock_parse")
RENDER_CLIP_DEFAULT_PRESET = "event_clip_v1"
RENDER_WORKER_MANIFEST_VERSION = "render_worker_v1"
DEMO_STATUS_ORDER = ("queued", "parsing", "analyzing", "completed", "failed")
ACTIVE_DEMO_STATUSES = {"queued", "parsing", "analyzing"}
ACTIVE_PARSE_JOB_STATUSES = {"queued", "pending", "processing"}
STALE_PARSE_AFTER_SECONDS = 15 * 60
DEMO_STATUS_SEARCH_ALIASES = {
    "uploaded": "queued",
    "upload": "queued",
    "ready": "completed",
    "complete": "completed",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DemoService:
    def __init__(
        self,
        db: Session,
        owner_id: str | None = None,
        storage: LocalStorageService | None = None,
    ):
        self.db = db
        self.owner_id = normalize_owner_id(owner_id)
        self._storage = storage

    @property
    def storage(self) -> LocalStorageService:
        return self._storage or LocalStorageService.from_settings()

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
        query = self.db.query(Demo).filter(Demo.owner_id == self.owner_id)

        if not include_archived:
            query = query.filter(Demo.archived.is_(False))

        normalized_search = (search or "").strip().lower()
        if normalized_search:
            for search_token in normalized_search.split():
                like_search = f"%{search_token}%"
                search_terms = [
                    func.lower(Demo.id).like(like_search),
                    func.lower(Demo.name).like(like_search),
                    func.lower(Demo.original_filename).like(like_search),
                    func.lower(Demo.map_name).like(like_search),
                    func.lower(Demo.status).like(like_search),
                    func.lower(Demo.error_message).like(like_search),
                ]
                status_alias = DEMO_STATUS_SEARCH_ALIASES.get(search_token)
                if status_alias:
                    search_terms.append(Demo.status == status_alias)
                query = query.filter(or_(*search_terms))

        if status and status != "all":
            query = query.filter(Demo.status == status)
        if map_name and map_name != "all":
            query = query.filter(Demo.map_name == map_name)

        status_sort = case(
            *[(Demo.status == item, index) for index, item in enumerate(DEMO_STATUS_ORDER)],
            else_=len(DEMO_STATUS_ORDER),
        )
        sort_column = {
            "recent": Demo.created_at,
            "created": Demo.created_at,
            "updated": Demo.updated_at,
            "name": func.lower(Demo.name),
            "map": func.lower(Demo.map_name),
            "status": status_sort,
        }.get(sort, Demo.created_at)
        normalized_order = order or ("desc" if sort in {"recent", "created", "updated"} else "asc")
        direction = desc if normalized_order == "desc" else asc
        demos = (
            query.order_by(
                direction(sort_column),
                asc(func.lower(Demo.name)),
                asc(func.lower(Demo.original_filename)),
                asc(Demo.id),
            )
            .all()
        )
        return [self.demo_list_item(demo) for demo in demos]

    def demo_list_item(self, demo: Demo) -> DemoListItem:
        video = self._video_status_for_list(demo)
        latest_render_job = self._latest_render_clip_job(demo)
        return DemoListItem.model_validate(demo).model_copy(
            update={
                "video_status": _optional_str(video.get("status")),
                "video_source": _optional_str(video.get("source")),
                "video_url": _optional_str(video.get("url")),
                "latest_render_status": latest_render_job.status if latest_render_job else None,
                "ingestion": self.demo_ingestion_status(demo),
            }
        )

    def demo_status(self, demo: Demo) -> DemoStatus:
        return DemoStatus.model_validate(demo).model_copy(
            update={"ingestion": self.demo_ingestion_status(demo)}
        )

    def get_demo(self, demo_id: str) -> Demo | None:
        return (
            self.db.query(Demo)
            .filter(Demo.id == demo_id, Demo.owner_id == self.owner_id)
            .one_or_none()
        )

    def update_demo(
        self,
        demo_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
    ) -> Demo | None:
        demo = self.get_demo(demo_id)
        if demo is None:
            return None

        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise ValueError("name cannot be blank")
            if len(normalized_name) > 255:
                raise ValueError("name must be 255 characters or fewer")
            demo.name = normalized_name
        if archived is not None:
            demo.archived = archived

        self.db.commit()
        self.db.refresh(demo)
        return demo

    def archive_demo(self, demo_id: str) -> Demo | None:
        return self.update_demo(demo_id, archived=True)

    def create_mock_demo(self) -> DemoListItem:
        demo_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        demo = Demo(
            id=demo_id,
            owner_id=self.owner_id,
            legacy_user_id=self.owner_id,
            name=f"Mock Match {demo_id[:8]}",
            original_filename=f"mock_demo_{demo_id[:8]}.dem",
            map_name="de_inferno",
            tick_rate=64,
            round_count=0,
            coaching_event_count=0,
            status="queued",
        )
        job = DemoJob(
            id=job_id,
            demo_id=demo_id,
            job_type="mock_parse",
            status="queued",
            attempts=0,
        )

        self.db.add(demo)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(demo)

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo_id}),
        )

        return self.demo_list_item(demo)

    async def create_real_demo(self, upload: Any) -> DemoListItem:
        demo_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        stored_upload = await store_demo_upload(demo_id, upload)

        demo = Demo(
            id=demo_id,
            owner_id=self.owner_id,
            legacy_user_id=self.owner_id,
            name=f"Uploaded Demo {demo_id[:8]}",
            original_filename=stored_upload.original_filename,
            source_storage_key=stored_upload.storage_key,
            map_name="unknown",
            tick_rate=64,
            round_count=0,
            coaching_event_count=0,
            status="queued",
        )
        job = DemoJob(
            id=job_id,
            demo_id=demo_id,
            job_type="real_parse",
            status="queued",
            attempts=0,
        )

        self.db.add(demo)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(demo)

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo_id}),
        )

        return self.demo_list_item(demo)

    def latest_parse_job(self, demo: Demo) -> DemoJob | None:
        return (
            self.db.query(DemoJob)
            .filter(DemoJob.demo_id == demo.id, DemoJob.job_type.in_(PARSE_JOB_TYPES))
            .order_by(desc(DemoJob.created_at), desc(DemoJob.id))
            .first()
        )

    def demo_ingestion_status(self, demo: Demo) -> DemoIngestionStatus:
        job = self.latest_parse_job(demo)
        updated_at = _aware_datetime(
            getattr(demo, "updated_at", None)
            or getattr(demo, "created_at", None)
            or utc_now()
        )
        active = demo.status in ACTIVE_DEMO_STATUSES or (
            job is not None and job.status in ACTIVE_PARSE_JOB_STATUSES
        )
        retryable = self._parse_retryable(demo, job)
        attempt_count = int(job.attempts) if job is not None else 0
        failure = (
            self._parse_failure_metadata(demo, job, retryable=retryable, attempt_count=attempt_count)
            if demo.status == "failed" or (job is not None and job.status == "failed")
            else None
        )
        stale_since = _aware_datetime(
            (job.started_at if job is not None else None)
            or updated_at
            or (job.created_at if job is not None else None)
        )
        stale = active and (utc_now() - stale_since).total_seconds() > STALE_PARSE_AFTER_SECONDS

        return DemoIngestionStatus(
            phase=_ingestion_phase(demo.status),
            active=active,
            stale=stale,
            retryable=retryable,
            attemptCount=attempt_count,
            jobId=job.id if job is not None else None,
            jobType=job.job_type if job is not None else None,
            jobStatus=job.status if job is not None else None,
            hasSourceDemo=bool(getattr(demo, "source_storage_key", None)),
            updatedAt=updated_at,
            startedAt=(
                _aware_datetime(job.started_at)
                if job is not None and job.started_at is not None
                else None
            ),
            finishedAt=(
                _aware_datetime(job.finished_at)
                if job is not None and job.finished_at is not None
                else None
            ),
            failure=failure,
        )

    def retry_parse_job(self, demo: Demo) -> DemoListItem:
        if demo.status != "failed":
            raise ValueError("Only failed parse jobs can be retried")
        source_storage_key = getattr(demo, "source_storage_key", None)
        if not source_storage_key:
            raise ValueError("Uploaded source demo is not available for retry")
        try:
            source_exists = self.storage.exists(source_storage_key)
        except (OSError, StorageKeyError):
            source_exists = False
        if not source_exists:
            raise ValueError("Uploaded source demo artifact is missing")

        latest_job = self.latest_parse_job(demo)
        if latest_job is not None and latest_job.status in ACTIVE_PARSE_JOB_STATUSES:
            raise ValueError("Parse is already active")

        job_id = str(uuid.uuid4())
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type="real_parse",
            status="queued",
            attempts=0,
        )
        demo.status = "queued"
        demo.error_message = None
        demo.completed_at = None
        self.db.add(job)
        self.db.commit()
        self.db.refresh(demo)

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo.id}),
        )
        return self.demo_list_item(demo)

    def claim_parse_job(self, demo: Demo, job: DemoJob) -> None:
        self._ensure_parse_job(job)
        job.status = "processing"
        job.attempts += 1
        job.started_at = utc_now()
        job.finished_at = None
        job.error_message = None
        job.metadata_json = _metadata_json({**_job_metadata(job), "phase": "parsing"})
        demo.status = "parsing"
        demo.error_message = None
        self.db.commit()

    def mark_parse_analyzing(self, demo: Demo, job: DemoJob) -> None:
        self._ensure_parse_job(job)
        demo.status = "analyzing"
        job.metadata_json = _metadata_json({**_job_metadata(job), "phase": "analyzing"})
        self.db.commit()

    def complete_parse_job(
        self,
        demo: Demo,
        job: DemoJob,
        replay: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        name: str | None = None,
    ) -> None:
        self._ensure_parse_job(job)
        replay_storage_key = self.write_replay_blob(demo.id, replay)

        self.db.query(CoachingEvent).filter(CoachingEvent.demo_id == demo.id).delete()
        self.db.add_all(CoachingEvent(**event) for event in events)

        demo.status = "completed"
        if name is not None:
            demo.name = name
        demo.map_name = replay["mapName"]
        demo.tick_rate = replay["tickRate"]
        demo.round_count = len(replay["rounds"])
        demo.coaching_event_count = len(events)
        demo.replay_storage_key = replay_storage_key
        demo.completed_at = utc_now()
        demo.error_message = None

        job.status = "completed"
        job.finished_at = utc_now()
        job.error_message = None
        job.metadata_json = _metadata_json({**_job_metadata(job), "phase": "ready"})
        self.db.commit()

    def fail_parse_job(self, demo: Demo, job: DemoJob, error: str) -> None:
        self._ensure_parse_job(job)
        failed_at = utc_now()
        short_message = _compact_failure_message(error)
        failure_metadata = {
            "errorCode": "PARSER_FAILED",
            "message": short_message,
            "failedAt": failed_at.isoformat(),
            "updatedAt": failed_at.isoformat(),
        }
        metadata = _job_metadata(job)
        metadata["failure"] = failure_metadata
        metadata["phase"] = "failed"

        demo.status = "failed"
        demo.error_message = short_message
        job.status = "failed"
        job.error_message = short_message
        job.finished_at = failed_at
        job.metadata_json = _metadata_json(metadata)
        self.db.commit()

    def _ensure_parse_job(self, job: DemoJob) -> None:
        if job.job_type not in PARSE_JOB_TYPES:
            raise ValueError("Only parse jobs support parser status transitions")

    def _parse_retryable(self, demo: Demo, job: DemoJob | None) -> bool:
        if demo.status != "failed":
            return False
        if job is not None and job.status in ACTIVE_PARSE_JOB_STATUSES:
            return False
        source_storage_key = getattr(demo, "source_storage_key", None)
        if not source_storage_key:
            return False
        try:
            return self.storage.exists(source_storage_key)
        except (OSError, StorageKeyError):
            return False

    def _parse_failure_metadata(
        self,
        demo: Demo,
        job: DemoJob | None,
        *,
        retryable: bool,
        attempt_count: int,
    ) -> ParseFailureMetadata:
        metadata = _job_metadata(job) if job is not None else {}
        stored_failure = metadata.get("failure") if isinstance(metadata.get("failure"), dict) else {}
        message = _compact_failure_message(
            _optional_str(stored_failure.get("message"))
            or (job.error_message if job is not None else None)
            or demo.error_message
            or "Parser failed"
        )
        failed_at = _parse_datetime(_optional_str(stored_failure.get("failedAt")))
        if failed_at is None and job is not None:
            failed_at = job.finished_at
        failed_at = _aware_datetime(failed_at) if failed_at is not None else None
        updated_at = _parse_datetime(_optional_str(stored_failure.get("updatedAt")))
        if updated_at is None:
            updated_at = demo.updated_at or failed_at or utc_now()

        return ParseFailureMetadata(
            errorCode=_optional_str(stored_failure.get("errorCode")) or "PARSER_FAILED",
            message=message,
            failedAt=failed_at,
            updatedAt=_aware_datetime(updated_at),
            retryable=retryable,
            attemptCount=attempt_count,
        )

    def source_demo_path(self, demo: Demo) -> Path:
        return self.storage.path_for_key(self.source_demo_storage_key(demo))

    def source_demo_storage_key(self, demo: Demo) -> str:
        return getattr(demo, "source_storage_key", None) or demo_upload_key(
            demo.id,
            demo.original_filename,
        )

    def create_mock_render_job(self, demo: Demo) -> DemoJob:
        replay = self.load_replay_blob(demo)
        if replay is None:
            raise ValueError("Replay blob is not ready")

        job_id = str(uuid.uuid4())
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type="mock_render",
            status="queued",
            attempts=0,
        )
        self.db.add(job)
        self.update_replay_video(
            demo,
            {
                **self.get_video_status(demo),
                "status": "queued",
                "source": "rendered",
                "url": None,
                "errorMessage": None,
            },
        )
        self.db.commit()

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo.id}),
        )
        return job

    def create_render_clip_job(self, demo: Demo, request: RenderClipRequest) -> DemoJob:
        replay = self.load_replay_blob(demo)
        if replay is None:
            raise ValueError("Replay blob is not ready")

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

        metadata = _compact_render_clip_metadata(
            request,
            duration_seconds=duration_seconds,
            max_duration_seconds=max_duration_seconds,
            demo_storage_key=self.source_demo_storage_key(demo),
            replay_storage_key=getattr(demo, "replay_storage_key", None) or self.replay_blob_key(demo.id),
        )

        job_id = str(uuid.uuid4())
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type=RENDER_CLIP_JOB_TYPE,
            status="queued",
            attempts=0,
            metadata_json=json.dumps(metadata, separators=(",", ":")),
        )
        self.db.add(job)
        self.update_render_clip_video_status(demo, "queued", None)
        self.db.commit()
        self.db.refresh(job)

        get_redis_client().lpush(
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

    def list_render_clip_jobs(self, demo: Demo) -> list[RenderJobStatus]:
        jobs = (
            self.db.query(DemoJob)
            .filter(DemoJob.demo_id == demo.id, DemoJob.job_type == RENDER_CLIP_JOB_TYPE)
            .order_by(desc(DemoJob.created_at))
            .limit(20)
            .all()
        )
        return [self.render_job_status(job) for job in jobs]

    def claim_render_clip_job(self, job: DemoJob) -> DemoJob:
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs can be claimed by render workers")
        if job.status in {"completed", "failed"}:
            raise ValueError(f"Render job is already {job.status}")
        if job.status not in {"queued", "pending"}:
            return job

        job.status = "rendering"
        job.attempts += 1
        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = None
        job.error_message = None
        self.update_render_clip_video_status(job.demo, "rendering", None)
        self.db.commit()
        self.db.refresh(job)
        return job

    def _latest_render_clip_job(self, demo: Demo) -> DemoJob | None:
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

    def render_job_status(self, job: DemoJob) -> RenderJobStatus:
        metadata = _job_metadata(job)
        video = self.get_video_status(job.demo) if job.demo is not None else {}
        return RenderJobStatus(
            job_id=job.id,
            demo_id=job.demo_id,
            job_type=job.job_type,
            status=job.status,
            source=_optional_str(video.get("source")) or "unknown",
            video_status=_optional_str(video.get("status")),
            video_url=_optional_str(video.get("url")),
            tick_start=_optional_int(metadata.get("tickStart")),
            tick_end=_optional_int(metadata.get("tickEnd")),
            tick_rate=_optional_int(metadata.get("tickRate")),
            duration_seconds=_optional_float(metadata.get("durationSeconds")),
            event_id=_optional_str(metadata.get("eventId")),
            player_id=_optional_str(metadata.get("playerId")),
            pov_steam_id=_optional_str(metadata.get("povSteamId")),
            round_number=_optional_int(metadata.get("roundNumber")),
            render_preset=_optional_str(metadata.get("renderPreset")),
            metadata=metadata,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    def render_job_manifest(self, job: DemoJob) -> RenderJobManifest:
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs have render worker manifests")

        demo = job.demo
        metadata = _job_metadata(job)
        return RenderJobManifest(
            manifestVersion=RENDER_WORKER_MANIFEST_VERSION,
            jobId=job.id,
            demoId=demo.id,
            jobType=job.job_type,
            status=job.status,
            demoFilePath=str(self.source_demo_path(demo)),
            demoStorageKey=_optional_str(metadata.get("demoStorageKey")) or self.source_demo_storage_key(demo),
            replayStorageKey=_optional_str(metadata.get("replayStorageKey")) or getattr(demo, "replay_storage_key", None),
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
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs accept render worker results")
        if job.status in {"completed", "failed"}:
            raise ValueError(f"Render job is already {job.status}")

        normalized_status = result.status.lower()
        if normalized_status in {"completed", "ready"}:
            video = self.complete_render_clip_video(job.demo, result)
            job.status = "completed"
            job.error_message = None
        elif normalized_status == "failed":
            error_message = result.errorMessage or "Render worker reported failure"
            video = self.update_render_clip_video_status(job.demo, "failed", error_message[:1000])
            job.status = "failed"
            job.error_message = error_message[:1000]
        else:
            raise ValueError("status must be completed or failed")

        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = utc_now()
        self.db.commit()
        self.db.refresh(job)
        return video

    def replay_blob_path(self, demo_id: str) -> Path:
        return self.storage.path_for_key(self.replay_blob_key(demo_id))

    def replay_blob_key(self, demo_id: str) -> str:
        return self.storage.replay_key(demo_id)

    def write_replay_blob(self, demo_id: str, replay: dict[str, Any]) -> str:
        storage_key = self.replay_blob_key(demo_id)
        self.storage.write_json(storage_key, replay)
        return storage_key

    def load_replay_blob(self, demo: Demo) -> dict[str, object] | None:
        keys = [getattr(demo, "replay_storage_key", None), self.replay_blob_key(demo.id)]
        for storage_key in dict.fromkeys(key for key in keys if key):
            if self.storage.exists(storage_key):
                replay = self.storage.read_json(storage_key)
                return self._with_replay_contract_defaults(replay)
        return None

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
                "errorMessage": None,
                "timeOriginSeconds": 0,
            }
        video = replay["video"]
        if isinstance(video, dict):
            return video
        raise ValueError("Invalid replay video contract")

    def _video_status_for_list(self, demo: Demo) -> dict[str, Any]:
        try:
            return self.get_video_status(demo)
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "status": "unknown",
                "url": None,
                "source": "unknown",
            }

    def attach_manual_video(self, demo: Demo, stored_video: StoredVideoUpload) -> dict[str, Any]:
        current_video = self.get_video_status(demo)
        return self.update_replay_video(
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
        current_video = self.get_video_status(demo)
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
        return self.update_replay_video(demo, next_video)

    def update_replay_video(self, demo: Demo, video: dict[str, Any]) -> dict[str, Any]:
        replay = self.load_replay_blob(demo)
        if replay is None:
            raise ValueError("Replay blob is not ready")
        replay["video"] = self._with_video_contract_defaults(video, replay)
        self.write_replay_blob(demo.id, replay)
        return replay["video"]

    def update_render_clip_video_status(
        self,
        demo: Demo,
        status: str,
        error_message: str | None,
    ) -> dict[str, Any]:
        current_video = self.get_video_status(demo)
        if current_video.get("source") == "manual_upload":
            return current_video

        return self.update_replay_video(
            demo,
            {
                **current_video,
                "status": status,
                "source": "rendered",
                "url": None,
                "errorMessage": error_message,
            },
        )

    def complete_render_clip_video(
        self,
        demo: Demo,
        result: RenderWorkerResult,
    ) -> dict[str, Any]:
        if result.tickRate <= 0:
            raise ValueError("tickRate must be greater than zero")
        if result.tickEnd <= result.tickStart:
            raise ValueError("tickEnd must be greater than tickStart")
        if result.durationSeconds < 0:
            raise ValueError("durationSeconds must be zero or greater")
        if result.timeOriginSeconds < 0:
            raise ValueError("timeOriginSeconds must be zero or greater")

        video_reference = _render_output_reference(result, self.storage)
        if video_reference is None:
            raise ValueError("completed render output must include videoUrl or localMediaPath")

        return self.update_replay_video(
            demo,
            {
                "status": "ready",
                "url": video_reference["url"],
                "storageKey": video_reference["storageKey"],
                "durationSeconds": result.durationSeconds,
                "tickStart": result.tickStart,
                "tickEnd": result.tickEnd,
                "tickRate": result.tickRate,
                "source": "rendered",
                "errorMessage": None,
                "timeOriginSeconds": result.timeOriginSeconds,
            },
        )

    def list_coaching_events(self, demo_id: str) -> list[CoachingEventOut]:
        events = (
            self.db.query(CoachingEvent)
            .filter(CoachingEvent.demo_id == demo_id)
            .order_by(CoachingEvent.tick_start.asc())
            .all()
        )
        return [CoachingEventOut.model_validate(event) for event in events]

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

        return {
            **video,
            "status": video.get("status", "pending"),
            "url": video.get("url"),
            "durationSeconds": max(0, float(duration_seconds)),
            "tickStart": tick_start,
            "tickEnd": tick_end,
            "tickRate": tick_rate,
            "source": video.get("source", "mock"),
            "errorMessage": video.get("errorMessage"),
            "timeOriginSeconds": max(0, time_origin_seconds),
        }


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
        return value.replace(tzinfo=timezone.utc)
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
