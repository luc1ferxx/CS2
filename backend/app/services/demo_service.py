import io
import json
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

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
from app.services.artifact_binding import (
    AcceptedArtifactError,
    AcceptedArtifactSnapshot,
    VerifiedAcceptedArtifact,
    head_accepted_video,
    materialize_accepted_demo,
    open_accepted_video_range,
    parse_source_artifact_snapshot,
    read_accepted_replay_json,
    verify_accepted_artifact,
)
from app.services.artifact_intake import ArtifactIntakePolicy, ArtifactIntakeService
from app.services.storage import (
    ArtifactMetadata,
    ArtifactStore,
    ArtifactStoreError,
    LocalStorageService,
    StorageKeyError,
    artifact_store_from_settings,
)
from app.services.upload_service import StoredVideoUpload, demo_upload_key


RENDER_CLIP_JOB_TYPE = "render_clip"
RENDER_FAILED_ERROR_CODE = "RENDER_FAILED"
RENDER_FAILED_PUBLIC_MESSAGE = "Render output could not be produced."
RENDER_WORKER_UNAVAILABLE_ERROR_CODE = "RENDER_WORKER_UNAVAILABLE"
RENDER_CLIP_NOT_CONNECTED_ERROR = (
    "GPU worker not connected for render_clip. "
    "A separate Windows/Linux GPU worker or manual operator must process this job."
)
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
logger = logging.getLogger(__name__)


class DemoArtifactBindError(RuntimeError):
    pass


class DemoDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class _PendingReplayUpdate:
    demo_id: str
    video: dict[str, Any]
    previous_reference: str | None
    next_reference: str


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
        return offset

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise OSError("Private artifact read is closed")
        if self._opened is None:
            self.seek(0)
        return self._opened.read(size)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._opened is not None:
                self._opened.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

    def _owner_id(self) -> str:
        if self.owner_id is None:
            raise RuntimeError("Owner-scoped operations require an explicit owner context")
        return self.owner_id

    @property
    def storage(self) -> LocalStorageService:
        return self._storage or LocalStorageService.from_settings()

    @property
    def artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            self._artifact_store = artifact_store_from_settings()
        return self._artifact_store

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
        query = self.db.query(Demo).filter(Demo.owner_id == self._owner_id())

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
        video = self._public_video_status_for_list(demo)
        latest_render_job = self._latest_render_clip_job(demo)
        return DemoListItem.model_validate(demo).model_copy(
            update={
                "video_status": _optional_str(video.get("status")),
                "video_source": _optional_str(video.get("source")),
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
            .filter(Demo.id == demo_id, Demo.owner_id == self._owner_id())
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
            owner_id=self._owner_id(),
            legacy_user_id=self._owner_id(),
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

    def create_real_demo(self, upload: Any) -> DemoListItem:
        demo_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        stream = getattr(upload, "file", None)
        if stream is None or not hasattr(stream, "read"):
            raise DemoArtifactBindError("Demo intake could not be completed")
        try:
            stream.seek(0)
        except (AttributeError, OSError):
            raise DemoArtifactBindError("Demo intake could not be completed") from None

        accepted = ArtifactIntakeService(
            self.artifact_store,
            policy=ArtifactIntakePolicy(
                max_source_bytes=settings.max_demo_upload_bytes,
                min_source_bytes=16,
                stream_chunk_bytes=settings.upload_chunk_bytes,
                quarantine_ttl_seconds=settings.artifact_quarantine_ttl_seconds,
            ),
        ).intake_demo(
            owner_id=self._owner_id(),
            demo_id=demo_id,
            filename=getattr(upload, "filename", None) or "demo.dem",
            content_type=getattr(upload, "content_type", None),
            stream=stream,
            release_stream_before_promotion=True,
        )

        demo = Demo(
            id=demo_id,
            owner_id=self._owner_id(),
            legacy_user_id=self._owner_id(),
            name=f"Uploaded Demo {demo_id[:8]}",
            original_filename=accepted.display_filename,
            source_storage_key=accepted.reference,
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
            metadata_json=_metadata_json(
                {
                    "phase": "uploaded",
                    "sourceArtifact": accepted.as_snapshot(),
                }
            ),
        )

        self.db.add(demo)
        self.db.add(job)
        try:
            self.db.commit()
        except BaseException as exc:
            self.db.rollback()
            if not self._source_reference_is_bound(demo_id, accepted.reference):
                self._delete_artifact_safely(
                    accepted.reference,
                    expected_generation=accepted.generation,
                )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise DemoArtifactBindError("Demo intake could not be completed") from None
        self.db.refresh(demo)

        try:
            get_redis_client().lpush(
                settings.redis_queue_name,
                json.dumps({"job_id": job_id, "demo_id": demo_id}),
            )
        except Exception:
            logger.warning("Parser dispatch unavailable for job %s", job_id)
            raise DemoDispatchError("Parser dispatch could not be completed") from None

        return self.demo_list_item(demo)

    def _source_reference_is_bound(self, demo_id: str, reference: str) -> bool:
        try:
            found = (
                self.db.query(Demo.source_storage_key)
                .filter(Demo.id == demo_id)
                .scalar()
            )
        except Exception:
            # An uncertain database outcome must not delete a possibly bound object.
            return True
        return found == reference

    def _delete_artifact_safely(
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
        source_key_for_ingestion = self._retry_source_storage_key(demo, job)
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
            hasSourceDemo=bool(source_key_for_ingestion),
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
        latest_job = self.latest_parse_job(demo)
        if latest_job is None or latest_job.job_type != "real_parse":
            raise ValueError("Uploaded source demo is not available for retry")
        try:
            verified = self.verify_source_artifact(demo, latest_job)
        except AcceptedArtifactError:
            raise ValueError("Uploaded source demo artifact is missing or invalid") from None

        if latest_job is not None and latest_job.status in ACTIVE_PARSE_JOB_STATUSES:
            raise ValueError("Parse is already active")

        job_id = str(uuid.uuid4())
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type="real_parse",
            status="queued",
            attempts=0,
            metadata_json=_metadata_json(
                {
                    "phase": "uploaded",
                    "sourceArtifact": verified.snapshot.as_dict(),
                }
            ),
        )
        demo.status = "queued"
        demo.error_message = None
        demo.completed_at = None
        self.db.add(job)
        self.db.commit()
        self.db.refresh(demo)

        try:
            get_redis_client().lpush(
                settings.redis_queue_name,
                json.dumps({"job_id": job_id, "demo_id": demo.id}),
            )
        except Exception:
            logger.warning("Parser retry dispatch unavailable for job %s", job_id)
            raise DemoDispatchError("Parser dispatch could not be completed") from None
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
        previous_replay_reference = getattr(demo, "replay_storage_key", None)
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
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._delete_artifact_safely(replay_storage_key)
            raise
        if (
            previous_replay_reference
            and previous_replay_reference.startswith("artifact://")
            and previous_replay_reference != replay_storage_key
        ):
            self._delete_artifact_safely(previous_replay_reference)

    def fail_parse_job(
        self,
        demo: Demo,
        job: DemoJob,
        error: str,
        *,
        error_code: str = "PARSER_FAILED",
    ) -> None:
        self._ensure_parse_job(job)
        failed_at = utc_now()
        short_message = _compact_failure_message(error)
        failure_metadata = {
            "errorCode": error_code,
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
        if job is None or job.job_type != "real_parse":
            return False
        try:
            self.verify_source_artifact(demo, job)
            return True
        except AcceptedArtifactError:
            return False

    def _retry_source_storage_key(self, demo: Demo, job: DemoJob | None) -> str | None:
        stored_key = getattr(demo, "source_storage_key", None)
        if not stored_key or job is None or job.job_type != "real_parse":
            return None
        try:
            snapshot = self.source_artifact_snapshot(job)
            self.artifact_store.require_binding(
                stored_key,
                owner_id=demo.owner_id,
                demo_id=demo.id,
                kind="source",
                state="accepted",
            )
        except (AcceptedArtifactError, ArtifactStoreError, ValueError):
            return None
        return stored_key if snapshot.reference == stored_key else None

    def source_artifact_snapshot(self, job: DemoJob) -> AcceptedArtifactSnapshot:
        if job.job_type != "real_parse":
            raise AcceptedArtifactError("ARTIFACT_SNAPSHOT_INVALID")
        return parse_source_artifact_snapshot(_job_metadata(job).get("sourceArtifact"))

    def verify_source_artifact(
        self,
        demo: Demo,
        job: DemoJob,
    ) -> VerifiedAcceptedArtifact:
        if job.demo_id != demo.id or job.job_type != "real_parse":
            raise AcceptedArtifactError("ARTIFACT_BINDING_INVALID")
        reference = _optional_str(getattr(demo, "source_storage_key", None))
        if reference is None:
            raise AcceptedArtifactError("ARTIFACT_NOT_AVAILABLE")
        snapshot = self.source_artifact_snapshot(job)
        if snapshot.reference != reference:
            raise AcceptedArtifactError("ARTIFACT_INTEGRITY_FAILED")
        return verify_accepted_artifact(
            self.artifact_store,
            reference,
            owner_id=demo.owner_id,
            demo_id=demo.id,
            kind="source",
            snapshot=snapshot,
            max_bytes=settings.max_demo_upload_bytes,
        )

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

    @contextmanager
    def materialized_source_demo(
        self,
        demo: Demo,
        job: DemoJob,
    ) -> Iterator[Path]:
        snapshot = self.source_artifact_snapshot(job)
        reference = _optional_str(getattr(demo, "source_storage_key", None))
        if reference is None or snapshot.reference != reference:
            raise AcceptedArtifactError("ARTIFACT_BINDING_INVALID")
        with materialize_accepted_demo(
            self.artifact_store,
            reference,
            owner_id=demo.owner_id,
            demo_id=demo.id,
            snapshot=snapshot,
            max_bytes=settings.max_demo_upload_bytes,
        ) as path:
            yield path

    def source_demo_storage_key(self, demo: Demo) -> str:
        fallback_key = demo_upload_key(demo.id, demo.original_filename)
        stored_key = getattr(demo, "source_storage_key", None)
        if not stored_key:
            return fallback_key
        if stored_key.startswith("artifact://"):
            try:
                self.artifact_store.require_binding(
                    stored_key,
                    owner_id=demo.owner_id,
                    demo_id=demo.id,
                    kind="source",
                    state="accepted",
                )
            except ArtifactStoreError:
                raise ValueError("Accepted source artifact binding is invalid") from None
            return stored_key
        if not self.storage.upload_key_belongs_to_demo(demo.id, stored_key):
            return fallback_key
        return stored_key

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
        pending = self._prepare_replay_video_update(
            demo,
            {
                **self.get_video_status(demo),
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
            self._abort_replay_update(pending)
            raise
        self._finish_replay_update(pending)

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

        job_id = str(uuid.uuid4())
        _, pending = self._prepare_render_clip_video_status(demo, "queued", None)
        replay_storage_key = (
            getattr(demo, "replay_storage_key", None) or self.replay_blob_key(demo.id)
        )
        metadata = _compact_render_clip_metadata(
            request,
            duration_seconds=duration_seconds,
            max_duration_seconds=max_duration_seconds,
            demo_storage_key=self.source_demo_storage_key(demo),
            replay_storage_key=replay_storage_key,
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
            self._abort_replay_update(pending)
            raise
        if pending is not None:
            self._finish_replay_update(pending)
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
        pending = self._prepare_replay_video_update(
            job.demo,
            {
                **self.get_video_status(job.demo),
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
            self._abort_replay_update(pending)
            raise
        self._finish_replay_update(pending)
        self.db.refresh(job)
        return pending.video

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
        _, pending = self._prepare_render_clip_video_status(
            job.demo,
            "rendering",
            None,
        )
        metadata = _job_metadata(job)
        metadata["replayStorageKey"] = (
            getattr(job.demo, "replay_storage_key", None)
            or self.replay_blob_key(job.demo_id)
        )
        job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._abort_replay_update(pending)
            raise
        if pending is not None:
            self._finish_replay_update(pending)
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
        public_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in {"demoStorageKey", "replayStorageKey", "outputArtifact"}
        }
        video = self.public_video_status(job.demo) if job.demo is not None else {}
        error_code, error_message = _public_render_failure(
            job.status,
            None,
            job.error_message,
        )
        return RenderJobStatus(
            job_id=job.id,
            demo_id=job.demo_id,
            job_type=job.job_type,
            status=job.status,
            source=_optional_str(video.get("source")) or "unknown",
            video_status=_optional_str(video.get("status")),
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
            or self.source_demo_storage_key(demo)
        )
        demo_file_path = (
            ""
            if demo_storage_key.startswith("artifact://")
            else str(self.source_demo_path(demo))
        )
        return RenderJobManifest(
            manifestVersion=RENDER_WORKER_MANIFEST_VERSION,
            jobId=job.id,
            demoId=demo.id,
            jobType=job.job_type,
            status=job.status,
            demoFilePath=demo_file_path,
            demoStorageKey=demo_storage_key,
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

    def bind_render_worker_media(
        self,
        job: DemoJob,
        stored_video: StoredVideoUpload,
    ) -> AcceptedArtifactSnapshot:
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs accept render worker media")
        if job.status != "rendering":
            raise ValueError("Render worker media requires a rendering job")
        if not stored_video.storage_key.startswith("artifact://"):
            raise ValueError("Render worker media must use accepted artifact storage")

        previous = self._job_output_artifact(job)
        try:
            verified = head_accepted_video(
                self.artifact_store,
                stored_video.storage_key,
                owner_id=job.demo.owner_id,
                demo_id=job.demo_id,
                max_bytes=settings.max_video_upload_bytes,
            )
        except AcceptedArtifactError:
            self._delete_artifact_safely(
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
            self._delete_artifact_safely(
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
                self._delete_artifact_safely(
                    stored_video.storage_key,
                    expected_generation=snapshot.generation,
                )
            raise
        self.db.refresh(job)

        if previous is not None and previous.reference != snapshot.reference:
            self._delete_artifact_safely(
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

    def _job_output_artifact(self, job: DemoJob) -> AcceptedArtifactSnapshot | None:
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

    def apply_render_worker_result(
        self,
        job: DemoJob,
        result: RenderWorkerResult,
    ) -> dict[str, Any]:
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

        video = self._completed_render_clip_video(job, result)
        pending = self._prepare_replay_video_update(job.demo, video)
        video = pending.video
        job.status = "completed"
        job.error_message = None
        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = utc_now()
        metadata = _job_metadata(job)
        metadata["replayStorageKey"] = pending.next_reference
        job.metadata_json = _metadata_json(metadata)
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._abort_replay_update(pending)
            raise
        self._finish_replay_update(pending)
        self.db.refresh(job)
        return video

    def fail_render_clip_job(
        self,
        job: DemoJob,
        error_message: str,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        if job.job_type != RENDER_CLIP_JOB_TYPE:
            raise ValueError("Only render_clip jobs can enter render failure")
        if job.status in {"completed", "failed"}:
            raise ValueError(f"Render job is already {job.status}")

        output_artifact = self._job_output_artifact(job)
        video, pending = self._prepare_render_clip_video_status(
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
            self._abort_replay_update(pending)
            raise
        if pending is not None:
            self._finish_replay_update(pending)
        if output_artifact is not None:
            self._delete_artifact_safely(
                output_artifact.reference,
                expected_generation=output_artifact.generation,
            )
        self.db.refresh(job)
        return video

    def replay_blob_path(self, demo_id: str) -> Path:
        return self.storage.path_for_key(self.replay_blob_key(demo_id))

    def replay_blob_key(self, demo_id: str) -> str:
        return self.storage.replay_key(demo_id)

    def write_replay_blob(self, demo_id: str, replay: dict[str, Any]) -> str:
        demo = self.db.query(Demo).filter(Demo.id == demo_id).one_or_none()
        if demo is None:
            raise ValueError("Demo is not available for replay storage")
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
            self._delete_artifact_safely(reference)
            raise ValueError("Replay artifact could not be stored") from None
        return reference

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
        replay = self.load_replay_blob(demo)
        if replay is None:
            return None
        return _public_replay_contract(replay, self.public_video_status(demo))

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
        video = replay["video"]
        if isinstance(video, dict):
            return video
        raise ValueError("Invalid replay video contract")

    def public_video_status(self, demo: Demo) -> dict[str, Any]:
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
        error_code, error_message = _public_render_failure(
            _optional_str(internal_video.get("status")),
            _optional_str(internal_video.get("errorCode")),
            _optional_str(internal_video.get("errorMessage")),
        )
        video["errorCode"] = error_code
        video["errorMessage"] = error_message
        video["url"] = (
            f"/demos/{demo.id}/media/video"
            if self.private_video_available(demo)
            else None
        )
        return video

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
    ) -> tuple[BinaryIO, Any] | None:
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
        video = self.get_video_status(demo)
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

    def _public_video_status_for_list(self, demo: Demo) -> dict[str, Any]:
        try:
            return self.public_video_status(demo)
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "status": "unknown",
                "url": None,
                "source": "unknown",
            }

    def attach_manual_video(self, demo: Demo, stored_video: StoredVideoUpload) -> dict[str, Any]:
        current_video = self.get_video_status(demo)
        previous_reference = _optional_str(current_video.get("storageKey"))
        try:
            updated = self.update_replay_video(
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
                self._delete_artifact_safely(
                    stored_video.storage_key,
                    expected_generation=stored_video.generation,
                )
            raise
        if (
            previous_reference
            and previous_reference.startswith("artifact://")
            and previous_reference != stored_video.storage_key
        ):
            self._delete_artifact_safely(previous_reference)
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
        pending = self._prepare_replay_video_update(demo, video)
        self._commit_replay_update(pending)
        return pending.video

    def _prepare_replay_video_update(
        self,
        demo: Demo,
        video: dict[str, Any],
    ) -> _PendingReplayUpdate:
        replay = self.load_replay_blob(demo)
        if replay is None:
            raise ValueError("Replay blob is not ready")
        replay["video"] = self._with_video_contract_defaults(video, replay)
        previous_reference = getattr(demo, "replay_storage_key", None)
        next_reference = self.write_replay_blob(demo.id, replay)
        demo.replay_storage_key = next_reference
        return _PendingReplayUpdate(
            demo_id=demo.id,
            video=replay["video"],
            previous_reference=previous_reference,
            next_reference=next_reference,
        )

    def _commit_replay_update(self, pending: _PendingReplayUpdate) -> None:
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._delete_artifact_safely(pending.next_reference)
            raise
        self._finish_replay_update(pending)

    def _finish_replay_update(self, pending: _PendingReplayUpdate) -> None:
        if (
            pending.previous_reference
            and pending.previous_reference.startswith("artifact://")
            and pending.previous_reference != pending.next_reference
        ):
            self._delete_artifact_safely(pending.previous_reference)

    def _abort_replay_update(self, pending: _PendingReplayUpdate | None) -> None:
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
            self._delete_artifact_safely(pending.next_reference)

    def _prepare_render_clip_video_status(
        self,
        demo: Demo,
        status: str,
        error_message: str | None,
        *,
        error_code: str | None = None,
    ) -> tuple[dict[str, Any], _PendingReplayUpdate | None]:
        current_video = self.get_video_status(demo)
        if current_video.get("source") == "manual_upload":
            return current_video, None

        public_error_code, public_error_message = _public_render_failure(
            status,
            error_code,
            error_message,
        )
        pending = self._prepare_replay_video_update(
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
        video, pending = self._prepare_render_clip_video_status(
            demo,
            status,
            error_message,
            error_code=error_code,
        )
        if pending is not None:
            self._commit_replay_update(pending)
        return video

    def _completed_render_clip_video(
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
            snapshot = self._job_output_artifact(job)
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
            "errorCode": video.get("errorCode"),
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


def _public_render_failure(
    status: str | None,
    error_code: str | None,
    error_message: str | None,
) -> tuple[str | None, str | None]:
    if status != "failed":
        return None, None
    if (
        error_code == RENDER_WORKER_UNAVAILABLE_ERROR_CODE
        or (error_message or "").startswith("GPU worker not connected for render_clip")
    ):
        return RENDER_WORKER_UNAVAILABLE_ERROR_CODE, RENDER_CLIP_NOT_CONNECTED_ERROR
    return RENDER_FAILED_ERROR_CODE, RENDER_FAILED_PUBLIC_MESSAGE


def _public_replay_contract(
    replay: dict[str, Any],
    video: dict[str, Any],
) -> dict[str, object]:
    public: dict[str, object] = {
        "demoId": replay["demoId"],
        "mapName": replay["mapName"],
        "tickRate": replay["tickRate"],
        "video": video,
        "rounds": [
            _project_fields(
                item,
                (
                    "roundNumber",
                    "startTick",
                    "freezeEndTick",
                    "endTick",
                    "winnerSide",
                    "winnerReason",
                ),
            )
            for item in replay.get("rounds", [])
            if isinstance(item, dict)
        ],
        "players": [
            _project_fields(item, ("id", "name", "side", "color"))
            for item in replay.get("players", [])
            if isinstance(item, dict)
        ],
        "frames": [
            _public_replay_frame(item)
            for item in replay.get("frames", [])
            if isinstance(item, dict)
        ],
        "kills": [
            _public_kill(item)
            for item in replay.get("kills", [])
            if isinstance(item, dict)
        ],
        "deaths": [
            _public_kill(item)
            for item in replay.get("deaths", [])
            if isinstance(item, dict)
        ],
        "events": [
            _public_replay_event(item)
            for item in replay.get("events", [])
            if isinstance(item, dict)
        ],
        "generatedAt": replay["generatedAt"],
        "contractVersion": replay["contractVersion"],
        "diagnostics": _public_replay_diagnostics(replay.get("diagnostics")),
    }
    map_metadata = replay.get("mapMetadata")
    if isinstance(map_metadata, dict):
        public["mapMetadata"] = _public_map_metadata(map_metadata)
    return public


def _public_map_metadata(value: dict[str, Any]) -> dict[str, Any]:
    projected = _project_fields(
        value,
        (
            "mapName",
            "displayName",
            "radarImagePath",
            "secondaryRadarImagePath",
            "calibrated",
            "confidence",
            "attribution",
            "source",
        ),
    )
    transform = value.get("transform")
    if isinstance(transform, dict):
        projected["transform"] = _project_fields(
            transform,
            (
                "type",
                "posX",
                "posY",
                "scale",
                "imageSize",
                "minX",
                "maxX",
                "minY",
                "maxY",
            ),
        )
    return projected


def _public_replay_frame(value: dict[str, Any]) -> dict[str, Any]:
    projected = _project_fields(value, ("tick", "timeSeconds", "roundNumber"))
    players = value.get("players")
    projected["players"] = [
        _project_fields(
            player,
            ("id", "name", "side", "x", "y", "alive", "hp", "hasBomb"),
        )
        for player in players
        if isinstance(player, dict)
    ] if isinstance(players, list) else []
    bomb_state = value.get("bombState")
    projected["bombState"] = (
        _project_fields(bomb_state, ("status", "carrierPlayerId", "x", "y", "site"))
        if isinstance(bomb_state, dict)
        else {"status": "carried"}
    )
    return projected


def _public_kill(value: dict[str, Any]) -> dict[str, Any]:
    return _project_fields(
        value,
        (
            "tick",
            "roundNumber",
            "attackerId",
            "attackerName",
            "attackerSide",
            "victimId",
            "victimName",
            "victimSide",
            "assisterId",
            "assisterName",
            "weapon",
            "headshot",
        ),
    )


def _public_replay_event(value: dict[str, Any]) -> dict[str, Any]:
    projected = _project_fields(
        value,
        (
            "id",
            "type",
            "tick",
            "roundNumber",
            "source",
            "playerIds",
            "playerId",
            "playerName",
            "side",
            "x",
            "y",
            "label",
        ),
    )
    metadata = value.get("metadata")
    projected["metadata"] = (
        _project_fields(
            metadata,
            (
                "attackerId",
                "attackerName",
                "attackerSide",
                "victimId",
                "victimName",
                "victimSide",
                "assisterId",
                "assisterName",
                "weapon",
                "headshot",
                "damageHealth",
                "damageArmor",
                "health",
                "armor",
                "site",
                "reason",
                "round_end_reason",
                "winner_reason",
                "winnerReason",
                "winnerSide",
            ),
        )
        if isinstance(metadata, dict)
        else {}
    )
    return projected


def _public_replay_diagnostics(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected = _project_fields(
        value,
        (
            "contractVersion",
            "normalizedLegacy",
            "parserEventCount",
            "roundCount",
            "playerCount",
            "frameCount",
            "missingFields",
            "degradedFields",
            "missingEventFamilies",
        ),
    )
    family_counts = value.get("eventFamilyCounts")
    projected["eventFamilyCounts"] = (
        {
            key: count
            for key, count in family_counts.items()
            if key in {"combat", "damage", "objective", "utility"}
        }
        if isinstance(family_counts, dict)
        else {}
    )
    return projected


def _project_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: value[field] for field in fields if field in value}
