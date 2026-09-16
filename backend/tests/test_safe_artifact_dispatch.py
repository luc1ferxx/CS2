import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import Demo, DemoJob
from app.services.artifact_intake import ArtifactIntakeError
from app.services.demo_service import DemoDispatchError, DemoService
from app.services.storage import ArtifactReference, LocalArtifactStore

VALID_DEMO = b"bounded-demo-content-for-intake"


class FakeUpload:
    def __init__(
        self,
        filename: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(body)

    async def read(self, size: int = -1) -> bytes:
        return self.file.read(size)


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.items: list[tuple[str, str]] = []

    def lpush(self, queue: str, payload: str) -> int:
        if self.fail:
            raise ConnectionError("redis://secret@internal/0 traceback")
        self.items.append((queue, payload))
        return len(self.items)


class TrackingArtifactStore(LocalArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.created_references: list[str] = []

    def new_reference(self, **kwargs):
        reference = super().new_reference(**kwargs)
        self.created_references.append(reference)
        return reference


class SafeArtifactDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_valid_demo_is_accepted_bound_then_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.Session() as db:
            store = TrackingArtifactStore(Path(directory))
            redis = FakeRedis()
            with patch("app.services.demo_service.get_redis_client", return_value=redis):
                created = DemoService(
                    db,
                    owner_id="owner-a",
                    artifact_store=store,
                ).create_real_demo(FakeUpload("match.dem", VALID_DEMO))

            demo = db.query(Demo).filter(Demo.id == created.id).one()
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo.id).one()
            source = ArtifactReference.parse(demo.source_storage_key)
            metadata = store.head(demo.source_storage_key)
            job_metadata = json.loads(job.metadata_json)

            self.assertEqual(source.owner_id, "owner-a")
            self.assertEqual(source.demo_id, demo.id)
            self.assertEqual(source.kind, "source")
            self.assertEqual(source.state, "accepted")
            self.assertIsNotNone(metadata)
            self.assertIsNone(store.head(store.created_references[0]))
            self.assertEqual(job_metadata["sourceArtifact"]["reference"], demo.source_storage_key)
            self.assertEqual(job_metadata["sourceArtifact"]["state"], "accepted")
            self.assertEqual(job_metadata["sourceArtifact"]["sizeBytes"], len(VALID_DEMO))
            self.assertEqual(job_metadata["sourceArtifact"]["sha256"], metadata.sha256)
            self.assertEqual(job_metadata["sourceArtifact"]["generation"], metadata.generation)
            self.assertEqual(len(redis.items), 1)
            self.assertEqual(
                json.loads(redis.items[0][1]),
                {"job_id": job.id, "demo_id": demo.id},
            )

    def test_rejected_upload_creates_no_demo_job_or_dispatch(self) -> None:
        for filename, body in (
            ("match.zip", VALID_DEMO),
            ("match.dem", b""),
            ("match.dem", b"short"),
            ("match.dem", b"PK\x03\x04" + b"archive-content"),
        ):
            with self.subTest(filename=filename, body=body[:4]):
                with tempfile.TemporaryDirectory() as directory, self.Session() as db:
                    store = TrackingArtifactStore(Path(directory))
                    redis = FakeRedis()
                    with patch(
                        "app.services.demo_service.get_redis_client",
                        return_value=redis,
                    ):
                        with self.assertRaises(ArtifactIntakeError):
                            DemoService(
                                db,
                                owner_id="owner-a",
                                artifact_store=store,
                            ).create_real_demo(FakeUpload(filename, body))

                    self.assertEqual(db.query(Demo).count(), 0)
                    self.assertEqual(db.query(DemoJob).count(), 0)
                    self.assertEqual(redis.items, [])
                    for reference in store.created_references:
                        parsed = ArtifactReference.parse(reference)
                        self.assertIsNone(store.head(reference))
                        accepted = ArtifactReference(
                            owner_id=parsed.owner_id,
                            demo_id=parsed.demo_id,
                            kind=parsed.kind,
                            state="accepted",
                            artifact_id=parsed.artifact_id,
                        ).to_uri()
                        self.assertIsNone(store.head(accepted))

    def test_database_bind_failure_removes_the_accepted_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.Session() as db:
            store = TrackingArtifactStore(Path(directory))
            redis = FakeRedis()
            original_commit = db.commit
            commit_calls = 0

            def fail_first_commit() -> None:
                nonlocal commit_calls
                commit_calls += 1
                if commit_calls == 1:
                    raise RuntimeError("postgresql://secret@internal/path traceback")
                original_commit()

            with patch.object(db, "commit", side_effect=fail_first_commit), patch(
                "app.services.demo_service.get_redis_client",
                return_value=redis,
            ):
                with self.assertRaisesRegex(RuntimeError, "could not be completed") as raised:
                    DemoService(
                        db,
                        owner_id="owner-a",
                        artifact_store=store,
                    ).create_real_demo(FakeUpload("match.dem", VALID_DEMO))

            self.assertNotIn("secret", str(raised.exception).lower())
            self.assertNotIn("internal", str(raised.exception).lower())
            self.assertEqual(redis.items, [])
            self.assertEqual(len(store.created_references), 1)
            quarantine = ArtifactReference.parse(store.created_references[0])
            accepted = ArtifactReference(
                owner_id=quarantine.owner_id,
                demo_id=quarantine.demo_id,
                kind=quarantine.kind,
                state="accepted",
                artifact_id=quarantine.artifact_id,
            ).to_uri()
            self.assertIsNone(store.head(store.created_references[0]))
            self.assertIsNone(store.head(accepted))

    def test_dispatch_failure_is_safe_and_preserves_only_the_bound_queued_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.Session() as db:
            store = TrackingArtifactStore(Path(directory))
            redis = FakeRedis(fail=True)
            with patch(
                "app.services.demo_service.get_redis_client",
                return_value=redis,
            ), self.assertLogs("app.services.demo_service", level="WARNING") as captured:
                with self.assertRaises(DemoDispatchError) as raised:
                    DemoService(
                        db,
                        owner_id="owner-a",
                        artifact_store=store,
                    ).create_real_demo(FakeUpload("match.dem", VALID_DEMO))

            demo = db.query(Demo).one()
            job = db.query(DemoJob).one()
            metadata = json.loads(job.metadata_json)
            self.assertEqual(demo.status, "queued")
            self.assertEqual(job.status, "queued")
            self.assertEqual(metadata["sourceArtifact"]["reference"], demo.source_storage_key)
            self.assertIsNotNone(store.head(demo.source_storage_key))
            combined = " ".join([str(raised.exception), *captured.output]).lower()
            self.assertNotIn("redis://", combined)
            self.assertNotIn("secret", combined)
            self.assertNotIn("traceback", combined)


if __name__ == "__main__":
    unittest.main()
