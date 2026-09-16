import json
import logging
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import steam as steam_api
from app.core.access_log import suppress_outbound_http_request_logging
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings
from app.core.database import Base, get_db
from app.models import Account, ExternalIdentity, SteamConnection
from app.services.auth_service import get_auth_service
from app.services.steam_credentials import SteamCredentialCipher
from app.services.steam_match_service import (
    STEAM_MATCH_HISTORY_ENDPOINT,
    SteamAuthorizationRequiredError,
    SteamConnectionConflictError,
    SteamIdentityRequiredError,
    SteamMatchService,
    SteamSyncInProgressError,
    SteamSyncRetryError,
    SteamUpstreamProtocolError,
)
from app.services.steam_sync_rate_limit import (
    SteamSyncRateLimitError,
    SteamSyncRateLimitUnavailableError,
)

OWNER_A = "owner_v1_stage2_owner_a"
OWNER_B = "owner_v1_stage2_owner_b"
# Synthetic bit-layout fixtures near the uint32 account-id ceiling. They were
# not collected from a user or copied from a Steam profile.
STEAM_A = "76561202255233022"
STEAM_B = "76561202255233021"
GAME_AUTH_CODE = "AAAA-AAAAA-AAAA"
INITIAL_CODE = "CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
NEXT_CODE = "CSGO-FFFFF-GGGGG-HHHHH-IIIII-JJJJJ"
TEST_ENCRYPTION_KEY = "Y3MyLWRldi1zdGVhbS1jcmVkZW50aWFsLWtleS12MSE="


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.content = (
            b"invalid-json"
            if isinstance(payload, Exception)
            else json.dumps(payload).encode("utf-8")
        )

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def iter_bytes(self, *, chunk_size: int = 4096):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


class SequenceHttpClient:
    def __init__(self, *responses: object):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected Steam Web API request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, FakeResponse)
        return response

    def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        if method != "GET":
            raise AssertionError(f"Unexpected Steam Web API method: {method}")
        return self.get(url, **kwargs)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class BarrierCipher(SteamCredentialCipher):
    def __init__(self, settings: Settings, barrier: threading.Barrier):
        super().__init__(settings)
        self.barrier = barrier

    def encrypt(self, value: str, *, owner_id: str, purpose: str, record_id: str):
        if purpose == "known-match-code":
            self.barrier.wait(timeout=5)
        return super().encrypt(
            value,
            owner_id=owner_id,
            purpose=purpose,
            record_id=record_id,
        )


class BarrierClaimSteamMatchService(SteamMatchService):
    def __init__(self, *args: object, claim_barrier: threading.Barrier, **kwargs: object):
        super().__init__(*args, **kwargs)
        self.claim_barrier = claim_barrier

    def _claim_sync_run(
        self,
        connection_id: str,
        run_id: str,
        now: datetime,
    ) -> SteamConnection:
        self.claim_barrier.wait(timeout=5)
        return super()._claim_sync_run(connection_id, run_id, now)


class BlockingHttpClient:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.calls = 0

    def stream(self, method: str, _url: str, **_kwargs: object) -> FakeResponse:
        if method != "GET":
            raise AssertionError(f"Unexpected Steam Web API method: {method}")
        with self.lock:
            self.calls += 1
            self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("Timed out waiting to release Steam HTTP request")
        return caught_up_response()


class RaisingSyncService:
    def __init__(self, error: Exception):
        self.error = error

    def sync_now(self):
        raise self.error


class RecordingSyncLimiter:
    def __init__(self, *, rejection: Exception | None = None):
        self.rejection = rejection
        self.enforced_owners: list[str] = []
        self.publisher_blocks: list[int] = []

    def enforce(self, owner_id: str) -> None:
        self.enforced_owners.append(owner_id)
        if self.rejection is not None:
            raise self.rejection

    def block_publisher(self, retry_after_seconds: int) -> None:
        self.publisher_blocks.append(retry_after_seconds)


class SteamMatchSyncTest(unittest.TestCase):
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
            steam_sync_max_matches=20,
            steam_sync_timeout_seconds=5.0,
            steam_sync_retry_base_seconds=30,
            steam_sync_retry_max_seconds=3600,
            steam_scheduled_sync_enabled=False,
        )
        self.clock = MutableClock()
        with self.Session() as db:
            self._add_account(db, OWNER_A, STEAM_A)
            self._add_account(db, OWNER_B, STEAM_B)
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_200_then_202_saves_match_and_advances_encrypted_cursor(self) -> None:
        http = SequenceHttpClient(
            next_code_response(NEXT_CODE),
            caught_up_response(),
        )
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            connection = service.save_credentials(
                game_auth_code=GAME_AUTH_CODE,
                initial_match_sharing_code=INITIAL_CODE,
            )
            outcome = service.sync_now()

            self.assertEqual(outcome.discovered_count, 1)
            self.assertTrue(outcome.caught_up)
            self.assertFalse(outcome.limit_reached)
            self.assertEqual(service.get_connection().status, "caught_up")
            matches = service.list_matches()
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].status, "discovered")
            self.assertNotIn(NEXT_CODE.encode(), matches[0].share_code_ciphertext)

            cipher = SteamCredentialCipher(self.settings)
            stored_cursor = cipher.decrypt(
                ciphertext=service.get_connection().known_code_ciphertext,
                nonce=service.get_connection().known_code_nonce,
                key_version=service.get_connection().encryption_key_version,
                owner_id=OWNER_A,
                purpose="known-match-code",
                record_id=connection.id,
            )
            self.assertEqual(stored_cursor, NEXT_CODE)

        self.assertEqual(len(http.calls), 2)
        url, request = http.calls[0]
        self.assertEqual(url, STEAM_MATCH_HISTORY_ENDPOINT)
        self.assertEqual(
            set(request["params"]),
            {"steamid", "steamidkey", "knowncode"},
        )
        self.assertNotIn("key", request["params"])
        self.assertEqual(request["headers"]["X-WebAPI-Key"], "a" * 32)
        self.assertFalse(request["follow_redirects"])
        self.assertEqual(request["timeout"], 5.0)

    def test_discovery_and_cursor_remain_committed_when_later_request_retries(self) -> None:
        http = SequenceHttpClient(
            next_code_response(NEXT_CODE),
            FakeResponse(503, {}),
        )
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            connection = self._connect(service)

            with self.assertRaises(SteamSyncRetryError):
                service.sync_now()

            self.assertEqual(len(service.list_matches()), 1)
            self.assertEqual(service.get_connection().status, "retry_wait")
            stored_cursor = service.cipher.decrypt(
                ciphertext=service.get_connection().known_code_ciphertext,
                nonce=service.get_connection().known_code_nonce,
                key_version=service.get_connection().encryption_key_version,
                owner_id=OWNER_A,
                purpose="known-match-code",
                record_id=connection.id,
            )
            self.assertEqual(stored_cursor, NEXT_CODE)

    def test_first_sync_stops_after_twenty_matches(self) -> None:
        responses = [next_code_response(numbered_code(index)) for index in range(20)]
        http = SequenceHttpClient(*responses, caught_up_response())
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            self._connect(service)
            outcome = service.sync_now()

            self.assertEqual(outcome.discovered_count, 20)
            self.assertFalse(outcome.caught_up)
            self.assertTrue(outcome.limit_reached)
            self.assertEqual(outcome.status, "connected")
            self.assertEqual(len(service.list_matches(limit=100)), 20)
        self.assertEqual(len(http.calls), 20)
        self.assertEqual(len(http.responses), 1)

    def test_202_caught_up_creates_no_match(self) -> None:
        http = SequenceHttpClient(caught_up_response())
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            self._connect(service)
            outcome = service.sync_now()

            self.assertTrue(outcome.caught_up)
            self.assertEqual(outcome.discovered_count, 0)
            self.assertEqual(service.list_matches(), [])

    def test_403_and_412_stop_and_require_authorization_repair(self) -> None:
        for status_code, error_code in (
            (403, "steam_authorization_invalid"),
            (412, "steam_cursor_invalid"),
        ):
            with self.subTest(status_code=status_code):
                http = SequenceHttpClient(FakeResponse(status_code, {}))
                with self.Session() as db:
                    service = self._service(db, OWNER_A, http)
                    self._connect(service)
                    with self.assertRaises(SteamAuthorizationRequiredError) as raised:
                        service.sync_now()
                    self.assertEqual(raised.exception.error_code, error_code)
                    connection = service.get_connection()
                    self.assertEqual(connection.status, "authorization_required")
                    self.assertEqual(connection.last_error_code, error_code)
                    call_count = len(http.calls)
                    with self.assertRaises(SteamAuthorizationRequiredError):
                        service.sync_now()
                    self.assertEqual(len(http.calls), call_count)

    def test_active_sync_lease_blocks_overlap_and_stale_runner_can_be_replaced(self) -> None:
        http = SequenceHttpClient(caught_up_response())
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            connection = self._connect(service)
            connection.status = "syncing"
            connection.sync_run_id = "active-run"
            connection.last_sync_started_at = self.clock.now
            connection.updated_at = self.clock.now
            db.commit()

            with self.assertRaises(SteamSyncInProgressError):
                service.sync_now()
            self.assertEqual(http.calls, [])

            self.clock.now += timedelta(minutes=6)
            outcome = service.sync_now()
            self.assertTrue(outcome.caught_up)
            self.assertEqual(len(http.calls), 1)

    def test_sqlite_atomic_claim_allows_only_one_concurrent_valve_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{temp_dir}/steam-sync.db",
                connect_args={"check_same_thread": False, "timeout": 5},
            )
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
            with Session() as db:
                self._add_account(db, OWNER_A, STEAM_A)
                db.commit()
                self._connect(
                    SteamMatchService(
                        db,
                        owner_id=OWNER_A,
                        runtime_settings=self.settings,
                        cipher=SteamCredentialCipher(self.settings),
                        http_client=SequenceHttpClient(),
                        clock=self.clock,
                    )
                )

            claim_barrier = threading.Barrier(2)
            outcomes: list[str] = []
            errors: list[BaseException] = []
            http = BlockingHttpClient()
            rejected = threading.Event()

            def run_sync() -> None:
                try:
                    with Session() as db:
                        service = BarrierClaimSteamMatchService(
                            db,
                            owner_id=OWNER_A,
                            runtime_settings=self.settings,
                            cipher=SteamCredentialCipher(self.settings),
                            http_client=http,
                            clock=self.clock,
                            claim_barrier=claim_barrier,
                        )
                        service.sync_now()
                        outcomes.append("caught_up")
                except SteamSyncInProgressError:
                    outcomes.append("in_progress")
                    rejected.set()
                except BaseException as exc:  # pragma: no cover - assertion detail
                    errors.append(exc)

            threads = [threading.Thread(target=run_sync) for _ in range(2)]
            for thread in threads:
                thread.start()
            http_entered = http.entered.wait(timeout=5)
            overlap_rejected = rejected.wait(timeout=5)
            http.release.set()
            for thread in threads:
                thread.join(timeout=10)

            self.assertTrue(http_entered)
            self.assertTrue(overlap_rejected)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sorted(outcomes), ["caught_up", "in_progress"])
            self.assertEqual(http.calls, 1)
            engine.dispose()

    def test_concurrent_first_credentials_returns_conflict_instead_of_500(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{temp_dir}/steam-credentials.db",
                connect_args={"check_same_thread": False, "timeout": 5},
            )
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
            with Session() as db:
                self._add_account(db, OWNER_A, STEAM_A)
                db.commit()

            encryption_barrier = threading.Barrier(2)
            outcomes: list[str] = []
            errors: list[BaseException] = []

            def save_connection() -> None:
                try:
                    with Session() as db:
                        service = SteamMatchService(
                            db,
                            owner_id=OWNER_A,
                            runtime_settings=self.settings,
                            cipher=BarrierCipher(self.settings, encryption_barrier),
                            http_client=SequenceHttpClient(),
                            clock=self.clock,
                        )
                        service.save_credentials(
                            game_auth_code=GAME_AUTH_CODE,
                            initial_match_sharing_code=INITIAL_CODE,
                        )
                        outcomes.append("saved")
                except SteamConnectionConflictError:
                    outcomes.append("conflict")
                except BaseException as exc:  # pragma: no cover - assertion detail
                    errors.append(exc)

            threads = [threading.Thread(target=save_connection) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sorted(outcomes), ["conflict", "saved"])
            with Session() as db:
                self.assertEqual(db.query(SteamConnection).count(), 1)
            engine.dispose()

    def test_credentials_save_tolerates_disconnect_immediately_after_commit(self) -> None:
        plain_session_factory = self.Session

        class DeleteConnectionAfterCommitSession(OrmSession):
            def commit(inner_self) -> None:
                super().commit()
                if not inner_self.info.pop("delete_connection_after_commit", False):
                    return
                with plain_session_factory() as competing_db:
                    competing_db.query(SteamConnection).filter(
                        SteamConnection.owner_id == OWNER_A
                    ).delete(synchronize_session=False)
                    competing_db.commit()

        racing_session_factory = sessionmaker(
            bind=self.engine,
            class_=DeleteConnectionAfterCommitSession,
            autocommit=False,
            autoflush=False,
        )
        with racing_session_factory() as db:
            db.info["delete_connection_after_commit"] = True
            connection = self._service(
                db,
                OWNER_A,
                SequenceHttpClient(),
            ).save_credentials(
                game_auth_code=GAME_AUTH_CODE,
                initial_match_sharing_code=INITIAL_CODE,
            )
            self.assertIsInstance(connection, SteamConnection)

        with self.Session() as db:
            self.assertEqual(db.query(SteamConnection).count(), 0)

    def test_connection_requires_owner_bound_steam_identity(self) -> None:
        with self.Session() as db:
            identity = (
                db.query(ExternalIdentity)
                .filter(
                    ExternalIdentity.owner_id == OWNER_A,
                    ExternalIdentity.provider == "steam",
                )
                .one()
            )
            db.delete(identity)
            db.commit()
            service = self._service(db, OWNER_A, SequenceHttpClient())
            with self.assertRaises(SteamIdentityRequiredError):
                self._connect(service)
            self.assertIsNone(service.get_connection())

    def test_429_and_503_persist_exponential_backoff_without_sleep(self) -> None:
        http = SequenceHttpClient(
            FakeResponse(429, {}, headers={"Retry-After": "45"}),
            FakeResponse(503, {}),
        )
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            self._connect(service)

            with self.assertRaises(SteamSyncRetryError) as first:
                service.sync_now()
            self.assertEqual(first.exception.upstream_status, 429)
            self.assertEqual(first.exception.retry_after_seconds, 45)
            connection = service.get_connection()
            self.assertEqual(connection.status, "retry_wait")
            self.assertEqual(connection.consecutive_failures, 1)
            self.assertEqual(
                aware(connection.next_retry_at),
                self.clock.now + timedelta(seconds=45),
            )

            call_count = len(http.calls)
            with self.assertRaises(SteamSyncRetryError):
                service.sync_now()
            self.assertEqual(len(http.calls), call_count)

            self.clock.now += timedelta(seconds=45)
            with self.assertRaises(SteamSyncRetryError) as second:
                service.sync_now()
            self.assertEqual(second.exception.upstream_status, 503)
            self.assertEqual(second.exception.retry_after_seconds, 60)
            self.assertEqual(service.get_connection().consecutive_failures, 2)

    def test_rate_limiter_rejects_before_claim_decryption_or_http(self) -> None:
        limiter = RecordingSyncLimiter(rejection=SteamSyncRateLimitError(19))
        http = SequenceHttpClient(caught_up_response())
        with self.Session() as db:
            service = SteamMatchService(
                db,
                owner_id=OWNER_A,
                runtime_settings=self.settings,
                cipher=SteamCredentialCipher(self.settings),
                sync_rate_limiter=limiter,
                http_client=http,
                clock=self.clock,
            )
            self._connect(service)

            with self.assertRaises(SteamSyncRateLimitError):
                service.sync_now()

            self.assertEqual(limiter.enforced_owners, [OWNER_A])
            self.assertEqual(http.calls, [])
            connection = service.get_connection()
            self.assertEqual(connection.status, "retry_wait")
            self.assertEqual(connection.last_error_code, "steam_sync_rate_limited")
            self.assertEqual(
                aware(connection.next_retry_at),
                self.clock.now + timedelta(seconds=19),
            )

    def test_valve_429_sets_the_shared_publisher_breaker(self) -> None:
        limiter = RecordingSyncLimiter()
        http = SequenceHttpClient(
            FakeResponse(429, {}, headers={"Retry-After": "45"})
        )
        with self.Session() as db:
            service = SteamMatchService(
                db,
                owner_id=OWNER_A,
                runtime_settings=self.settings,
                cipher=SteamCredentialCipher(self.settings),
                sync_rate_limiter=limiter,
                http_client=http,
                clock=self.clock,
            )
            self._connect(service)

            with self.assertRaises(SteamSyncRetryError):
                service.sync_now()

            self.assertEqual(limiter.enforced_owners, [OWNER_A])
            self.assertEqual(limiter.publisher_blocks, [45])

    def test_oversized_numeric_retry_after_falls_back_without_stranding_lease(self) -> None:
        http = SequenceHttpClient(
            FakeResponse(429, {}, headers={"Retry-After": "9" * 10_000})
        )
        with self.Session() as db:
            service = self._service(db, OWNER_A, http)
            self._connect(service)

            with self.assertRaises(SteamSyncRetryError) as raised:
                service.sync_now()

            self.assertEqual(raised.exception.retry_after_seconds, 30)
            connection = service.get_connection()
            self.assertEqual(connection.status, "retry_wait")
            self.assertIsNone(connection.sync_run_id)

    def test_timeout_uses_503_backoff_without_logging_secrets(self) -> None:
        request = httpx.Request("GET", STEAM_MATCH_HISTORY_ENDPOINT)
        http = SequenceHttpClient(httpx.ReadTimeout("timeout", request=request))
        sensitive = [GAME_AUTH_CODE, INITIAL_CODE, STEAM_A, "a" * 32]
        handler = CapturingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with self.Session() as db:
                service = self._service(db, OWNER_A, http)
                self._connect(service)
                with self.assertRaises(SteamSyncRetryError) as raised:
                    service.sync_now()
                self.assertEqual(raised.exception.upstream_status, 503)
        finally:
            root_logger.removeHandler(handler)
        output = "\n".join(handler.messages)
        for secret in sensitive:
            self.assertNotIn(secret, output)

    def test_real_httpx_request_logging_is_suppressed_for_query_credentials(self) -> None:
        handler = CapturingHandler()
        httpx_logger = logging.getLogger("httpx")
        previous_level = httpx_logger.level
        httpx_logger.addHandler(handler)
        try:
            httpx_logger.setLevel(logging.INFO)
            suppress_outbound_http_request_logging()
            transport = httpx.MockTransport(
                lambda _request: httpx.Response(
                    202,
                    json={"result": {"nextcode": "n/a"}},
                )
            )
            with httpx.Client(transport=transport) as client:
                client.get(
                    STEAM_MATCH_HISTORY_ENDPOINT,
                    params={
                        "steamid": STEAM_A,
                        "steamidkey": GAME_AUTH_CODE,
                        "knowncode": INITIAL_CODE,
                    },
                )
        finally:
            httpx_logger.removeHandler(handler)
            httpx_logger.setLevel(previous_level)

        output = "\n".join(handler.messages)
        self.assertEqual(output, "")

    def test_duplicate_match_is_idempotent_and_cursor_still_advances(self) -> None:
        with self.Session() as db:
            first_http = SequenceHttpClient(next_code_response(NEXT_CODE), caught_up_response())
            first = self._service(db, OWNER_A, first_http)
            connection = self._connect(first)
            first.sync_now()

            encrypted_initial = first.cipher.encrypt(
                INITIAL_CODE,
                owner_id=OWNER_A,
                purpose="known-match-code",
                record_id=connection.id,
            )
            stored_connection = first.get_connection()
            stored_connection.known_code_ciphertext = encrypted_initial.ciphertext
            stored_connection.known_code_nonce = encrypted_initial.nonce
            stored_connection.encryption_key_version = encrypted_initial.key_version
            stored_connection.status = "connected"
            db.commit()

            replay_http = SequenceHttpClient(next_code_response(NEXT_CODE), caught_up_response())
            replay = self._service(db, OWNER_A, replay_http)
            outcome = replay.sync_now()
            self.assertEqual(outcome.discovered_count, 0)
            self.assertEqual(len(replay.list_matches()), 1)

    def test_invalid_upstream_payload_fails_closed_without_advancing(self) -> None:
        invalid_responses = (
            FakeResponse(200, {"result": {}}),
            FakeResponse(200, {"result": {"nextcode": "n/a"}}),
            FakeResponse(202, {"result": {"nextcode": NEXT_CODE}}),
            FakeResponse(200, ValueError("invalid json")),
            FakeResponse(200, {"oversized": "x" * 20_000}),
        )
        for response in invalid_responses:
            with self.subTest(status=response.status_code, payload=response.payload):
                http = SequenceHttpClient(response)
                with self.Session() as db:
                    service = self._service(db, OWNER_A, http)
                    self._connect(service)
                    with self.assertRaises(SteamUpstreamProtocolError):
                        service.sync_now()
                    self.assertEqual(service.get_connection().status, "error")
                    self.assertEqual(service.list_matches(), [])

    def test_owner_isolation_and_disconnect_delete_only_owned_connection_data(self) -> None:
        with self.Session() as db:
            service_a = self._service(
                db,
                OWNER_A,
                SequenceHttpClient(next_code_response(NEXT_CODE), caught_up_response()),
            )
            service_b = self._service(
                db,
                OWNER_B,
                SequenceHttpClient(next_code_response(numbered_code(99)), caught_up_response()),
            )
            self._connect(service_a)
            self._connect(service_b)
            service_a.sync_now()
            service_b.sync_now()

            self.assertEqual(len(service_a.list_matches()), 1)
            self.assertEqual(len(service_b.list_matches()), 1)
            match_b_id = service_b.list_matches()[0].id
            self.assertTrue(service_a.delete_connection())
            self.assertIsNone(service_a.get_connection())
            self.assertEqual(service_a.list_matches(), [])
            self.assertIsNotNone(service_b.get_connection())
            self.assertEqual(service_b.list_matches()[0].id, match_b_id)
            self.assertFalse(service_a.delete_connection())

    def test_api_never_returns_credentials_cursor_ciphertext_or_steam_id(self) -> None:
        app = FastAPI()
        app.include_router(steam_api.router)
        owner = {"value": OWNER_A}

        def override_service():
            db = self.Session()
            try:
                yield SteamMatchService(
                    db,
                    owner_id=owner["value"],
                    runtime_settings=self.settings,
                    cipher=SteamCredentialCipher(self.settings),
                    http_client=SequenceHttpClient(),
                    clock=self.clock,
                )
            finally:
                db.close()

        app.dependency_overrides[steam_api.get_steam_match_service] = override_service
        app.dependency_overrides[steam_api.require_trusted_origin] = lambda: None
        client = TestClient(app)

        response = client.post(
            "/steam/connection/credentials",
            json={
                "game_auth_code": GAME_AUTH_CODE,
                "initial_match_sharing_code": INITIAL_CODE,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        serialized = response.text
        for forbidden in (
            GAME_AUTH_CODE,
            INITIAL_CODE,
            STEAM_A,
            "ciphertext",
            "nonce",
            "known_code",
        ):
            self.assertNotIn(forbidden, serialized)

        owner["value"] = OWNER_B
        other = client.get("/steam/connection")
        self.assertEqual(other.status_code, 200)
        self.assertFalse(other.json()["connected"])
        self.assertEqual(client.get("/steam/matches").json(), [])

        owner["value"] = OWNER_A
        rejected_secret = "AAAA-secret-invalid"
        rejected = client.post(
            "/steam/connection/credentials",
            json={
                "game_auth_code": rejected_secret,
                "initial_match_sharing_code": "not-a-sharing-code",
            },
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertNotIn(rejected_secret, rejected.text)
        self.assertNotIn("not-a-sharing-code", rejected.text)

        unexpected_secret = "must-not-be-reflected"
        extra_field = client.post(
            "/steam/connection/credentials",
            json={
                "game_auth_code": GAME_AUTH_CODE,
                "initial_match_sharing_code": INITIAL_CODE,
                "unexpected": unexpected_secret,
            },
        )
        self.assertEqual(extra_field.status_code, 422)
        self.assertNotIn(unexpected_secret, extra_field.text)

    def test_api_maps_sync_repair_retry_rate_and_protocol_errors_safely(self) -> None:
        cases = (
            (SteamAuthorizationRequiredError("steam_cursor_invalid"), 409, None),
            (SteamSyncRetryError(429, 17), 429, "17"),
            (SteamSyncRetryError(503, 23), 503, "23"),
            (SteamSyncRateLimitError(29), 429, "29"),
            (
                SteamSyncRateLimitUnavailableError("redis unavailable"),
                503,
                None,
            ),
            (SteamUpstreamProtocolError("unsafe upstream body"), 502, None),
        )
        for error, expected_status, expected_retry_after in cases:
            with self.subTest(error=type(error).__name__):
                app = FastAPI()
                app.include_router(steam_api.router)
                app.dependency_overrides[steam_api.get_steam_match_service] = (
                    lambda: RaisingSyncService(error)
                )
                app.dependency_overrides[steam_api.require_trusted_origin] = lambda: None
                response = TestClient(app).post("/steam/sync")

                self.assertEqual(response.status_code, expected_status, response.text)
                self.assertEqual(
                    response.headers.get("Retry-After"),
                    expected_retry_after,
                )
                self.assertNotIn("unsafe upstream body", response.text)
                self.assertNotIn("redis unavailable", response.text)

    def test_anonymous_and_expired_production_sessions_fail_before_steam_access(self) -> None:
        runtime_settings = Settings(
            auth_mode="production",
            auth_provider="steam",
            auth_session_cookie_name="__Host-cs2_session",
            frontend_public_url="https://coach.example.test",
            cors_origins_raw="https://coach.example.test",
        )
        auth_service = MissingSessionAuthService(runtime_settings)
        app = FastAPI()
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=runtime_settings,
            auth_service_factory=lambda: auth_service,
        )
        app.include_router(steam_api.router)
        app.dependency_overrides[get_auth_service] = lambda: auth_service

        def unusable_db():
            yield object()

        app.dependency_overrides[get_db] = unusable_db
        client = TestClient(app, base_url="https://coach.example.test")

        for headers in (
            {},
            {"X-Dev-User-Id": OWNER_A},
            {"Cookie": "__Host-cs2_session=expired-session"},
        ):
            with self.subTest(headers=headers):
                response = client.get(
                    "/steam/connection",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                self.assertIn("Cookie", response.headers["Vary"])
                self.assertIn("Origin", response.headers["Vary"])

        mutation = client.post(
            "/steam/sync",
            headers={"Origin": "https://coach.example.test"},
        )
        self.assertEqual(mutation.status_code, 401)
        self.assertEqual(mutation.headers["Cache-Control"], "private, no-store")

    def _service(
        self,
        db,
        owner_id: str,
        http: SequenceHttpClient,
    ) -> SteamMatchService:
        return SteamMatchService(
            db,
            owner_id=owner_id,
            runtime_settings=self.settings,
            cipher=SteamCredentialCipher(self.settings),
            http_client=http,
            clock=self.clock,
        )

    def _connect(self, service: SteamMatchService) -> SteamConnection:
        return service.save_credentials(
            game_auth_code=GAME_AUTH_CODE,
            initial_match_sharing_code=INITIAL_CODE,
        )

    @staticmethod
    def _add_account(db, owner_id: str, steam_id: str) -> None:
        now = datetime.now(UTC)
        db.add(
            Account(
                owner_id=owner_id,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            ExternalIdentity(
                id=f"identity-{owner_id}",
                provider="steam",
                subject=steam_id,
                owner_id=owner_id,
                created_at=now,
                last_login_at=now,
            )
        )


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class MissingSessionAuthService:
    def __init__(self, runtime_settings: Settings):
        self.settings = runtime_settings

    @staticmethod
    def resolve_session(_token: str | None) -> None:
        return None


def next_code_response(code: str) -> FakeResponse:
    return FakeResponse(200, {"result": {"nextcode": code}})


def caught_up_response() -> FakeResponse:
    return FakeResponse(202, {"result": {"nextcode": "n/a"}})


def numbered_code(number: int) -> str:
    return f"CSGO-{number:05d}-AAAAA-BBBBB-CCCCC-DDDDD"


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


if __name__ == "__main__":
    unittest.main()
