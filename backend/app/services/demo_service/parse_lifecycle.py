"""The parse job state machine: ingestion status, claim/analyzing/complete/fail, retry, and the stale-job reclaim/requeue/abandon recovery, including the linked SteamMatch parser state."""

import logging
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, or_, update

from app.core.config import settings
from app.models.coaching import CoachingEvent
from app.models.demo import Demo
from app.models.job import DemoJob
from app.schemas.demo import DemoIngestionStatus, DemoListItem, ParseFailureMetadata
from app.services.artifact_binding import AcceptedArtifactError
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import (
    _aware_datetime,
    _compact_failure_message,
    _ingestion_phase,
    _job_metadata,
    _metadata_json,
    _optional_str,
    _parse_datetime,
    utc_now,
)
from app.services.demo_service.constants import (
    ACTIVE_DEMO_STATUSES,
    ACTIVE_PARSE_JOB_STATUSES,
    PARSE_ABANDONED_ERROR_CODE,
    PARSE_ABANDONED_MESSAGE,
    PARSE_JOB_TYPES,
    REPLAY_ARTIFACT_MISSING_ERROR_CODE,
    REPLAY_ARTIFACT_MISSING_MESSAGE,
    STALE_PARSE_AFTER_SECONDS,
)
from app.services.demo_service.errors import DemoDispatchError, DemoGoneError
from app.services.demo_service.gone import row_identity, rows_missing
from app.services.demo_service.match_summary import build_match_summary
from app.services.demo_service.steam_match import SteamMatchParseState
from app.services.storage import ArtifactStoreError

logger = logging.getLogger(__name__)


class ParseLifecycle(ServiceComponent):
    """Reached as ``DemoService.parse``."""

    @property
    def _steam_matches(self) -> SteamMatchParseState:
        return SteamMatchParseState(self.db)

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
        replay_missing = self._service.replay.replay_artifact_is_missing(demo)
        retryable = self._parse_retryable(demo, job, replay_missing=replay_missing)
        source_key_for_ingestion = self._retry_source_storage_key(demo, job)
        attempt_count = int(job.attempts) if job is not None else 0
        failure: ParseFailureMetadata | None
        if replay_missing:
            # Not a parse failure -- the parse succeeded and the artifact was
            # lost afterwards -- so this carries its own code instead of the
            # stored parser failure, which is absent or stale in this state.
            failure = ParseFailureMetadata(
                errorCode=REPLAY_ARTIFACT_MISSING_ERROR_CODE,
                message=REPLAY_ARTIFACT_MISSING_MESSAGE,
                failedAt=None,
                updatedAt=updated_at,
                retryable=retryable,
                attemptCount=attempt_count,
            )
        elif demo.status == "failed" or (job is not None and job.status == "failed"):
            failure = self._parse_failure_metadata(
                demo, job, retryable=retryable, attempt_count=attempt_count
            )
        else:
            failure = None
        stale_since = _aware_datetime(
            (job.started_at if job is not None else None)
            or updated_at
            or (job.created_at if job is not None else None)
        )
        stale = active and (utc_now() - stale_since).total_seconds() > STALE_PARSE_AFTER_SECONDS

        has_source = bool(source_key_for_ingestion)
        return DemoIngestionStatus(
            phase=_ingestion_phase(demo.status),
            active=active,
            stale=stale,
            retryable=retryable,
            attemptCount=attempt_count,
            jobId=job.id if job is not None else None,
            jobType=job.job_type if job is not None else None,
            jobStatus=job.status if job is not None else None,
            hasSourceDemo=has_source,
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
            replayUpgradePending=(
                not replay_missing
                and self._service.upgrade.replay_upgrade_pending(demo, job, has_source=has_source)
            ),
        )

    def retry_parse_job(
        self,
        demo: Demo,
        *,
        admission: Callable[[], AbstractContextManager[object]] = nullcontext,
    ) -> DemoListItem:
        """Queue a fresh parse of a failed demo's verified source.

        `admission` wraps only the new job's commit, so a caller can run its
        capacity check under the same lock without holding it across the
        source-artifact verification or the queue dispatch.
        """
        if demo.status != "failed" and not self._service.replay.replay_artifact_is_missing(demo):
            raise ValueError("Only failed parse jobs can be retried")
        latest_job = self.latest_parse_job(demo)
        if latest_job is None or latest_job.job_type != "real_parse":
            raise ValueError("Uploaded source demo is not available for retry")
        try:
            verified = self._service.ingest.verify_source_artifact(demo, latest_job)
        except AcceptedArtifactError:
            raise ValueError("Uploaded source demo artifact is missing or invalid") from None

        if latest_job is not None and latest_job.status in ACTIVE_PARSE_JOB_STATUSES:
            raise ValueError("Parse is already active")

        # Only the commit that makes the demo active runs inside the admission:
        # source verification above and dispatch below can block on the network.
        demo_id = demo.id
        with admission():
            # Lock the demo first: a deletion that committed since get_demo()
            # must answer 404 here rather than fail the job insert on its FK,
            # and one still in flight waits for this commit and then removes
            # the job this retry queues along with everything else.
            locked = (
                self.db.query(Demo)
                .filter(Demo.id == demo_id, Demo.owner_id == demo.owner_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if locked is None:
                raise DemoGoneError("Demo was deleted")
            demo = locked
            # A concurrent retry of this demo may have queued its job since the
            # checks above; the admission serializes this re-read with its commit.
            if self._active_parse_job_exists(demo):
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
            linked_match_id = self._steam_matches.linked_match_id(demo, latest_job)
            if linked_match_id is not None:
                self._steam_matches.reset_for_retry(linked_match_id, demo)
            self.db.add(job)
            self.db.commit()
        self.db.refresh(demo)

        try:
            self._service.ingest.dispatch_parse_job(
                job_id=job_id,
                demo_id=demo.id,
            )
        except DemoDispatchError:
            marked_unavailable = 0
            if linked_match_id is not None:
                marked_unavailable = self._steam_matches.mark_dispatch_unavailable(
                    linked_match_id, demo, job_id, utc_now(),
                )
                self.db.commit()
            if linked_match_id is not None and marked_unavailable != 1:
                self.db.expire_all()
                current_job = (
                    self.db.query(DemoJob)
                    .filter(
                        DemoJob.id == job_id,
                        DemoJob.demo_id == demo.id,
                    )
                    .one_or_none()
                )
                if current_job is not None and current_job.status != "queued":
                    current_demo = (
                        self.db.query(Demo)
                        .filter(Demo.id == demo.id, Demo.owner_id == demo.owner_id)
                        .one_or_none()
                    )
                    if current_demo is not None:
                        return self._service.library.demo_list_item(current_demo)
            raise
        if linked_match_id is not None:
            self._steam_matches.mark_dispatched(linked_match_id, demo, job_id, utc_now())
            self.db.commit()
        return self._service.library.demo_list_item(demo)

    def claim_parse_job(self, demo: Demo, job: DemoJob) -> bool:
        self._ensure_parse_job(job)
        started_at = utc_now()
        metadata_json = _metadata_json({**_job_metadata(job), "phase": "parsing"})
        claimed = (
            self.db.query(DemoJob)
            .filter(
                DemoJob.id == job.id,
                DemoJob.demo_id == demo.id,
                DemoJob.job_type.in_(PARSE_JOB_TYPES),
                DemoJob.status == "queued",
            )
            .update(
                {
                    DemoJob.status: "processing",
                    DemoJob.attempts: DemoJob.attempts + 1,
                    DemoJob.started_at: started_at,
                    DemoJob.finished_at: None,
                    DemoJob.error_message: None,
                    DemoJob.metadata_json: metadata_json,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            self.db.rollback()
            return False
        demo_id, job_id = row_identity(demo), row_identity(job)
        try:
            self.db.refresh(job)
            demo.status = "parsing"
            demo.error_message = None
            if job.job_type == "real_parse":
                self._steam_matches.mark_parsing(demo, job, started_at)
            self.db.commit()
        except Exception:
            if rows_missing(self.db, demo_id=demo_id, job_id=job_id):
                _log_deleted_during_parse(demo_id, job_id)
                return False
            raise
        return True

    def mark_parse_analyzing(self, demo: Demo, job: DemoJob) -> bool:
        """Move a claimed parse to "analyzing"; False when the demo was deleted meanwhile."""
        demo_id, job_id = row_identity(demo), row_identity(job)
        try:
            self._ensure_parse_job(job)
            demo.status = "analyzing"
            job.metadata_json = _metadata_json({**_job_metadata(job), "phase": "analyzing"})
            self.db.commit()
        except Exception:
            if rows_missing(self.db, demo_id=demo_id, job_id=job_id):
                _log_deleted_during_parse(demo_id, job_id)
                return False
            raise
        return True

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
        """Store the parse result; False when the demo was deleted meanwhile.

        `team_names` (player id -> clan name, from the parser) only feeds the
        match summary stored next to the replay.

        A deletion anywhere up to the commit must not resurrect anything: the
        rows roll back and the replay blob staged for them is removed. Should
        that removal fail, the deletion's final storage sweep still finds it.
        """
        demo_id, job_id = row_identity(demo), row_identity(job)
        replay_storage_key: str | None = None
        try:
            self._ensure_parse_job(job)
            previous_replay_reference = getattr(demo, "replay_storage_key", None)
            replay_storage_key = self._service.replay.write_replay_blob(demo.id, replay)

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
            demo.match_summary = _match_summary_or_none(replay, team_names)
            demo.completed_at = utc_now()
            demo.error_message = None

            job.status = "completed"
            completed_at = utc_now()
            job.finished_at = completed_at
            job.error_message = None
            metadata = {**_job_metadata(job), "phase": "ready"}
            # The background replay upgrade reads this marker instead of the
            # (large) replay itself; see replay_upgrade.py.
            metadata.pop("replayUpgrade", None)
            metadata["replayContractVersion"] = replay.get("contractVersion")
            job.metadata_json = _metadata_json(metadata)
            if job.job_type == "real_parse":
                self._steam_matches.mark_ready(demo, replay, completed_at)
            self.db.commit()
        except BaseException as exc:
            self.db.rollback()
            if replay_storage_key is not None:
                self._service.delete_artifact_safely(replay_storage_key)
            if isinstance(exc, Exception) and (
                isinstance(exc, DemoGoneError)
                or rows_missing(self.db, demo_id=demo_id, job_id=job_id)
            ):
                _log_deleted_during_parse(demo_id, job_id)
                return False
            raise
        if (
            previous_replay_reference
            and previous_replay_reference.startswith("artifact://")
            and previous_replay_reference != replay_storage_key
        ):
            self._service.delete_artifact_safely(previous_replay_reference)
        return True

    def fail_parse_job(
        self,
        demo: Demo,
        job: DemoJob,
        error: str,
        *,
        error_code: str = "PARSER_FAILED",
    ) -> bool:
        """Record a parse failure; False when the demo was deleted meanwhile."""
        demo_id, job_id = row_identity(demo), row_identity(job)
        try:
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
            if job.job_type == "real_parse":
                self._steam_matches.mark_parse_failed(demo, failed_at)
            self.db.commit()
        except Exception:
            if rows_missing(self.db, demo_id=demo_id, job_id=job_id):
                _log_deleted_during_parse(demo_id, job_id)
                return False
            raise
        return True

    def _active_parse_job_exists(self, demo: Demo) -> bool:
        return (
            self.db.query(DemoJob.id)
            .filter(
                DemoJob.demo_id == demo.id,
                DemoJob.job_type.in_(PARSE_JOB_TYPES),
                DemoJob.status.in_(ACTIVE_PARSE_JOB_STATUSES),
            )
            .first()
            is not None
        )

    def _ensure_parse_job(self, job: DemoJob) -> None:
        if job.job_type not in PARSE_JOB_TYPES:
            raise ValueError("Only parse jobs support parser status transitions")

    def _parse_retryable(
        self,
        demo: Demo,
        job: DemoJob | None,
        *,
        replay_missing: bool | None = None,
    ) -> bool:
        # A completed demo whose replay artifact is gone is as unusable as a
        # failed one: the replay endpoint 404s and every clip request 409s. It
        # gets the same one-click recovery rather than a dead end that only a
        # hand-written UPDATE could unstick.
        if replay_missing is None:
            replay_missing = self._service.replay.replay_artifact_is_missing(demo)
        if demo.status != "failed" and not replay_missing:
            return False
        if job is not None and job.status in ACTIVE_PARSE_JOB_STATUSES:
            return False
        if job is None or job.job_type != "real_parse":
            return False
        try:
            # Re-parsing reads the source demo, so a retry is only honest to
            # offer while that artifact still verifies.
            self._service.ingest.verify_source_artifact(demo, job)
            return True
        except AcceptedArtifactError:
            return False

    def _retry_source_storage_key(self, demo: Demo, job: DemoJob | None) -> str | None:
        stored_key = getattr(demo, "source_storage_key", None)
        if not stored_key or job is None or job.job_type != "real_parse":
            return None
        try:
            snapshot = self._service.ingest.source_artifact_snapshot(job)
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

    def _parse_failure_metadata(
        self,
        demo: Demo,
        job: DemoJob | None,
        *,
        retryable: bool,
        attempt_count: int,
    ) -> ParseFailureMetadata:
        metadata = _job_metadata(job) if job is not None else {}
        failure = metadata.get("failure")
        stored_failure: dict[str, Any] = failure if isinstance(failure, dict) else {}
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

    def _stale_parse_jobs(self, status: str, cutoff: datetime, limit: int) -> list[DemoJob]:
        timestamp = DemoJob.started_at if status == "processing" else DemoJob.created_at
        return (
            self.db.query(DemoJob)
            # Same reason as the render_clip sweep: requeueing has to roll the
            # demo's status back with the job, which needs a demo to roll back.
            .join(Demo, DemoJob.demo_id == Demo.id)
            .filter(
                DemoJob.job_type.in_(PARSE_JOB_TYPES),
                DemoJob.status == status,
                # claim_parse_job always stamps started_at, so "processing"
                # without one is already broken data -- treat it as stale rather
                # than leave the row wedged.
                or_(timestamp <= cutoff, timestamp.is_(None)),
            )
            .order_by(timestamp.asc())
            .limit(limit)
            .all()
        )

    def _requeue_parse_job(self, job: DemoJob, *, from_status: str = "processing") -> bool:
        """Put an abandoned parse job back on "queued", demo status included.

        claim_parse_job moved the demo to "parsing" when it claimed the job; a
        requeue that only touched the job row would leave the demo page claiming
        a parse is running while the queue says it is waiting.
        """
        demo = job.demo if job.demo is not None else (
            self.db.query(Demo).filter(Demo.id == job.demo_id).one_or_none()
        )
        requeued = self.db.execute(
            update(DemoJob)
            .where(
                DemoJob.id == job.id,
                DemoJob.job_type.in_(PARSE_JOB_TYPES),
                DemoJob.status == from_status,
            )
            .values(
                status="queued",
                # Leaving the old timestamp would put the job permanently past
                # any cutoff -- reclaimed again the moment it is reclaimed.
                started_at=None,
                # Keeps queued_at meaning "when this row last entered queued"
                # for every job type, not just render_clip.
                queued_at=utc_now(),
                finished_at=None,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if requeued.rowcount != 1:
            # Someone finished or reclaimed it between the query and here. Their
            # result wins.
            self.db.rollback()
            return False
        if demo is not None and demo.status in ACTIVE_DEMO_STATUSES:
            demo.status = "queued"
            demo.error_message = None
        self.db.commit()
        self.db.refresh(job)
        logger.info(
            "Requeued parse job %s for demo %s from %s (attempt %s of %s)",
            job.id,
            job.demo_id,
            from_status,
            job.attempts,
            settings.parse_max_attempts,
        )
        return True

    def abandon_parse_job(self, job: DemoJob) -> bool:
        """Fail a parse job that has burned its attempt budget."""
        demo = job.demo if job.demo is not None else (
            self.db.query(Demo).filter(Demo.id == job.demo_id).one_or_none()
        )
        if demo is None:
            return False
        return self.fail_parse_job(
            demo,
            job,
            PARSE_ABANDONED_MESSAGE,
            error_code=PARSE_ABANDONED_ERROR_CODE,
        )

    def recover_parse_job(self, job_id: str, demo_id: str) -> bool:
        """Decide what to do with one message recovered from a dead consumer.

        Returns True when the message should go back on the queue, False when the
        job is finished with -- either because it has been failed for good or
        because it is no longer in a state a redelivery could help.
        """
        job = (
            self.db.query(DemoJob)
            .filter(
                DemoJob.id == job_id,
                DemoJob.demo_id == demo_id,
                DemoJob.job_type.in_(PARSE_JOB_TYPES),
            )
            .one_or_none()
        )
        if job is None:
            return False
        if job.status == "queued":
            # The row never got claimed, so the message alone was lost. Nothing
            # to roll back -- just let it be delivered again.
            return True
        if job.status != "processing":
            return False
        if job.attempts >= settings.parse_max_attempts:
            self.abandon_parse_job(job)
            return False
        return self._requeue_parse_job(job)

    def reclaim_stale_parse_jobs(
        self,
        *,
        older_than_seconds: int,
        redispatch_after_seconds: int,
        now: datetime | None = None,
        limit: int = 20,
        redis_client: Any | None = None,
    ) -> list[str]:
        """Recover parse jobs the queue can no longer account for.

        This is the layer that survives what Redis cannot. The processing lists
        and leases in app.workers.queue recover a dead worker's messages within
        seconds, but Redis runs without persistence here: one restart drops every
        lease and every in-flight message, and the rows they described would sit
        on "processing" forever. Wall-clock age against the database is the only
        signal left, so that is what this uses.

        Two shapes are covered. A job on "processing" past older_than_seconds was
        claimed by a worker that never came back -- it goes back to "queued" and
        is redispatched, or is failed once it has burned settings.parse_max_attempts.
        A job on "queued" past redispatch_after_seconds is one whose message is
        simply gone; it is redispatched as-is. Redispatching without first
        checking whether a message still exists is deliberate: claim_parse_job is
        a compare-and-set, so a duplicate delivery finds the row already claimed
        and returns False. A harmless extra message beats a LRANGE scan of the
        whole queue.
        """
        moment = _aware_datetime(now)
        client = redis_client
        reclaimed: list[str] = []

        processing_cutoff = moment - timedelta(seconds=older_than_seconds)
        for job in self._stale_parse_jobs("processing", processing_cutoff, limit):
            job_id = row_identity(job)
            try:
                # An earlier iteration's commit expired this row; reloading it
                # raises if its match was deleted since the query above.
                attempts = job.attempts
            except Exception:
                if rows_missing(self.db, job_id=job_id):
                    continue
                raise
            if attempts >= settings.parse_max_attempts:
                if self.abandon_parse_job(job):
                    reclaimed.append(str(job_id))
                continue
            if self._requeue_parse_job(job):
                reclaimed.append(str(job_id))
                client = self._redispatch_parse_job(job, client)

        queued_cutoff = moment - timedelta(seconds=redispatch_after_seconds)
        for job in self._stale_parse_jobs("queued", queued_cutoff, limit):
            job_id = row_identity(job)
            if str(job_id) in reclaimed:
                continue
            reclaimed.append(str(job_id))
            client = self._redispatch_parse_job(job, client)
        return reclaimed

    def _redispatch_parse_job(self, job: DemoJob, redis_client: Any | None) -> Any | None:
        client = redis_client if redis_client is not None else self._service.queue_client()
        try:
            self._service.ingest.dispatch_parse_job(
                job_id=str(job.id),
                demo_id=str(job.demo_id),
                redis_client=client,
            )
        except DemoDispatchError:
            # Already logged with the reason scrubbed. The row stays on "queued",
            # so the next pass tries again rather than the worker dying here.
            logger.warning("Could not redispatch parse job %s", job.id)
        return client


def _match_summary_or_none(
    replay: dict[str, Any],
    team_names: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    # The summary is a library nicety; it must never cost the parse itself.
    try:
        return build_match_summary(replay, team_names)
    except Exception:
        logger.warning("match summary could not be computed", exc_info=True)
        return None


def _log_deleted_during_parse(demo_id: str | None, job_id: str | None) -> None:
    logger.info("demo deleted during parse: demo %s, job %s", demo_id, job_id)
