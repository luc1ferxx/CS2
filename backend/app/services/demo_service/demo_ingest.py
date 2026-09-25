"""Demo creation and the accepted source artifact: mock/real intake, prepare/commit/discard, parse dispatch, and every read of the stored .dem source."""

import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from app.core.config import settings
from app.models.demo import Demo
from app.models.job import DemoJob
from app.schemas.demo import DemoListItem
from app.services.artifact_binding import (
    AcceptedArtifactError,
    AcceptedArtifactSnapshot,
    VerifiedAcceptedArtifact,
    materialize_accepted_demo,
    parse_source_artifact_snapshot,
    verify_accepted_artifact,
)
from app.services.artifact_intake import AcceptedArtifact, ArtifactIntakePolicy, ArtifactIntakeService
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import _job_metadata, _metadata_json, _optional_str
from app.services.demo_service.errors import DemoArtifactBindError, DemoDispatchError
from app.services.storage import ArtifactStoreError
from app.services.upload_service import demo_upload_key

logger = logging.getLogger(__name__)


class QueueClient(Protocol):
    """The one Redis operation parse dispatch performs."""

    def lpush(self, name: str, *values: Any) -> Any: ...


@dataclass(frozen=True)
class PreparedRealDemo:
    demo: Demo
    job: DemoJob
    accepted: AcceptedArtifact


class DemoIngest(ServiceComponent):
    """Reached as ``DemoService.ingest``."""

    def create_mock_demo(self) -> DemoListItem:
        demo_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        demo = Demo(
            id=demo_id,
            owner_id=self._service.require_owner_id(),
            legacy_user_id=self._service.require_owner_id(),
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

        self._service.queue_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo_id}),
        )

        return self._service.library.demo_list_item(demo)

    def create_real_demo(self, upload: Any) -> DemoListItem:
        stream = getattr(upload, "file", None)
        prepared = self.prepare_real_demo(
            stream=stream,
            filename=getattr(upload, "filename", None) or "demo.dem",
            content_type=getattr(upload, "content_type", None),
        )
        self.commit_prepared_real_demo(prepared)
        self.dispatch_prepared_real_demo(prepared)
        return self._service.library.demo_list_item(prepared.demo)

    def prepare_real_demo(
        self,
        *,
        stream: BinaryIO | None,
        filename: str,
        content_type: str | None,
        demo_id: str | None = None,
        job_id: str | None = None,
    ) -> PreparedRealDemo:
        if stream is None or not hasattr(stream, "read"):
            raise DemoArtifactBindError("Demo intake could not be completed")
        try:
            stream.seek(0)
        except (AttributeError, OSError):
            raise DemoArtifactBindError("Demo intake could not be completed") from None

        resolved_demo_id = demo_id or str(uuid.uuid4())
        resolved_job_id = job_id or str(uuid.uuid4())

        accepted = ArtifactIntakeService(
            self.artifact_store,
            policy=ArtifactIntakePolicy(
                max_source_bytes=settings.max_demo_upload_bytes,
                min_source_bytes=16,
                stream_chunk_bytes=settings.upload_chunk_bytes,
                quarantine_ttl_seconds=settings.artifact_quarantine_ttl_seconds,
            ),
        ).intake_demo(
            owner_id=self._service.require_owner_id(),
            demo_id=resolved_demo_id,
            filename=filename,
            content_type=content_type,
            stream=stream,
            release_stream_before_promotion=True,
        )

        demo = Demo(
            id=resolved_demo_id,
            owner_id=self._service.require_owner_id(),
            legacy_user_id=self._service.require_owner_id(),
            name=accepted.display_filename,
            original_filename=accepted.display_filename,
            source_storage_key=accepted.reference,
            map_name="unknown",
            tick_rate=64,
            round_count=0,
            coaching_event_count=0,
            status="queued",
        )
        job = DemoJob(
            id=resolved_job_id,
            demo_id=resolved_demo_id,
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
        return PreparedRealDemo(demo=demo, job=job, accepted=accepted)

    def commit_prepared_real_demo(self, prepared: PreparedRealDemo) -> None:
        try:
            self.commit_prepared_real_demo_rows()
        except BaseException:
            self.discard_prepared_real_demo(prepared)
            raise
        self.reload_prepared_real_demo(prepared)

    def commit_prepared_real_demo_rows(self) -> None:
        """Commit the prepared demo and job, and nothing else.

        For a caller inside `parse_admission`: after this commit releases the
        session's connection, a reload or cleanup query would need a fresh
        checkout while holding the lock. That caller discards the prepared
        demo on failure and reloads it once the admission has ended.
        """
        try:
            self.db.commit()
        except BaseException as exc:
            self.db.rollback()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise DemoArtifactBindError("Demo intake could not be completed") from None

    def reload_prepared_real_demo(self, prepared: PreparedRealDemo) -> None:
        self.db.refresh(prepared.demo)

    def discard_prepared_real_demo(self, prepared: PreparedRealDemo) -> None:
        self.db.rollback()
        if not self._source_reference_is_bound(
            prepared.demo.id,
            prepared.accepted.reference,
        ):
            self._service.delete_artifact_safely(
                prepared.accepted.reference,
                expected_generation=prepared.accepted.generation,
            )

    def dispatch_prepared_real_demo(
        self,
        prepared: PreparedRealDemo,
        *,
        redis_client: QueueClient | None = None,
    ) -> None:
        self.dispatch_parse_job(
            job_id=prepared.job.id,
            demo_id=prepared.demo.id,
            redis_client=redis_client,
        )

    def dispatch_parse_job(
        self,
        *,
        job_id: str,
        demo_id: str,
        redis_client: QueueClient | None = None,
    ) -> None:
        try:
            client = redis_client if redis_client is not None else self._service.queue_client()
            client.lpush(
                settings.redis_queue_name,
                json.dumps(
                    {
                        "job_id": job_id,
                        "demo_id": demo_id,
                    }
                ),
            )
        except Exception:
            logger.warning("Parser dispatch unavailable for job %s", job_id)
            raise DemoDispatchError("Parser dispatch could not be completed") from None

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
