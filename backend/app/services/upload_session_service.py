"""Chunked, resumable .dem upload sessions (docs/api_reference_v1.md, "Upload sessions").

A session is created with the file's name and size, receives its parts as
separate PUTs (in any order, in parallel, repeatable) into the local staging
directory (`app.services.storage.UploadStagingStore`), and `complete` hands
the concatenated parts to the unchanged intake (`DemoService.prepare_real_demo`)
and commits through the same admission as the legacy upload
(`app.services.upload_admission.admit_and_commit`).

State machine of `upload_sessions` (`UploadSession`):

    open        accepts parts; `complete` starts; DELETE removes it; expiry sweeps it
    completing  `lease_until` set; a retried `complete` answers 202 inside the
                lease and takes over after it (purging the previous attempt's
                objects first); ends completed, failed, or back to open
                (quota refused, retryable storage error, parts missing)
    completed   `demo_id` set; `complete` answers 200 while the demo exists;
                the row is deleted after FINISHED_ROW_GRACE
    failed      `error_code` set; staging purged at once; the row is deleted
                after FINISHED_ROW_GRACE

Rules this module keeps:

* Which parts arrived is read from disk, never stored per part in the database.
* No storage I/O inside a transaction that holds a lock; the account fence and
  every state change are short transactions. Every UPDATE that ends a
  `completing` attempt is conditional on that attempt's `pending_demo_id`.
* The upload ledger is written only by `admit_and_commit`, in the transaction
  that creates the demo; abandoned, expired and failed sessions count nothing.
* The in-flight part counter (`PARTS_IN_FLIGHT`) and the lock serializing the
  global open-session count with its insert (`_CREATE_LOCK`) are in process
  memory: the API runs as one uvicorn process (docker-compose.prod.yml), like
  the intake slot. A second process would need both moved into the database.
  A part PUT enters it before reading the session state and `complete` checks
  it after committing `completing`, so no part that saw `open` can still be
  writing once `complete` reads the parts.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import sys
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO, TextIO, cast

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.core.config import Settings, settings
from app.core.database import SessionLocal
from app.core.request_limits import INTAKE_BUSY_RETRY_AFTER_SECONDS
from app.core.upload_slots import IntakeSlot
from app.models.demo import Demo
from app.models.upload_session import UPLOAD_SESSION_ACTIVE_STATES, UploadSession
from app.schemas.demo import DemoListItem
from app.schemas.upload_session import UploadSessionError as UploadSessionErrorOut
from app.schemas.upload_session import UploadSessionStatus
from app.services.artifact_intake import (
    PROHIBITED_CONTENT_TYPES,
    ArtifactIntakeError,
    normalize_content_type,
    normalize_display_filename,
)
from app.services.deletion_service import AccountDeletedError, account_exists_for_write
from app.services.demo_service import DemoArtifactBindError, DemoDispatchError, DemoService
from app.services.storage import (
    ArtifactStore,
    StagingError,
    StagingIncomplete,
    StagingSessionGone,
    UploadStagingStore,
    upload_part_count,
    upload_staging_store_from_settings,
)
from app.services.upload_admission import UploadSessionGone, admit_and_commit
from app.services.upload_quota import UploadQuotaExceeded, UploadQuotaService

logger = logging.getLogger(__name__)
# The per-upload speed line (docs/vps_deploy_v1.md) has a logger of its own:
# uvicorn configures only its `uvicorn.*` loggers, so an INFO record of any
# other `app.*` logger is dropped. app.main gives this one a stderr handler.
UPLOAD_METRICS_LOGGER = "app.upload_metrics"
metrics_logger = logging.getLogger(UPLOAD_METRICS_LOGGER)

COMPLETING_LEASE = timedelta(seconds=900)
FINISHED_ROW_GRACE = timedelta(seconds=3600)
# A session directory is created before its row commits; younger orphans may
# still be a create in progress.
ORPHAN_DIR_MIN_AGE = timedelta(minutes=10)
STALE_TEMP_AGE = timedelta(hours=1)
PARTS_IN_FLIGHT_WAIT_SECONDS = 2.0
PARTS_IN_FLIGHT_RETRY_AFTER_SECONDS = 1
STORAGE_RETRY_AFTER_SECONDS = 5
CAPACITY_RETRY_AFTER_SECONDS = 60
STORAGE_FULL_RETRY_AFTER_SECONDS = 300
# The intake's own floor (DemoIngest.prepare_real_demo, min_source_bytes=16).
MIN_DEMO_BYTES = 16
# Bytes of part 0 handed to the intake's signature check (it reads up to 262).
SIGNATURE_PREFIX_BYTES = 512
SWEEP_BATCH = 200
PREFIX_PURGE_PASSES = 20
MAX_UPLOAD_TOKEN_LENGTH = 128

SESSION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
PART_INDEX_PATTERN = re.compile(r"[0-9]{1,5}")
PART_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

INTAKE_STATUS_CODES = {
    "INTAKE_TOO_LARGE": 413,
    "INTAKE_STORAGE_UNAVAILABLE": 503,
}
# Intake outcomes that leave the session open for another `complete`.
RETRYABLE_INTAKE_CODES = frozenset({"INTAKE_STORAGE_UNAVAILABLE"})

NO_STORE = {"Cache-Control": "private, no-store"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive; the stored value is UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def part_length(file_size: int, part_size: int, index: int) -> int:
    """Exact byte length of part `index` (the last part may be shorter)."""
    return min(part_size, file_size - index * part_size)


def is_session_id(value: str) -> bool:
    return SESSION_ID_PATTERN.fullmatch(value) is not None


# -- Refusals ------------------------------------------------------------------


class UploadSessionError(Exception):
    """A refusal in the structured shape `{"detail": {code, message, retryAfterSeconds?, ...}}`.

    Extra fields (`sessionId`, `missingParts`, ...) sit flat inside `detail`.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.extra = dict(extra or {})

    def to_response(self) -> JSONResponse:
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        headers = dict(NO_STORE)
        if self.retry_after_seconds is not None:
            detail["retryAfterSeconds"] = self.retry_after_seconds
            headers["Retry-After"] = str(self.retry_after_seconds)
        detail.update(self.extra)
        return JSONResponse(status_code=self.status_code, content={"detail": detail}, headers=headers)


class UploadIntakeRefusal(Exception):
    """A refusal in the legacy intake shape `{"detail": safe, "errorCode": code}`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds

    @classmethod
    def from_intake_error(cls, exc: ArtifactIntakeError) -> UploadIntakeRefusal:
        status_code = INTAKE_STATUS_CODES.get(exc.code, 400)
        return cls(
            status_code,
            exc.code,
            exc.safe_message,
            retry_after_seconds=STORAGE_RETRY_AFTER_SECONDS if status_code == 503 else None,
        )

    def to_response(self) -> JSONResponse:
        headers = dict(NO_STORE)
        if self.retry_after_seconds is not None:
            headers["Retry-After"] = str(self.retry_after_seconds)
        return JSONResponse(
            status_code=self.status_code,
            content={"detail": self.message, "errorCode": self.code},
            headers=headers,
        )


def session_not_found() -> UploadSessionError:
    return UploadSessionError(404, "upload_session_not_found", "Upload session not found.")


def session_not_open() -> UploadSessionError:
    return UploadSessionError(409, "upload_session_not_open", "The upload session no longer accepts changes.")


def part_invalid(message: str = "The upload part is invalid.") -> UploadSessionError:
    return UploadSessionError(400, "upload_part_invalid", message)


def intake_busy(retry_after_seconds: int = INTAKE_BUSY_RETRY_AFTER_SECONDS) -> UploadIntakeRefusal:
    return UploadIntakeRefusal(
        503,
        "INTAKE_BUSY",
        "Artifact upload capacity is busy",
        retry_after_seconds=retry_after_seconds,
    )


def _staging_unavailable() -> UploadIntakeRefusal:
    return UploadIntakeRefusal.from_intake_error(ArtifactIntakeError("INTAKE_STORAGE_UNAVAILABLE"))


def storage_unavailable() -> UploadIntakeRefusal:
    """503 INTAKE_STORAGE_UNAVAILABLE in the intake shape, with Retry-After."""
    return _staging_unavailable()


def account_deleted() -> UploadSessionError:
    return UploadSessionError(401, AccountDeletedError.code, AccountDeletedError.message)


# -- In-flight parts (process memory; one uvicorn process) ---------------------


class PartsInFlight:
    """Per-session count of part PUTs between entering and finishing, plus a PUT tally."""

    MAX_TALLIES = 1024

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._counts: dict[str, int] = {}
        self._tallies: OrderedDict[str, int] = OrderedDict()

    def try_enter(self, session_id: str, limit: int) -> bool:
        with self._condition:
            current = self._counts.get(session_id, 0)
            if current >= limit:
                return False
            self._counts[session_id] = current + 1
            self._tallies[session_id] = self._tallies.pop(session_id, 0) + 1
            while len(self._tallies) > self.MAX_TALLIES:
                self._tallies.popitem(last=False)
            return True

    def leave(self, session_id: str) -> None:
        with self._condition:
            remaining = self._counts.get(session_id, 0) - 1
            if remaining > 0:
                self._counts[session_id] = remaining
            else:
                self._counts.pop(session_id, None)
            self._condition.notify_all()

    def count(self, session_id: str) -> int:
        with self._condition:
            return self._counts.get(session_id, 0)

    def wait_idle(self, session_id: str, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._counts.get(session_id, 0) == 0, timeout)

    def pop_tally(self, session_id: str) -> int | None:
        with self._condition:
            return self._tallies.pop(session_id, None)


PARTS_IN_FLIGHT = PartsInFlight()


def parts_in_flight() -> PartsInFlight:
    """The process-wide in-flight table, resolved at call time (tests swap it)."""
    return PARTS_IN_FLIGHT

# Serializes the global open-session count with the insert it admits (one process).
_CREATE_LOCK = threading.Lock()


# -- Dependencies --------------------------------------------------------------


def upload_staging_from_settings(runtime_settings: Settings = settings) -> UploadStagingStore:
    """The one place the staging store is built (local disk, independent of the artifact backend)."""
    return upload_staging_store_from_settings(configured_settings=runtime_settings)


def get_upload_staging() -> UploadStagingStore:
    """FastAPI dependency (tests override it)."""
    return upload_staging_from_settings()


def get_upload_session_factory() -> Callable[[], Session]:
    """FastAPI dependency for the part PUT, which opens its own short session (tests override it)."""
    return SessionLocal


# -- Snapshots -----------------------------------------------------------------


@dataclass(frozen=True)
class SessionSnapshot:
    """Plain values of one row, safe to use after the transaction that read it ended."""

    id: str
    owner_id: str
    state: str
    display_filename: str
    content_type: str | None
    file_size: int
    part_size: int
    part_count: int
    pending_demo_id: str | None
    demo_id: str | None
    error_code: str | None
    lease_until: datetime | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, row: UploadSession) -> SessionSnapshot:
        return cls(
            id=row.id,
            owner_id=row.owner_id,
            state=row.state,
            display_filename=row.display_filename,
            content_type=row.content_type,
            file_size=int(row.file_size),
            part_size=int(row.part_size),
            part_count=int(row.part_count),
            pending_demo_id=row.pending_demo_id,
            demo_id=row.demo_id,
            error_code=row.error_code,
            lease_until=_as_utc(row.lease_until) if row.lease_until is not None else None,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            expires_at=_as_utc(row.expires_at),
        )

    def expired(self, now: datetime) -> bool:
        return self.expires_at <= now

    def lease_expired(self, now: datetime) -> bool:
        return self.lease_until is None or self.lease_until <= now


@dataclass(frozen=True)
class PartTarget:
    owner_id: str
    session_id: str
    index: int
    expected_size: int


@dataclass(frozen=True)
class CompleteResult:
    """201/200 with the demo, or 202 while another attempt holds the lease."""

    status_code: int
    demo: DemoListItem | None = None


@dataclass(frozen=True)
class UploadSweepResult:
    expired: int = 0
    abandoned: int = 0
    finished: int = 0
    orphan_dirs: int = 0
    temp_files: int = 0


# -- The owner-scoped service --------------------------------------------------


class UploadSessionService:
    def __init__(
        self,
        db: Session,
        owner_id: str,
        *,
        staging: UploadStagingStore,
        artifact_store: ArtifactStore | None = None,
        runtime_settings: Settings = settings,
        clock: Callable[[], datetime] = utc_now,
        tracker: PartsInFlight | None = None,
    ) -> None:
        self.db = db
        self.owner_id = owner_id
        self.staging = staging
        self._artifact_store = artifact_store
        self.settings = runtime_settings
        self.clock = clock
        self.tracker = tracker if tracker is not None else parts_in_flight()

    # -- create ----------------------------------------------------------------
    def create(
        self,
        *,
        filename: str,
        size: int,
        content_type: str | None,
        replace: bool = False,
    ) -> tuple[SessionSnapshot, str]:
        """Validate, check limits, create the staging directory, then commit the row.

        Raises ArtifactIntakeError (name, type, size), UploadQuotaExceeded
        (advisory, production only), UploadSessionError (409/503/401).
        """
        display_filename = normalize_display_filename(filename)
        normalized_content_type = normalize_content_type(content_type)
        if normalized_content_type in PROHIBITED_CONTENT_TYPES:
            raise ArtifactIntakeError("INTAKE_CONTENT_MISMATCH")
        if size <= 0:
            raise ArtifactIntakeError("INTAKE_EMPTY")
        if size < MIN_DEMO_BYTES:
            raise ArtifactIntakeError("INTAKE_TRUNCATED")
        if size > self.settings.max_demo_upload_bytes:
            raise ArtifactIntakeError("INTAKE_TOO_LARGE")

        # Advisory only: `complete` re-checks inside the admission.
        UploadQuotaService(self.db, self.settings).check_new_upload(self.owner_id)

        now = self.clock()
        replace_id: str | None = None
        existing = self._active_snapshot()
        if existing is not None and existing.expired(now):
            if retire_expired_session(self.db, self.staging, self.artifact_store, existing, now=now):
                existing = None
        if existing is not None:
            if existing.state != "open" or not replace:
                raise self._session_exists(existing)
            replace_id = existing.id

        # A replaced session frees its own place.
        others = self._active_session_count(now) - (1 if replace_id is not None else 0)
        if others >= self.settings.upload_session_global_limit:
            raise self._capacity_busy()
        try:
            free_bytes = self.staging.free_bytes()
        except Exception:
            logger.warning("Upload staging free space could not be read")
            raise _staging_unavailable() from None
        if free_bytes < size + self.settings.upload_staging_min_free_bytes:
            raise UploadSessionError(
                503,
                "upload_storage_full",
                "Upload storage is temporarily full. Try again later.",
                retry_after_seconds=STORAGE_FULL_RETRY_AFTER_SECONDS,
            )

        session_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        part_size = self.settings.upload_part_bytes
        # End the read transaction before touching the disk.
        self.db.rollback()
        try:
            self.staging.create_session(self.owner_id, session_id)
        except Exception:
            logger.warning("Upload staging directory could not be created")
            raise _staging_unavailable() from None

        committed = False
        try:
            with _CREATE_LOCK:
                try:
                    if not account_exists_for_write(self.db, self.owner_id, self.settings):
                        raise AccountDeletedError
                    if replace_id is not None:
                        replaced = self.db.execute(
                            delete(UploadSession)
                            .where(
                                UploadSession.id == replace_id,
                                UploadSession.owner_id == self.owner_id,
                                UploadSession.state == "open",
                            )
                            .execution_options(synchronize_session=False)
                        )
                        if int(getattr(replaced, "rowcount", 0) or 0) != 1:
                            raise _CreateRaced
                    if self._active_session_count(now) >= self.settings.upload_session_global_limit:
                        raise self._capacity_busy()
                    row = UploadSession(
                        id=session_id,
                        owner_id=self.owner_id,
                        active_owner_id=self.owner_id,
                        state="open",
                        display_filename=display_filename,
                        content_type=normalized_content_type,
                        file_size=size,
                        part_size=part_size,
                        part_count=upload_part_count(size, part_size),
                        token_sha256=token_digest(token),
                        created_at=now,
                        updated_at=now,
                        expires_at=now + timedelta(seconds=self.settings.upload_session_ttl_seconds),
                    )
                    self.db.add(row)
                    self.db.commit()
                except BaseException:
                    self.db.rollback()
                    raise
            committed = True
        except AccountDeletedError:
            raise account_deleted() from None
        except (_CreateRaced, IntegrityError):
            # Another create (or a `complete`) for this owner got there first.
            raced = self._active_snapshot()
            if raced is not None:
                raise self._session_exists(raced) from None
            raise UploadSessionError(
                409, "upload_session_exists", "Another upload is already in progress."
            ) from None
        finally:
            if not committed:
                self._purge_staging(session_id)

        if replace_id is not None:
            self._purge_staging(replace_id)
            self.tracker.pop_tally(replace_id)
        snapshot = self._load(session_id)
        if snapshot is None:  # deleted between commit and reload (account deletion)
            raise session_not_found()
        return snapshot, token

    # -- reads -----------------------------------------------------------------
    def current(self) -> UploadSessionStatus | None:
        snapshot = self._active_snapshot()
        if snapshot is None or snapshot.expired(self.clock()):
            return None
        return self.status_of(snapshot)

    def status(self, session_id: str) -> UploadSessionStatus:
        snapshot = self._visible(session_id)
        return self.status_of(snapshot)

    def status_of(self, snapshot: SessionSnapshot) -> UploadSessionStatus:
        received: dict[int, int] = {}
        part0_sha256: str | None = None
        if snapshot.state in UPLOAD_SESSION_ACTIVE_STATES:
            received = self._received_parts(snapshot)
            if 0 in received:
                try:
                    part0_sha256 = self.staging.part_sha256(snapshot.owner_id, snapshot.id, 0)
                except StagingSessionGone:
                    part0_sha256 = None
                except StagingError:
                    logger.warning("Upload staging could not be read for a status")
                    raise _staging_unavailable() from None
        demo: DemoListItem | None = None
        if snapshot.state == "completed" and snapshot.demo_id is not None:
            demo = self._demo_item(snapshot.demo_id)
        error: UploadSessionErrorOut | None = None
        if snapshot.state == "failed" and snapshot.error_code:
            error = UploadSessionErrorOut(
                code=snapshot.error_code,
                message=ArtifactIntakeError(snapshot.error_code).safe_message,
            )
        return UploadSessionStatus(
            sessionId=snapshot.id,
            state=snapshot.state,  # type: ignore[arg-type]
            filename=snapshot.display_filename,
            size=snapshot.file_size,
            partSize=snapshot.part_size,
            partCount=snapshot.part_count,
            maxParallelParts=self.settings.upload_max_parallel_parts,
            receivedParts=sorted(received),
            receivedBytes=sum(received.values()),
            part0Sha256=part0_sha256,
            expiresAt=snapshot.expires_at,
            demo=demo,
            error=error,
        )

    # -- token -----------------------------------------------------------------
    def reissue_token(self, session_id: str) -> tuple[SessionSnapshot, str]:
        """A fresh upload token for an open session; the previous one stops working."""
        if not is_session_id(session_id):
            raise session_not_found()
        token = secrets.token_urlsafe(32)
        now = self.clock()
        try:
            row = self._locked_row(session_id)
            if row is None or SessionSnapshot.of(row).expired(now):
                raise session_not_found()
            if row.state != "open":
                raise session_not_open()
            row.token_sha256 = token_digest(token)
            row.updated_at = now
            snapshot = SessionSnapshot.of(row)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return snapshot, token

    # -- delete ----------------------------------------------------------------
    def delete(self, session_id: str) -> None:
        snapshot = self._visible(session_id)
        if snapshot.state == "completing":
            raise UploadSessionError(
                409, "upload_session_not_open", "The upload is being completed and cannot be cancelled."
            )
        try:
            result = self.db.execute(
                delete(UploadSession)
                .where(
                    UploadSession.id == snapshot.id,
                    UploadSession.owner_id == self.owner_id,
                    UploadSession.state == snapshot.state,
                )
                .execution_options(synchronize_session=False)
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            # It changed state meanwhile (a `complete` started): say so rather than guess.
            raise session_not_open()
        self._purge_staging(snapshot.id)
        self.tracker.pop_tally(snapshot.id)

    # -- complete --------------------------------------------------------------
    def complete(self, session_id: str, *, intake_slot: IntakeSlot) -> CompleteResult:
        if not is_session_id(session_id):
            raise session_not_found()
        if not intake_slot.try_acquire():
            raise intake_busy()
        try:
            return self._complete_holding_slot(session_id)
        finally:
            intake_slot.release()

    def _complete_holding_slot(self, session_id: str) -> CompleteResult:
        snapshot = self._begin_completion(session_id)
        if isinstance(snapshot, CompleteResult):
            return snapshot
        pending_demo_id = snapshot.pending_demo_id
        assert pending_demo_id is not None

        # Every part that saw `open` entered the counter before reading it.
        if not self.tracker.wait_idle(snapshot.id, PARTS_IN_FLIGHT_WAIT_SECONDS):
            self._reopen(snapshot)
            raise UploadSessionError(
                409,
                "upload_parts_in_flight",
                "Parts of this upload are still being received.",
                retry_after_seconds=PARTS_IN_FLIGHT_RETRY_AFTER_SECONDS,
            )

        try:
            stream = self.staging.open_concat(
                snapshot.owner_id,
                snapshot.id,
                part_count=snapshot.part_count,
                part_size=snapshot.part_size,
                file_size=snapshot.file_size,
            )
        except StagingIncomplete as exc:
            self._reopen(snapshot)
            missing = sorted(int(index) for index in getattr(exc, "missing", ()))
            raise UploadSessionError(
                409,
                "upload_parts_missing",
                "Some parts of this upload have not been received.",
                extra={"missingParts": missing},
            ) from None
        except StagingSessionGone:
            # The staging directory is gone (deleted, or a database restored
            # without it): this session can never complete.
            self._drop(snapshot)
            raise session_not_found() from None
        except Exception:
            logger.warning("Upload staging could not be read for completion")
            self._reopen(snapshot)
            raise _staging_unavailable() from None

        service = self._demo_service()
        try:
            try:
                prepared = service.prepare_real_demo(
                    # The read/seek/tell surface both store backends use;
                    # typing.BinaryIO is nominal, so a wrapper cannot be one.
                    stream=cast(BinaryIO, stream),
                    filename=snapshot.display_filename,
                    content_type=snapshot.content_type,
                    demo_id=pending_demo_id,
                )
            finally:
                _close_quietly(stream)
        except ArtifactIntakeError as exc:
            self.db.rollback()
            if exc.code in RETRYABLE_INTAKE_CODES:
                self._reopen(snapshot)
            else:
                self._fail(snapshot, exc.code)
            raise UploadIntakeRefusal.from_intake_error(exc) from None
        except DemoArtifactBindError:
            self.db.rollback()
            self._reopen(snapshot)
            raise UploadIntakeRefusal(
                503,
                "INTAKE_UNAVAILABLE",
                "Demo intake could not be completed",
                retry_after_seconds=STORAGE_RETRY_AFTER_SECONDS,
            ) from None
        except Exception:
            self.db.rollback()
            self._reopen(snapshot)
            raise

        now = self.clock()

        def mark_completed(db: Session) -> None:
            result = db.execute(
                update(UploadSession)
                .where(
                    UploadSession.id == snapshot.id,
                    UploadSession.owner_id == snapshot.owner_id,
                    UploadSession.state == "completing",
                    UploadSession.pending_demo_id == pending_demo_id,
                )
                .values(
                    state="completed",
                    demo_id=pending_demo_id,
                    active_owner_id=None,
                    lease_until=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                raise UploadSessionGone

        quota = UploadQuotaService(self.db, self.settings)
        try:
            admit_and_commit(
                self.db,
                service,
                quota,
                self.owner_id,
                prepared,
                extra_writes=mark_completed,
            )
        except UploadQuotaExceeded:
            # Not a failure: the received parts stay for a later `complete`.
            self._reopen(snapshot)
            raise
        except AccountDeletedError:
            # The account is gone (its deletion normally took this row too).
            self._drop(snapshot)
            self._purge_staging(snapshot.id)
            raise account_deleted() from None
        except UploadSessionGone:
            raise session_not_found() from None
        except DemoArtifactBindError:
            self._reopen(snapshot)
            raise UploadIntakeRefusal(
                503,
                "INTAKE_UNAVAILABLE",
                "Demo intake could not be completed",
                retry_after_seconds=STORAGE_RETRY_AFTER_SECONDS,
            ) from None
        except Exception:
            # admit_and_commit has rolled back and discarded the prepared demo.
            self._reopen(snapshot)
            raise

        self._purge_staging(snapshot.id)
        self._log_completed(snapshot, now)
        try:
            service.reload_prepared_real_demo(prepared)
            service.dispatch_prepared_real_demo(prepared)
        except DemoDispatchError:
            # The demo exists and the session is completed; the stale-job
            # recovery re-dispatches it, exactly as for the legacy upload.
            raise UploadIntakeRefusal(
                503,
                "INTAKE_UNAVAILABLE",
                "Parser dispatch could not be completed",
                retry_after_seconds=STORAGE_RETRY_AFTER_SECONDS,
            ) from None
        return CompleteResult(201, service.demo_list_item(prepared.demo))

    def _begin_completion(self, session_id: str) -> SessionSnapshot | CompleteResult:
        """Move the session to `completing` under a fresh lease, or answer from its state."""
        for _ in range(2):
            now = self.clock()
            snapshot = self._load(session_id)
            if snapshot is None:
                raise session_not_found()
            if snapshot.state == "completed":
                demo = self._demo_item(snapshot.demo_id) if snapshot.demo_id else None
                if demo is None:
                    raise session_not_found()
                return CompleteResult(200, demo)
            if snapshot.expired(now):
                raise session_not_found()
            if snapshot.state == "failed":
                raise UploadIntakeRefusal.from_intake_error(
                    ArtifactIntakeError(snapshot.error_code or "INTAKE_REJECTED")
                )
            takeover = snapshot.state == "completing"
            if takeover:
                if not snapshot.lease_expired(now):
                    return CompleteResult(202)
                # The previous attempt died: remove what it may have stored,
                # outside any transaction, before taking its place.
                self.db.rollback()
                if not self._purge_abandoned_attempt(snapshot):
                    raise _staging_unavailable()

            pending_demo_id = str(uuid.uuid4())
            lease_until = now + COMPLETING_LEASE
            try:
                row = self._locked_row(session_id)
                current = SessionSnapshot.of(row) if row is not None else None
                unchanged = current is not None and (
                    (
                        takeover
                        and current.state == "completing"
                        and current.lease_until == snapshot.lease_until
                        and current.pending_demo_id == snapshot.pending_demo_id
                    )
                    or (not takeover and current.state == "open")
                )
                if not unchanged or row is None:
                    self.db.rollback()
                    continue
                row.state = "completing"
                row.pending_demo_id = pending_demo_id
                row.lease_until = lease_until
                row.updated_at = now
                begun = SessionSnapshot.of(row)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return begun
        # Contended twice in a row: another `complete` is moving it.
        return CompleteResult(202)

    def _purge_abandoned_attempt(self, snapshot: SessionSnapshot) -> bool:
        try:
            return purge_pending_attempt(self.db, self.artifact_store, snapshot)
        except Exception:
            logger.warning("Abandoned upload attempt could not be purged; retry later")
            return False

    # -- state changes ending an attempt (all conditional on the attempt) -------
    def _reopen(self, snapshot: SessionSnapshot) -> None:
        self._end_attempt(
            snapshot,
            state="open",
            pending_demo_id=None,
            lease_until=None,
        )

    def _fail(self, snapshot: SessionSnapshot, error_code: str) -> None:
        self._end_attempt(
            snapshot,
            state="failed",
            error_code=error_code[:64],
            active_owner_id=None,
            pending_demo_id=None,
            lease_until=None,
        )
        self._purge_staging(snapshot.id)

    def _drop(self, snapshot: SessionSnapshot) -> None:
        try:
            self.db.execute(
                delete(UploadSession)
                .where(
                    UploadSession.id == snapshot.id,
                    UploadSession.owner_id == snapshot.owner_id,
                    UploadSession.state == "completing",
                    UploadSession.pending_demo_id == snapshot.pending_demo_id,
                )
                .execution_options(synchronize_session=False)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.warning("Upload session without staging could not be removed")

    def _end_attempt(self, snapshot: SessionSnapshot, **values: Any) -> None:
        try:
            self.db.rollback()
            self.db.execute(
                update(UploadSession)
                .where(
                    UploadSession.id == snapshot.id,
                    UploadSession.owner_id == snapshot.owner_id,
                    UploadSession.state == "completing",
                    UploadSession.pending_demo_id == snapshot.pending_demo_id,
                )
                .values(updated_at=self.clock(), **values)
                .execution_options(synchronize_session=False)
            )
            self.db.commit()
        except Exception:
            # The lease expires and a later `complete` takes over.
            self.db.rollback()
            logger.warning("Upload session state could not be updated")

    # -- helpers ---------------------------------------------------------------
    @property
    def artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            self._artifact_store = self._demo_service().artifact_store
        return self._artifact_store

    def _demo_service(self) -> DemoService:
        return DemoService(self.db, owner_id=self.owner_id, artifact_store=self._artifact_store)

    def _demo_item(self, demo_id: str | None) -> DemoListItem | None:
        if demo_id is None:
            return None
        service = self._demo_service()
        demo = service.get_demo(demo_id)
        return service.demo_list_item(demo) if demo is not None else None

    def _load(self, session_id: str) -> SessionSnapshot | None:
        if not is_session_id(session_id):
            return None
        row = (
            self.db.query(UploadSession)
            .filter(UploadSession.id == session_id, UploadSession.owner_id == self.owner_id)
            .one_or_none()
        )
        if row is None:
            return None
        snapshot = SessionSnapshot.of(row)
        # Plain values from here: end the read transaction (releases the connection).
        self.db.rollback()
        return snapshot

    def _locked_row(self, session_id: str) -> UploadSession | None:
        return (
            self.db.query(UploadSession)
            .filter(UploadSession.id == session_id, UploadSession.owner_id == self.owner_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )

    def _visible(self, session_id: str) -> SessionSnapshot:
        """The owner's session, or 404 when missing or past `expires_at` (the sweep's rule)."""
        snapshot = self._load(session_id)
        if snapshot is None:
            raise session_not_found()
        if snapshot.state != "completed" and snapshot.expired(self.clock()):
            raise session_not_found()
        return snapshot

    def _active_snapshot(self) -> SessionSnapshot | None:
        row = (
            self.db.query(UploadSession)
            .filter(
                UploadSession.active_owner_id == self.owner_id,
                UploadSession.owner_id == self.owner_id,
            )
            .one_or_none()
        )
        if row is None:
            return None
        snapshot = SessionSnapshot.of(row)
        self.db.rollback()
        return snapshot

    def _active_session_count(self, now: datetime) -> int:
        return int(
            self.db.query(func.count(UploadSession.id))
            .filter(
                UploadSession.state.in_(UPLOAD_SESSION_ACTIVE_STATES),
                UploadSession.expires_at > now,
            )
            .scalar()
            or 0
        )

    def _received_parts(self, snapshot: SessionSnapshot) -> dict[int, int]:
        try:
            listed = self.staging.list_parts(snapshot.owner_id, snapshot.id)
        except StagingSessionGone:
            return {}
        except StagingError:
            logger.warning("Upload staging could not be listed")
            raise _staging_unavailable() from None
        return {
            index: size
            for index, size in listed.items()
            if 0 <= index < snapshot.part_count
            and size == part_length(snapshot.file_size, snapshot.part_size, index)
        }

    def _session_exists(self, existing: SessionSnapshot) -> UploadSessionError:
        try:
            received = self._received_parts(existing)
        except UploadIntakeRefusal:
            received = {}
        return UploadSessionError(
            409,
            "upload_session_exists",
            "Another upload is already in progress.",
            extra={
                "sessionId": existing.id,
                "filename": existing.display_filename,
                "size": existing.file_size,
                "receivedBytes": sum(received.values()),
                "state": existing.state,
            },
        )

    @staticmethod
    def _capacity_busy() -> UploadSessionError:
        return UploadSessionError(
            503,
            "upload_capacity_busy",
            "Too many uploads are in progress. Try again in a few minutes.",
            retry_after_seconds=CAPACITY_RETRY_AFTER_SECONDS,
        )

    def _purge_staging(self, session_id: str) -> None:
        _purge_session_dir(self.staging, self.owner_id, session_id)

    def _log_completed(self, snapshot: SessionSnapshot, now: datetime) -> None:
        # Compact and content-free: sizes, counts and duration only.
        seconds = max(0.0, (now - snapshot.created_at).total_seconds())
        puts = self.tracker.pop_tally(snapshot.id)
        metrics_logger.info(
            "Upload session completed: bytes=%d parts=%d part_puts=%s seconds=%.1f",
            snapshot.file_size,
            snapshot.part_count,
            puts if puts is not None else "unknown",
            seconds,
        )


class _CreateRaced(Exception):
    pass


# -- Part PUT (token-authenticated, short sessions only) -----------------------


def resolve_part_target(
    db: Session,
    session_id: str,
    token: str | None,
    index: int,
    *,
    now: datetime | None = None,
) -> PartTarget:
    """Authorize one part PUT by its upload token and return where it goes.

    A missing session and a wrong token are the same 404. The caller closes
    `db` before reading the body.
    """
    if not token or len(token) > MAX_UPLOAD_TOKEN_LENGTH or not token.isascii():
        raise session_not_found()
    row = db.get(UploadSession, session_id)
    if row is None or not hmac.compare_digest(row.token_sha256, token_digest(token)):
        raise session_not_found()
    snapshot = SessionSnapshot.of(row)
    if snapshot.expired(now or utc_now()):
        raise session_not_found()
    if snapshot.state != "open":
        raise session_not_open()
    if not 0 <= index < snapshot.part_count:
        raise part_invalid("The part index is out of range.")
    return PartTarget(
        owner_id=snapshot.owner_id,
        session_id=snapshot.id,
        index=index,
        expected_size=part_length(snapshot.file_size, snapshot.part_size, index),
    )


def fail_session_for_content(
    db: Session,
    staging: UploadStagingStore,
    target: PartTarget,
    error_code: str,
    *,
    now: datetime | None = None,
) -> None:
    """Part 0 carries an archive or executable signature: fail the session now."""
    try:
        db.execute(
            update(UploadSession)
            .where(
                UploadSession.id == target.session_id,
                UploadSession.owner_id == target.owner_id,
                UploadSession.state == "open",
            )
            .values(
                state="failed",
                error_code=error_code,
                active_owner_id=None,
                pending_demo_id=None,
                lease_until=None,
                updated_at=now or utc_now(),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Upload session could not be marked failed")
    _purge_session_dir(staging, target.owner_id, target.session_id)


# -- Startup and sweep ---------------------------------------------------------


def reset_completing_leases(db: Session, *, now: datetime | None = None) -> int:
    """At API startup: no `complete` survives a restart (one process), so every lease ends now."""
    moment = now or utc_now()
    try:
        result = db.execute(
            update(UploadSession)
            .where(UploadSession.state == "completing")
            .values(lease_until=moment)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return int(getattr(result, "rowcount", 0) or 0)


def purge_pending_attempt(db: Session, artifact_store: ArtifactStore, snapshot: SessionSnapshot) -> bool:
    """Purge a dead `complete` attempt's objects; True when nothing of it is left.

    Never touches a prefix whose demo row exists (that attempt committed).
    Runs outside any transaction; the caller re-checks the row afterwards.
    """
    pending = snapshot.pending_demo_id
    if pending is None:
        return True
    exists = db.query(Demo.id).filter(Demo.id == pending).first() is not None
    db.rollback()
    if exists:
        return True
    for _ in range(PREFIX_PURGE_PASSES):
        result = artifact_store.purge_prefix(owner_id=snapshot.owner_id, demo_id=pending)
        if result.complete:
            return True
    return False


def retire_expired_session(
    db: Session,
    staging: UploadStagingStore,
    artifact_store: ArtifactStore,
    snapshot: SessionSnapshot,
    *,
    now: datetime,
) -> bool:
    """Delete one expired session (row, then staging); False if it must wait (live lease)."""
    if not snapshot.expired(now):
        return False
    conditions = [
        UploadSession.id == snapshot.id,
        UploadSession.owner_id == snapshot.owner_id,
        UploadSession.state == snapshot.state,
        UploadSession.expires_at <= now,
    ]
    if snapshot.state == "completing":
        if not snapshot.lease_expired(now):
            return False
        try:
            if not purge_pending_attempt(db, artifact_store, snapshot):
                return False
        except Exception:
            logger.warning("Expired upload attempt could not be purged; retry later")
            return False
        conditions.append(UploadSession.pending_demo_id == snapshot.pending_demo_id)
        conditions.append(UploadSession.lease_until <= now)
    elif snapshot.state not in ("open", "failed"):
        return False
    try:
        result = db.execute(
            delete(UploadSession).where(*conditions).execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        return False
    _purge_session_dir(staging, snapshot.owner_id, snapshot.id)
    parts_in_flight().pop_tally(snapshot.id)
    return True


def sweep_upload_sessions(
    db: Session,
    staging: UploadStagingStore,
    artifact_store: ArtifactStore,
    now: datetime | None = None,
) -> UploadSweepResult:
    """Expire, abandon and forget upload sessions; run at API startup and hourly by the worker.

    1. expired `open`/`failed`: delete the row, then the staging directory;
    2. expired `completing` whose lease also ended: purge that attempt's
       objects (no demo row for it), then the row and directory;
    3. `completed`/`failed` rows past FINISHED_ROW_GRACE: delete;
    4. staging directories with no row, older than ORPHAN_DIR_MIN_AGE: delete
       (the durable backstop for an account deletion's best-effort purge);
    5. staging temp files older than STALE_TEMP_AGE: delete.

    Each step is best-effort and logs by error type only.
    """
    moment = now or utc_now()
    counts = {"expired": 0, "abandoned": 0, "finished": 0, "orphan_dirs": 0, "temp_files": 0}

    def expired_rows(states: tuple[str, ...], *, lease_ended: bool = False) -> list[SessionSnapshot]:
        query = db.query(UploadSession).filter(
            UploadSession.state.in_(states),
            UploadSession.expires_at <= moment,
        )
        if lease_ended:
            query = query.filter(UploadSession.lease_until <= moment)
        rows = [SessionSnapshot.of(row) for row in query.order_by(UploadSession.expires_at).limit(SWEEP_BATCH)]
        db.rollback()
        return rows

    for key, states, lease_ended in (
        ("expired", ("open", "failed"), False),
        ("abandoned", ("completing",), True),
    ):
        try:
            for snapshot in expired_rows(states, lease_ended=lease_ended):
                if retire_expired_session(db, staging, artifact_store, snapshot, now=moment):
                    counts[key] += 1
        except Exception as exc:
            db.rollback()
            logger.warning("Upload session sweep step %s failed (%s)", key, type(exc).__name__)

    try:
        cutoff = moment - FINISHED_ROW_GRACE
        finished = [
            (row_id, owner_id, state)
            for row_id, owner_id, state in db.query(
                UploadSession.id, UploadSession.owner_id, UploadSession.state
            )
            .filter(
                UploadSession.state.in_(("completed", "failed")),
                UploadSession.updated_at <= cutoff,
            )
            .limit(SWEEP_BATCH)
            .all()
        ]
        if finished:
            result = db.execute(
                delete(UploadSession)
                .where(
                    UploadSession.id.in_([row_id for row_id, _, _ in finished]),
                    UploadSession.state.in_(("completed", "failed")),
                    UploadSession.updated_at <= cutoff,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            counts["finished"] = int(getattr(result, "rowcount", 0) or 0)
            for row_id, owner_id, state in finished:
                if state == "failed":
                    _purge_session_dir(staging, owner_id, row_id)
        else:
            db.rollback()
    except Exception as exc:
        db.rollback()
        logger.warning("Upload session sweep step finished failed (%s)", type(exc).__name__)

    try:
        orphan_cutoff = moment - ORPHAN_DIR_MIN_AGE
        for owner_id, session_id, created_at in staging.iter_session_dirs():
            if _as_utc(created_at) > orphan_cutoff:
                continue
            known = (
                db.query(UploadSession.id)
                .filter(UploadSession.id == session_id, UploadSession.owner_id == owner_id)
                .first()
            )
            db.rollback()
            if known is None:
                _purge_session_dir(staging, owner_id, session_id)
                counts["orphan_dirs"] += 1
    except Exception as exc:
        db.rollback()
        logger.warning("Upload session sweep step orphan_dirs failed (%s)", type(exc).__name__)

    try:
        counts["temp_files"] = int(staging.purge_stale_temp(moment - STALE_TEMP_AGE) or 0)
    except Exception as exc:
        logger.warning("Upload session sweep step temp_files failed (%s)", type(exc).__name__)

    return UploadSweepResult(**counts)


# -- Small helpers -------------------------------------------------------------


class _UploadMetricsHandler(logging.StreamHandler[TextIO]):
    """stderr at INFO for the upload speed line only (marks it as installed)."""


def install_upload_metrics_log() -> None:
    """Let the completed-upload line reach the API log (`docker compose logs api`).

    Called once by app.main; idempotent. Only `app.upload_metrics` gets the
    handler, so every other `app.*` logger keeps today's behaviour, and the
    line never propagates into a root handler a second time.
    """
    if any(isinstance(handler, _UploadMetricsHandler) for handler in metrics_logger.handlers):
        return
    handler = _UploadMetricsHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    metrics_logger.addHandler(handler)
    metrics_logger.setLevel(logging.INFO)
    metrics_logger.propagate = False


def _purge_session_dir(staging: UploadStagingStore, owner_id: str, session_id: str) -> None:
    try:
        staging.purge_session(owner_id, session_id)
    except Exception as exc:
        # The orphan-directory sweep removes it later.
        logger.warning("Upload staging purge failed (%s)", type(exc).__name__)


def _close_quietly(stream: Any) -> None:
    try:
        stream.close()
    except Exception:
        pass
