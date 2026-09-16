import hashlib
import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import steam as steam_api
from app.core.config import Settings
from app.core.database import Base
from app.models import Account, Demo, DemoJob, SteamConnection, SteamMatch
from app.services.demo_service import DemoDispatchError, DemoService
from app.services.demo_source_provider import DemoSource, DisabledDemoSourceProvider
from app.services.secure_demo_downloader import DemoDownloadError, DownloadedDemo
from app.services.steam_credentials import SteamCredentialCipher
from app.services.steam_demo_download_limiter import (
    SteamDemoDownloadCapacityError,
)
from app.services.steam_demo_import_service import (
    SteamDemoImportFailedError,
    SteamDemoImportInProgressError,
    SteamDemoImportNotFoundError,
    SteamDemoImportService,
    SteamDemoSourceUnavailableError,
)
from app.services.steam_match_service import SteamMatchService
from app.services.storage import LocalArtifactStore

OWNER_A = "owner_v1_stage3_owner_a"
MATCH_ID = "stage3-match-a"
CONNECTION_ID = "stage3-connection-a"
SHARE_CODE = "CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
TEST_ENCRYPTION_KEY = "Y3MyLWRldi1zdGVhbS1jcmVkZW50aWFsLWtleS12MSE="
DEMO_BYTES = b"HL2DEMO\x00" + (b"stage3-demo-byte" * 8)


class FakeLicensedProvider:
    provider_id = "licensed-test"
    available = True

    def __init__(self) -> None:
        self.share_codes: list[str] = []

    def resolve(self, *, share_code: str) -> DemoSource:
        self.share_codes.append(share_code)
        return DemoSource(
            provider_id=self.provider_id,
            url="https://licensed.example/match.dem?signature=secret",
            filename="steam-match.dem",
        )


class FakeDownloader:
    def __init__(self) -> None:
        self.sources: list[DemoSource] = []

    @contextmanager
    def download(self, source: DemoSource):
        self.sources.append(source)
        stream = io.BytesIO(DEMO_BYTES)
        try:
            yield DownloadedDemo(
                path=Path("/private/scratch/source.dem"),
                stream=stream,
                filename=source.filename,
                content_type="application/octet-stream",
                size_bytes=len(DEMO_BYTES),
                sha256=hashlib.sha256(DEMO_BYTES).hexdigest(),
            )
        finally:
            stream.close()


class InterruptingDownloader:
    @contextmanager
    def download(self, source: DemoSource):
        del source
        raise DemoDownloadError(
            "demo_download_interrupted",
            "Demo download was interrupted.",
        )
        yield  # pragma: no cover


class CallbackDownloader(FakeDownloader):
    def __init__(self, callback: object) -> None:
        super().__init__()
        self.callback = callback

    @contextmanager
    def download(self, source: DemoSource):
        self.sources.append(source)
        self.callback()
        stream = io.BytesIO(DEMO_BYTES)
        try:
            yield DownloadedDemo(
                path=Path("/private/scratch/source.dem"),
                stream=stream,
                filename=source.filename,
                content_type="application/octet-stream",
                size_bytes=len(DEMO_BYTES),
                sha256=hashlib.sha256(DEMO_BYTES).hexdigest(),
            )
        finally:
            stream.close()


class CleanupFailingDownloader(FakeDownloader):
    @contextmanager
    def download(self, source: DemoSource):
        self.sources.append(source)
        stream = io.BytesIO(DEMO_BYTES)
        try:
            yield DownloadedDemo(
                path=Path("/private/scratch/source.dem"),
                stream=stream,
                filename=source.filename,
                content_type="application/octet-stream",
                size_bytes=len(DEMO_BYTES),
                sha256=hashlib.sha256(DEMO_BYTES).hexdigest(),
            )
        finally:
            stream.close()
            raise OSError("private scratch cleanup failed")


class FakeDownloadLimiter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.owners: list[str] = []

    @contextmanager
    def lease(self, owner_id: str):
        self.owners.append(owner_id)
        if self.error is not None:
            raise self.error
        yield


class FakeRedis:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def lpush(self, queue: str, payload: str) -> None:
        self.items.append((queue, payload))


class FailOnceRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def lpush(self, queue: str, payload: str) -> None:
        if not self.failed:
            self.failed = True
            raise ConnectionError("redis unavailable")
        super().lpush(queue, payload)


class CallbackRedis(FakeRedis):
    def __init__(self, callback: object, *, fail_after_callback: bool = False) -> None:
        super().__init__()
        self.callback = callback
        self.fail_after_callback = fail_after_callback

    def lpush(self, queue: str, payload: str) -> None:
        super().lpush(queue, payload)
        self.callback()
        if self.fail_after_callback:
            raise ConnectionError("Redis reply was lost after enqueue")


class SteamDemoImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
        )
        self.settings = Settings(
            auth_mode="test",
            auth_provider="steam",
            steam_web_api_key="a" * 32,
            steam_credential_encryption_key=TEST_ENCRYPTION_KEY,
            steam_credential_encryption_key_version="v1",
        )
        self.cipher = SteamCredentialCipher(self.settings)
        now = datetime.now(UTC)
        encrypted = self.cipher.encrypt(
            SHARE_CODE,
            owner_id=OWNER_A,
            purpose="match-sharing-code",
            record_id=MATCH_ID,
        )
        with self.Session() as db:
            db.add(Account(owner_id=OWNER_A, created_at=now, updated_at=now))
            db.add(
                SteamConnection(
                    id=CONNECTION_ID,
                    owner_id=OWNER_A,
                    steam_id64="76561202255233022",
                    game_auth_code_ciphertext=b"ciphertext-and-tag",
                    game_auth_code_nonce=b"1" * 12,
                    known_code_ciphertext=b"ciphertext-and-tag",
                    known_code_nonce=b"2" * 12,
                    encryption_key_version="v1",
                    status="connected",
                    consecutive_failures=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                SteamMatch(
                    id=MATCH_ID,
                    connection_id=CONNECTION_ID,
                    owner_id=OWNER_A,
                    share_code_hash=self.cipher.fingerprint(SHARE_CODE),
                    share_code_ciphertext=encrypted.ciphertext,
                    share_code_nonce=encrypted.nonce,
                    encryption_key_version=encrypted.key_version,
                    status="discovered",
                    discovered_at=now,
                    updated_at=now,
                )
            )
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_disabled_provider_returns_unavailable_and_preserves_manual_fallback(self) -> None:
        app = FastAPI()
        app.include_router(steam_api.router)

        def override_import_service():
            with self.Session() as db:
                yield SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=DisabledDemoSourceProvider(),
                    cipher=self.cipher,
                )

        def override_match_service():
            with self.Session() as db:
                yield SteamMatchService(
                    db,
                    owner_id=OWNER_A,
                    runtime_settings=self.settings,
                    cipher=self.cipher,
                )

        app.dependency_overrides[steam_api.get_steam_demo_import_service] = (
            override_import_service
        )
        app.dependency_overrides[steam_api.get_steam_match_service] = (
            override_match_service
        )
        app.dependency_overrides[steam_api.require_trusted_origin] = lambda: None
        client = TestClient(app)

        response = client.post(f"/steam/matches/{MATCH_ID}/import")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "demo_source_unavailable",
                "message": "Automatic Demo source is unavailable. Upload the .dem file manually.",
                "manualUploadSupported": True,
            },
        )
        matches = client.get("/steam/matches").json()
        self.assertEqual(matches[0]["status"], "unavailable")
        self.assertEqual(matches[0]["import_error_code"], "provider_not_configured")
        self.assertIsNone(matches[0]["demo_id"])
        self.assertIsNone(matches[0]["map_name"])
        self.assertIsNone(matches[0]["players"])
        connection = client.get("/steam/connection").json()
        self.assertFalse(connection["demo_import_available"])
        self.assertEqual(connection["demo_source_provider"], "disabled")
        self.assertTrue(connection["manual_upload_supported"])

    def test_default_disabled_dependency_does_not_open_redis_or_network(self) -> None:
        with self.Session() as db, patch(
            "app.api.steam.get_redis_client",
            side_effect=AssertionError("disabled provider must not open Redis"),
        ):
            service = steam_api.get_steam_demo_import_service(
                db=db,
                owner_id=OWNER_A,
            )

        self.assertEqual(service.provider.provider_id, "disabled")
        self.assertFalse(service.provider.available)
        self.assertIsNone(service.downloader)
        self.assertIsNone(service.download_limiter)

    def test_disabled_provider_fails_before_decrypting_match_credentials(self) -> None:
        class ExplodingCipher:
            def decrypt(self, **_kwargs: object) -> str:
                raise AssertionError("disabled provider must not decrypt")

        with self.Session() as db:
            service = SteamDemoImportService(
                db,
                owner_id=OWNER_A,
                provider=DisabledDemoSourceProvider(),
                cipher=ExplodingCipher(),
            )
            with self.assertRaises(SteamDemoSourceUnavailableError):
                service.import_match(MATCH_ID)

    def test_credential_decryption_failure_is_safe_and_stops_before_provider(self) -> None:
        provider = FakeLicensedProvider()
        with self.Session() as db:
            match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            match.share_code_ciphertext = b"corrupted-ciphertext"
            db.commit()
            service = SteamDemoImportService(
                db,
                owner_id=OWNER_A,
                provider=provider,
                cipher=self.cipher,
            )

            with self.assertRaises(SteamDemoImportFailedError) as raised:
                service.import_match(MATCH_ID)

            self.assertEqual(raised.exception.code, "credential_decryption_failed")
            self.assertNotIn(SHARE_CODE, str(raised.exception))
            self.assertEqual(provider.share_codes, [])
            db.expire_all()
            stored = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            self.assertEqual(stored.status, "unavailable")
            self.assertEqual(
                stored.last_import_error_code,
                "credential_decryption_failed",
            )

    def test_licensed_source_uses_intake_and_duplicate_import_reuses_one_demo(self) -> None:
        provider = FakeLicensedProvider()
        downloader = FakeDownloader()
        limiter = FakeDownloadLimiter()
        redis = FakeRedis()
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=provider,
                    cipher=self.cipher,
                    downloader=downloader,
                    download_limiter=limiter,
                    artifact_store=store,
                    redis_client=redis,
                    runtime_settings=self.settings,
                )

                first = service.import_match(MATCH_ID)
                second = service.import_match(MATCH_ID)

                self.assertEqual(first.demo_id, second.demo_id)
                self.assertEqual(second.status, "parsing")
                self.assertIsNone(second.last_import_error_code)
                self.assertEqual(provider.share_codes, [SHARE_CODE])
                self.assertEqual(len(downloader.sources), 1)
                self.assertEqual(limiter.owners, [OWNER_A])
                self.assertEqual(db.query(Demo).count(), 1)
                self.assertEqual(db.query(DemoJob).count(), 1)
                demo = db.query(Demo).one()
                job = db.query(DemoJob).one()
                self.assertEqual(demo.owner_id, OWNER_A)
                self.assertEqual(demo.id, first.demo_id)
                self.assertEqual(demo.status, "queued")
                self.assertEqual(job.demo_id, demo.id)
                self.assertEqual(job.job_type, "real_parse")
                self.assertTrue(demo.source_storage_key.startswith("artifact://v1/accepted/"))
                artifact = store.head(demo.source_storage_key)
                self.assertIsNotNone(artifact)
                self.assertEqual(artifact.owner_id, OWNER_A)
                self.assertEqual(artifact.demo_id, demo.id)
                self.assertEqual(artifact.sha256, hashlib.sha256(DEMO_BYTES).hexdigest())

        self.assertEqual(len(redis.items), 1)
        queue, payload = redis.items[0]
        self.assertEqual(queue, self.settings.redis_queue_name)
        self.assertEqual(
            json.loads(payload),
            {"job_id": job.id, "demo_id": first.demo_id},
        )

    def test_download_interruption_is_safely_mapped_and_marks_match_unavailable(self) -> None:
        app = FastAPI()
        app.include_router(steam_api.router)

        def override_import_service():
            with self.Session() as db:
                yield SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=InterruptingDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    runtime_settings=self.settings,
                )

        def override_match_service():
            with self.Session() as db:
                yield SteamMatchService(
                    db,
                    owner_id=OWNER_A,
                    runtime_settings=self.settings,
                    cipher=self.cipher,
                )

        app.dependency_overrides[steam_api.get_steam_demo_import_service] = (
            override_import_service
        )
        app.dependency_overrides[steam_api.get_steam_match_service] = (
            override_match_service
        )
        app.dependency_overrides[steam_api.require_trusted_origin] = lambda: None
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(f"/steam/matches/{MATCH_ID}/import")

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "demo_download_interrupted")
        self.assertNotIn("signature", response.text)
        self.assertNotIn(SHARE_CODE, response.text)
        match = client.get("/steam/matches").json()[0]
        self.assertEqual(match["status"], "unavailable")
        self.assertEqual(match["import_error_code"], "demo_download_interrupted")

    def test_downloader_cleanup_failure_happens_before_database_binding(self) -> None:
        redis = FakeRedis()
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=CleanupFailingDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=redis,
                    runtime_settings=self.settings,
                )

                with self.assertRaises(SteamDemoImportFailedError) as raised:
                    service.import_match(MATCH_ID)

                self.assertEqual(raised.exception.code, "demo_import_failed")
                self.assertEqual(db.query(Demo).count(), 0)
                self.assertEqual(db.query(DemoJob).count(), 0)
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                self.assertEqual(match.status, "unavailable")
                self.assertIsNone(match.demo_id)
                self.assertEqual(match.last_import_error_code, "demo_import_failed")
            self.assertEqual(
                [path for path in Path(artifact_root).rglob("*") if path.is_file()],
                [],
            )
        self.assertEqual(redis.items, [])

    def test_capacity_limit_fails_closed_before_download_and_remains_retryable(self) -> None:
        app = FastAPI()
        app.include_router(steam_api.router)
        downloader = FakeDownloader()

        def override_import_service():
            with self.Session() as db:
                yield SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=downloader,
                    download_limiter=FakeDownloadLimiter(
                        SteamDemoDownloadCapacityError("capacity full")
                    ),
                    runtime_settings=self.settings,
                )

        def override_match_service():
            with self.Session() as db:
                yield SteamMatchService(
                    db,
                    owner_id=OWNER_A,
                    runtime_settings=self.settings,
                    cipher=self.cipher,
                )

        app.dependency_overrides[steam_api.get_steam_demo_import_service] = (
            override_import_service
        )
        app.dependency_overrides[steam_api.get_steam_match_service] = (
            override_match_service
        )
        app.dependency_overrides[steam_api.require_trusted_origin] = lambda: None
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(f"/steam/matches/{MATCH_ID}/import")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "demo_download_capacity_full")
        self.assertEqual(downloader.sources, [])
        match = client.get("/steam/matches").json()[0]
        self.assertEqual(match["status"], "unavailable")
        self.assertEqual(match["import_error_code"], "demo_download_capacity_full")

    def test_dispatch_retry_reuses_bound_demo_and_does_not_redownload(self) -> None:
        provider = FakeLicensedProvider()
        downloader = FakeDownloader()
        redis = FailOnceRedis()
        with tempfile.TemporaryDirectory() as artifact_root:
            with self.Session() as db:
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=provider,
                    cipher=self.cipher,
                    downloader=downloader,
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=LocalArtifactStore(Path(artifact_root)),
                    redis_client=redis,
                    runtime_settings=self.settings,
                )

                with self.assertRaises(SteamDemoImportFailedError) as first_error:
                    service.import_match(MATCH_ID)
                self.assertEqual(
                    first_error.exception.code,
                    "parser_dispatch_unavailable",
                )
                first = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                bound_demo_id = first.demo_id
                self.assertIsNotNone(bound_demo_id)
                self.assertEqual(first.status, "unavailable")

                retried = service.import_match(MATCH_ID)

                self.assertEqual(retried.demo_id, bound_demo_id)
                self.assertEqual(retried.status, "parsing")
                self.assertIsNone(retried.last_import_error_code)
                self.assertEqual(len(provider.share_codes), 1)
                self.assertEqual(len(downloader.sources), 1)
                self.assertEqual(db.query(Demo).count(), 1)
                self.assertEqual(db.query(DemoJob).count(), 1)
                self.assertEqual(len(redis.items), 1)

    def test_stale_queued_marker_recovers_lost_redis_item_without_redownload(self) -> None:
        provider = FakeLicensedProvider()
        downloader = FakeDownloader()
        redis = FakeRedis()
        with tempfile.TemporaryDirectory() as artifact_root:
            with self.Session() as db:
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=provider,
                    cipher=self.cipher,
                    downloader=downloader,
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=LocalArtifactStore(Path(artifact_root)),
                    redis_client=redis,
                    runtime_settings=self.settings,
                )
                imported = service.import_match(MATCH_ID)
                job = db.query(DemoJob).one()
                self.assertEqual(imported.parser_dispatched_job_id, job.id)
                service.import_match(MATCH_ID)
                self.assertEqual(len(redis.items), 1)

                redis.items.clear()
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                match.status = "parsing"
                match.parser_dispatched_at = datetime(
                    2000,
                    1,
                    1,
                    tzinfo=UTC,
                )
                self.assertEqual(match.parser_dispatched_job_id, job.id)
                db.commit()

                recovered = service.import_match(MATCH_ID)

                self.assertEqual(recovered.status, "parsing")
                self.assertEqual(recovered.parser_dispatched_job_id, job.id)
                self.assertEqual(len(redis.items), 1)
                self.assertEqual(len(provider.share_codes), 1)
                self.assertEqual(len(downloader.sources), 1)
                self.assertEqual(db.query(Demo).count(), 1)
                self.assertEqual(db.query(DemoJob).count(), 1)

    def test_lost_redis_reply_cannot_downgrade_fast_parser_completion(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            def complete_before_redis_reply() -> None:
                with self.Session() as callback_db:
                    job = callback_db.query(DemoJob).one()
                    job.status = "completed"
                    match = (
                        callback_db.query(SteamMatch)
                        .filter(SteamMatch.id == MATCH_ID)
                        .one()
                    )
                    match.status = "ready"
                    match.last_import_error_code = None
                    match.last_import_error_message = None
                    callback_db.commit()

            redis = CallbackRedis(
                complete_before_redis_reply,
                fail_after_callback=True,
            )
            with self.Session() as db:
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=FakeDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=LocalArtifactStore(Path(artifact_root)),
                    redis_client=redis,
                    runtime_settings=self.settings,
                )

                completed = service.import_match(MATCH_ID)

                self.assertEqual(completed.status, "ready")
                self.assertIsNone(completed.last_import_error_code)
                self.assertEqual(db.query(Demo).count(), 1)
                self.assertEqual(db.query(DemoJob).one().status, "completed")

    def test_lost_requeue_reply_returns_fast_parser_completion(self) -> None:
        provider = FakeLicensedProvider()
        downloader = FakeDownloader()
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=provider,
                    cipher=self.cipher,
                    downloader=downloader,
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                )
                imported = service.import_match(MATCH_ID)
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                match.parser_dispatched_at = datetime(
                    2000,
                    1,
                    1,
                    tzinfo=UTC,
                )
                db.commit()

                def complete_before_requeue_reply() -> None:
                    with self.Session() as callback_db:
                        callback_job = callback_db.query(DemoJob).one()
                        callback_job.status = "completed"
                        callback_match = (
                            callback_db.query(SteamMatch)
                            .filter(SteamMatch.id == MATCH_ID)
                            .one()
                        )
                        callback_match.status = "ready"
                        callback_match.last_import_error_code = None
                        callback_match.last_import_error_message = None
                        callback_db.commit()

                service.redis_client = CallbackRedis(
                    complete_before_requeue_reply,
                    fail_after_callback=True,
                )
                completed = service.import_match(MATCH_ID)

                self.assertEqual(completed.demo_id, imported.demo_id)
                self.assertEqual(completed.status, "ready")
                self.assertIsNone(completed.last_import_error_code)
                self.assertEqual(len(provider.share_codes), 1)
                self.assertEqual(len(downloader.sources), 1)

    def test_failed_parser_retry_dispatch_is_recoverable_from_steam_import(self) -> None:
        provider = FakeLicensedProvider()
        downloader = FakeDownloader()
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                initial = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=provider,
                    cipher=self.cipher,
                    downloader=downloader,
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                ).import_match(MATCH_ID)
                demo = db.query(Demo).filter(Demo.id == initial.demo_id).one()
                original_job = db.query(DemoJob).one()
                original_job.status = "failed"
                demo.status = "failed"
                demo.error_message = "Parser failed"
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                match.status = "unavailable"
                match.last_import_error_code = "parser_failed"
                db.commit()

                retry_redis = FailOnceRedis()
                with patch(
                    "app.services.demo_service.get_redis_client",
                    return_value=retry_redis,
                ):
                    with self.assertRaises(DemoDispatchError):
                        DemoService(
                            db,
                            owner_id=OWNER_A,
                            artifact_store=store,
                        ).retry_parse_job(demo)

                db.expire_all()
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                jobs = (
                    db.query(DemoJob)
                    .order_by(DemoJob.created_at.asc(), DemoJob.id.asc())
                    .all()
                )
                retry_job = next(job for job in jobs if job.id != original_job.id)
                self.assertEqual(retry_job.status, "queued")
                self.assertEqual(match.status, "unavailable")
                self.assertEqual(
                    match.last_import_error_code,
                    "parser_dispatch_unavailable",
                )
                self.assertIsNone(match.parser_dispatched_job_id)

                recovery_redis = FakeRedis()
                recovered = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    artifact_store=store,
                    redis_client=recovery_redis,
                    runtime_settings=self.settings,
                ).import_match(MATCH_ID)

                self.assertEqual(recovered.status, "parsing")
                self.assertEqual(recovered.parser_dispatched_job_id, retry_job.id)
                self.assertEqual(len(recovery_redis.items), 1)
                self.assertEqual(db.query(Demo).count(), 1)
                self.assertEqual(db.query(DemoJob).count(), 2)

    def test_lost_retry_reply_cannot_downgrade_worker_claim(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                imported = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=FakeDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                ).import_match(MATCH_ID)
                demo = db.query(Demo).filter(Demo.id == imported.demo_id).one()
                original_job = db.query(DemoJob).one()
                original_job.status = "failed"
                demo.status = "failed"
                demo.error_message = "Parser failed"
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                match.status = "unavailable"
                match.last_import_error_code = "parser_failed"
                db.commit()

                def claim_retry_before_reply() -> None:
                    with self.Session() as callback_db:
                        callback_demo = (
                            callback_db.query(Demo)
                            .filter(Demo.id == imported.demo_id)
                            .one()
                        )
                        retry_job = (
                            callback_db.query(DemoJob)
                            .filter(
                                DemoJob.demo_id == callback_demo.id,
                                DemoJob.status == "queued",
                            )
                            .order_by(
                                DemoJob.created_at.desc(),
                                DemoJob.id.desc(),
                            )
                            .first()
                        )
                        self.assertIsNotNone(retry_job)
                        claimed = DemoService.for_internal(
                            callback_db,
                            artifact_store=store,
                        ).claim_parse_job(callback_demo, retry_job)
                        self.assertTrue(claimed)

                redis = CallbackRedis(
                    claim_retry_before_reply,
                    fail_after_callback=True,
                )
                with patch(
                    "app.services.demo_service.get_redis_client",
                    return_value=redis,
                ):
                    result = DemoService(
                        db,
                        owner_id=OWNER_A,
                        artifact_store=store,
                    ).retry_parse_job(demo)

                db.expire_all()
                retry_job = (
                    db.query(DemoJob)
                    .filter(DemoJob.id != original_job.id)
                    .one()
                )
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                self.assertEqual(retry_job.status, "processing")
                self.assertEqual(match.status, "parsing")
                self.assertEqual(match.parser_dispatched_job_id, retry_job.id)
                self.assertIsNone(match.last_import_error_code)
                self.assertEqual(result.status, "parsing")

    def test_delayed_old_retry_reply_cannot_mark_newer_queued_job_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                imported = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=FakeDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                ).import_match(MATCH_ID)
                demo = db.query(Demo).filter(Demo.id == imported.demo_id).one()
                original_job = db.query(DemoJob).one()
                original_job.status = "failed"
                demo.status = "failed"
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                match.status = "unavailable"
                match.last_import_error_code = "parser_failed"
                db.commit()

                def supersede_before_old_reply() -> None:
                    with self.Session() as callback_db:
                        old_retry = (
                            callback_db.query(DemoJob)
                            .filter(
                                DemoJob.demo_id == demo.id,
                                DemoJob.id != original_job.id,
                                DemoJob.status == "queued",
                            )
                            .one()
                        )
                        old_retry.status = "failed"
                        newer_job = DemoJob(
                            id="stage3-newer-retry-job",
                            demo_id=demo.id,
                            job_type="real_parse",
                            status="queued",
                            attempts=0,
                            metadata_json=old_retry.metadata_json,
                            created_at=datetime(
                                2099,
                                1,
                                1,
                                tzinfo=UTC,
                            ),
                        )
                        callback_db.add(newer_job)
                        callback_demo = (
                            callback_db.query(Demo)
                            .filter(Demo.id == demo.id)
                            .one()
                        )
                        callback_demo.status = "queued"
                        callback_match = (
                            callback_db.query(SteamMatch)
                            .filter(SteamMatch.id == MATCH_ID)
                            .one()
                        )
                        callback_match.status = "parsing"
                        callback_match.parser_dispatched_at = None
                        callback_match.parser_dispatched_job_id = None
                        callback_match.last_import_error_code = None
                        callback_match.last_import_error_message = None
                        callback_db.commit()

                with patch(
                    "app.services.demo_service.get_redis_client",
                    return_value=CallbackRedis(supersede_before_old_reply),
                ):
                    DemoService(
                        db,
                        owner_id=OWNER_A,
                        artifact_store=store,
                    ).retry_parse_job(demo)

                db.expire_all()
                latest_job = (
                    db.query(DemoJob)
                    .filter(DemoJob.demo_id == demo.id)
                    .order_by(DemoJob.created_at.desc(), DemoJob.id.desc())
                    .first()
                )
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                self.assertEqual(latest_job.id, "stage3-newer-retry-job")
                self.assertEqual(latest_job.status, "queued")
                self.assertIsNone(match.parser_dispatched_job_id)
                self.assertIsNone(match.parser_dispatched_at)

    def test_owner_boundary_and_active_lease_stop_before_provider(self) -> None:
        provider = FakeLicensedProvider()
        with self.Session() as db:
            owner_b_service = SteamDemoImportService(
                db,
                owner_id="owner_v1_stage3_owner_b",
                provider=provider,
                cipher=self.cipher,
            )
            with self.assertRaises(SteamDemoImportNotFoundError):
                owner_b_service.import_match(MATCH_ID)
            self.assertEqual(provider.share_codes, [])

            match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            match.import_run_id = "active-run"
            match.import_lease_expires_at = datetime(2099, 1, 1, tzinfo=UTC)
            db.commit()

            owner_a_service = SteamDemoImportService(
                db,
                owner_id=OWNER_A,
                provider=provider,
                cipher=self.cipher,
            )
            with self.assertRaises(SteamDemoImportInProgressError):
                owner_a_service.import_match(MATCH_ID)
            self.assertEqual(provider.share_codes, [])

    def test_expired_import_lease_can_be_reclaimed_without_exposing_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            with self.Session() as db:
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                match.status = "downloading"
                match.import_run_id = "abandoned-private-run-id"
                match.import_lease_expires_at = datetime(
                    2000,
                    1,
                    1,
                    tzinfo=UTC,
                )
                db.commit()

                imported = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=FakeDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=LocalArtifactStore(Path(artifact_root)),
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                ).import_match(MATCH_ID)

                self.assertEqual(imported.status, "parsing")
                self.assertIsNotNone(imported.demo_id)
                self.assertIsNone(imported.import_run_id)
                self.assertIsNone(imported.import_lease_expires_at)

    def test_stale_run_cannot_overwrite_newer_import_state(self) -> None:
        with self.Session() as db:
            match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            match.import_run_id = "newer-run"
            match.status = "downloading"
            db.commit()
            service = SteamDemoImportService(db, owner_id=OWNER_A, cipher=self.cipher)

            service._finish_unavailable(
                MATCH_ID,
                "stale-run",
                "stale_error",
                "A stale worker failed.",
            )

            db.expire_all()
            current = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            self.assertEqual(current.import_run_id, "newer-run")
            self.assertEqual(current.status, "downloading")
            self.assertIsNone(current.last_import_error_code)

    def test_connection_removal_during_download_cannot_bind_orphan_demo(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))
            with self.Session() as db:
                def remove_connection() -> None:
                    connection = (
                        db.query(SteamConnection)
                        .filter(SteamConnection.id == CONNECTION_ID)
                        .one()
                    )
                    db.delete(connection)
                    db.commit()

                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=CallbackDownloader(remove_connection),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                )

                with self.assertRaises(SteamDemoImportNotFoundError):
                    service.import_match(MATCH_ID)

                self.assertEqual(db.query(Demo).count(), 0)
                self.assertEqual(db.query(DemoJob).count(), 0)
                self.assertEqual(db.query(SteamMatch).count(), 0)
            self.assertEqual(
                [path for path in Path(artifact_root).rglob("*") if path.is_file()],
                [],
            )

    def test_newer_import_run_fences_stale_final_binding(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            store = LocalArtifactStore(Path(artifact_root))

            def supersede_run() -> None:
                with self.Session() as callback_db:
                    match = (
                        callback_db.query(SteamMatch)
                        .filter(SteamMatch.id == MATCH_ID)
                        .one()
                    )
                    match.import_run_id = "newer-import-run"
                    match.import_lease_expires_at = datetime(
                        2099,
                        1,
                        1,
                        tzinfo=UTC,
                    )
                    match.status = "downloading"
                    callback_db.commit()

            with self.Session() as db:
                redis = FakeRedis()
                service = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=CallbackDownloader(supersede_run),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=store,
                    redis_client=redis,
                    runtime_settings=self.settings,
                )

                with self.assertRaises(SteamDemoImportInProgressError):
                    service.import_match(MATCH_ID)

                db.expire_all()
                match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
                self.assertEqual(match.import_run_id, "newer-import-run")
                self.assertEqual(match.status, "downloading")
                self.assertIsNone(match.demo_id)
                self.assertEqual(db.query(Demo).count(), 0)
                self.assertEqual(db.query(DemoJob).count(), 0)
                self.assertEqual(redis.items, [])
            self.assertEqual(
                [path for path in Path(artifact_root).rglob("*") if path.is_file()],
                [],
            )

    def test_match_api_exposes_only_ready_parser_summary_and_safe_player_names(self) -> None:
        app = FastAPI()
        app.include_router(steam_api.router)

        def override_match_service():
            with self.Session() as db:
                yield SteamMatchService(
                    db,
                    owner_id=OWNER_A,
                    runtime_settings=self.settings,
                    cipher=self.cipher,
                )

        app.dependency_overrides[steam_api.get_steam_match_service] = (
            override_match_service
        )
        client = TestClient(app)
        with self.Session() as db:
            match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            match.status = "downloading"
            match.import_run_id = "expired-import-run"
            match.import_lease_expires_at = datetime(
                2000,
                1,
                1,
                tzinfo=UTC,
            )
            db.commit()

        expired = client.get("/steam/matches").json()[0]
        self.assertTrue(expired["import_retryable"])
        self.assertNotIn("import_run_id", expired)
        self.assertNotIn("import_lease_expires_at", expired)

        with self.Session() as db:
            now = datetime.now(UTC)
            db.add(
                Demo(
                    id="stage3-demo-ready",
                    owner_id=OWNER_A,
                    legacy_user_id=OWNER_A,
                    name="Imported match",
                    original_filename="match.dem",
                    map_name="unknown",
                    tick_rate=64,
                    round_count=0,
                    coaching_event_count=0,
                    status="queued",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                DemoJob(
                    id="stage3-job-ready",
                    demo_id="stage3-demo-ready",
                    job_type="real_parse",
                    status="queued",
                    attempts=0,
                    created_at=now,
                )
            )
            match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            match.demo_id = "stage3-demo-ready"
            match.status = "parsing"
            match.import_run_id = None
            match.import_lease_expires_at = None
            match.provider_id = "licensed-test"
            match.map_name = "de_mirage"
            match.duration_seconds = 2120
            match.ct_round_wins = 13
            match.t_round_wins = 11
            match.players_json = json.dumps(
                [
                    {"id": "player-secret-id", "name": "Alice", "side": "CT"},
                    {"id": "p2", "name": "Bob", "side": "T"},
                ]
            )
            db.commit()

        parsing = client.get("/steam/matches").json()[0]
        self.assertIsNone(parsing["map_name"])
        self.assertIsNone(parsing["duration_seconds"])
        self.assertIsNone(parsing["players"])
        self.assertFalse(parsing["import_retryable"])
        self.assertTrue(parsing["parser_dispatch_pending"])

        with self.Session() as db:
            match = db.query(SteamMatch).filter(SteamMatch.id == MATCH_ID).one()
            match.status = "ready"
            db.commit()

        ready = client.get("/steam/matches").json()[0]
        self.assertEqual(ready["map_name"], "de_mirage")
        self.assertEqual(ready["duration_seconds"], 2120)
        self.assertEqual(ready["ct_round_wins"], 13)
        self.assertEqual(ready["t_round_wins"], 11)
        self.assertEqual(ready["players"], ["Alice", "Bob"])
        self.assertFalse(ready["parser_dispatch_pending"])
        self.assertNotIn("player-secret-id", json.dumps(ready))

    def test_disconnect_removes_discovery_credentials_but_retains_bound_demo(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            with self.Session() as db:
                imported = SteamDemoImportService(
                    db,
                    owner_id=OWNER_A,
                    provider=FakeLicensedProvider(),
                    cipher=self.cipher,
                    downloader=FakeDownloader(),
                    download_limiter=FakeDownloadLimiter(),
                    artifact_store=LocalArtifactStore(Path(artifact_root)),
                    redis_client=FakeRedis(),
                    runtime_settings=self.settings,
                ).import_match(MATCH_ID)
                demo_id = imported.demo_id
                self.assertIsNotNone(demo_id)

                deleted = SteamMatchService(
                    db,
                    owner_id=OWNER_A,
                    runtime_settings=self.settings,
                    cipher=self.cipher,
                ).delete_connection()

                self.assertTrue(deleted)
                self.assertEqual(db.query(SteamConnection).count(), 0)
                self.assertEqual(db.query(SteamMatch).count(), 0)
                retained = db.query(Demo).filter(Demo.id == demo_id).one()
                self.assertEqual(retained.owner_id, OWNER_A)
                self.assertIsNotNone(retained.source_storage_key)


if __name__ == "__main__":
    unittest.main()
