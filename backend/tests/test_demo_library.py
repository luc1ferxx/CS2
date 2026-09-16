import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Demo, DemoJob
from app.services.artifact_intake import ArtifactIntakeService
from app.services.demo_service import DemoService
from app.services.storage import LocalArtifactStore


class DemoLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_list_demos_hides_archived_by_default_and_can_include_them(self) -> None:
        db = self.Session()
        active = add_demo(db, "demo-active", "Active Dust2", "active.dem", "de_dust2")
        archived = add_demo(
            db,
            "demo-archived",
            "Archived Inferno",
            "archived.dem",
            "de_inferno",
            archived=True,
            created_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
        service = DemoService(db, owner_id=settings.dev_user_id)

        default_items = service.list_demos()
        all_items = service.list_demos(include_archived=True)

        self.assertEqual([item.id for item in default_items], [active.id])
        self.assertEqual([item.id for item in all_items], [archived.id, active.id])
        self.assertTrue(all_items[0].archived)
        self.assertFalse(all_items[1].archived)

    def test_list_demos_search_filter_and_sort(self) -> None:
        db = self.Session()
        add_demo(
            db,
            "demo-mirage",
            "Alpha Mirage",
            "alpha.dem",
            "de_mirage",
            status="failed",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        dust_late = add_demo(
            db,
            "demo-dust-late",
            "Bravo Dust",
            "furia-dust2.dem",
            "de_dust2",
            status="completed",
            created_at=datetime(2026, 5, 3, tzinfo=UTC),
        )
        dust_early = add_demo(
            db,
            "demo-dust-early",
            "Charlie Dust",
            "ancient-source.dem",
            "de_dust2",
            status="completed",
            created_at=datetime(2026, 5, 2, tzinfo=UTC),
        )
        service = DemoService(db, owner_id=settings.dev_user_id)

        by_name = service.list_demos(
            search="dust",
            status="completed",
            map_name="de_dust2",
            sort="name",
            order="asc",
        )
        by_recent = service.list_demos(sort="recent", order="desc")

        self.assertEqual([item.id for item in by_name], [dust_late.id, dust_early.id])
        self.assertEqual([item.id for item in by_recent], [dust_late.id, dust_early.id, "demo-mirage"])

    def test_list_demos_searches_visible_status_and_id_fields(self) -> None:
        db = self.Session()
        add_demo(
            db,
            "demo-visible-failed",
            "Inferno Review",
            "inferno.dem",
            "de_inferno",
            status="failed",
        )
        add_demo(db, "demo-visible-ready", "Nuke Review", "nuke.dem", "de_nuke")
        service = DemoService(db, owner_id=settings.dev_user_id)

        failed = service.list_demos(search="failed")
        ready = service.list_demos(search="ready")
        dust_ready = service.list_demos(search="nuke ready")
        by_id = service.list_demos(search="visible-ready")

        self.assertEqual([item.id for item in failed], ["demo-visible-failed"])
        self.assertEqual([item.id for item in ready], ["demo-visible-ready"])
        self.assertEqual([item.id for item in dust_ready], ["demo-visible-ready"])
        self.assertEqual([item.id for item in by_id], ["demo-visible-ready"])

    def test_list_demos_uses_deterministic_tiebreakers_for_equal_sort_fields(self) -> None:
        db = self.Session()
        timestamp = datetime(2026, 5, 10, tzinfo=UTC)
        add_demo(db, "demo-tie-b", "Same Name", "b.dem", "de_dust2", created_at=timestamp)
        add_demo(db, "demo-tie-a", "Same Name", "a.dem", "de_dust2", created_at=timestamp)
        service = DemoService(db, owner_id=settings.dev_user_id)

        by_name = service.list_demos(sort="name", order="asc")
        by_recent = service.list_demos(sort="recent", order="desc")

        self.assertEqual([item.id for item in by_name], ["demo-tie-a", "demo-tie-b"])
        self.assertEqual([item.id for item in by_recent], ["demo-tie-a", "demo-tie-b"])

    def test_update_demo_renames_demo_and_rejects_blank_name(self) -> None:
        db = self.Session()
        demo = add_demo(db, "demo-rename", "Old Name", "old.dem", "de_nuke")
        service = DemoService(db, owner_id=settings.dev_user_id)

        updated = service.update_demo(demo.id, name="New Library Name")

        self.assertIsNotNone(updated)
        self.assertEqual(updated.name, "New Library Name")
        self.assertEqual(service.get_demo(demo.id).name, "New Library Name")
        with self.assertRaisesRegex(ValueError, "name cannot be blank"):
            service.update_demo(demo.id, name="   ")

    def test_archive_demo_hides_from_default_list_but_detail_lookup_still_works(self) -> None:
        db = self.Session()
        demo = add_demo(db, "demo-archive", "Archive Me", "archive.dem", "de_ancient")
        service = DemoService(db, owner_id=settings.dev_user_id)

        archived = service.archive_demo(demo.id)

        self.assertIsNotNone(archived)
        self.assertTrue(archived.archived)
        self.assertEqual(service.list_demos(), [])
        self.assertEqual(service.get_demo(demo.id).id, demo.id)
        self.assertEqual([item.id for item in service.list_demos(include_archived=True)], [demo.id])

    def test_mock_upload_remains_visible_in_library(self) -> None:
        db = self.Session()
        service = DemoService(db, owner_id=settings.dev_user_id)

        with patch("app.services.demo_service.get_redis_client", return_value=FakeRedis()) as redis_factory:
            created = service.create_mock_demo()
            listed = service.list_demos()

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].id, created.id)
        self.assertFalse(listed[0].archived)
        self.assertEqual(listed[0].status, "queued")
        self.assertEqual(len(redis_factory.return_value.payloads), 1)

    def test_list_item_includes_compact_ingestion_snapshot_for_active_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self.Session()
            timestamp = datetime.now(UTC)
            demo = add_demo(
                db,
                "demo-active-upload",
                "Active Upload",
                "active.dem",
                "unknown",
                status="queued",
                created_at=timestamp,
                updated_at=timestamp,
            )
            store = LocalArtifactStore(directory)
            accepted = ArtifactIntakeService(store).intake_demo(
                owner_id=demo.owner_id,
                demo_id=demo.id,
                filename=demo.original_filename,
                content_type="application/octet-stream",
                stream=io.BytesIO(b"HL2DEMO\x00demo-library-fixture"),
            )
            demo.source_storage_key = accepted.reference
            add_parse_job(
                db,
                demo.id,
                "real_parse",
                status="queued",
                attempts=0,
                metadata={"phase": "uploaded", "sourceArtifact": accepted.as_snapshot()},
            )
            db.commit()
            service = DemoService(
                db,
                owner_id=settings.dev_user_id,
                artifact_store=store,
            )

            item = service.demo_list_item(demo)

        self.assertEqual(item.ingestion.phase, "uploaded")
        self.assertTrue(item.ingestion.active)
        self.assertFalse(item.ingestion.stale)
        self.assertFalse(item.ingestion.retryable)
        self.assertEqual(item.ingestion.attemptCount, 0)
        self.assertEqual(item.ingestion.jobType, "real_parse")
        self.assertEqual(item.ingestion.jobStatus, "queued")
        self.assertTrue(item.ingestion.hasSourceDemo)
        self.assertIsNone(item.ingestion.failure)

    def test_failed_parse_ingestion_snapshot_has_compact_failure_metadata(self) -> None:
        db = self.Session()
        timestamp = datetime(2026, 5, 8, tzinfo=UTC)
        demo = add_demo(
            db,
            "demo-parse-failed",
            "Failed Parse",
            "failed.dem",
            "unknown",
            status="failed",
            created_at=timestamp,
            updated_at=timestamp + timedelta(minutes=4),
            source_storage_key="local://uploads/demo-parse-failed/failed.dem",
            error_message="Parser exploded while reading demo\nTraceback should not leak into the compact snapshot",
        )
        failed_at = timestamp + timedelta(minutes=3)
        add_parse_job(
            db,
            demo.id,
            "real_parse",
            status="failed",
            attempts=2,
            error_message="Parser exploded while reading demo\nTraceback should not leak",
            finished_at=failed_at,
        )
        service = DemoService(db, owner_id=settings.dev_user_id)

        item = service.demo_list_item(demo)

        self.assertEqual(item.ingestion.phase, "failed")
        self.assertFalse(item.ingestion.active)
        self.assertEqual(item.ingestion.attemptCount, 2)
        self.assertIsNotNone(item.ingestion.failure)
        self.assertEqual(item.ingestion.failure.errorCode, "PARSER_FAILED")
        self.assertEqual(item.ingestion.failure.message, "Parser exploded while reading demo")
        self.assertEqual(item.ingestion.failure.failedAt, failed_at)
        self.assertEqual(item.ingestion.failure.updatedAt, timestamp + timedelta(minutes=4))
        self.assertFalse(item.ingestion.failure.retryable)
        self.assertEqual(item.ingestion.failure.attemptCount, 2)

    def test_active_parse_snapshot_marks_stale_when_status_is_old(self) -> None:
        db = self.Session()
        old_timestamp = datetime.now(UTC) - timedelta(minutes=30)
        demo = add_demo(
            db,
            "demo-stale-parse",
            "Stale Parse",
            "stale.dem",
            "unknown",
            status="parsing",
            created_at=old_timestamp,
            updated_at=old_timestamp,
            source_storage_key="local://uploads/demo-stale-parse/stale.dem",
        )
        add_parse_job(
            db,
            demo.id,
            "real_parse",
            status="processing",
            attempts=1,
            started_at=old_timestamp,
        )
        service = DemoService(db, owner_id=settings.dev_user_id)

        item = service.demo_list_item(demo)

        self.assertEqual(item.ingestion.phase, "parsing")
        self.assertTrue(item.ingestion.active)
        self.assertTrue(item.ingestion.stale)


class FakeRedis:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def lpush(self, _: str, payload: str) -> None:
        self.payloads.append(payload)


def add_demo(
    db,
    demo_id: str,
    name: str,
    original_filename: str,
    map_name: str,
    *,
    status: str = "completed",
    archived: bool = False,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    source_storage_key: str | None = None,
    error_message: str | None = None,
) -> Demo:
    timestamp = created_at or datetime(2026, 5, 8, tzinfo=UTC)
    demo = Demo(
        id=demo_id,
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
        name=name,
        original_filename=original_filename,
        map_name=map_name,
        tick_rate=64,
        round_count=12,
        coaching_event_count=3,
        status=status,
        archived=archived,
        created_at=timestamp,
        updated_at=updated_at or timestamp,
        source_storage_key=source_storage_key,
        error_message=error_message,
        replay_storage_key=f"local://replays/{demo_id}.json" if status == "completed" else None,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


def add_parse_job(
    db,
    demo_id: str,
    job_type: str,
    *,
    status: str,
    attempts: int,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> DemoJob:
    job = DemoJob(
        id=f"{demo_id}-{job_type}-{status}",
        demo_id=demo_id,
        job_type=job_type,
        status=status,
        attempts=attempts,
        started_at=started_at,
        finished_at=finished_at,
        error_message=error_message,
        metadata_json=json.dumps(metadata) if metadata is not None else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


if __name__ == "__main__":
    unittest.main()
