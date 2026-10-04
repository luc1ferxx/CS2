"""Chunked, resumable .dem upload sessions: API, state machine, admission and sweep.

Every database here enforces foreign keys (PRAGMA foreign_keys=ON on each
connection), storage is a local artifact store and a staging store under a
temporary directory, and Redis is a fake. Parts are 64 bytes so a small
fixture spans several of them.
"""

import hashlib
import json
import tempfile
import threading
import time
import unittest
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, QueuePool, StaticPool

from app.api import demos, uploads
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.core.request_limits import MultipartRequestLimitMiddleware
from app.core.upload_slots import IntakeSlot, get_intake_slot
from app.models import Account, Demo, DemoJob, UploadLedger, UploadSession
from app.services import upload_session_service
from app.services.artifact_intake import ArtifactIntakeError
from app.services.auth_service import AuthService, get_auth_service
from app.services.demo_service import ACTIVE_DEMO_STATUSES, DemoService
from app.services.storage import LocalArtifactStore, UploadStagingStore
from app.services.storage.contract import _encode_reference_identity
from app.services.upload_session_service import (
    PartsInFlight,
    UploadSessionService,
    get_upload_session_factory,
    get_upload_staging,
    reset_completing_leases,
    sweep_upload_sessions,
    token_digest,
)

OWNER = "owner_v1_session_owner"
OTHER_OWNER = "owner_v1_session_other"
ORIGIN = "https://coach.example.test"
SESSION_TOKENS = {OWNER: "session-owner-cookie", OTHER_OWNER: "session-other-cookie"}
PART = 64
# 9 parts: eight of 64 bytes and a last one of 8.
DEMO = b"HL2DEMO\x00" + bytes(range(256)) * 2
ZIP_DEMO = b"PK\x03\x04" + b"\x00" * 300
QUOTA_FIELDS = ("demo_upload_daily_limit", "demo_active_parse_limit", "parse_queue_global_limit")
SESSION_FIELDS = (
    "upload_part_bytes",
    "upload_max_parallel_parts",
    "upload_part_pool",
    "upload_session_ttl_seconds",
    "upload_session_global_limit",
    "upload_staging_min_free_bytes",
)
SMALL_POOL: dict[str, Any] = {"poolclass": QueuePool, "pool_size": 2, "max_overflow": 0, "pool_timeout": 1}


def parts_of(data: bytes) -> list[bytes]:
    return [data[offset : offset + PART] for offset in range(0, len(data), PART)]


def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.payloads: list[str] = []

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def lpush(self, _queue: str, payload: str) -> None:
        self.payloads.append(payload)


class UploadSessionTestBase(unittest.TestCase):
    mode = "development"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.use_memory_database()
        overrides: dict[str, Any] = {
            "auth_mode": self.mode,
            "artifact_storage_backend": "local",
            "artifact_storage_root": self.root / "artifacts",
            "replay_storage_dir": self.root / "artifacts" / "replays",
            "demo_upload_storage_dir": self.root / "artifacts" / "uploads",
            "video_storage_dir": self.root / "artifacts" / "videos",
            "demo_upload_daily_limit": 10,
            "demo_active_parse_limit": 5,
            "parse_queue_global_limit": 50,
            "upload_part_bytes": PART,
            "upload_max_parallel_parts": 4,
            "upload_part_pool": 8,
            "upload_session_ttl_seconds": 86_400,
            "upload_session_global_limit": 6,
            "upload_staging_min_free_bytes": 0,
        }
        self.original_settings = {name: getattr(settings, name) for name in overrides}
        for name, value in overrides.items():
            object.__setattr__(settings, name, value)
        self.addCleanup(self.restore_settings)

        self.redis = FakeRedis()
        redis_patch = patch("app.services.demo_service.get_redis_client", return_value=self.redis)
        redis_patch.start()
        self.addCleanup(redis_patch.stop)
        self.tracker = PartsInFlight()
        tracker_patch = patch.object(upload_session_service, "PARTS_IN_FLIGHT", self.tracker)
        tracker_patch.start()
        self.addCleanup(tracker_patch.stop)

        self.staging = UploadStagingStore(self.root / "staging")
        self.intake_slot = IntakeSlot()
        self.auth_service = AuthService(
            Settings(
                auth_mode="production",
                auth_provider="oidc",
                frontend_public_url=ORIGIN,
                backend_public_url=ORIGIN,
                cors_origins_raw=ORIGIN,
                auth_cookie_secure=True,
            )
            if self.mode == "production"
            else Settings(auth_mode=self.mode),
            self.redis,
        )
        for owner_id, cookie in SESSION_TOKENS.items():
            self.redis.setex(
                "auth:session:" + hashlib.sha256(cookie.encode("ascii")).hexdigest(),
                300,
                json.dumps({"ownerId": owner_id, "expiresAt": int(time.time()) + 3600}),
            )
        self.addCleanup(self.temp_dir.cleanup)

    def restore_settings(self) -> None:
        for name, value in self.original_settings.items():
            object.__setattr__(settings, name, value)

    def use_memory_database(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._finish_engine()

    def use_file_database(self, **pool: Any) -> None:
        # Concurrent requests need a connection each; StaticPool shares one.
        self.engine.dispose()
        db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(db_dir.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(db_dir.name, 'sessions.sqlite').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            **(pool or {"poolclass": NullPool}),
        )
        self._finish_engine()

    def _finish_engine(self) -> None:
        event.listen(self.engine, "connect", _enforce_foreign_keys)
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.addCleanup(self.engine.dispose)
        with self.Session() as db:
            for owner_id in SESSION_TOKENS:
                db.add(Account(owner_id=owner_id))
            db.commit()

    # -- clients ---------------------------------------------------------------
    def client(
        self,
        owner_id: str = OWNER,
        *,
        cookie: bool = True,
        origin: str | None = ORIGIN,
        with_limits: bool = False,
    ) -> TestClient:
        app = FastAPI()
        if with_limits:
            app.add_middleware(MultipartRequestLimitMiddleware, intake_slot=self.intake_slot)
        if self.mode == "production":
            app.add_middleware(
                SessionCsrfMiddleware,
                runtime_settings=self.auth_service.settings,
                auth_service_factory=lambda: self.auth_service,
            )
        app.include_router(demos.router)
        app.include_router(uploads.router)

        def override_get_db() -> Iterator[Any]:
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_upload_session_factory] = lambda: self.Session
        app.dependency_overrides[get_upload_staging] = lambda: self.staging
        app.dependency_overrides[get_intake_slot] = lambda: self.intake_slot
        app.dependency_overrides[get_auth_service] = lambda: self.auth_service
        headers = {"Origin": origin} if origin else {}
        if self.mode != "production":
            headers["X-Dev-User-Id"] = owner_id
        client = TestClient(app, base_url=ORIGIN, headers=headers)
        if self.mode == "production" and cookie:
            client.cookies.set("__Host-cs2_session", SESSION_TOKENS[owner_id])
        return client

    # -- requests --------------------------------------------------------------
    def create(self, client: TestClient, *, data: bytes = DEMO, **body: Any) -> Any:
        payload = {"filename": "match.dem", "size": len(data), **body}
        return client.post("/uploads/sessions", json=payload)

    def create_ok(self, client: TestClient, *, data: bytes = DEMO, **body: Any) -> dict[str, Any]:
        response = self.create(client, data=data, **body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def put(
        self,
        client: TestClient,
        session_id: str,
        token: str | None,
        index: int,
        *,
        data: bytes = DEMO,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        content = parts_of(data)[index] if body is None else body
        sent = {"Content-Type": "application/octet-stream"}
        if token is not None:
            sent["X-Upload-Token"] = token
        sent.update(headers or {})
        return client.put(f"/uploads/sessions/{session_id}/parts/{index}", content=content, headers=sent)

    def upload_parts(
        self,
        client: TestClient,
        created: dict[str, Any],
        *,
        data: bytes = DEMO,
        indexes: list[int] | None = None,
    ) -> None:
        for index in indexes if indexes is not None else range(created["partCount"]):
            response = self.put(client, created["sessionId"], created["uploadToken"], index, data=data)
            self.assertEqual(response.status_code, 200, response.text)

    def complete(self, client: TestClient, session_id: str) -> Any:
        return client.post(f"/uploads/sessions/{session_id}/complete")

    # -- inspection ------------------------------------------------------------
    def row(self, session_id: str) -> UploadSession | None:
        with self.Session() as db:
            row = db.get(UploadSession, session_id)
            if row is not None:
                db.expunge(row)
            return row

    def session_dir(self, session_id: str, owner_id: str = OWNER) -> Path:
        return self.root / "staging" / "v1" / _encode_reference_identity(owner_id) / session_id

    def counts(self) -> tuple[int, int, int]:
        with self.Session() as db:
            return (db.query(Demo).count(), db.query(DemoJob).count(), db.query(UploadLedger).count())

    def artifact_files(self) -> list[Path]:
        root = self.root / "artifacts"
        return sorted(path for path in root.rglob("*") if path.is_file()) if root.exists() else []

    def update_row(self, session_id: str, **values: Any) -> None:
        with self.Session() as db:
            row = db.get(UploadSession, session_id)
            assert row is not None
            for name, value in values.items():
                setattr(row, name, value)
            db.commit()

    def assert_session_error(self, response: Any, status_code: int, code: str) -> dict[str, Any]:
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], code, response.text)
        self.assertIsInstance(detail["message"], str)
        return detail

    def assert_intake_error(self, response: Any, status_code: int, code: str) -> None:
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.json()["errorCode"], code)
        self.assertIsInstance(response.json()["detail"], str)


class CreateSessionTest(UploadSessionTestBase):
    def test_create_returns_the_contract_and_stores_only_the_token_hash(self) -> None:
        response = self.create(self.client())

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        body = response.json()
        self.assertEqual(
            sorted(body),
            sorted(
                [
                    "sessionId",
                    "uploadToken",
                    "partSize",
                    "partCount",
                    "maxParallelParts",
                    "expiresAt",
                    "receivedParts",
                ]
            ),
        )
        self.assertRegex(body["sessionId"], r"^[0-9a-f]{32}$")
        self.assertEqual((body["partSize"], body["partCount"], body["maxParallelParts"]), (PART, 9, 4))
        self.assertEqual(body["receivedParts"], [])
        row = self.row(body["sessionId"])
        assert row is not None
        self.assertEqual((row.state, row.owner_id, row.active_owner_id), ("open", OWNER, OWNER))
        self.assertEqual(row.token_sha256, token_digest(body["uploadToken"]))
        self.assertNotEqual(row.token_sha256, body["uploadToken"])
        self.assertTrue(self.session_dir(body["sessionId"]).is_dir())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_create_rejects_bad_names_types_and_sizes_with_the_intake_codes(self) -> None:
        cases = (
            ({"filename": "match.zip"}, 400, "INTAKE_TYPE_REJECTED"),
            ({"filename": "../match.dem"}, 400, "INTAKE_TYPE_REJECTED"),
            ({"size": 0}, 400, "INTAKE_EMPTY"),
            ({"size": 15}, 400, "INTAKE_TRUNCATED"),
            ({"size": settings.max_demo_upload_bytes + 1}, 413, "INTAKE_TOO_LARGE"),
            ({"contentType": "application/zip"}, 400, "INTAKE_CONTENT_MISMATCH"),
        )
        client = self.client()
        for body, status_code, code in cases:
            with self.subTest(body=body):
                payload = {"filename": "match.dem", "size": len(DEMO), **body}
                self.assert_intake_error(client.post("/uploads/sessions", json=payload), status_code, code)
        with self.Session() as db:
            self.assertEqual(db.query(UploadSession).count(), 0)
        self.assertFalse((self.root / "staging").exists() and any((self.root / "staging").rglob("*.session")))

    def test_one_open_session_per_owner_and_replace_discards_the_old_one(self) -> None:
        client = self.client()
        first = self.create_ok(client)
        self.upload_parts(client, first, indexes=[0, 1])

        detail = self.assert_session_error(self.create(client), 409, "upload_session_exists")
        self.assertEqual(detail["sessionId"], first["sessionId"])
        self.assertEqual(detail["filename"], "match.dem")
        self.assertEqual(detail["size"], len(DEMO))
        self.assertEqual(detail["receivedBytes"], 2 * PART)

        second = self.create_ok(client, replace=True)

        self.assertNotEqual(second["sessionId"], first["sessionId"])
        self.assertIsNone(self.row(first["sessionId"]))
        self.assertFalse(self.session_dir(first["sessionId"]).exists())
        stale = self.put(client, first["sessionId"], first["uploadToken"], 2)
        self.assert_session_error(stale, 404, "upload_session_not_found")
        self.assertFalse(self.session_dir(first["sessionId"]).exists())
        # Another owner is unaffected by this owner's session.
        self.create_ok(self.client(OTHER_OWNER))

    def test_an_expired_open_session_does_not_block_a_new_one(self) -> None:
        client = self.client()
        old = self.create_ok(client)
        self.update_row(old["sessionId"], expires_at=datetime.now(UTC) - timedelta(seconds=1))

        new = self.create_ok(client)

        self.assertNotEqual(new["sessionId"], old["sessionId"])
        self.assertIsNone(self.row(old["sessionId"]))
        self.assertFalse(self.session_dir(old["sessionId"]).exists())

    def test_replace_refuses_a_session_that_is_completing(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        self.update_row(
            created["sessionId"],
            state="completing",
            pending_demo_id=str(uuid.uuid4()),
            lease_until=datetime.now(UTC) + timedelta(minutes=10),
        )

        detail = self.assert_session_error(self.create(client, replace=True), 409, "upload_session_exists")

        self.assertEqual(detail["state"], "completing")
        self.assertEqual(self.row(created["sessionId"]).state, "completing")  # type: ignore[union-attr]

    def test_the_global_open_session_limit_answers_503_with_retry_after(self) -> None:
        object.__setattr__(settings, "upload_session_global_limit", 1)
        self.create_ok(self.client())

        response = self.create(self.client(OTHER_OWNER))

        detail = self.assert_session_error(response, 503, "upload_capacity_busy")
        self.assertEqual(response.headers["retry-after"], str(detail["retryAfterSeconds"]))
        with self.Session() as db:
            self.assertEqual(db.query(UploadSession).count(), 1)

    def test_replacing_at_the_global_limit_reuses_the_owners_own_place(self) -> None:
        object.__setattr__(settings, "upload_session_global_limit", 1)
        client = self.client()
        first = self.create_ok(client)

        second = self.create_ok(client, replace=True)

        self.assertIsNone(self.row(first["sessionId"]))
        self.assertEqual(self.row(second["sessionId"]).state, "open")  # type: ignore[union-attr]

    def test_low_staging_free_space_answers_503_and_leaves_nothing(self) -> None:
        object.__setattr__(settings, "upload_staging_min_free_bytes", 1024)
        with patch.object(self.staging, "free_bytes", return_value=len(DEMO) + 1023):
            response = self.create(self.client())

        detail = self.assert_session_error(response, 503, "upload_storage_full")
        self.assertEqual(response.headers["retry-after"], str(detail["retryAfterSeconds"]))
        with self.Session() as db:
            self.assertEqual(db.query(UploadSession).count(), 0)
        staging_root = self.root / "staging"
        self.assertEqual(list(staging_root.rglob(".session")) if staging_root.exists() else [], [])

    def test_a_failed_insert_removes_the_directory_it_created(self) -> None:
        with patch.object(upload_session_service, "account_exists_for_write", return_value=False):
            response = self.create(self.client())

        self.assert_session_error(response, 401, "account_deleted")
        staging_root = self.root / "staging"
        self.assertEqual(list(staging_root.rglob(".session")) if staging_root.exists() else [], [])


class PartUploadTest(UploadSessionTestBase):
    def test_parts_arrive_in_any_order_and_a_duplicate_replaces_its_copy(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]

        last = self.put(client, sid, token, 8)
        first = self.put(client, sid, token, 0)
        again = self.put(client, sid, token, 0)

        self.assertEqual(last.status_code, 200, last.text)
        self.assertEqual(last.headers["cache-control"], "private, no-store")
        self.assertEqual(
            last.json(),
            {
                "index": 8,
                "sizeBytes": 8,
                "sha256": hashlib.sha256(parts_of(DEMO)[8]).hexdigest(),
                "receivedCount": 1,
            },
        )
        self.assertEqual(first.json()["receivedCount"], 2)
        self.assertEqual(again.json()["receivedCount"], 2)
        status = client.get(f"/uploads/sessions/{sid}").json()
        self.assertEqual(status["receivedParts"], [0, 8])
        self.assertEqual(status["receivedBytes"], PART + 8)
        self.assertEqual(status["part0Sha256"], hashlib.sha256(parts_of(DEMO)[0]).hexdigest())
        # Parts are never recorded in the database.
        self.assertEqual(self.row(sid).state, "open")  # type: ignore[union-attr]

    def test_token_is_required_and_a_wrong_or_replaced_token_is_a_plain_404(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]

        missing = self.put(client, sid, None, 0)
        wrong = self.put(client, sid, "not-the-token", 0)
        unknown = self.put(client, uuid.uuid4().hex, token, 0)
        for response in (missing, wrong, unknown):
            self.assert_session_error(response, 404, "upload_session_not_found")
        self.assertEqual(missing.json(), unknown.json())

        reissued = client.post(f"/uploads/sessions/{sid}/token")
        self.assertEqual(reissued.status_code, 200, reissued.text)
        self.assertEqual(reissued.headers["cache-control"], "private, no-store")
        new_token = reissued.json()["uploadToken"]
        self.assertNotEqual(new_token, token)
        self.assertEqual(reissued.json()["sessionId"], sid)
        self.assertEqual(reissued.json()["state"], "open")

        self.assert_session_error(self.put(client, sid, token, 0), 404, "upload_session_not_found")
        self.assertEqual(self.put(client, sid, new_token, 0).status_code, 200)

    def test_malformed_paths_are_not_found(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        token = created["uploadToken"]
        for path in (
            f"/uploads/sessions/{created['sessionId'].upper()}/parts/0",
            f"/uploads/sessions/{created['sessionId']}/parts/-1",
            f"/uploads/sessions/{created['sessionId']}/parts/+1",
            f"/uploads/sessions/{created['sessionId']}/parts/123456",
        ):
            with self.subTest(path=path):
                response = client.put(path, content=parts_of(DEMO)[0], headers={"X-Upload-Token": token})
                self.assertEqual(response.status_code, 404)

    def test_lengths_index_and_digest_are_checked_before_anything_is_written(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]
        part = parts_of(DEMO)[1]

        out_of_range = self.put(client, sid, token, 9, body=b"x" * 8)
        short = self.put(client, sid, token, 1, body=part[:-1])
        long = self.put(client, sid, token, 1, body=part + b"!")
        bad_header = self.put(client, sid, token, 1, headers={"X-Part-SHA256": "zz"})
        mismatch = self.put(client, sid, token, 1, headers={"X-Part-SHA256": "0" * 64})

        for response in (out_of_range, short, long, bad_header):
            self.assert_session_error(response, 400, "upload_part_invalid")
        self.assert_session_error(mismatch, 422, "upload_part_digest_mismatch")
        self.assertEqual(self.staging.list_parts(OWNER, sid), {})

        matched = self.put(
            client, sid, token, 1, headers={"X-Part-SHA256": hashlib.sha256(part).hexdigest().upper()}
        )
        self.assertEqual(matched.status_code, 200, matched.text)
        self.assertEqual(self.staging.list_parts(OWNER, sid), {1: PART})

    def test_an_archive_signature_in_part_zero_fails_the_session_at_once(self) -> None:
        client = self.client()
        created = self.create_ok(client, data=ZIP_DEMO)
        sid, token = created["sessionId"], created["uploadToken"]
        self.assertEqual(self.put(client, sid, token, 1, data=ZIP_DEMO).status_code, 200)

        response = self.put(client, sid, token, 0, data=ZIP_DEMO)

        self.assert_intake_error(response, 400, "INTAKE_CONTENT_MISMATCH")
        row = self.row(sid)
        assert row is not None
        self.assertEqual((row.state, row.error_code, row.active_owner_id), ("failed", "INTAKE_CONTENT_MISMATCH", None))
        self.assertFalse(self.session_dir(sid).exists())
        self.assert_session_error(self.put(client, sid, token, 2, data=ZIP_DEMO), 409, "upload_session_not_open")
        self.assertFalse(self.session_dir(sid).exists())
        failed = self.complete(client, sid)
        self.assert_intake_error(failed, 400, "INTAKE_CONTENT_MISMATCH")
        status = client.get(f"/uploads/sessions/{sid}").json()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"]["code"], "INTAKE_CONTENT_MISMATCH")
        self.assertIsInstance(status["error"]["message"], str)
        # The failed session no longer holds the owner's upload slot.
        self.create_ok(client)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_parts_for_a_session_that_is_not_open_or_expired_are_refused(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]

        self.update_row(
            sid,
            state="completing",
            pending_demo_id=str(uuid.uuid4()),
            lease_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.assert_session_error(self.put(client, sid, token, 0), 409, "upload_session_not_open")

        self.update_row(sid, state="open", pending_demo_id=None, lease_until=None)
        self.update_row(sid, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        self.assert_session_error(self.put(client, sid, token, 0), 404, "upload_session_not_found")
        self.assert_session_error(self.complete(client, sid), 404, "upload_session_not_found")
        self.assert_session_error(client.get(f"/uploads/sessions/{sid}"), 404, "upload_session_not_found")
        self.assertEqual(self.staging.list_parts(OWNER, sid), {})

    def test_a_session_deleted_with_its_directory_rejects_late_parts_without_recreating_it(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]
        self.upload_parts(client, created, indexes=[0])
        # What an account deletion does: the row in its transaction, the
        # directory after the commit.
        with self.Session() as db:
            db.query(UploadSession).filter(UploadSession.owner_id == OWNER).delete()
            db.commit()
        self.staging.purge_owner(OWNER)

        response = self.put(client, sid, token, 1)

        self.assert_session_error(response, 404, "upload_session_not_found")
        self.assertFalse(self.session_dir(sid).exists())

    def test_a_directory_purged_after_the_token_check_is_never_recreated(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]
        resolve = upload_session_service.resolve_part_target

        def resolve_then_purge(*args: Any, **kwargs: Any) -> Any:
            target = resolve(*args, **kwargs)
            self.staging.purge_session(OWNER, sid)
            return target

        with patch.object(uploads, "resolve_part_target", resolve_then_purge):
            response = self.put(client, sid, token, 3)

        self.assert_session_error(response, 404, "upload_session_not_found")
        self.assertFalse(self.session_dir(sid).exists())

    def test_the_per_session_parallel_limit_answers_intake_busy(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        for _ in range(4):
            self.assertTrue(self.tracker.try_enter(sid, 4))

        response = self.put(client, sid, created["uploadToken"], 0)

        self.assert_intake_error(response, 503, "INTAKE_BUSY")
        self.assertEqual(response.headers["retry-after"], "2")
        self.assertEqual(self.tracker.count(sid), 4)
        # Another session is not limited by this one.
        other = self.create_ok(self.client(OTHER_OWNER))
        self.assertEqual(self.put(client, other["sessionId"], other["uploadToken"], 0).status_code, 200)

    def test_cross_owner_requests_are_not_found(self) -> None:
        created = self.create_ok(self.client())
        sid = created["sessionId"]
        other = self.client(OTHER_OWNER)

        for response in (
            other.get(f"/uploads/sessions/{sid}"),
            other.post(f"/uploads/sessions/{sid}/token"),
            other.post(f"/uploads/sessions/{sid}/complete"),
            other.delete(f"/uploads/sessions/{sid}"),
        ):
            self.assert_session_error(response, 404, "upload_session_not_found")
        self.assertEqual(other.get("/uploads/sessions/current").json(), {"session": None})
        self.assertEqual(self.row(sid).state, "open")  # type: ignore[union-attr]


class SlowPartConnectionTest(UploadSessionTestBase):
    def test_a_slow_part_body_holds_no_pooled_connection(self) -> None:
        self.use_file_database(**SMALL_POOL)
        client = self.client()
        created = self.create_ok(client)
        sid, token = created["sessionId"], created["uploadToken"]
        started: list[int] = []
        started_lock = threading.Lock()
        release = threading.Event()
        part = parts_of(DEMO)[0]

        def slow_body() -> Iterator[bytes]:
            # Runs once the route reads the body, i.e. after its token check.
            with started_lock:
                started.append(1)
            release.wait(10)
            yield part

        results: dict[str, int] = {}

        def run() -> None:
            response = self.client().put(
                f"/uploads/sessions/{sid}/parts/0",
                content=slow_body(),
                headers={"X-Upload-Token": token, "Content-Length": str(len(part))},
            )
            results["put"] = response.status_code

        threads = [threading.Thread(target=run) for _ in range(3)]
        try:
            for thread in threads:
                thread.start()
            self.assertTrue(wait_until(lambda: len(started) == 3))
            # Three bodies are being read, and none holds a connection: the
            # whole two-connection pool is free for everything else.
            self.assertEqual(self.tracker.count(sid), 3)
            self.assertEqual(self.engine.pool.checkedout(), 0)
            other = self.client(OTHER_OWNER)
            self.assertEqual(other.get("/uploads/sessions/current").status_code, 200)
            self.assertEqual(self.create(other).status_code, 201)
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual(results["put"], 200)
        self.assertEqual(self.staging.list_parts(OWNER, sid), {0: PART})


class CompleteTest(UploadSessionTestBase):
    def test_complete_creates_exactly_one_demo_job_ledger_row_and_dispatch(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created, indexes=[8, 3, 0, 5, 1, 7, 2, 6, 4])

        response = self.complete(client, sid)

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        demo = response.json()
        self.assertEqual((demo["status"], demo["name"]), ("queued", "match.dem"))
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(len(self.redis.payloads), 1)
        self.assertEqual(json.loads(self.redis.payloads[0])["demo_id"], demo["id"])
        with self.Session() as db:
            job = db.query(DemoJob).one()
            source = json.loads(job.metadata_json)["sourceArtifact"]
        self.assertEqual(source["sha256"], hashlib.sha256(DEMO).hexdigest())
        self.assertEqual(source["sizeBytes"], len(DEMO))
        row = self.row(sid)
        assert row is not None
        self.assertEqual((row.state, row.demo_id, row.active_owner_id), ("completed", demo["id"], None))
        self.assertFalse(self.session_dir(sid).exists())
        status = client.get(f"/uploads/sessions/{sid}").json()
        self.assertEqual((status["state"], status["demo"]["id"]), ("completed", demo["id"]))
        self.assertEqual(client.get("/uploads/sessions/current").json(), {"session": None})

        again = self.complete(client, sid)

        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(set(again.json()), set(demo))
        self.assertEqual(again.json()["id"], demo["id"])
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(len(self.redis.payloads), 1)
        # The same DemoListItem fields as the legacy upload answers.
        legacy = self.client(OTHER_OWNER).post(
            "/uploads/demo", files={"file": ("legacy.dem", DEMO, "application/octet-stream")}
        )
        self.assertEqual(legacy.status_code, 201, legacy.text)
        self.assertEqual(set(demo), set(legacy.json()))

    def test_complete_after_the_demo_was_deleted_is_not_found(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        self.upload_parts(client, created)
        demo_id = self.complete(client, created["sessionId"]).json()["id"]
        with self.Session() as db:
            db.query(DemoJob).filter(DemoJob.demo_id == demo_id).delete()
            db.query(Demo).filter(Demo.id == demo_id).delete()
            db.commit()

        self.assert_session_error(self.complete(client, created["sessionId"]), 404, "upload_session_not_found")

    def test_missing_parts_reopen_the_session_and_list_what_is_missing(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created, indexes=[0, 1, 2, 4, 5, 6, 7])

        detail = self.assert_session_error(self.complete(client, sid), 409, "upload_parts_missing")

        self.assertEqual(detail["missingParts"], [3, 8])
        row = self.row(sid)
        assert row is not None
        self.assertEqual((row.state, row.pending_demo_id, row.active_owner_id), ("open", None, OWNER))
        self.assertEqual(self.intake_slot.in_use, 0)
        self.upload_parts(client, created, indexes=[3, 8])
        self.assertEqual(self.complete(client, sid).status_code, 201)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_parts_still_in_flight_reopen_the_session_with_retry_after(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)
        self.assertTrue(self.tracker.try_enter(sid, 4))

        with patch.object(upload_session_service, "PARTS_IN_FLIGHT_WAIT_SECONDS", 0.05):
            response = self.complete(client, sid)

        self.assert_session_error(response, 409, "upload_parts_in_flight")
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertEqual(self.row(sid).state, "open")  # type: ignore[union-attr]
        self.tracker.leave(sid)
        self.assertEqual(self.complete(client, sid).status_code, 201)

    def test_a_busy_intake_slot_answers_intake_busy_and_changes_nothing(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        self.upload_parts(client, created)
        self.assertTrue(self.intake_slot.try_acquire())

        response = self.complete(client, created["sessionId"])

        self.assert_intake_error(response, 503, "INTAKE_BUSY")
        self.assertEqual(response.headers["retry-after"], "5")
        self.assertEqual(self.row(created["sessionId"]).state, "open")  # type: ignore[union-attr]
        self.intake_slot.release()
        self.assertEqual(self.complete(client, created["sessionId"]).status_code, 201)
        self.assertEqual(self.intake_slot.in_use, 0)

    def test_complete_and_the_legacy_upload_share_one_intake_slot(self) -> None:
        client = self.client(with_limits=True)
        created = self.create_ok(client)
        self.upload_parts(client, created)
        self.assertTrue(self.intake_slot.try_acquire())

        legacy = client.post(
            "/uploads/demo", files={"file": ("legacy.dem", DEMO, "application/octet-stream")}
        )
        completed = self.complete(client, created["sessionId"])
        # Parts take the part pool, not the intake slot.
        part = self.put(client, created["sessionId"], created["uploadToken"], 0)

        self.assert_intake_error(legacy, 503, "INTAKE_BUSY")
        self.assert_intake_error(completed, 503, "INTAKE_BUSY")
        self.assertEqual(part.status_code, 200, part.text)
        self.intake_slot.release()
        self.assertEqual(client.post(
            "/uploads/demo", files={"file": ("legacy.dem", DEMO, "application/octet-stream")}
        ).status_code, 201)
        self.assertEqual(self.complete(client, created["sessionId"]).status_code, 201)
        self.assertEqual(self.counts(), (2, 2, 2))

    def test_a_live_lease_answers_202_and_an_expired_one_is_taken_over(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)
        abandoned_demo = str(uuid.uuid4())
        # The dead attempt had already stored its quarantine object.
        store = LocalArtifactStore(self.root / "artifacts")
        reference = store.new_reference(owner_id=OWNER, demo_id=abandoned_demo, kind="source", state="quarantine")
        with open(self.root / "probe.bin", "wb") as handle:
            handle.write(DEMO)
        with open(self.root / "probe.bin", "rb") as handle:
            store.write_stream(
                reference,
                handle,
                max_bytes=len(DEMO),
                chunk_size=1024,
                content_type=None,
                policy_version="artifact_intake_v1",
                now=datetime.now(UTC),
            )
        abandoned_token = _encode_reference_identity(abandoned_demo)
        self.assertTrue(any(abandoned_token in path.as_posix() for path in self.artifact_files()))
        self.update_row(
            sid,
            state="completing",
            pending_demo_id=abandoned_demo,
            lease_until=datetime.now(UTC) + timedelta(minutes=10),
        )

        live = self.complete(client, sid)
        self.assertEqual(live.status_code, 202, live.text)
        self.assertEqual(live.json(), {"state": "completing"})
        self.assertEqual(live.headers["cache-control"], "private, no-store")
        self.assertEqual(self.counts(), (0, 0, 0))

        self.update_row(sid, lease_until=datetime.now(UTC) - timedelta(seconds=1))
        taken = self.complete(client, sid)

        self.assertEqual(taken.status_code, 201, taken.text)
        self.assertNotEqual(taken.json()["id"], abandoned_demo)
        self.assertEqual(self.counts(), (1, 1, 1))
        # Only the new demo's accepted source is left.
        remaining = self.artifact_files()
        self.assertTrue(remaining)
        self.assertFalse(any(abandoned_token in path.as_posix() for path in remaining))
        self.assertTrue(any(_encode_reference_identity(taken.json()["id"]) in path.as_posix() for path in remaining))

    def test_startup_ends_every_lease_so_a_retry_takes_over_at_once(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)
        self.update_row(
            sid,
            state="completing",
            pending_demo_id=str(uuid.uuid4()),
            lease_until=datetime.now(UTC) + timedelta(minutes=15),
        )
        self.assertEqual(self.complete(client, sid).status_code, 202)

        with self.Session() as db:
            self.assertEqual(reset_completing_leases(db), 1)

        self.assertEqual(self.complete(client, sid).status_code, 201)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_an_intake_rejection_fails_the_session_and_purges_staging(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)

        with patch.object(DemoService, "prepare_real_demo", side_effect=ArtifactIntakeError("INTAKE_INTEGRITY_FAILED")):
            response = self.complete(client, sid)

        self.assert_intake_error(response, 400, "INTAKE_INTEGRITY_FAILED")
        row = self.row(sid)
        assert row is not None
        self.assertEqual((row.state, row.error_code, row.active_owner_id), ("failed", "INTAKE_INTEGRITY_FAILED", None))
        self.assertFalse(self.session_dir(sid).exists())
        self.assert_intake_error(self.complete(client, sid), 400, "INTAKE_INTEGRITY_FAILED")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.redis.payloads, [])

    def test_unavailable_storage_keeps_the_session_open_with_its_parts(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)

        with patch.object(
            DemoService, "prepare_real_demo", side_effect=ArtifactIntakeError("INTAKE_STORAGE_UNAVAILABLE")
        ):
            response = self.complete(client, sid)

        self.assert_intake_error(response, 503, "INTAKE_STORAGE_UNAVAILABLE")
        self.assertIn("retry-after", response.headers)
        self.assertEqual(self.row(sid).state, "open")  # type: ignore[union-attr]
        self.assertEqual(len(self.staging.list_parts(OWNER, sid)), 9)
        self.assertEqual(self.complete(client, sid).status_code, 201)

    def test_concurrent_out_of_order_parts_with_a_duplicate_then_complete(self) -> None:
        self.use_file_database()
        data = DEMO * 3  # 25 parts
        client = self.client()
        created = self.create_ok(client, data=data)
        sid, token = created["sessionId"], created["uploadToken"]
        indexes = [*range(created["partCount"] - 1, -1, -1), 7]
        statuses: list[int] = []
        lock = threading.Lock()

        def worker(chunk: list[int]) -> None:
            local = self.client()
            for index in chunk:
                code = self.put(local, sid, token, index, data=data).status_code
                with lock:
                    statuses.append(code)

        threads = [threading.Thread(target=worker, args=(indexes[offset::4],)) for offset in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(statuses, [200] * len(indexes))
        response = self.complete(client, sid)
        self.assertEqual(response.status_code, 201, response.text)
        with self.Session() as db:
            source = json.loads(db.query(DemoJob).one().metadata_json)["sourceArtifact"]
        self.assertEqual(source["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertFalse(self.session_dir(sid).exists())


class DeleteAndStatusTest(UploadSessionTestBase):
    def test_delete_removes_row_and_staging_and_then_is_not_found(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created, indexes=[0, 1])

        response = client.delete(f"/uploads/sessions/{sid}")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertIsNone(self.row(sid))
        self.assertFalse(self.session_dir(sid).exists())
        self.assert_session_error(client.delete(f"/uploads/sessions/{sid}"), 404, "upload_session_not_found")
        self.assert_session_error(self.put(client, sid, created["uploadToken"], 2), 404, "upload_session_not_found")
        self.assertFalse(self.session_dir(sid).exists())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_delete_refuses_a_completing_session_and_an_expired_one_is_gone(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.update_row(
            sid,
            state="completing",
            pending_demo_id=str(uuid.uuid4()),
            lease_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.assert_session_error(client.delete(f"/uploads/sessions/{sid}"), 409, "upload_session_not_open")

        self.update_row(sid, state="open", pending_demo_id=None, lease_until=None)
        self.update_row(sid, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        self.assert_session_error(client.delete(f"/uploads/sessions/{sid}"), 404, "upload_session_not_found")

    def test_unreadable_staging_is_a_503_not_a_500(self) -> None:
        from app.services.storage import StagingError

        client = self.client()
        created = self.create_ok(client)

        with patch.object(self.staging, "list_parts", side_effect=StagingError("Upload staging is unavailable")):
            status = client.get(f"/uploads/sessions/{created['sessionId']}")
            exists = self.create(client)

        self.assert_intake_error(status, 503, "INTAKE_STORAGE_UNAVAILABLE")
        detail = self.assert_session_error(exists, 409, "upload_session_exists")
        self.assertEqual(detail["receivedBytes"], 0)

    def test_current_reports_the_open_session_for_resume(self) -> None:
        client = self.client()
        self.assertEqual(client.get("/uploads/sessions/current").json(), {"session": None})
        created = self.create_ok(client)
        self.upload_parts(client, created, indexes=[0, 2])

        response = client.get("/uploads/sessions/current")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        session = response.json()["session"]
        self.assertEqual(session["sessionId"], created["sessionId"])
        self.assertEqual(session["state"], "open")
        self.assertEqual((session["filename"], session["size"]), ("match.dem", len(DEMO)))
        self.assertEqual((session["partSize"], session["partCount"]), (PART, 9))
        self.assertEqual(session["receivedParts"], [0, 2])
        self.assertEqual(session["receivedBytes"], 2 * PART)
        self.assertEqual(session["part0Sha256"], hashlib.sha256(parts_of(DEMO)[0]).hexdigest())
        self.assertIsNone(session["demo"])
        self.assertIsNone(session["error"])
        self.assertNotIn("uploadToken", session)


class ProductionUploadSessionTest(UploadSessionTestBase):
    mode = "production"

    def test_anonymous_session_routes_are_401_but_the_part_route_needs_only_its_token(self) -> None:
        anonymous = self.client(cookie=False)
        sid = uuid.uuid4().hex
        for method, path, kwargs in (
            ("POST", "/uploads/sessions", {"json": {"filename": "a.dem", "size": 100}}),
            ("GET", "/uploads/sessions/current", {}),
            ("GET", f"/uploads/sessions/{sid}", {}),
            ("POST", f"/uploads/sessions/{sid}/token", {}),
            ("POST", f"/uploads/sessions/{sid}/complete", {}),
            ("DELETE", f"/uploads/sessions/{sid}", {}),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(anonymous.request(method, path, **kwargs).status_code, 401)

        created = self.create_ok(self.client())
        sid, token = created["sessionId"], created["uploadToken"]

        tokenless = self.client(cookie=False)
        self.assertEqual(self.put(tokenless, sid, token, 0).status_code, 200)
        self.assertEqual(self.put(self.client(cookie=False, origin=None), sid, token, 1).status_code, 403)
        self.assertEqual(
            self.put(self.client(cookie=False, origin="https://attacker.example.test"), sid, token, 1).status_code,
            403,
        )
        # No cookie fallback: the owner's own cookie without the token is a 404.
        self.assert_session_error(self.put(self.client(), sid, None, 1), 404, "upload_session_not_found")
        self.assert_session_error(self.put(self.client(), sid, "wrong", 1), 404, "upload_session_not_found")
        # A cookie route with a foreign Origin is refused by the CSRF check.
        foreign = self.client(origin="https://attacker.example.test")
        self.assertEqual(self.complete(foreign, sid).status_code, 403)
        self.assertEqual(self.staging.list_parts(OWNER, sid), {0: PART})

    def test_a_quota_refusal_at_complete_reopens_the_session_and_counts_nothing(self) -> None:
        object.__setattr__(settings, "demo_active_parse_limit", 1)
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)
        # Another of the owner's demos started processing meanwhile.
        blocker = str(uuid.uuid4())
        with self.Session() as db:
            db.add(
                Demo(
                    id=blocker,
                    owner_id=OWNER,
                    legacy_user_id=OWNER,
                    name="busy.dem",
                    original_filename="busy.dem",
                    map_name="de_dust2",
                    tick_rate=64,
                    round_count=0,
                    coaching_event_count=0,
                    status="parsing",
                )
            )
            db.commit()

        response = self.complete(client, sid)

        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["detail"]["code"], "active_parse_limit")
        self.assertEqual(response.headers["retry-after"], "60")
        row = self.row(sid)
        assert row is not None
        self.assertEqual((row.state, row.active_owner_id, row.pending_demo_id), ("open", OWNER, None))
        self.assertEqual(self.counts(), (1, 0, 0))
        self.assertEqual(self.artifact_files(), [])
        self.assertEqual(len(self.staging.list_parts(OWNER, sid)), 9)
        self.assertEqual(self.redis.payloads, [])

        with self.Session() as db:
            db.get(Demo, blocker).status = "completed"  # type: ignore[union-attr]
            db.commit()
        admitted = self.complete(client, sid)
        self.assertEqual(admitted.status_code, 201, admitted.text)
        self.assertEqual(self.counts(), (2, 1, 1))

    def test_a_deleted_account_cannot_complete(self) -> None:
        client = self.client()
        created = self.create_ok(client)
        sid = created["sessionId"]
        self.upload_parts(client, created)
        with self.Session() as db:
            db.query(UploadSession).filter(UploadSession.owner_id == OWNER).delete()
            db.query(Account).filter(Account.owner_id == OWNER).delete()
            db.commit()

        self.assert_session_error(self.complete(client, sid), 404, "upload_session_not_found")

        # The fence itself, for a completion already past its session read.
        with self.Session() as db:
            db.add(Account(owner_id=OWNER))
            db.commit()
        again = self.create_ok(client)
        self.upload_parts(client, again)
        with patch("app.services.upload_admission.account_exists_for_write", return_value=False):
            fenced = self.complete(client, again["sessionId"])
        self.assert_session_error(fenced, 401, "account_deleted")
        self.assertIsNone(self.row(again["sessionId"]))
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.artifact_files(), [])
        self.assertFalse(self.session_dir(again["sessionId"]).exists())


class SweepTest(UploadSessionTestBase):
    def service(self) -> UploadSessionService:
        return UploadSessionService(self.Session(), OWNER, staging=self.staging)

    def sweep(self, now: datetime) -> Any:
        with self.Session() as db:
            return sweep_upload_sessions(db, self.staging, LocalArtifactStore(self.root / "artifacts"), now)

    def test_sweep_expires_abandons_forgets_and_removes_orphans(self) -> None:
        now = datetime.now(UTC)
        client = self.client()
        expired = self.create_ok(client)
        self.update_row(expired["sessionId"], expires_at=now - timedelta(seconds=1))
        abandoned = self.create_ok(self.client(OTHER_OWNER))
        self.update_row(
            abandoned["sessionId"],
            state="completing",
            pending_demo_id=str(uuid.uuid4()),
            lease_until=now - timedelta(seconds=1),
            expires_at=now - timedelta(seconds=1),
        )
        live = self.create_ok(self.client("owner_v1_session_third"))
        finished = self.create_ok(self.client("owner_v1_session_fourth"))
        self.update_row(
            finished["sessionId"],
            state="completed",
            demo_id=str(uuid.uuid4()),
            active_owner_id=None,
            updated_at=now - timedelta(hours=2),
        )
        old_orphan = uuid.uuid4().hex
        young_orphan = uuid.uuid4().hex
        self.staging.create_session(OWNER, old_orphan)
        self.staging.create_session(OWNER, young_orphan)
        aged = (now - timedelta(minutes=30)).timestamp()
        marker = self.session_dir(old_orphan) / ".session"
        import os

        os.utime(marker, (aged, aged))

        result = self.sweep(now)

        self.assertEqual((result.expired, result.abandoned, result.finished, result.orphan_dirs), (1, 1, 1, 1))
        self.assertIsNone(self.row(expired["sessionId"]))
        self.assertIsNone(self.row(abandoned["sessionId"]))
        self.assertIsNone(self.row(finished["sessionId"]))
        self.assertEqual(self.row(live["sessionId"]).state, "open")  # type: ignore[union-attr]
        self.assertFalse(self.session_dir(expired["sessionId"]).exists())
        self.assertFalse(self.session_dir(abandoned["sessionId"], OTHER_OWNER).exists())
        self.assertFalse(self.session_dir(old_orphan).exists())
        self.assertTrue(self.session_dir(young_orphan).exists())
        self.assertTrue(self.session_dir(live["sessionId"], "owner_v1_session_third").exists())

    def test_a_failing_step_does_not_stop_the_others(self) -> None:
        now = datetime.now(UTC)
        expired = self.create_ok(self.client())
        self.update_row(expired["sessionId"], expires_at=now - timedelta(seconds=1))

        with patch.object(self.staging, "iter_session_dirs", side_effect=OSError("disk")), self.assertLogs(
            upload_session_service.logger, level="WARNING"
        ) as logs:
            result = self.sweep(now)

        self.assertEqual(result.expired, 1)
        self.assertNotIn("disk", " ".join(logs.output))


class UploadSlotsTest(unittest.TestCase):
    def test_slots_count_refuse_when_full_and_reject_an_extra_release(self) -> None:
        from app.core.upload_slots import PartPool

        slot = IntakeSlot()
        self.assertTrue(slot.try_acquire())
        self.assertFalse(slot.try_acquire())
        slot.release()
        self.assertEqual(slot.in_use, 0)
        with self.assertRaises(RuntimeError):
            slot.release()
        pool = PartPool(2)
        self.assertTrue(pool.try_acquire() and pool.try_acquire())
        self.assertFalse(pool.try_acquire())
        with self.assertRaises(ValueError):
            PartPool(0)

    def test_parts_in_flight_waits_for_the_last_part(self) -> None:
        tracker = PartsInFlight()
        self.assertTrue(tracker.try_enter("s", 2))
        self.assertTrue(tracker.try_enter("s", 2))
        self.assertFalse(tracker.try_enter("s", 2))
        self.assertFalse(tracker.wait_idle("s", 0.01))
        threading.Timer(0.05, tracker.leave, args=("s",)).start()
        tracker.leave("s")
        self.assertTrue(tracker.wait_idle("s", 5))
        self.assertEqual(tracker.count("s"), 0)
        self.assertEqual(tracker.pop_tally("s"), 2)


class MiddlewareWiringTest(unittest.TestCase):
    def test_every_new_upload_route_is_registered(self) -> None:
        paths = {(route.path, tuple(sorted(route.methods))) for route in uploads.router.routes}  # type: ignore[attr-defined]
        for expected in (
            ("/uploads/sessions", ("POST",)),
            ("/uploads/sessions/current", ("GET",)),
            ("/uploads/sessions/{session_id}", ("GET",)),
            ("/uploads/sessions/{session_id}", ("DELETE",)),
            ("/uploads/sessions/{session_id}/token", ("POST",)),
            ("/uploads/sessions/{session_id}/complete", ("POST",)),
            ("/uploads/sessions/{session_id}/parts/{index}", ("PUT",)),
            ("/uploads/demo", ("POST",)),
        ):
            self.assertIn(expected, paths)

    def test_the_app_shares_the_intake_slot_and_sizes_the_part_pool_from_settings(self) -> None:
        from app.core.upload_slots import INTAKE_SLOT
        from app.main import app

        kwargs = {middleware.cls: middleware.kwargs for middleware in app.user_middleware}
        limits: dict[str, Any] = kwargs[MultipartRequestLimitMiddleware]  # type: ignore[index]
        self.assertIs(limits["intake_slot"], INTAKE_SLOT)
        self.assertEqual(limits["part_pool_size"], settings.upload_part_pool)
        self.assertEqual(limits["upload_part_envelope_limit_bytes"], settings.upload_part_bytes + 64 * 1024)


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


# The active-demo statuses this file seeds must count as in flight.
assert "parsing" in ACTIVE_DEMO_STATUSES


if __name__ == "__main__":
    unittest.main()
