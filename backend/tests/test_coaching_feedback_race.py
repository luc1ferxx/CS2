import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import CoachingEvent, CoachingFeedback, Demo
from app.services.demo_service import DemoService

OWNER = "owner-a"


class CoachingFeedbackRaceTest(unittest.TestCase):
    """The upsert is a lookup followed by an insert. Two verdicts for the same
    suggestion can both miss the lookup; the unique constraint then rejects the
    second insert, and that rejection must become an update, not a 500."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        now = datetime(2026, 9, 18, tzinfo=UTC)
        with self.Session() as db:
            db.add(Demo(
                id="demo-a", owner_id=OWNER, legacy_user_id=OWNER, name="Demo", original_filename="a.dem",
                map_name="de_inferno", tick_rate=64, round_count=1, coaching_event_count=1, status="completed",
                archived=False, replay_storage_key="local://replays/demo-a.json",
                created_at=now, updated_at=now, completed_at=now,
            ))
            db.add(CoachingEvent(
                id="event-1", demo_id="demo-a", round_number=1, player_id="p1", player_name="P1",
                tick_start=100, tick_end=200, category="positioning", severity="medium", title="t",
                message="m", structured_context_json={"ruleId": "isolated_entry"}, confidence=0.7,
            ))
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_second_verdict_that_loses_the_insert_race_becomes_an_update(self) -> None:
        with self.Session() as db:
            service = DemoService(db, owner_id=OWNER)
            demo = service.get_demo("demo-a")
            assert demo is not None
            real_commit = db.commit
            commits = {"count": 0}

            def racing_commit() -> None:
                commits["count"] += 1
                if commits["count"] > 1:
                    real_commit()
                    return
                # The other request lands between our lookup and our insert: drop
                # our pending insert, let theirs commit, and fail ours the way the
                # database would.
                db.rollback()
                with self.Session() as other:
                    now = datetime(2026, 9, 18, tzinfo=UTC)
                    other.add(CoachingFeedback(
                        id="theirs", demo_id="demo-a", event_id="event-1", owner_id=OWNER,
                        verdict="unsure", note=None, created_at=now, updated_at=now,
                    ))
                    other.commit()
                raise IntegrityError("INSERT INTO coaching_feedback", {}, Exception("UNIQUE constraint failed"))

            db.commit = racing_commit  # type: ignore[method-assign]
            saved = service.save_coaching_feedback(demo, "event-1", verdict="helpful", note="mine")

            assert saved is not None
            self.assertEqual((saved.verdict, saved.note), ("helpful", "mine"))
            self.assertEqual(commits["count"], 2)

        with self.Session() as check:
            rows = check.query(CoachingFeedback).all()
            self.assertEqual([(row.id, row.verdict, row.note) for row in rows], [("theirs", "helpful", "mine")])
