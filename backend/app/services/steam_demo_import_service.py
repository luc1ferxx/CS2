from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.auth import normalize_owner_id
from app.core.config import Settings, settings
from app.models.demo import Demo
from app.models.job import DemoJob
from app.models.steam import SteamMatch
from app.services.artifact_intake import ArtifactIntakeError
from app.services.deletion_service import AccountDeletedError, account_exists_for_write
from app.services.demo_service import (
    DemoArtifactBindError,
    DemoDispatchError,
    DemoService,
)
from app.services.demo_source_provider import (
    DemoSource,
    DemoSourceProvider,
    DemoSourceUnavailableError,
    demo_source_provider_from_settings,
)
from app.services.secure_demo_downloader import DemoDownloadError
from app.services.steam_credentials import SteamCredentialCipher
from app.services.steam_demo_download_limiter import (
    SteamDemoDownloadCapacityError,
    SteamDemoDownloadLimiterUnavailableError,
)
from app.services.storage import ArtifactStore
from app.services.upload_quota import record_upload

PARSER_DISPATCH_RETRY_AFTER_SECONDS = 30


def utc_now() -> datetime:
    return datetime.now(UTC)


class SteamDemoImportNotFoundError(RuntimeError):
    pass


class SteamDemoSourceUnavailableError(RuntimeError):
    code = "demo_source_unavailable"
    manual_upload_supported = True


class SteamDemoImportInProgressError(RuntimeError):
    pass


class SteamDemoImportFailedError(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        super().__init__(safe_message)


class SteamDemoImportService:
    def __init__(
        self,
        db: Session,
        *,
        owner_id: str,
        provider: DemoSourceProvider | None = None,
        cipher: SteamCredentialCipher | None = None,
        downloader: object | None = None,
        download_limiter: object | None = None,
        artifact_store: ArtifactStore | None = None,
        redis_client: object | None = None,
        runtime_settings: Settings = settings,
        clock: object = utc_now,
    ):
        self.db = db
        self.owner_id = normalize_owner_id(owner_id)
        self.settings = runtime_settings
        self.provider = (
            provider
            if provider is not None
            else demo_source_provider_from_settings(runtime_settings)
        )
        self._cipher = cipher
        self.downloader = downloader
        self.download_limiter = download_limiter
        self.artifact_store = artifact_store
        self.redis_client = redis_client
        self.clock = clock

    @property
    def cipher(self) -> SteamCredentialCipher:
        if self._cipher is None:
            self._cipher = SteamCredentialCipher(self.settings)
        return self._cipher

    def import_match(self, match_id: str) -> SteamMatch:
        match = self._get_match(match_id)
        if match is None:
            raise SteamDemoImportNotFoundError("Steam match was not found")
        if match.demo_id is not None:
            return self._resume_linked_demo(match)

        run_id = str(uuid.uuid4())
        now = self.clock()
        lease_expires_at = now + timedelta(
            seconds=self.settings.steam_demo_download_concurrency_lease_seconds
        )
        claimed = (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.demo_id.is_(None),
                or_(
                    SteamMatch.import_run_id.is_(None),
                    SteamMatch.import_lease_expires_at.is_(None),
                    SteamMatch.import_lease_expires_at <= now,
                ),
            )
            .update(
                {
                    SteamMatch.status: "demo_pending",
                    SteamMatch.import_run_id: run_id,
                    SteamMatch.import_lease_expires_at: lease_expires_at,
                    SteamMatch.import_attempts: SteamMatch.import_attempts + 1,
                    SteamMatch.import_started_at: now,
                    SteamMatch.import_completed_at: None,
                    SteamMatch.parser_dispatched_at: None,
                    SteamMatch.parser_dispatched_job_id: None,
                    SteamMatch.last_import_error_code: None,
                    SteamMatch.last_import_error_message: None,
                    SteamMatch.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if claimed != 1:
            current = self._get_match(match_id)
            if current is None:
                raise SteamDemoImportNotFoundError("Steam match was not found")
            if current.demo_id is not None:
                return current
            raise SteamDemoImportInProgressError("Demo import is already running")
        match = self._run_match(match_id, run_id)

        if not bool(getattr(self.provider, "available", False)):
            message = (
                "Automatic Demo source is unavailable. "
                "Upload the .dem file manually."
            )
            self._finish_unavailable(
                match_id,
                run_id,
                "provider_not_configured",
                message,
            )
            raise SteamDemoSourceUnavailableError(message)

        try:
            share_code = self.cipher.decrypt(
                ciphertext=match.share_code_ciphertext,
                nonce=match.share_code_nonce,
                key_version=match.encryption_key_version,
                owner_id=self.owner_id,
                purpose="match-sharing-code",
                record_id=match.id,
            )
        except Exception:
            self._finish_unavailable(
                match_id,
                run_id,
                "credential_decryption_failed",
                "Steam match authorization could not be decrypted safely.",
            )
            raise SteamDemoImportFailedError(
                "credential_decryption_failed",
                "Steam match authorization could not be decrypted safely.",
            ) from None
        try:
            source = self.provider.resolve(share_code=share_code)
        except DemoSourceUnavailableError as exc:
            self._finish_unavailable(match_id, run_id, exc.code, str(exc))
            raise SteamDemoSourceUnavailableError(str(exc)) from None
        except Exception:
            self._finish_unavailable(
                match_id,
                run_id,
                "provider_failed",
                "Automatic Demo source is temporarily unavailable.",
            )
            raise SteamDemoImportFailedError(
                "provider_failed",
                "Automatic Demo source is temporarily unavailable.",
            ) from None

        if not isinstance(source, DemoSource) or source.provider_id != getattr(
            self.provider,
            "provider_id",
            None,
        ):
            self._finish_unavailable(
                match_id,
                run_id,
                "provider_response_invalid",
                "Automatic Demo source returned an invalid response.",
            )
            raise SteamDemoImportFailedError(
                "provider_response_invalid",
                "Automatic Demo source returned an invalid response.",
            )
        if self.downloader is None:
            self._finish_retryable(
                match_id,
                run_id,
                "downloader_not_configured",
                "Automatic Demo download is not configured.",
            )
            raise SteamDemoImportFailedError(
                "downloader_not_configured",
                "Automatic Demo download is not configured.",
            )
        if self.download_limiter is None:
            self._finish_retryable(
                match_id,
                run_id,
                "download_limiter_not_configured",
                "Automatic Demo download capacity control is unavailable.",
            )
            raise SteamDemoImportFailedError(
                "download_limiter_not_configured",
                "Automatic Demo download capacity control is unavailable.",
            )

        demo_service = DemoService(
            self.db,
            owner_id=self.owner_id,
            artifact_store=self.artifact_store,
        )
        prepared = None
        committed = False
        try:
            with self.download_limiter.lease(self.owner_id):
                self._set_downloading(match_id, run_id, source.provider_id)
                with self.downloader.download(source) as downloaded:
                    prepared = demo_service.prepare_real_demo(
                        stream=downloaded.stream,
                        filename=downloaded.filename,
                        content_type=downloaded.content_type,
                    )
                    if (
                        prepared.accepted.size_bytes != downloaded.size_bytes
                        or not hmac.compare_digest(
                            prepared.accepted.sha256,
                            downloaded.sha256,
                        )
                    ):
                        demo_service.discard_prepared_real_demo(prepared)
                        prepared = None
                        raise SteamDemoImportFailedError(
                            "download_intake_mismatch",
                            "Downloaded Demo failed intake verification.",
                        )

            # Bind only after the downloader and limiter contexts have exited. A
            # cleanup failure must happen before the durable Demo/job transaction.
            assert prepared is not None
            # The account fence comes first, before the bind locks the match
            # row: an account delete locks the account and then deletes the
            # Steam rows, so taking them in the same order cannot deadlock.
            if not account_exists_for_write(self.db, self.owner_id, self.settings):
                raise AccountDeletedError
            prepared.demo.name = f"Steam Match {match_id[:8]}"
            bound_at = self.clock()
            # An import consumes the daily upload quota like an upload does.
            record_upload(self.db, self.owner_id, now=bound_at)
            self.db.flush()
            bound = (
                self.db.query(SteamMatch)
                .filter(
                    SteamMatch.id == match_id,
                    SteamMatch.owner_id == self.owner_id,
                    SteamMatch.import_run_id == run_id,
                    SteamMatch.demo_id.is_(None),
                )
                .update(
                    {
                        SteamMatch.demo_id: prepared.demo.id,
                        SteamMatch.status: "parsing",
                        SteamMatch.provider_id: source.provider_id,
                        SteamMatch.import_run_id: None,
                        SteamMatch.import_lease_expires_at: None,
                        SteamMatch.import_completed_at: bound_at,
                        SteamMatch.parser_dispatched_at: None,
                        SteamMatch.parser_dispatched_job_id: None,
                        SteamMatch.last_import_error_code: None,
                        SteamMatch.last_import_error_message: None,
                        SteamMatch.updated_at: bound_at,
                    },
                    synchronize_session=False,
                )
            )
            if bound != 1:
                demo_service.discard_prepared_real_demo(prepared)
                prepared = None
                current = self._get_match(match_id)
                if current is None:
                    raise SteamDemoImportNotFoundError(
                        "Steam match was removed while importing"
                    )
                raise SteamDemoImportInProgressError(
                    "Demo import was superseded by a newer run"
                )
            demo_service.commit_prepared_real_demo(prepared)
            committed = True
        except SteamDemoDownloadCapacityError:
            self._finish_retryable(
                match_id,
                run_id,
                "demo_download_capacity_full",
                "Automatic Demo download capacity is temporarily full.",
            )
            raise SteamDemoImportFailedError(
                "demo_download_capacity_full",
                "Automatic Demo download capacity is temporarily full.",
            ) from None
        except SteamDemoDownloadLimiterUnavailableError:
            self._finish_retryable(
                match_id,
                run_id,
                "demo_download_limiter_unavailable",
                "Automatic Demo download capacity control is unavailable.",
            )
            raise SteamDemoImportFailedError(
                "demo_download_limiter_unavailable",
                "Automatic Demo download capacity control is unavailable.",
            ) from None
        except SteamDemoImportFailedError as exc:
            self._finish_unavailable(match_id, run_id, exc.code, str(exc))
            raise
        except DemoDownloadError as exc:
            self._finish_unavailable(match_id, run_id, exc.code, exc.safe_message)
            raise SteamDemoImportFailedError(exc.code, exc.safe_message) from None
        except SteamDemoImportInProgressError:
            raise
        except ArtifactIntakeError as exc:
            self._finish_unavailable(match_id, run_id, exc.code, exc.safe_message)
            raise SteamDemoImportFailedError(exc.code, exc.safe_message) from None
        except DemoArtifactBindError:
            self._finish_unavailable(
                match_id,
                run_id,
                "demo_bind_failed",
                "Downloaded Demo could not be bound for parsing.",
            )
            raise SteamDemoImportFailedError(
                "demo_bind_failed",
                "Downloaded Demo could not be bound for parsing.",
            ) from None
        except SteamDemoImportNotFoundError:
            raise
        except AccountDeletedError:
            # The match row went with the account; there is nothing to mark.
            raise SteamDemoImportFailedError(
                AccountDeletedError.code,
                "The account was deleted while the Demo was importing.",
            ) from None
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self._finish_unavailable(
                match_id,
                run_id,
                "demo_import_failed",
                "Automatic Demo import failed safely.",
            )
            raise SteamDemoImportFailedError(
                "demo_import_failed",
                "Automatic Demo import failed safely.",
            ) from None
        finally:
            if prepared is not None and not committed:
                demo_service.discard_prepared_real_demo(prepared)

        assert prepared is not None
        try:
            demo_service.dispatch_prepared_real_demo(
                prepared,
                redis_client=self.redis_client,
            )
        except DemoDispatchError:
            marked_unavailable = self._mark_linked_demo_unavailable(
                match_id,
                prepared.demo.id,
                prepared.job.id,
                "parser_dispatch_unavailable",
                "Parser dispatch is temporarily unavailable.",
            )
            if not marked_unavailable:
                current = self._get_match(match_id)
                if current is not None:
                    return current
            raise SteamDemoImportFailedError(
                "parser_dispatch_unavailable",
                "Parser dispatch is temporarily unavailable.",
            ) from None
        self._mark_linked_parser_dispatched(
            match_id,
            prepared.demo.id,
            prepared.job.id,
        )

        current = self._get_match(match_id)
        if current is None:
            raise SteamDemoImportNotFoundError("Steam match was not found")
        return current

    def _get_match(self, match_id: str) -> SteamMatch | None:
        return (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
            )
            .one_or_none()
        )

    def _resume_linked_demo(self, match: SteamMatch) -> SteamMatch:
        if match.status == "ready":
            return match
        demo = (
            self.db.query(Demo)
            .filter(
                Demo.id == match.demo_id,
                Demo.owner_id == self.owner_id,
            )
            .one_or_none()
        )
        job = (
            self.db.query(DemoJob)
            .filter(
                DemoJob.demo_id == match.demo_id,
                DemoJob.job_type == "real_parse",
            )
            .order_by(DemoJob.created_at.desc(), DemoJob.id.desc())
            .first()
        )
        if demo is None or job is None:
            raise SteamDemoImportFailedError(
                "parser_dispatch_state_invalid",
                "Parser dispatch could not be retried safely.",
            )
        if job.status != "queued":
            return self._get_match(match.id) or match
        if not steam_match_parser_dispatch_retryable(
            self.db,
            match,
            now=self.clock(),
            latest_job=job,
        ):
            return self._get_match(match.id) or match
        service = DemoService(
            self.db,
            owner_id=self.owner_id,
            artifact_store=self.artifact_store,
        )
        try:
            service.dispatch_parse_job(
                job_id=job.id,
                demo_id=demo.id,
                redis_client=self.redis_client,
            )
        except DemoDispatchError:
            marked_unavailable = self._mark_linked_demo_unavailable(
                match.id,
                demo.id,
                job.id,
                "parser_dispatch_unavailable",
                "Parser dispatch is temporarily unavailable.",
            )
            if not marked_unavailable:
                current = self._get_match(match.id)
                if current is not None:
                    return current
                raise SteamDemoImportNotFoundError(
                    "Steam match was removed while importing"
                ) from None
            raise SteamDemoImportFailedError(
                "parser_dispatch_unavailable",
                "Parser dispatch is temporarily unavailable.",
            ) from None
        self._mark_linked_parser_dispatched(match.id, demo.id, job.id)
        self._mark_linked_demo_parsing(match.id, demo.id, job.id)
        return self._get_match(match.id) or match

    def _run_match(self, match_id: str, run_id: str) -> SteamMatch:
        match = (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.import_run_id == run_id,
            )
            .one_or_none()
        )
        if match is None:
            raise SteamDemoImportNotFoundError(
                "Steam match was removed while importing"
            )
        return match

    def _set_downloading(self, match_id: str, run_id: str, provider_id: str) -> None:
        updated = (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.import_run_id == run_id,
            )
            .update(
                {
                    SteamMatch.status: "downloading",
                    SteamMatch.provider_id: provider_id,
                    SteamMatch.updated_at: self.clock(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if updated != 1:
            raise SteamDemoImportNotFoundError(
                "Steam match was removed while importing"
            )

    def _finish_unavailable(
        self,
        match_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        self.db.rollback()
        (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.import_run_id == run_id,
            )
            .update(
                {
                    SteamMatch.status: "unavailable",
                    SteamMatch.import_run_id: None,
                    SteamMatch.import_lease_expires_at: None,
                    SteamMatch.import_completed_at: self.clock(),
                    SteamMatch.last_import_error_code: error_code[:64],
                    SteamMatch.last_import_error_message: error_message[:255],
                    SteamMatch.updated_at: self.clock(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()

    def _finish_retryable(
        self,
        match_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        self.db.rollback()
        (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.import_run_id == run_id,
            )
            .update(
                {
                    SteamMatch.status: "unavailable",
                    SteamMatch.import_run_id: None,
                    SteamMatch.import_lease_expires_at: None,
                    SteamMatch.import_completed_at: self.clock(),
                    SteamMatch.last_import_error_code: error_code[:64],
                    SteamMatch.last_import_error_message: error_message[:255],
                    SteamMatch.updated_at: self.clock(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()

    def _mark_linked_demo_unavailable(
        self,
        match_id: str,
        demo_id: str,
        job_id: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        updated = (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.demo_id == demo_id,
                SteamMatch.status != "ready",
                self.db.query(DemoJob.id)
                .filter(
                    DemoJob.id == job_id,
                    DemoJob.demo_id == demo_id,
                    DemoJob.job_type == "real_parse",
                    DemoJob.status == "queued",
                )
                .exists(),
            )
            .update(
                {
                    SteamMatch.status: "unavailable",
                    SteamMatch.last_import_error_code: error_code,
                    SteamMatch.last_import_error_message: error_message,
                    SteamMatch.updated_at: self.clock(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        return updated == 1

    def _mark_linked_demo_parsing(
        self,
        match_id: str,
        demo_id: str,
        job_id: str,
    ) -> bool:
        updated = (
            self.db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == self.owner_id,
                SteamMatch.demo_id == demo_id,
                SteamMatch.status != "ready",
                self.db.query(DemoJob.id)
                .filter(
                    DemoJob.id == job_id,
                    DemoJob.demo_id == demo_id,
                    DemoJob.job_type == "real_parse",
                    DemoJob.status == "queued",
                )
                .exists(),
            )
            .update(
                {
                    SteamMatch.status: "parsing",
                    SteamMatch.last_import_error_code: None,
                    SteamMatch.last_import_error_message: None,
                    SteamMatch.updated_at: self.clock(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        return updated == 1

    def _mark_linked_parser_dispatched(
        self,
        match_id: str,
        demo_id: str,
        job_id: str,
    ) -> None:
        try:
            (
                self.db.query(SteamMatch)
                .filter(
                    SteamMatch.id == match_id,
                    SteamMatch.owner_id == self.owner_id,
                    SteamMatch.demo_id == demo_id,
                    or_(
                        SteamMatch.parser_dispatched_job_id.is_(None),
                        SteamMatch.parser_dispatched_job_id == job_id,
                    ),
                    self.db.query(DemoJob.id)
                    .filter(
                        DemoJob.id == job_id,
                        DemoJob.demo_id == demo_id,
                        DemoJob.job_type == "real_parse",
                        DemoJob.status != "failed",
                    )
                    .exists(),
                )
                .update(
                    {
                        SteamMatch.parser_dispatched_at: self.clock(),
                        SteamMatch.parser_dispatched_job_id: job_id,
                    },
                    synchronize_session=False,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise SteamDemoImportFailedError(
                "parser_dispatch_state_update_failed",
                "Parser dispatch state could not be updated safely.",
            ) from None


def steam_match_import_retryable(
    match: SteamMatch,
    *,
    now: datetime,
) -> bool:
    if (
        match.demo_id is not None
        or match.status not in {"demo_pending", "downloading"}
        or match.import_run_id is None
        or match.import_lease_expires_at is None
    ):
        return False
    return _as_utc(match.import_lease_expires_at) <= _as_utc(now)


def steam_match_parser_dispatch_retryable(
    db: Session,
    match: SteamMatch,
    *,
    now: datetime,
    latest_job: DemoJob | None = None,
) -> bool:
    if (
        match.demo_id is None
        or match.status not in {"parsing", "unavailable"}
    ):
        return False
    job = latest_job
    if job is None:
        job = (
            db.query(DemoJob)
            .filter(
                DemoJob.demo_id == match.demo_id,
                DemoJob.job_type == "real_parse",
            )
            .order_by(DemoJob.created_at.desc(), DemoJob.id.desc())
            .first()
        )
    if job is None or job.status != "queued":
        return False
    if (
        match.parser_dispatched_job_id != job.id
        or match.parser_dispatched_at is None
    ):
        return True
    return (
        _as_utc(now) - _as_utc(match.parser_dispatched_at)
    ).total_seconds() >= PARSER_DISPATCH_RETRY_AFTER_SECONDS


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
