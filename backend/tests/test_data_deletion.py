"""Hard deletion of a match or a whole account: rows, storage, outbox, sessions, fences.

Every database here enforces foreign keys (PRAGMA foreign_keys=ON on each
connection), so the explicit delete order is exercised the way Postgres
enforces it. Storage is a real LocalArtifactStore in a temp dir, or the fake S3
client from the storage contract tests.
"""

import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from test_artifact_store_contract import _FakeS3Client

from app.api import auth as auth_api
from app.api import deletion as deletion_api
from app.api import uploads as uploads_api
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.migrations.runner import run_schema_migrations
from app.models import (
    Account,
    CoachingEvent,
    CoachingFeedback,
    DeletionTask,
    Demo,
    DemoJob,
    ExternalIdentity,
    SteamConnection,
    SteamMatch,
    UploadLedger,
    UploadSession,
)
from app.services import deletion_service as deletion_module
from app.services.auth_service import AuthService, get_auth_service
from app.services.deletion_service import (
    DeletionService,
    account_exists_for_write,
    final_sweep_delay,
)
from app.services.storage import (
    ArtifactStoreError,
    LocalArtifactStore,
    LocalStorageService,
    S3ArtifactStore,
    StagingSessionGone,
    UploadStagingStore,
)
from app.services.upload_quota import UploadQuotaService, prune_upload_ledger, record_upload

OWNER = "owner_v1_deletion_owner"
OTHER_OWNER = "owner_v1_deletion_other"
# base64url("abc") == "YWJj" is a prefix of base64url("abcd") == "YWJjZA".
PREFIX_OWNER = "abc"
PREFIX_OTHER_OWNER = "abcd"
ORIGIN = "https://coach.example.test"
SESSION_TOKEN = "deletion-owner-session"
SECOND_DEVICE_TOKEN = "deletion-owner-second-device"
OTHER_SESSION_TOKEN = "deletion-other-owner-session"
VALID_DEMO = b"HL2DEMO\x00deletion-fixture"
ACCOUNT_CONFIRMATION = {"confirm": "delete-my-account"}


def fk_engine(url: str = "sqlite://", **kwargs: Any) -> Engine:
    if url == "sqlite://":
        kwargs.setdefault("poolclass", StaticPool)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, **kwargs)

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.payloads: list[str] = []

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def lpush(self, _queue: str, payload: str) -> None:
        self.payloads.append(payload)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def put_artifact(
    store: Any,
    owner_id: str,
    demo_id: str,
    *,
    kind: str = "source",
    state: str = "accepted",
    payload: bytes = b"artifact-bytes",
    now: datetime | None = None,
) -> str:
    reference = store.new_reference(owner_id=owner_id, demo_id=demo_id, kind=kind, state=state)
    store.write_stream(reference, io.BytesIO(payload), max_bytes=1024 * 1024, now=now)
    return reference


def stored_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def seed_demo(
    db: Session,
    owner_id: str,
    *,
    demo_id: str | None = None,
    status: str = "completed",
    source_key: str | None = None,
    replay_key: str | None = None,
    job_metadata: dict[str, Any] | None = None,
    with_coaching: bool = True,
    created_at: datetime | None = None,
) -> str:
    demo_id = demo_id or str(uuid.uuid4())
    created = created_at or datetime.now(UTC)
    db.add(
        Demo(
            id=demo_id,
            owner_id=owner_id,
            legacy_user_id=owner_id,
            name=f"Demo {demo_id[:8]}",
            original_filename=f"{demo_id[:8]}.dem",
            map_name="de_dust2",
            tick_rate=64,
            round_count=1,
            coaching_event_count=1 if with_coaching else 0,
            status=status,
            source_storage_key=source_key,
            replay_storage_key=replay_key,
            created_at=created,
            updated_at=created,
        )
    )
    db.flush()
    db.add(
        DemoJob(
            id=str(uuid.uuid4()),
            demo_id=demo_id,
            job_type="real_parse",
            status="completed" if status == "completed" else "queued",
            attempts=1,
            metadata_json=json.dumps(job_metadata or {}),
        )
    )
    if with_coaching:
        event_id = str(uuid.uuid4())
        db.add(
            CoachingEvent(
                id=event_id,
                demo_id=demo_id,
                round_number=1,
                player_id="76561198000000001",
                player_name="player",
                tick_start=10,
                tick_end=20,
                category="utility",
                severity="medium",
                title="t",
                message="m",
                structured_context_json={},
                confidence=0.9,
            )
        )
        db.flush()
        db.add(
            CoachingFeedback(
                id=str(uuid.uuid4()),
                demo_id=demo_id,
                event_id=event_id,
                owner_id=owner_id,
                verdict="helpful",
            )
        )
    db.commit()
    return demo_id


def seed_account(db: Session, owner_id: str, *, steam_id: str = "76561198000000042") -> None:
    now = datetime.now(UTC)
    db.add(Account(owner_id=owner_id, display_name="Player", created_at=now, updated_at=now))
    db.flush()
    db.add(
        ExternalIdentity(
            id=str(uuid.uuid4()),
            provider="steam",
            subject=steam_id,
            owner_id=owner_id,
            created_at=now,
            last_login_at=now,
        )
    )
    db.commit()


def seed_steam_match(db: Session, owner_id: str, *, demo_id: str | None, steam_id: str) -> str:
    now = datetime.now(UTC)
    connection_id = str(uuid.uuid4())
    db.add(
        SteamConnection(
            id=connection_id,
            owner_id=owner_id,
            steam_id64=steam_id,
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
    db.flush()
    match_id = str(uuid.uuid4())
    db.add(
        SteamMatch(
            id=match_id,
            connection_id=connection_id,
            owner_id=owner_id,
            share_code_hash=hashlib.sha256(match_id.encode()).hexdigest(),
            share_code_ciphertext=b"share-code-ciphertext",
            share_code_nonce=b"3" * 12,
            encryption_key_version="v1",
            status="ready" if demo_id else "discovered",
            demo_id=demo_id,
            provider_id="licensed",
            import_attempts=1,
            import_completed_at=now,
            parser_dispatched_at=now,
            parser_dispatched_job_id=str(uuid.uuid4()),
            map_name="de_dust2",
            duration_seconds=1800,
            ct_round_wins=13,
            t_round_wins=9,
            players_json=json.dumps([{"steamId": "76561198000000001", "name": "p"}]),
            discovered_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return match_id


def seed_upload_session(
    db: Session,
    staging: UploadStagingStore | None,
    owner_id: str,
    *,
    state: str = "open",
    demo_id: str | None = None,
    parts: dict[int, bytes] | None = None,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    """One upload_sessions row in a valid state; with `staging`, its directory and parts too."""
    session_id = uuid.uuid4().hex
    now = created_at or datetime.now(UTC)
    part_size = 4 * 1024 * 1024
    if staging is not None:
        staging.create_session(owner_id, session_id)
        for index, data in (parts or {}).items():
            staging.write_part(owner_id, session_id, index, data, expected_size=len(data))
    active = state in ("open", "completing")
    db.add(
        UploadSession(
            id=session_id,
            owner_id=owner_id,
            active_owner_id=owner_id if active else None,
            state=state,
            display_filename="match.dem",
            content_type="application/octet-stream",
            file_size=part_size * 2,
            part_size=part_size,
            part_count=2,
            token_sha256=hashlib.sha256(session_id.encode()).hexdigest(),
            pending_demo_id=str(uuid.uuid4()) if state == "completing" else None,
            demo_id=demo_id if state == "completed" else None,
            error_code="INTAKE_TRUNCATED" if state == "failed" else None,
            lease_until=now + timedelta(minutes=15) if state == "completing" else None,
            created_at=now,
            updated_at=now,
            expires_at=expires_at or now + timedelta(hours=24),
        )
    )
    db.commit()
    return session_id


def session_key(token: str) -> str:
    return "auth:session:" + hashlib.sha256(token.encode("ascii")).hexdigest()


def seed_session(redis: FakeRedis, token: str, owner_id: str, *, issued_at_ms: int | None) -> None:
    record: dict[str, Any] = {"ownerId": owner_id, "expiresAt": int(time.time()) + 300}
    if issued_at_ms is not None:
        record["issuedAt"] = issued_at_ms
    redis.setex(session_key(token), 300, json.dumps(record))


def production_auth_settings() -> Settings:
    return Settings(
        auth_mode="production",
        auth_provider="oidc",
        frontend_public_url=ORIGIN,
        backend_public_url=ORIGIN,
        cors_origins_raw=ORIGIN,
        auth_cookie_secure=True,
    )


@contextmanager
def patched_settings(**values: Any) -> Iterator[None]:
    original = {name: getattr(settings, name) for name in values}
    for name, value in values.items():
        object.__setattr__(settings, name, value)
    try:
        yield
    finally:
        for name, value in original.items():
            object.__setattr__(settings, name, value)


# -- storage ------------------------------------------------------------------------


class LocalArtifactPurgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalArtifactStore(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_demo_purge_covers_every_state_and_kind_and_nothing_else(self) -> None:
        mine = [
            put_artifact(self.store, OWNER, "demo-a", kind=kind, state=state)
            for kind in ("source", "replay", "video", "summary")
            for state in ("quarantine", "accepted", "rejected", "abandoned", "cleaned")
        ]
        sibling = put_artifact(self.store, OWNER, "demo-b")
        other_owner = put_artifact(self.store, OTHER_OWNER, "demo-a")

        result = self.store.purge_prefix(owner_id=OWNER, demo_id="demo-a")

        self.assertEqual((result.found, result.deleted, result.complete), (len(mine), len(mine), True))
        for reference in mine:
            self.assertIsNone(self.store.head(reference))
        self.assertIsNotNone(self.store.head(sibling))
        self.assertIsNotNone(self.store.head(other_owner))
        # A second pass finds nothing: that is what lets an outbox task finish.
        self.assertEqual(self.store.purge_prefix(owner_id=OWNER, demo_id="demo-a").found, 0)
        # Emptied directories go too, not only the files.
        owner_token = base64.urlsafe_b64encode(OWNER.encode()).decode().rstrip("=")
        demo_token = base64.urlsafe_b64encode(b"demo-a").decode().rstrip("=")
        self.assertFalse(
            any(
                path.parts[-2:] == (owner_token, demo_token)
                for path in self.root.rglob("*")
                if path.is_dir()
            )
        )

    def test_owner_tokens_that_share_a_prefix_never_match(self) -> None:
        mine = put_artifact(self.store, PREFIX_OWNER, "demo-1")
        theirs = put_artifact(self.store, PREFIX_OTHER_OWNER, "demo-1")

        result = self.store.purge_prefix(owner_id=PREFIX_OWNER)

        self.assertEqual(result.found, 1)
        self.assertIsNone(self.store.head(mine))
        self.assertIsNotNone(self.store.head(theirs))

    def test_corrupt_metadata_and_leftover_temp_leaves_are_still_purged(self) -> None:
        reference = put_artifact(self.store, OWNER, "demo-a")
        leaves = stored_files(self.root)
        metadata_leaf = next(path for path in leaves if path.name.endswith(".metadata.json"))
        metadata_leaf.write_text("{not json", encoding="utf-8")
        (metadata_leaf.parent / f".{metadata_leaf.name.split('.')[0]}.abandoned.tmp").write_bytes(b"x")
        with self.assertRaises(ArtifactStoreError):
            self.store.delete(reference)

        result = self.store.purge_prefix(owner_id=OWNER, demo_id="demo-a")

        self.assertEqual(result.found, 1)
        self.assertEqual(stored_files(self.root), [])

    def test_created_before_keeps_newer_objects(self) -> None:
        cutoff = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        old = put_artifact(self.store, OWNER, "demo-old", now=cutoff - timedelta(minutes=5))
        new = put_artifact(self.store, OWNER, "demo-new", now=cutoff + timedelta(minutes=5))

        result = self.store.purge_prefix(owner_id=OWNER, created_before=cutoff)

        self.assertEqual(result.found, 1)
        self.assertIsNone(self.store.head(old))
        self.assertIsNotNone(self.store.head(new))

    def test_batch_limit_reports_an_incomplete_pass(self) -> None:
        for _ in range(3):
            put_artifact(self.store, OWNER, "demo-a", kind="replay")

        first = self.store.purge_prefix(owner_id=OWNER, demo_id="demo-a", max_objects=2)
        second = self.store.purge_prefix(owner_id=OWNER, demo_id="demo-a", max_objects=2)

        self.assertEqual((first.found, first.complete), (2, False))
        self.assertEqual((second.found, second.complete), (1, True))

    def test_exact_references_are_deleted_without_a_generation_check(self) -> None:
        reference = put_artifact(self.store, OWNER, "demo-a")
        next(path for path in stored_files(self.root) if path.name.endswith(".metadata.json")).write_text(
            "{}", encoding="utf-8"
        )

        self.assertEqual(self.store.purge_references([reference]), 1)
        self.assertEqual(stored_files(self.root), [])
        # Missing objects are nothing to do, not an error.
        self.assertEqual(self.store.purge_references([reference]), 1)

    def test_a_demo_purge_never_pulls_the_owner_directory_from_under_another_write(self) -> None:
        from app.services.storage.contract import purge_scope_tokens

        put_artifact(self.store, OWNER, "demo-deleted", kind="replay")
        backend = self.store.directory_backend
        original_open_child = backend.open_child
        owner_token = purge_scope_tokens(OWNER, None)[0]
        fired: list[Any] = []

        def open_child(directory: Any, name: str, *, create: bool) -> Any:
            child = original_open_child(directory, name, create=create)
            # The purge of the deleted demo runs right after the other write opened
            # {kind}/{owner} and before it creates its own demo directory inside.
            if create and name == owner_token and not fired:
                fired.append(self.store.purge_prefix(owner_id=OWNER, demo_id="demo-deleted"))
            return child

        with patch.object(backend, "open_child", open_child):
            written = put_artifact(self.store, OWNER, "demo-parsing", kind="replay")

        self.assertEqual(fired[0].found, 1)
        self.assertIsNotNone(self.store.head(written))
        # The owner-wide purge (an account deletion) still removes the owner directories.
        self.store.purge_prefix(owner_id=OWNER)
        self.assertFalse(any(path.name == owner_token for path in self.root.rglob("*")))

    def test_purging_an_empty_root_is_a_clean_no_op(self) -> None:
        missing = LocalArtifactStore(self.root / "never-created")
        self.assertEqual(missing.purge_prefix(owner_id=OWNER).found, 0)


class _PurgeFakeS3Client(_FakeS3Client):
    def __init__(self, *, delete_objects_fails: bool = False, per_key_errors: set[str] | None = None) -> None:
        super().__init__()
        self.delete_objects_calls: list[dict[str, Any]] = []
        self.delete_object_calls: list[dict[str, Any]] = []
        self.delete_objects_fails = delete_objects_fails
        self.per_key_errors = per_key_errors or set()

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_objects_calls.append(kwargs)
        if self.delete_objects_fails:
            raise RuntimeError("DeleteObjects is not supported")
        errors = []
        for item in kwargs["Delete"]["Objects"]:
            if item["Key"] in self.per_key_errors:
                errors.append({"Key": item["Key"], "Code": "AccessDenied", "Message": "no"})
                continue
            self.objects.pop((kwargs["Bucket"], item["Key"]), None)
        return {"Errors": errors} if errors else {}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_object_calls.append(kwargs)
        return super().delete_object(**kwargs)


class _VersionedPurgeFakeS3Client(_PurgeFakeS3Client):
    """Bucket versioning on: a delete without VersionId only adds a delete marker."""

    def __init__(self, status: str | Exception = "Enabled") -> None:
        super().__init__()
        self.status = status
        self.hidden: dict[tuple[str, str], list[Any]] = {}
        self.markers: dict[tuple[str, str], list[str]] = {}
        self.versioning_calls = 0

    def get_bucket_versioning(self, **_kwargs: Any) -> dict[str, Any]:
        self.versioning_calls += 1
        if isinstance(self.status, Exception):
            raise self.status
        return {"Status": self.status} if self.status else {}

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_objects_calls.append(kwargs)
        for item in kwargs["Delete"]["Objects"]:
            self._delete(kwargs["Bucket"], item["Key"], item.get("VersionId"))
        return {}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_object_calls.append(kwargs)
        self._delete(kwargs["Bucket"], kwargs["Key"], kwargs.get("VersionId"))
        return {}

    def _delete(self, bucket: str, key: str, version_id: str | None) -> None:
        slot = (bucket, key)
        if version_id is None:
            # The data stays as a noncurrent version behind a new delete marker.
            self.hidden.setdefault(slot, []).extend(self.objects.pop(slot, []))
            self.markers.setdefault(slot, []).append(f"marker-{uuid.uuid4().hex}")
            return
        for store in (self.objects, self.hidden):
            store[slot] = [item for item in store.get(slot, []) if item.version_id != version_id]
            if not store[slot]:
                store.pop(slot)
        self.markers[slot] = [marker for marker in self.markers.get(slot, []) if marker != version_id]
        if not self.markers[slot]:
            self.markers.pop(slot)

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        versions, markers = [], []
        for store in (self.objects, self.hidden):
            for (bucket, key), items in sorted(store.items()):
                if bucket == kwargs["Bucket"] and key.startswith(kwargs["Prefix"]):
                    versions += [
                        {"Key": key, "VersionId": item.version_id, "LastModified": item.last_modified}
                        for item in items
                    ]
        for (bucket, key), ids in sorted(self.markers.items()):
            if bucket == kwargs["Bucket"] and key.startswith(kwargs["Prefix"]):
                markers += [{"Key": key, "VersionId": marker, "LastModified": datetime.now(UTC)} for marker in ids]
        return {"Versions": versions, "DeleteMarkers": markers, "IsTruncated": False}

    def data_left(self) -> int:
        return sum(len(items) for items in self.objects.values()) + sum(len(items) for items in self.hidden.values())


class S3ArtifactPurgeTest(unittest.TestCase):
    def store(self, client: _PurgeFakeS3Client) -> S3ArtifactStore:
        return S3ArtifactStore(bucket="artifacts", prefix="cs2-artifacts-v1", client=client)

    def test_demo_purge_lists_every_prefix_and_deletes_by_raw_key(self) -> None:
        client = _PurgeFakeS3Client()
        store = self.store(client)
        mine = [
            put_artifact(store, OWNER, "demo-a", kind="replay", state=state)
            for state in ("quarantine", "accepted")
        ]
        sibling = put_artifact(store, OWNER, "demo-b")

        result = store.purge_prefix(owner_id=OWNER, demo_id="demo-a")

        self.assertEqual((result.found, result.deleted, result.complete), (2, 2, True))
        for reference in mine:
            self.assertIsNone(store.head(reference))
        self.assertIsNotNone(store.head(sibling))
        # Unconditional: no IfMatch/VersionId anywhere in a purge.
        for call in client.delete_objects_calls:
            self.assertEqual(set(call), {"Bucket", "Delete"})
            for item in call["Delete"]["Objects"]:
                self.assertEqual(set(item), {"Key"})

    def test_owner_prefix_ends_in_a_separator(self) -> None:
        client = _PurgeFakeS3Client()
        store = self.store(client)
        mine = put_artifact(store, PREFIX_OWNER, "demo-1")
        theirs = put_artifact(store, PREFIX_OTHER_OWNER, "demo-1")

        self.assertEqual(store.purge_prefix(owner_id=PREFIX_OWNER).found, 1)
        self.assertIsNone(store.head(mine))
        self.assertIsNotNone(store.head(theirs))

    def test_created_before_uses_last_modified(self) -> None:
        client = _PurgeFakeS3Client()
        store = self.store(client)
        old = put_artifact(store, OWNER, "demo-old")
        new = put_artifact(store, OWNER, "demo-new")
        cutoff = datetime.now(UTC)
        for (_bucket, key), versions in client.objects.items():
            if "ZGVtby1uZXc" in key:  # demo-new
                versions[-1].last_modified = cutoff + timedelta(minutes=1)
            else:
                versions[-1].last_modified = cutoff - timedelta(minutes=1)

        self.assertEqual(store.purge_prefix(owner_id=OWNER, created_before=cutoff).found, 1)
        self.assertIsNone(store.head(old))
        self.assertIsNotNone(store.head(new))

    def test_falls_back_to_plain_deletes_without_delete_objects(self) -> None:
        client = _PurgeFakeS3Client(delete_objects_fails=True)
        store = self.store(client)
        reference = put_artifact(store, OWNER, "demo-a")

        self.assertEqual(store.purge_references([reference]), 1)
        self.assertIsNone(store.head(reference))
        self.assertEqual(set(client.delete_object_calls[-1]), {"Bucket", "Key"})

    def test_a_versioned_bucket_loses_every_version_and_marker(self) -> None:
        client = _VersionedPurgeFakeS3Client()
        store = self.store(client)
        mine = put_artifact(store, OWNER, "demo-a")
        put_artifact(store, OWNER, "demo-a", kind="replay")
        sibling = put_artifact(store, OWNER, "demo-b")
        # An earlier plain delete already left a noncurrent version behind a marker.
        client._delete("artifacts", store._object_key(store.parse_reference(mine)), None)
        self.assertEqual(client.data_left(), 3)

        first = store.purge_prefix(owner_id=OWNER, demo_id="demo-a")
        second = store.purge_prefix(owner_id=OWNER, demo_id="demo-a")

        self.assertEqual((first.found, first.complete), (3, True))  # two versions and one marker
        self.assertEqual(second.found, 0, "the task may finish only once nothing is left")
        self.assertEqual(client.data_left(), 1)
        self.assertIsNotNone(store.head(sibling))
        self.assertEqual(client.markers, {})
        for call in client.delete_objects_calls:
            for item in call["Delete"]["Objects"]:
                self.assertEqual(set(item), {"Key", "VersionId"})
        self.assertEqual(client.versioning_calls, 1, "the bucket is asked once")

        store.purge_references([sibling])
        self.assertEqual(client.data_left(), 0)

    def test_versioned_cutoff_spares_keys_first_written_after_it(self) -> None:
        client = _VersionedPurgeFakeS3Client()
        store = self.store(client)
        old = put_artifact(store, OWNER, "demo-old")
        new = put_artifact(store, OWNER, "demo-new")
        cutoff = datetime.now(UTC)
        for (_bucket, key), versions in client.objects.items():
            versions[-1].last_modified = cutoff + timedelta(minutes=1) if "ZGVtby1uZXc" in key else cutoff - timedelta(minutes=1)

        self.assertEqual(store.purge_prefix(owner_id=OWNER, created_before=cutoff).found, 1)
        self.assertIsNone(store.head(old))
        self.assertIsNotNone(store.head(new))

    def test_r2_without_bucket_versioning_keeps_plain_deletes(self) -> None:
        not_implemented = Exception("GetBucketVersioning is not implemented")
        not_implemented.response = {"Error": {"Code": "NotImplemented"}}  # type: ignore[attr-defined]
        client = _VersionedPurgeFakeS3Client(status=not_implemented)
        store = self.store(client)
        put_artifact(store, OWNER, "demo-a")
        store.purge_prefix(owner_id=OWNER, demo_id="demo-a")
        store.purge_prefix(owner_id=OWNER, demo_id="demo-a")
        self.assertEqual(client.versioning_calls, 1)
        for call in client.delete_objects_calls:
            for item in call["Delete"]["Objects"]:
                self.assertEqual(set(item), {"Key"})

    def test_a_per_key_failure_is_reported_not_swallowed(self) -> None:
        client = _PurgeFakeS3Client()
        store = self.store(client)
        put_artifact(store, OWNER, "demo-a")
        client.per_key_errors = {key for (_bucket, key) in client.objects}

        with self.assertRaises(ArtifactStoreError):
            store.purge_prefix(owner_id=OWNER, demo_id="demo-a")


class LegacyKeyPurgeTest(unittest.TestCase):
    def test_legacy_keys_are_unlinked_when_present_and_never_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = LocalStorageService(root)
            path = legacy.path_for_key("local://uploads/demo-a/match.dem")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"dem")

            self.assertTrue(legacy.purge_key("local://uploads/demo-a/match.dem"))
            self.assertFalse(path.exists())
            self.assertFalse(legacy.purge_key("local://uploads/demo-a/match.dem"))
            self.assertFalse(legacy.purge_key("local://replays/../../escape.json"))
            self.assertFalse(legacy.purge_key("not-a-key"))


# -- demo deletion ------------------------------------------------------------------


class DemoDeletionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalArtifactStore(self.root / "artifacts")
        self.legacy = LocalStorageService(self.root / "legacy")
        self.clock = Clock(datetime.now(UTC))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.engine.dispose()

    def service(self, db: Session, **kwargs: Any) -> DeletionService:
        return DeletionService(
            db,
            artifact_store=kwargs.pop("artifact_store", self.store),
            legacy_storage=self.legacy,
            runtime_settings=kwargs.pop("runtime_settings", Settings(auth_mode="test")),
            clock=self.clock,
            **kwargs,
        )

    def seed_full_demo(self, owner_id: str = OWNER) -> tuple[str, list[str]]:
        demo_id = str(uuid.uuid4())
        source = put_artifact(self.store, owner_id, demo_id, kind="source")
        replay = put_artifact(self.store, owner_id, demo_id, kind="replay")
        old_replay = put_artifact(self.store, owner_id, demo_id, kind="replay")
        video = put_artifact(self.store, owner_id, demo_id, kind="video")
        quarantine = put_artifact(self.store, owner_id, demo_id, state="quarantine")
        with self.Session() as db:
            seed_demo(
                db,
                owner_id,
                demo_id=demo_id,
                source_key=source,
                replay_key=replay,
                job_metadata={"sourceArtifact": {"reference": source}, "outputArtifact": {"reference": video}},
            )
        return demo_id, [source, replay, old_replay, video, quarantine]

    def test_rows_and_every_object_go_and_the_task_finishes_after_the_final_sweep(self) -> None:
        demo_id, references = self.seed_full_demo()
        other_demo, other_refs = self.seed_full_demo(OTHER_OWNER)

        with self.Session() as db:
            self.assertTrue(self.service(db).delete_demo(OWNER, demo_id))

        with self.Session() as db:
            self.assertIsNone(db.get(Demo, demo_id))
            for model in (DemoJob, CoachingEvent, CoachingFeedback):
                self.assertEqual(db.query(model).filter(model.demo_id == demo_id).count(), 0)
            task = db.query(DeletionTask).one()
            self.assertEqual((task.owner_id, task.demo_id, task.attempts), (OWNER, demo_id, 0))
            self.assertIn(references[0], task.artifact_refs)
            # The immediate pass ran; the task now waits for the final sweep.
            self.assertEqual(
                task.next_attempt_at.replace(tzinfo=UTC),
                task.final_sweep_after.replace(tzinfo=UTC),
            )
            # The other owner is untouched.
            self.assertIsNotNone(db.get(Demo, other_demo))
            self.assertEqual(db.query(CoachingFeedback).filter_by(owner_id=OTHER_OWNER).count(), 1)
        for reference in references:
            self.assertIsNone(self.store.head(reference))
        for reference in other_refs:
            self.assertIsNotNone(self.store.head(reference))

        with self.Session() as db:
            # Not due yet: nothing is claimed before the final sweep.
            self.assertEqual(self.service(db).drain_due_tasks(), 0)
            self.clock.advance(final_sweep_delay(Settings()) + timedelta(seconds=1))
            self.assertEqual(self.service(db).drain_due_tasks(), 1)
            self.assertEqual(db.query(DeletionTask).count(), 0)

    def test_a_late_write_is_caught_by_the_final_sweep(self) -> None:
        demo_id, _ = self.seed_full_demo()
        with self.Session() as db:
            self.service(db).delete_demo(OWNER, demo_id)
        # A parse that was still running writes its replay after the delete.
        late = put_artifact(self.store, OWNER, demo_id, kind="replay")

        self.clock.advance(final_sweep_delay(Settings()) + timedelta(seconds=1))
        with self.Session() as db:
            self.service(db).drain_due_tasks()
            # It found (and deleted) an object, so it confirms with one more pass.
            self.assertIsNone(self.store.head(late))
            task_id = db.query(DeletionTask).one().id
            self.clock.advance(timedelta(minutes=2))
            self.service(db).drain_due_tasks()
            self.assertIsNone(db.get(DeletionTask, task_id))

    def test_storage_failures_are_recorded_and_retried(self) -> None:
        demo_id, references = self.seed_full_demo()

        class FailingStore(LocalArtifactStore):
            failing = True

            def purge_prefix(self, **kwargs: Any) -> Any:
                if FailingStore.failing:
                    raise ArtifactStoreError("Artifact purge listing failed safely")
                return super().purge_prefix(**kwargs)

        failing = FailingStore(self.root / "artifacts")
        with self.Session() as db, self.assertLogs(deletion_module.logger, "WARNING"):
            self.assertTrue(self.service(db, artifact_store=failing).delete_demo(OWNER, demo_id))
            task = db.query(DeletionTask).one()
            self.assertEqual(task.attempts, 1)
            self.assertEqual(task.last_error, "ArtifactStoreError: Artifact purge listing failed safely")
            self.assertNotIn(str(self.root), task.last_error)
            retry_at = task.next_attempt_at.replace(tzinfo=UTC)
            self.assertGreater(retry_at, self.clock.now)

        FailingStore.failing = False
        self.clock.now = retry_at
        with self.Session() as db:
            self.assertEqual(self.service(db, artifact_store=failing).drain_due_tasks(), 1)
            task = db.query(DeletionTask).one()
            self.assertIsNone(task.last_error)
        for reference in references:
            self.assertIsNone(self.store.head(reference))

    def test_a_drain_never_raises_when_the_store_is_down(self) -> None:
        demo_id, _ = self.seed_full_demo()

        class DownStore(LocalArtifactStore):
            def purge_prefix(self, **kwargs: Any) -> Any:
                raise OSError("connection refused to /secret/path")

            def purge_references(self, references: Any) -> int:
                raise OSError("connection refused")

        down = DownStore(self.root / "artifacts")
        with self.Session() as db, self.assertLogs(deletion_module.logger, "WARNING") as logs:
            self.service(db, artifact_store=down).delete_demo(OWNER, demo_id)
            self.clock.advance(timedelta(hours=2))
            self.service(db, artifact_store=down).drain_due_tasks()
            task = db.query(DeletionTask).one()
            self.assertEqual(task.attempts, 2)
            self.assertEqual(task.last_error, "OSError")
        self.assertFalse(any("/secret/path" in line for line in logs.output))

    def test_unknown_foreign_and_already_deleted_demos_are_not_found(self) -> None:
        demo_id, _ = self.seed_full_demo()
        with self.Session() as db:
            service = self.service(db)
            self.assertFalse(service.delete_demo(OTHER_OWNER, demo_id))
            self.assertFalse(service.delete_demo(OWNER, "no-such-demo"))
            self.assertTrue(service.delete_demo(OWNER, demo_id))
            self.assertFalse(service.delete_demo(OWNER, demo_id))
            self.assertEqual(db.query(DeletionTask).count(), 1)

    def test_a_steam_match_is_unlinked_and_reset_for_import(self) -> None:
        with self.Session() as db:
            seed_account(db, OWNER)
            demo_id = seed_demo(db, OWNER)
            match_id = seed_steam_match(db, OWNER, demo_id=demo_id, steam_id="76561198000000042")

        with self.Session() as db:
            self.service(db).delete_demo(OWNER, demo_id)

        with self.Session() as db:
            match = db.get(SteamMatch, match_id)
            assert match is not None
            self.assertIsNone(match.demo_id)
            self.assertEqual(match.status, "discovered")
            for field in (
                "players_json",
                "map_name",
                "duration_seconds",
                "ct_round_wins",
                "t_round_wins",
                "parser_dispatched_at",
                "parser_dispatched_job_id",
                "provider_id",
                "import_completed_at",
            ):
                self.assertIsNone(getattr(match, field), field)
            # The link and the account survive; only the demo went.
            self.assertIsNotNone(db.get(Account, OWNER))
            self.assertEqual(db.query(SteamConnection).count(), 1)

    def test_legacy_local_keys_of_a_mock_demo_never_block_deletion(self) -> None:
        demo_id = str(uuid.uuid4())
        legacy_key = f"local://uploads/{demo_id}/mock.dem"
        legacy_path = self.legacy.path_for_key(legacy_key)
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_bytes(b"mock")
        with self.Session() as db:
            seed_demo(
                db,
                OWNER,
                demo_id=demo_id,
                source_key=legacy_key,
                replay_key=f"local://replays/{demo_id}.json",  # backfilled, never written
            )
            self.assertTrue(self.service(db).delete_demo(OWNER, demo_id))
            self.assertEqual(db.query(DeletionTask).one().attempts, 0)
        self.assertFalse(legacy_path.exists())

    def test_production_skips_legacy_keys_entirely(self) -> None:
        demo_id = str(uuid.uuid4())
        legacy_key = f"local://uploads/{demo_id}/mock.dem"
        legacy_path = self.legacy.path_for_key(legacy_key)
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_bytes(b"mock")
        with self.Session() as db:
            seed_demo(db, OWNER, demo_id=demo_id, source_key=legacy_key)
            service = self.service(db, runtime_settings=Settings(auth_mode="production"))
            self.assertTrue(service.delete_demo(OWNER, demo_id))
        self.assertTrue(legacy_path.exists())

    def test_refs_of_another_owner_in_a_task_are_never_purged(self) -> None:
        demo_id, _ = self.seed_full_demo()
        foreign = put_artifact(self.store, OTHER_OWNER, "their-demo")
        with self.Session() as db:
            self.service(db).delete_demo(OWNER, demo_id)
            task = db.query(DeletionTask).one()
            task.artifact_refs = [*task.artifact_refs, foreign]
            task.next_attempt_at = self.clock.now
            db.commit()
            self.service(db).drain_due_tasks()
        self.assertIsNotNone(self.store.head(foreign))


class DemoDeletionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_dir = tempfile.TemporaryDirectory()
        self.engine = fk_engine(
            f"sqlite:///{Path(self.db_dir.name, 'deletion.sqlite').as_posix()}",
            poolclass=NullPool,
        )
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalArtifactStore(self.root)
        self.store_patch = patch.object(deletion_module, "artifact_store_from_settings", return_value=self.store)
        self.store_patch.start()
        self.legacy_patch = patch.object(
            deletion_module.LocalStorageService,
            "from_settings",
            return_value=LocalStorageService(self.root / "legacy"),
        )
        self.legacy_patch.start()

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.legacy_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()
        self.db_dir.cleanup()

    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(deletion_api.router)
        auth_service = AuthService(Settings(auth_mode="test"), FakeRedis())
        app.dependency_overrides[get_auth_service] = lambda: auth_service

        def override_get_db() -> Iterator[Session]:
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    def test_delete_is_owner_scoped_and_a_second_delete_is_not_found(self) -> None:
        demo_id = str(uuid.uuid4())
        reference = put_artifact(self.store, "del-test-1", demo_id)
        with self.Session() as db:
            seed_demo(db, "del-test-1", demo_id=demo_id, source_key=reference)
        client = self.client()

        foreign = client.delete(f"/demos/{demo_id}", headers={"X-Dev-User-Id": "del-test-2"})
        self.assertEqual(foreign.status_code, 404)
        self.assertIsNotNone(self.store.head(reference))

        first = client.delete(f"/demos/{demo_id}", headers={"X-Dev-User-Id": "del-test-1"})
        self.assertEqual(first.status_code, 204)
        self.assertEqual(first.content, b"")
        self.assertIsNone(self.store.head(reference))

        second = client.delete(f"/demos/{demo_id}", headers={"X-Dev-User-Id": "del-test-1"})
        self.assertEqual(second.status_code, 404)

    def test_concurrent_deletes_of_one_demo_answer_204_and_404(self) -> None:
        demo_id = str(uuid.uuid4())
        with self.Session() as db:
            seed_demo(db, "del-test-1", demo_id=demo_id)
        clients = [self.client(), self.client()]
        barrier = threading.Barrier(2)
        statuses: list[int] = []

        def fire(client: TestClient) -> None:
            barrier.wait()
            statuses.append(
                client.delete(f"/demos/{demo_id}", headers={"X-Dev-User-Id": "del-test-1"}).status_code
            )

        threads = [threading.Thread(target=fire, args=(client,)) for client in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(sorted(statuses), [204, 404])
        with self.Session() as db:
            self.assertEqual(db.query(Demo).count(), 0)
            self.assertEqual(db.query(DeletionTask).count(), 1)


class UploadLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = LocalArtifactStore(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.engine.dispose()

    def test_deleting_demos_does_not_refund_the_daily_quota(self) -> None:
        now = datetime.now(UTC)
        quota_settings = Settings(auth_mode="production", demo_upload_daily_limit=3)
        with self.Session() as db:
            demo_ids = []
            for _ in range(3):
                demo_ids.append(seed_demo(db, OWNER, with_coaching=False))
                record_upload(db, OWNER, now=now)
                db.commit()
            before = UploadQuotaService(db, quota_settings, clock=lambda: now).snapshot(OWNER)
            for demo_id in demo_ids:
                DeletionService(db, artifact_store=self.store).delete_demo(OWNER, demo_id)
            after = UploadQuotaService(db, quota_settings, clock=lambda: now).snapshot(OWNER)

        self.assertEqual((before.daily_used, after.daily_used), (3, 3))
        self.assertIsNotNone(after.daily_reset_seconds)

    def test_ledger_rows_are_pruned_after_the_window(self) -> None:
        now = datetime.now(UTC)
        with self.Session() as db:
            record_upload(db, OWNER, now=now - timedelta(hours=25))
            record_upload(db, OWNER, now=now - timedelta(hours=1))
            db.commit()
            self.assertEqual(prune_upload_ledger(db, now=now), 1)
            self.assertEqual(db.query(UploadLedger).count(), 1)

    def test_migration_backfills_the_last_24_hours_of_demos_with_fresh_ids(self) -> None:
        engine = create_engine("sqlite://")
        try:
            now = datetime.now(UTC)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE demos (id VARCHAR(36) PRIMARY KEY, "
                        "owner_id VARCHAR(64) NOT NULL, created_at TIMESTAMP)"
                    )
                )
                for demo_id, owner_id, created_at in (
                    ("recent-a", "owner-a", now - timedelta(hours=2)),
                    ("recent-b", "owner-b", now - timedelta(hours=23)),
                    ("old", "owner-a", now - timedelta(hours=30)),
                ):
                    connection.execute(
                        text("INSERT INTO demos (id, owner_id, created_at) VALUES (:id, :owner, :created)"),
                        {"id": demo_id, "owner": owner_id, "created": created_at.replace(tzinfo=None).isoformat(sep=" ")},
                    )
                run_schema_migrations(connection)
            with engine.connect() as connection:
                rows = connection.execute(text("SELECT id, owner_id FROM upload_ledger")).all()
            self.assertEqual(sorted(row.owner_id for row in rows), ["owner-a", "owner-b"])
            self.assertTrue({row.id for row in rows}.isdisjoint({"recent-a", "recent-b"}))
            self.assertIn("deletion_tasks", inspect(engine).get_table_names())
        finally:
            engine.dispose()


# -- account deletion ---------------------------------------------------------------


class AccountDeletionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalArtifactStore(self.root / "artifacts")
        self.redis = FakeRedis()
        self.auth_service = AuthService(production_auth_settings(), self.redis)
        self.store_patch = patch.object(deletion_module, "artifact_store_from_settings", return_value=self.store)
        self.store_patch.start()

        with self.Session() as db:
            seed_account(db, OWNER, steam_id="76561198000000042")
            seed_account(db, OTHER_OWNER, steam_id="76561198000000043")
            self.demo_ids = []
            self.references = []
            for _ in range(2):
                demo_id = str(uuid.uuid4())
                source = put_artifact(self.store, OWNER, demo_id)
                replay = put_artifact(self.store, OWNER, demo_id, kind="replay")
                seed_demo(db, OWNER, demo_id=demo_id, source_key=source, replay_key=replay)
                self.demo_ids.append(demo_id)
                self.references += [source, replay]
            # An aborted upload's quarantine object, never bound to a row.
            self.references.append(put_artifact(self.store, OWNER, str(uuid.uuid4()), state="quarantine"))
            seed_steam_match(db, OWNER, demo_id=self.demo_ids[0], steam_id="76561198000000042")
            self.other_demo = str(uuid.uuid4())
            self.other_reference = put_artifact(self.store, OTHER_OWNER, self.other_demo)
            seed_demo(db, OTHER_OWNER, demo_id=self.other_demo, source_key=self.other_reference)
            seed_steam_match(db, OTHER_OWNER, demo_id=self.other_demo, steam_id="76561198000000043")

        issued = int(time.time() * 1000) - 1000
        seed_session(self.redis, SESSION_TOKEN, OWNER, issued_at_ms=issued)
        seed_session(self.redis, SECOND_DEVICE_TOKEN, OWNER, issued_at_ms=issued)
        seed_session(self.redis, OTHER_SESSION_TOKEN, OTHER_OWNER, issued_at_ms=issued)

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.temp_dir.cleanup()
        self.engine.dispose()

    def app(self, auth_service: AuthService | None = None) -> FastAPI:
        service = auth_service or self.auth_service
        app = FastAPI()
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=service.settings,
            auth_service_factory=lambda: service,
        )
        app.include_router(auth_api.router)
        app.include_router(deletion_api.router)
        app.include_router(uploads_api.router)
        app.dependency_overrides[get_auth_service] = lambda: service

        def override_get_db() -> Iterator[Session]:
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        return app

    def client(self, token: str | None = SESSION_TOKEN) -> TestClient:
        client = TestClient(self.app(), base_url=ORIGIN, headers={"Origin": ORIGIN})
        if token is not None:
            client.cookies.set("__Host-cs2_session", token)
        return client

    def owner_rows(self, db: Session, owner_id: str) -> dict[str, int]:
        return {
            "accounts": db.query(Account).filter_by(owner_id=owner_id).count(),
            "identities": db.query(ExternalIdentity).filter_by(owner_id=owner_id).count(),
            "connections": db.query(SteamConnection).filter_by(owner_id=owner_id).count(),
            "matches": db.query(SteamMatch).filter_by(owner_id=owner_id).count(),
            "demos": db.query(Demo).filter_by(owner_id=owner_id).count(),
            "feedback": db.query(CoachingFeedback).filter_by(owner_id=owner_id).count(),
        }

    def test_account_deletion_removes_everything_and_ends_every_session(self) -> None:
        second_device = self.client(SECOND_DEVICE_TOKEN)
        self.assertEqual(second_device.get("/uploads/quota").status_code, 200)

        response = self.client().request("DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION)

        self.assertEqual(response.status_code, 204, response.text)
        set_cookie = response.headers["set-cookie"]
        self.assertIn("__Host-cs2_session=", set_cookie)
        self.assertIn("Max-Age=0", set_cookie)
        for attribute in ("Path=/", "Secure", "HttpOnly", "SameSite=lax"):
            self.assertIn(attribute.lower(), set_cookie.lower())
        with self.Session() as db:
            self.assertEqual(set(self.owner_rows(db, OWNER).values()), {0})
            self.assertEqual(db.query(DemoJob).count(), 1)  # the other owner's
            self.assertEqual(db.query(CoachingEvent).count(), 1)
            self.assertEqual(
                self.owner_rows(db, OTHER_OWNER),
                {"accounts": 1, "identities": 1, "connections": 1, "matches": 1, "demos": 1, "feedback": 1},
            )
            tasks = db.query(DeletionTask).filter_by(owner_id=OWNER).all()
            self.assertEqual(sorted(task.demo_id or "" for task in tasks), sorted(["", *self.demo_ids]))
        for reference in self.references:
            self.assertIsNone(self.store.head(reference))
        self.assertIsNotNone(self.store.head(self.other_reference))

        # Both of the owner's sessions are dead; the other owner's is not.
        self.assertEqual(second_device.get("/uploads/quota").status_code, 401)
        self.assertEqual(self.client().get("/uploads/quota").status_code, 401)
        self.assertEqual(self.client(OTHER_SESSION_TOKEN).get("/uploads/quota").status_code, 200)
        # A second attempt with the dead session is not a success.
        again = self.client(SECOND_DEVICE_TOKEN).request("DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION)
        self.assertEqual(again.status_code, 401)

    def test_a_session_issued_after_the_deletion_still_works(self) -> None:
        self.client().request("DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION)
        with self.Session() as db:
            seed_account(db, OWNER)  # the same owner id signs up again (OIDC)
        # Seconds later, as any real sign-in is (the marker is compared in ms).
        with patch("app.services.auth_service.time.time", return_value=time.time() + 5):
            grant = self.auth_service.create_session(OWNER)
        self.assertEqual(self.auth_service.resolve_session(grant.session_token), OWNER)
        # Records from before issuedAt existed cannot prove they are newer.
        seed_session(self.redis, "legacy-record", OWNER, issued_at_ms=None)
        self.assertIsNone(self.auth_service.resolve_session("legacy-record"))

    def test_confirmation_is_required(self) -> None:
        for body in (None, {}, {"confirm": "yes"}, ["delete-my-account"]):
            with self.subTest(body=body):
                kwargs: dict[str, Any] = {} if body is None else {"json": body}
                response = self.client().request("DELETE", "/auth/account", **kwargs)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"]["code"], "confirmation_required")
        garbled = self.client().request(
            "DELETE", "/auth/account", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(garbled.status_code, 400)
        with self.Session() as db:
            self.assertEqual(self.owner_rows(db, OWNER)["accounts"], 1)
        self.assertEqual(self.client().get("/uploads/quota").status_code, 200)

    def test_anonymous_and_cross_origin_requests_are_refused(self) -> None:
        anonymous = self.client(token=None).request("DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION)
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.headers["content-type"], "application/json")
        foreign = self.client().request(
            "DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION, headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(foreign.status_code, 403)
        with self.Session() as db:
            self.assertEqual(self.owner_rows(db, OWNER)["accounts"], 1)

    def test_development_mode_answers_409_and_deletes_nothing(self) -> None:
        dev_service = AuthService(Settings(auth_mode="development"), self.redis)
        client = TestClient(self.app(dev_service))
        response = client.request(
            "DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION, headers={"X-Dev-User-Id": OWNER}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "account_deletion_unavailable")
        with self.Session() as db:
            self.assertEqual(self.owner_rows(db, OWNER)["demos"], 2)
            self.assertEqual(db.query(DeletionTask).count(), 0)

    def test_a_redis_failure_aborts_the_deletion(self) -> None:
        class BrokenRedis(FakeRedis):
            def setex(self, key: str, ttl: int, value: str) -> None:
                if key.startswith("auth:owner-revoked:"):
                    raise ConnectionError("redis down")
                super().setex(key, ttl, value)

        with self.Session() as db:
            service = DeletionService(db, artifact_store=self.store, runtime_settings=production_auth_settings())
            broken = AuthService(production_auth_settings(), BrokenRedis())
            with self.assertRaises(ConnectionError):
                service.delete_account(OWNER, revoke_sessions=broken.revoke_owner_sessions)
        with self.Session() as db:
            self.assertEqual(self.owner_rows(db, OWNER)["demos"], 2)
            self.assertEqual(self.owner_rows(db, OWNER)["accounts"], 1)

    def test_demos_beyond_the_first_batch_are_deleted_by_the_owner_task(self) -> None:
        with self.Session() as db:
            extra = [seed_demo(db, OWNER, with_coaching=False) for _ in range(3)]
        with patch.object(deletion_module, "ACCOUNT_DEMO_BATCH", 2):
            response = self.client().request("DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION)
        self.assertEqual(response.status_code, 204)
        with self.Session() as db:
            self.assertEqual(db.query(Demo).filter(Demo.id.in_([*self.demo_ids, *extra])).count(), 0)

    def test_the_owner_task_spares_a_newer_account_under_the_same_owner_id(self) -> None:
        clock = Clock(datetime.now(UTC))
        with self.Session() as db:
            DeletionService(db, artifact_store=self.store, runtime_settings=production_auth_settings(), clock=clock).delete_account(
                OWNER, revoke_sessions=self.auth_service.revoke_owner_sessions
            )
            seed_account(db, OWNER)
            newer_demo = seed_demo(db, OWNER, created_at=clock.now + timedelta(minutes=1))
        newer_object = put_artifact(self.store, OWNER, newer_demo, now=clock.now + timedelta(minutes=1))
        clock.advance(final_sweep_delay(Settings()) + timedelta(minutes=2))
        with self.Session() as db:
            service = DeletionService(db, artifact_store=self.store, runtime_settings=production_auth_settings(), clock=clock)
            service.drain_due_tasks(limit=50)
            clock.advance(timedelta(minutes=2))
            service.drain_due_tasks(limit=50)
            self.assertEqual(db.query(DeletionTask).count(), 0)
            self.assertIsNotNone(db.get(Demo, newer_demo))
        self.assertIsNotNone(self.store.head(newer_object))


    def test_an_orphan_written_after_the_cutoff_is_swept_once_the_account_is_gone(self) -> None:
        clock = Clock(datetime.now(UTC))
        with self.Session() as db:
            DeletionService(db, artifact_store=self.store, runtime_settings=production_auth_settings(), clock=clock).delete_account(
                OWNER, revoke_sessions=self.auth_service.revoke_owner_sessions
            )
        # An upload fenced at its commit whose best-effort discard failed: accepted
        # after the cutoff, never bound to a row, named by no task of its own.
        orphan_demo = str(uuid.uuid4())
        orphan = put_artifact(self.store, OWNER, orphan_demo, now=clock.now + timedelta(minutes=1))
        orphan_quarantine = put_artifact(
            self.store, OWNER, orphan_demo, state="quarantine", now=clock.now + timedelta(minutes=1)
        )
        clock.advance(final_sweep_delay(Settings()) + timedelta(minutes=2))
        with self.Session() as db:
            service = DeletionService(db, artifact_store=self.store, runtime_settings=production_auth_settings(), clock=clock)
            service.drain_due_tasks(limit=50)
            clock.advance(timedelta(minutes=2))
            service.drain_due_tasks(limit=50)
            self.assertEqual(db.query(DeletionTask).count(), 0)
        self.assertIsNone(self.store.head(orphan))
        self.assertIsNone(self.store.head(orphan_quarantine))
        self.assertIsNotNone(self.store.head(self.other_reference))


class AccountFenceTest(unittest.TestCase):
    """An upload whose body streamed while the account was deleted must not commit."""

    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.redis = FakeRedis()
        self.settings_patch = patched_settings(
            auth_mode="production",
            artifact_storage_backend="local",
            artifact_storage_root=self.root,
            demo_upload_daily_limit=10,
            demo_active_parse_limit=2,
            parse_queue_global_limit=50,
        )
        self.settings_patch.__enter__()
        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=self.redis)
        self.redis_patch.start()
        with self.Session() as db:
            seed_account(db, OWNER)
        seed_session(self.redis, SESSION_TOKEN, OWNER, issued_at_ms=int(time.time() * 1000))

    def tearDown(self) -> None:
        self.redis_patch.stop()
        self.settings_patch.__exit__(None, None, None)
        self.temp_dir.cleanup()
        self.engine.dispose()

    def client(self) -> TestClient:
        auth_service = AuthService(production_auth_settings(), self.redis)
        app = FastAPI()
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=auth_service.settings,
            auth_service_factory=lambda: auth_service,
        )
        app.include_router(uploads_api.router)
        app.dependency_overrides[get_auth_service] = lambda: auth_service

        def override_get_db() -> Iterator[Session]:
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        client.cookies.set("__Host-cs2_session", SESSION_TOKEN)
        return client

    def upload(self) -> Any:
        return self.client().post(
            "/uploads/demo",
            files={"file": ("fence.dem", VALID_DEMO, "application/octet-stream")},
        )

    def test_an_upload_writes_one_ledger_row(self) -> None:
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        with self.Session() as db:
            self.assertEqual(db.query(UploadLedger).filter_by(owner_id=OWNER).count(), 1)

    def test_an_upload_racing_the_account_deletion_is_refused_and_leaves_nothing(self) -> None:
        from app.services.demo_service import DemoService

        original_prepare = DemoService.prepare_real_demo

        def prepare_then_delete_account(service: Any, **kwargs: Any) -> Any:
            prepared = original_prepare(service, **kwargs)
            # The account delete commits while this request is between its
            # body and its commit (its session still resolved at the start).
            with self.Session() as other:
                other.query(ExternalIdentity).filter_by(owner_id=OWNER).delete()
                other.query(Account).filter_by(owner_id=OWNER).delete()
                other.commit()
            return prepared

        with patch.object(DemoService, "prepare_real_demo", prepare_then_delete_account):
            response = self.upload()

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["code"], "account_deleted")
        with self.Session() as db:
            self.assertEqual(db.query(Demo).count(), 0)
            self.assertEqual(db.query(DemoJob).count(), 0)
            self.assertEqual(db.query(UploadLedger).count(), 0)
        self.assertEqual(stored_files(self.root), [])
        self.assertEqual(self.redis.payloads, [])

    def test_the_fence_is_production_only(self) -> None:
        with self.Session() as db:
            self.assertTrue(account_exists_for_write(db, "dev-user", Settings(auth_mode="development")))
            self.assertFalse(account_exists_for_write(db, "dev-user", Settings(auth_mode="production")))
            self.assertTrue(account_exists_for_write(db, OWNER, Settings(auth_mode="production")))


class UploadSessionDeletionTest(unittest.TestCase):
    """Chunked upload sessions go with their account (rows and staged parts) and their demo."""

    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalArtifactStore(self.root / "artifacts")
        self.staging = UploadStagingStore(self.root / "staging")
        self.redis = FakeRedis()
        self.auth_service = AuthService(production_auth_settings(), self.redis)
        with self.Session() as db:
            seed_account(db, OWNER, steam_id="76561198000000042")
            seed_account(db, OTHER_OWNER, steam_id="76561198000000043")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.engine.dispose()

    def service(self, db: Session, **kwargs: Any) -> DeletionService:
        return DeletionService(
            db,
            artifact_store=self.store,
            upload_staging=kwargs.pop("upload_staging", self.staging),
            runtime_settings=kwargs.pop("runtime_settings", production_auth_settings()),
            **kwargs,
        )

    def delete_account(self, owner_id: str = OWNER, **kwargs: Any) -> bool:
        with self.Session() as db:
            return self.service(db, **kwargs).delete_account(
                owner_id, revoke_sessions=self.auth_service.revoke_owner_sessions
            )

    def session_ids(self, owner_id: str) -> set[str]:
        with self.Session() as db:
            return set(db.scalars(select(UploadSession.id).where(UploadSession.owner_id == owner_id)))

    def test_account_deletion_removes_every_session_row_and_the_staged_parts(self) -> None:
        with self.Session() as db:
            open_id = seed_upload_session(db, self.staging, OWNER, parts={0: b"part-zero", 1: b"part-one"})
            demo_id = seed_demo(db, OWNER)
            seed_upload_session(db, None, OWNER, state="completed", demo_id=demo_id)
            seed_upload_session(db, None, OWNER, state="failed")
            other_id = seed_upload_session(db, self.staging, OTHER_OWNER, parts={0: b"other-part"})

        self.assertTrue(self.delete_account())

        self.assertEqual(self.session_ids(OWNER), set())
        self.assertFalse(self.staging.session_exists(OWNER, open_id))
        self.assertEqual(self.staging.list_parts(OWNER, open_id), {})
        # The other owner's upload keeps its row, directory and part.
        self.assertEqual(self.session_ids(OTHER_OWNER), {other_id})
        self.assertEqual(self.staging.list_parts(OTHER_OWNER, other_id), {0: len(b"other-part")})

    def test_a_late_part_after_the_account_deletion_never_recreates_the_directory(self) -> None:
        with self.Session() as db:
            session_id = seed_upload_session(db, self.staging, OWNER, parts={0: b"part-zero"})
        self.assertTrue(self.delete_account())

        # A part PUT that looked the token up just before the commit writes now.
        with self.assertRaises(StagingSessionGone):
            self.staging.write_part(OWNER, session_id, 1, b"late-part", expected_size=len(b"late-part"))

        self.assertFalse(self.staging.session_exists(OWNER, session_id))
        self.assertEqual([path for path in (self.root / "staging").rglob("*") if path.is_file()], [])

    def test_a_completing_session_is_deleted_with_the_account(self) -> None:
        with self.Session() as db:
            session_id = seed_upload_session(db, self.staging, OWNER, state="completing", parts={0: b"p0", 1: b"p1"})

        self.assertTrue(self.delete_account())

        self.assertEqual(self.session_ids(OWNER), set())
        self.assertFalse(self.staging.session_exists(OWNER, session_id))
        with self.Session() as db:
            self.assertEqual(db.query(UploadLedger).count(), 0)
            self.assertEqual(db.query(Demo).filter_by(owner_id=OWNER).count(), 0)

    def test_a_failed_staging_purge_never_fails_the_account_deletion(self) -> None:
        def raising(_owner_id: str) -> bool:
            raise OSError("disk gone")

        for purge in (raising, lambda _owner_id: False):
            with self.subTest(purge=purge):
                with self.Session() as db:
                    if db.get(Account, OWNER) is None:
                        seed_account(db, OWNER)
                    session_id = seed_upload_session(db, self.staging, OWNER, parts={0: b"part-zero"})
                broken = UploadStagingStore(self.root / "staging")
                with (
                    patch.object(broken, "purge_owner", side_effect=purge),
                    self.assertLogs(deletion_module.logger, level="WARNING") as logs,
                ):
                    self.assertTrue(self.delete_account(upload_staging=broken))
                self.assertEqual(self.session_ids(OWNER), set())
                self.assertTrue(any("Upload staging purge" in line for line in logs.output))
                self.assertFalse(any(OWNER in line for line in logs.output))
                # No row owns the directory any more: the hourly sweep's orphan.
                self.assertTrue(self.staging.session_exists(OWNER, session_id))

    def test_deleting_a_demo_removes_the_completed_session_that_created_it(self) -> None:
        with self.Session() as db:
            demo_id = seed_demo(db, OWNER)
            kept_demo = seed_demo(db, OWNER)
            completed = seed_upload_session(db, None, OWNER, state="completed", demo_id=demo_id)
            kept = seed_upload_session(db, None, OWNER, state="completed", demo_id=kept_demo)
            open_id = seed_upload_session(db, self.staging, OWNER, parts={0: b"part-zero"})
            other_demo = seed_demo(db, OTHER_OWNER, demo_id=str(uuid.uuid4()))
            other = seed_upload_session(db, None, OTHER_OWNER, state="completed", demo_id=other_demo)

        with self.Session() as db:
            self.assertTrue(self.service(db, runtime_settings=Settings(auth_mode="test")).delete_demo(OWNER, demo_id))

        self.assertNotIn(completed, self.session_ids(OWNER))
        self.assertEqual(self.session_ids(OWNER), {kept, open_id})
        self.assertEqual(self.session_ids(OTHER_OWNER), {other})
        # An unrelated open upload of the owner keeps its staged parts.
        self.assertEqual(self.staging.list_parts(OWNER, open_id), {0: len(b"part-zero")})

    def test_another_owners_demo_id_never_deletes_a_session(self) -> None:
        with self.Session() as db:
            demo_id = seed_demo(db, OWNER)
            # A malformed row of another owner naming the same demo id stays.
            stray = seed_upload_session(db, None, OTHER_OWNER, state="completed", demo_id=demo_id)

        with self.Session() as db:
            self.assertTrue(self.service(db, runtime_settings=Settings(auth_mode="test")).delete_demo(OWNER, demo_id))

        self.assertEqual(self.session_ids(OTHER_OWNER), {stray})


class UploadSessionAccountDeletionApiTest(unittest.TestCase):
    """Account deletion against the real chunked upload routes (production auth, FK-enforcing SQLite)."""

    PART_BYTES = 4 * 1024 * 1024

    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.staging = UploadStagingStore(self.root / "staging")
        self.redis = FakeRedis()
        self.settings_patch = patched_settings(
            auth_mode="production",
            artifact_storage_backend="local",
            artifact_storage_root=self.root / "artifacts",
            demo_upload_daily_limit=10,
            demo_active_parse_limit=2,
            parse_queue_global_limit=50,
            upload_staging_root=self.root / "staging",
            upload_part_bytes=self.PART_BYTES,
            upload_staging_min_free_bytes=0,
        )
        self.settings_patch.__enter__()
        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=self.redis)
        self.redis_patch.start()
        self.auth_service = AuthService(production_auth_settings(), self.redis)
        with self.Session() as db:
            seed_account(db, OWNER)
            seed_account(db, OTHER_OWNER, steam_id="76561198000000043")
        seed_session(self.redis, SESSION_TOKEN, OWNER, issued_at_ms=int(time.time() * 1000) - 1000)
        # 4 MiB + 1 KiB: two parts, a .dem-looking first part.
        self.data = b"PBDEMS2\x00" + b"\x01" * (self.PART_BYTES + 1024 - 8)

    def tearDown(self) -> None:
        self.redis_patch.stop()
        self.settings_patch.__exit__(None, None, None)
        self.temp_dir.cleanup()
        self.engine.dispose()

    def app(self) -> FastAPI:
        from app.core.upload_slots import IntakeSlot, get_intake_slot
        from app.services.upload_session_service import get_upload_session_factory, get_upload_staging

        service = self.auth_service
        app = FastAPI()
        app.add_middleware(
            SessionCsrfMiddleware,
            runtime_settings=service.settings,
            auth_service_factory=lambda: service,
        )
        app.include_router(auth_api.router)
        app.include_router(deletion_api.router)
        app.include_router(uploads_api.router)
        app.dependency_overrides[get_auth_service] = lambda: service

        def override_get_db() -> Iterator[Session]:
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_upload_staging] = lambda: self.staging
        app.dependency_overrides[get_upload_session_factory] = lambda: self.Session
        slot = IntakeSlot()
        app.dependency_overrides[get_intake_slot] = lambda: slot
        return app

    def client(self, *, cookie: bool = True) -> TestClient:
        client = TestClient(self.app(), base_url=ORIGIN, headers={"Origin": ORIGIN})
        if cookie:
            client.cookies.set("__Host-cs2_session", SESSION_TOKEN)
        return client

    def create_session(self, client: TestClient) -> tuple[str, str]:
        response = client.post("/uploads/sessions", json={"filename": "match.dem", "size": len(self.data)})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["partCount"], 2)
        return body["sessionId"], body["uploadToken"]

    def put_part(self, client: TestClient, session_id: str, token: str, index: int) -> Any:
        chunk = self.data[index * self.PART_BYTES : (index + 1) * self.PART_BYTES]
        return client.put(
            f"/uploads/sessions/{session_id}/parts/{index}",
            content=chunk,
            headers={"X-Upload-Token": token, "Content-Type": "application/octet-stream"},
        )

    def test_a_completed_upload_counts_once_and_deleting_its_demo_refunds_nothing(self) -> None:
        client = self.client()
        session_id, token = self.create_session(client)
        for index in (1, 0):
            self.assertEqual(self.put_part(client, session_id, token, index).status_code, 200)

        done = client.post(f"/uploads/sessions/{session_id}/complete")

        self.assertEqual(done.status_code, 201, done.text)
        demo_id = done.json()["id"]
        self.assertFalse(self.staging.session_exists(OWNER, session_id))
        with self.Session() as db:
            row = db.get(UploadSession, session_id)
            assert row is not None
            self.assertEqual((row.state, row.demo_id, row.active_owner_id), ("completed", demo_id, None))
            self.assertEqual(db.query(UploadLedger).filter_by(owner_id=OWNER).count(), 1)
        again = client.post(f"/uploads/sessions/{session_id}/complete")
        self.assertEqual((again.status_code, again.json()["id"]), (200, demo_id))

        deleted = client.delete(f"/demos/{demo_id}")

        self.assertEqual(deleted.status_code, 204, deleted.text)
        with self.Session() as db:
            self.assertIsNone(db.get(UploadSession, session_id))
            self.assertIsNone(db.get(Demo, demo_id))
            self.assertEqual(db.query(UploadLedger).filter_by(owner_id=OWNER).count(), 1)
        # The completed session went with its demo: complete is now a 404.
        self.assertEqual(client.post(f"/uploads/sessions/{session_id}/complete").status_code, 404)

    def test_a_part_after_the_account_deletion_is_404_and_recreates_nothing(self) -> None:
        client = self.client()
        session_id, token = self.create_session(client)
        self.assertEqual(self.put_part(client, session_id, token, 0).status_code, 200)

        deleted = client.request("DELETE", "/auth/account", json=ACCOUNT_CONFIRMATION)

        self.assertEqual(deleted.status_code, 204, deleted.text)
        with self.Session() as db:
            self.assertEqual(db.query(UploadSession).count(), 0)
        self.assertFalse(self.staging.session_exists(OWNER, session_id))
        # The token outlives sign-out by design, but not its session row.
        late = self.put_part(self.client(cookie=False), session_id, token, 1)
        self.assertEqual(late.status_code, 404, late.text)
        self.assertEqual(late.json()["detail"]["code"], "upload_session_not_found")
        self.assertEqual(stored_files(self.root / "staging"), [])
        with self.Session() as db:
            self.assertEqual(db.query(UploadLedger).count(), 0)

    def test_completing_while_the_account_is_deleted_is_refused_and_leaves_nothing(self) -> None:
        from app.services.demo_service import DemoService
        from app.services.upload_session_service import sweep_upload_sessions

        client = self.client()
        session_id, token = self.create_session(client)
        for index in (0, 1):
            self.assertEqual(self.put_part(client, session_id, token, index).status_code, 200)
        original_prepare = DemoService.prepare_real_demo

        def prepare_then_delete_account(service: Any, **kwargs: Any) -> Any:
            prepared = original_prepare(service, **kwargs)
            # The whole account deletion commits while `complete` is between
            # its intake and its commit.
            with self.Session() as other:
                DeletionService(
                    other,
                    upload_staging=self.staging,
                    runtime_settings=production_auth_settings(),
                ).delete_account(OWNER, revoke_sessions=self.auth_service.revoke_owner_sessions)
            return prepared

        with patch.object(DemoService, "prepare_real_demo", prepare_then_delete_account):
            response = client.post(f"/uploads/sessions/{session_id}/complete")

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["code"], "account_deleted")
        with self.Session() as db:
            self.assertEqual(db.query(Demo).count(), 0)
            self.assertEqual(db.query(DemoJob).count(), 0)
            self.assertEqual(db.query(UploadLedger).count(), 0)
            self.assertEqual(db.query(UploadSession).count(), 0)
            self.assertEqual(db.query(Account).filter_by(owner_id=OWNER).count(), 0)
        self.assertEqual(self.redis.payloads, [])
        # The prepared source was discarded; any staged leftover has no row and
        # goes with the orphan sweep (here, once it is old enough).
        self.assertEqual(stored_files(self.root / "artifacts"), [])
        with self.Session() as db:
            sweep_upload_sessions(
                db,
                self.staging,
                deletion_module.artifact_store_from_settings(),
                now=datetime.now(UTC) + timedelta(minutes=11),
            )
        self.assertEqual(stored_files(self.root / "staging"), [])


class IdleTickMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = LocalArtifactStore(Path(self.temp_dir.name))
        self.staging = UploadStagingStore(Path(self.temp_dir.name) / "upload-staging")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.engine.dispose()

    def test_the_outbox_drain_is_rate_limited(self) -> None:
        past = datetime.now(UTC) - timedelta(hours=1)
        leftover = put_artifact(self.store, OWNER, "gone-demo", kind="video")
        with self.Session() as db:
            db.add(
                DeletionTask(
                    id=str(uuid.uuid4()),
                    owner_id=OWNER,
                    demo_id="gone-demo",
                    artifact_refs=[],
                    final_sweep_after=past,
                    next_attempt_at=past,
                    attempts=0,
                    created_at=past,
                    updated_at=past,
                )
            )
            db.commit()

        self.assertEqual(
            deletion_module.drain_deletion_outbox(force=True, session_factory=self.Session, artifact_store=self.store),
            1,
        )
        self.assertIsNone(self.store.head(leftover))
        # Within the interval the tick does nothing at all.
        self.assertEqual(
            deletion_module.drain_deletion_outbox(session_factory=self.Session, artifact_store=self.store),
            0,
        )

    def test_hourly_maintenance_cleans_aborted_uploads_and_prunes_the_ledger(self) -> None:
        now = datetime.now(UTC)
        aborted = put_artifact(self.store, OWNER, "aborted", state="quarantine", now=now - timedelta(hours=2))
        streaming = put_artifact(self.store, OWNER, "streaming", state="quarantine", now=now)
        with self.Session() as db:
            record_upload(db, OWNER, now=now - timedelta(hours=30))
            record_upload(db, OWNER, now=now - timedelta(hours=2))
            db.commit()

        deletion_module.run_hourly_storage_maintenance(
            force=True, session_factory=self.Session, artifact_store=self.store, upload_staging=self.staging
        )

        self.assertIsNone(self.store.head(aborted))
        self.assertIsNotNone(self.store.head(streaming))
        with self.Session() as db:
            self.assertEqual(db.query(UploadLedger).count(), 1)

    def age_session_dir(self, session_id: str, age: timedelta) -> None:
        marker = next(path for path in self.staging.root.rglob(".session") if path.parent.name == session_id)
        stamp = (datetime.now(UTC) - age).timestamp()
        os.utime(marker, (stamp, stamp))

    def test_hourly_maintenance_sweeps_expired_upload_sessions_and_orphaned_staging(self) -> None:
        now = datetime.now(UTC)
        with self.Session() as db:
            expired = seed_upload_session(
                db, self.staging, OWNER, parts={0: b"part-zero"},
                created_at=now - timedelta(hours=25), expires_at=now - timedelta(hours=1),
            )
            live = seed_upload_session(db, self.staging, OTHER_OWNER, parts={0: b"part-zero"})
        # Directories whose row is gone: an account deletion's failed purge
        # (old enough to sweep) and a session still being created (too new).
        old_orphan, new_orphan = uuid.uuid4().hex, uuid.uuid4().hex
        self.staging.create_session(OWNER, old_orphan)
        self.staging.create_session(OWNER, new_orphan)
        self.age_session_dir(old_orphan, timedelta(minutes=20))

        deletion_module.run_hourly_storage_maintenance(
            force=True, session_factory=self.Session, artifact_store=self.store, upload_staging=self.staging
        )

        with self.Session() as db:
            self.assertEqual(set(db.scalars(select(UploadSession.id))), {live})
            # Abandoned and expired sessions never touch the ledger.
            self.assertEqual(db.query(UploadLedger).count(), 0)
        self.assertFalse(self.staging.session_exists(OWNER, expired))
        self.assertFalse(self.staging.session_exists(OWNER, old_orphan))
        self.assertTrue(self.staging.session_exists(OWNER, new_orphan))
        self.assertEqual(self.staging.list_parts(OTHER_OWNER, live), {0: len(b"part-zero")})

    def test_one_failing_hourly_chore_never_skips_the_others(self) -> None:
        now = datetime.now(UTC)
        aborted = put_artifact(self.store, OWNER, "aborted", state="quarantine", now=now - timedelta(hours=2))
        with self.Session() as db:
            record_upload(db, OWNER, now=now - timedelta(hours=30))
            db.commit()
            expired = seed_upload_session(
                db, self.staging, OWNER, created_at=now - timedelta(hours=25), expires_at=now - timedelta(hours=1)
            )

        # The sweep fails: the quarantine cleanup and the ledger prune still run.
        with (
            patch("app.services.upload_session_service.sweep_upload_sessions", side_effect=RuntimeError("sweep")),
            self.assertRaises(RuntimeError),
        ):
            deletion_module.run_hourly_storage_maintenance(
                force=True, session_factory=self.Session, artifact_store=self.store, upload_staging=self.staging
            )
        self.assertIsNone(self.store.head(aborted))
        with self.Session() as db:
            self.assertEqual(db.query(UploadLedger).count(), 0)
            self.assertEqual(db.query(UploadSession).count(), 1)

        # The quarantine cleanup fails: the upload session sweep still runs.
        from app.services.artifact_intake import ArtifactIntakeService

        with (
            patch.object(ArtifactIntakeService, "cleanup_abandoned", side_effect=ArtifactStoreError("down")),
            self.assertRaises(ArtifactStoreError),
        ):
            deletion_module.run_hourly_storage_maintenance(
                force=True, session_factory=self.Session, artifact_store=self.store, upload_staging=self.staging
            )
        with self.Session() as db:
            self.assertEqual(db.query(UploadSession).count(), 0)
        self.assertFalse(self.staging.session_exists(OWNER, expired))


class SessionRevocationMarkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.service = AuthService(production_auth_settings(), self.redis)

    def test_new_sessions_carry_issued_at_and_the_marker_revokes_older_ones(self) -> None:
        before = self.service.create_session(OWNER)
        record = json.loads(self.redis.values[session_key(before.session_token)])
        self.assertIsInstance(record["issuedAt"], int)
        other = self.service.create_session(OTHER_OWNER)

        with patch("app.services.auth_service.time.time", return_value=time.time() + 1):
            self.service.revoke_owner_sessions(OWNER)
        marker_keys = [key for key in self.redis.values if key.startswith("auth:owner-revoked:")]
        self.assertEqual(len(marker_keys), 1)
        self.assertNotIn(OWNER, marker_keys[0])

        self.assertIsNone(self.service.resolve_session(before.session_token))
        # Rejected sessions are deleted, not just refused.
        self.assertNotIn(session_key(before.session_token), self.redis.values)
        self.assertEqual(self.service.resolve_session(other.session_token), OTHER_OWNER)
        with patch("app.services.auth_service.time.time", return_value=time.time() + 2):
            after = self.service.create_session(OWNER)
        self.assertEqual(self.service.resolve_session(after.session_token), OWNER)


class SteamImportFenceTest(unittest.TestCase):
    def setUp(self) -> None:
        from test_steam_demo_import import (
            CONNECTION_ID,
            MATCH_ID,
            OWNER_A,
            SHARE_CODE,
            TEST_ENCRYPTION_KEY,
        )

        from app.services.steam_credentials import SteamCredentialCipher

        self.match_id = MATCH_ID
        self.owner_id = OWNER_A
        # No FK enforcement here: on Postgres the fence runs before the account
        # delete could cascade to the Steam rows (both take the account row
        # first), which is the ordering this test models.
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.settings = Settings(
            auth_mode="production",
            auth_provider="steam",
            steam_web_api_key="a" * 32,
            steam_credential_encryption_key=TEST_ENCRYPTION_KEY,
            steam_credential_encryption_key_version="v1",
        )
        self.cipher = SteamCredentialCipher(self.settings)
        encrypted = self.cipher.encrypt(
            SHARE_CODE, owner_id=OWNER_A, purpose="match-sharing-code", record_id=MATCH_ID
        )
        now = datetime.now(UTC)
        with self.Session() as db:
            seed_account(db, OWNER_A)
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
            db.flush()
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

    def import_match(self, db: Session, store: LocalArtifactStore, on_download: Callable[[], None] | None) -> Any:
        from test_steam_demo_import import (
            CallbackDownloader,
            FakeDownloader,
            FakeDownloadLimiter,
            FakeLicensedProvider,
        )
        from test_steam_demo_import import FakeRedis as ImportRedis

        from app.services.steam_demo_import_service import SteamDemoImportService

        return SteamDemoImportService(
            db,
            owner_id=self.owner_id,
            provider=FakeLicensedProvider(),
            cipher=self.cipher,
            downloader=CallbackDownloader(on_download) if on_download else FakeDownloader(),
            download_limiter=FakeDownloadLimiter(),
            artifact_store=store,
            redis_client=ImportRedis(),
            runtime_settings=self.settings,
        ).import_match(self.match_id)

    def test_an_import_consumes_the_daily_quota(self) -> None:
        with tempfile.TemporaryDirectory() as root, self.Session() as db:
            self.import_match(db, LocalArtifactStore(Path(root)), None)
            self.assertEqual(db.query(UploadLedger).filter_by(owner_id=self.owner_id).count(), 1)

    def test_an_import_racing_the_account_deletion_fails_cleanly(self) -> None:
        from app.services.steam_demo_import_service import SteamDemoImportFailedError

        with tempfile.TemporaryDirectory() as root, self.Session() as db:

            def delete_account_rows() -> None:
                db.query(ExternalIdentity).filter_by(owner_id=self.owner_id).delete()
                db.query(Account).filter_by(owner_id=self.owner_id).delete()
                db.commit()

            with self.assertRaises(SteamDemoImportFailedError) as raised:
                self.import_match(db, LocalArtifactStore(Path(root)), delete_account_rows)
            self.assertEqual(raised.exception.code, "account_deleted")
            self.assertEqual(db.query(Demo).count(), 0)
            self.assertEqual(db.query(UploadLedger).count(), 0)
            self.assertEqual(stored_files(Path(root)), [])


if __name__ == "__main__":
    unittest.main()
