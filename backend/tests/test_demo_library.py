import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Demo
from app.services.demo_service import DemoService


class DemoLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
            created_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
        )
        service = DemoService(db)

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
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        dust_late = add_demo(
            db,
            "demo-dust-late",
            "Bravo Dust",
            "furia-dust2.dem",
            "de_dust2",
            status="completed",
            created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
        )
        dust_early = add_demo(
            db,
            "demo-dust-early",
            "Charlie Dust",
            "ancient-source.dem",
            "de_dust2",
            status="completed",
            created_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )
        service = DemoService(db)

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

    def test_update_demo_renames_demo_and_rejects_blank_name(self) -> None:
        db = self.Session()
        demo = add_demo(db, "demo-rename", "Old Name", "old.dem", "de_nuke")
        service = DemoService(db)

        updated = service.update_demo(demo.id, name="New Library Name")

        self.assertIsNotNone(updated)
        self.assertEqual(updated.name, "New Library Name")
        self.assertEqual(service.get_demo(demo.id).name, "New Library Name")
        with self.assertRaisesRegex(ValueError, "name cannot be blank"):
            service.update_demo(demo.id, name="   ")

    def test_archive_demo_hides_from_default_list_but_detail_lookup_still_works(self) -> None:
        db = self.Session()
        demo = add_demo(db, "demo-archive", "Archive Me", "archive.dem", "de_ancient")
        service = DemoService(db)

        archived = service.archive_demo(demo.id)

        self.assertIsNotNone(archived)
        self.assertTrue(archived.archived)
        self.assertEqual(service.list_demos(), [])
        self.assertEqual(service.get_demo(demo.id).id, demo.id)
        self.assertEqual([item.id for item in service.list_demos(include_archived=True)], [demo.id])

    def test_mock_upload_remains_visible_in_library(self) -> None:
        db = self.Session()
        service = DemoService(db)

        with patch("app.services.demo_service.get_redis_client", return_value=FakeRedis()) as redis_factory:
            created = service.create_mock_demo()
            listed = service.list_demos()

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].id, created.id)
        self.assertFalse(listed[0].archived)
        self.assertEqual(listed[0].status, "queued")
        self.assertEqual(len(redis_factory.return_value.payloads), 1)


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
) -> Demo:
    timestamp = created_at or datetime(2026, 5, 8, tzinfo=timezone.utc)
    demo = Demo(
        id=demo_id,
        user_id=settings.dev_user_id,
        name=name,
        original_filename=original_filename,
        map_name=map_name,
        tick_rate=64,
        round_count=12,
        coaching_event_count=3,
        status=status,
        archived=archived,
        created_at=timestamp,
        updated_at=timestamp,
        replay_storage_key=f"local://replays/{demo_id}.json" if status == "completed" else None,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


if __name__ == "__main__":
    unittest.main()
