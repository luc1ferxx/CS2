import unittest
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import coaching
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import CoachingEvent, CoachingFeedback, Demo

OWNER_A = "owner-a"
OWNER_B = "owner-b"


class CoachingFeedbackApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.original_auth_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "test")

        self.app = FastAPI()
        self.app.include_router(coaching.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(self.app)

        with self.Session() as db:
            add_demo(db, "demo-a", OWNER_A)
            add_demo(db, "demo-a2", OWNER_A)
            add_demo(db, "demo-b", OWNER_B)
            add_event(db, "event-entry", "demo-a", rule_id="isolated_entry", tick_start=1100)
            add_event(db, "event-spacing", "demo-a", rule_id="poor_spacing", tick_start=2200)
            add_event(db, "event-spacing-2", "demo-a2", rule_id="poor_spacing", tick_start=900)
            add_event(db, "event-b", "demo-b", rule_id="isolated_entry", tick_start=500)

    def tearDown(self) -> None:
        object.__setattr__(settings, "auth_mode", self.original_auth_mode)
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def test_verdict_round_trips_and_is_projected_onto_the_event(self) -> None:
        saved = self.client.put(
            "/demos/demo-a/coaching/event-entry/feedback",
            json={"verdict": "helpful", "note": "  trade partner really was too far  "},
            headers=headers(OWNER_A),
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["verdict"], "helpful")
        self.assertEqual(saved.json()["note"], "trade partner really was too far")

        listed = self.client.get("/demos/demo-a/coaching", headers=headers(OWNER_A)).json()
        by_id = {event["id"]: event for event in listed}
        self.assertEqual(by_id["event-entry"]["feedback"]["verdict"], "helpful")
        self.assertIsNone(by_id["event-spacing"]["feedback"])

        # A second verdict replaces the first instead of adding a row.
        replaced = self.client.put(
            "/demos/demo-a/coaching/event-entry/feedback",
            json={"verdict": "irrelevant"},
            headers=headers(OWNER_A),
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(replaced.json(), {**replaced.json(), "verdict": "irrelevant", "note": None})
        with self.Session() as db:
            self.assertEqual(db.query(CoachingFeedback).count(), 1)

        cleared = self.client.delete("/demos/demo-a/coaching/event-entry/feedback", headers=headers(OWNER_A))
        self.assertEqual(cleared.status_code, 204)
        listed = self.client.get("/demos/demo-a/coaching", headers=headers(OWNER_A)).json()
        self.assertIsNone(next(event for event in listed if event["id"] == "event-entry")["feedback"])
        # Clearing again is still "no verdict", not an error.
        self.assertEqual(
            self.client.delete("/demos/demo-a/coaching/event-entry/feedback", headers=headers(OWNER_A)).status_code,
            204,
        )

    def test_verdicts_are_owner_private(self) -> None:
        self.client.put(
            "/demos/demo-a/coaching/event-entry/feedback",
            json={"verdict": "helpful"},
            headers=headers(OWNER_A),
        )
        for method, path in (
            ("get", "/demos/demo-a/coaching"),
            ("put", "/demos/demo-a/coaching/event-entry/feedback"),
            ("delete", "/demos/demo-a/coaching/event-entry/feedback"),
            ("get", "/coaching/feedback/summary?demo_id=demo-a"),
        ):
            with self.subTest(method=method, path=path):
                request = getattr(self.client, method)
                kwargs = {"json": {"verdict": "unsure"}} if method == "put" else {}
                self.assertEqual(request(path, headers=headers(OWNER_B), **kwargs).status_code, 404)
        with self.Session() as db:
            self.assertEqual(db.query(CoachingFeedback).filter(CoachingFeedback.owner_id == OWNER_B).count(), 0)

    def test_unknown_events_and_invalid_bodies_are_rejected(self) -> None:
        # An event that belongs to another demo is not this demo's event.
        foreign = self.client.put(
            "/demos/demo-a/coaching/event-b/feedback", json={"verdict": "helpful"}, headers=headers(OWNER_A)
        )
        self.assertEqual(foreign.status_code, 404)
        missing = self.client.put(
            "/demos/demo-a/coaching/no-such-event/feedback", json={"verdict": "helpful"}, headers=headers(OWNER_A)
        )
        self.assertEqual(missing.status_code, 404)
        bad_verdict = self.client.put(
            "/demos/demo-a/coaching/event-entry/feedback", json={"verdict": "great"}, headers=headers(OWNER_A)
        )
        self.assertEqual(bad_verdict.status_code, 422)
        long_note = self.client.put(
            "/demos/demo-a/coaching/event-entry/feedback",
            json={"verdict": "helpful", "note": "x" * 241},
            headers=headers(OWNER_A),
        )
        self.assertEqual(long_note.status_code, 422)
        with self.Session() as db:
            self.assertEqual(db.query(CoachingFeedback).count(), 0)

    def test_summary_groups_by_rule_and_only_counts_verdicts_on_live_events(self) -> None:
        for event_id, demo_id, verdict in (
            ("event-entry", "demo-a", "helpful"),
            ("event-spacing", "demo-a", "irrelevant"),
            ("event-spacing-2", "demo-a2", "unsure"),
        ):
            response = self.client.put(
                f"/demos/{demo_id}/coaching/{event_id}/feedback",
                json={"verdict": verdict},
                headers=headers(OWNER_A),
            )
            self.assertEqual(response.status_code, 200, response.text)
        with self.Session() as db:
            # A verdict left behind by a suggestion that a later re-parse no
            # longer produces, and another owner's verdict: neither may count.
            db.add(feedback_row("stale", "demo-a", "event-gone", OWNER_A, "helpful"))
            db.add(feedback_row("other-owner", "demo-b", "event-b", OWNER_B, "helpful"))
            db.commit()

        summary = self.client.get("/coaching/feedback/summary", headers=headers(OWNER_A)).json()
        self.assertEqual(
            {key: summary[key] for key in ("demo_count", "total", "rated", "helpful", "irrelevant", "unsure")},
            {"demo_count": 2, "total": 3, "rated": 3, "helpful": 1, "irrelevant": 1, "unsure": 1},
        )
        self.assertEqual(
            summary["rules"],
            [
                {"rule_id": "isolated_entry", "total": 1, "rated": 1, "helpful": 1, "irrelevant": 0, "unsure": 0},
                {"rule_id": "poor_spacing", "total": 2, "rated": 2, "helpful": 0, "irrelevant": 1, "unsure": 1},
            ],
        )

        one_demo = self.client.get("/coaching/feedback/summary?demo_id=demo-a2", headers=headers(OWNER_A)).json()
        self.assertEqual(one_demo["demo_count"], 1)
        self.assertEqual(one_demo["rules"], [
            {"rule_id": "poor_spacing", "total": 1, "rated": 1, "helpful": 0, "irrelevant": 0, "unsure": 1},
        ])

    def test_verdict_survives_a_reparse_that_recreates_the_same_suggestion(self) -> None:
        self.client.put(
            "/demos/demo-a/coaching/event-entry/feedback", json={"verdict": "helpful"}, headers=headers(OWNER_A)
        )
        # complete_parse_job deletes every coaching row for the demo and inserts
        # the analyzer's output again; unchanged rules reproduce the same ids.
        with self.Session() as db:
            db.query(CoachingEvent).filter(CoachingEvent.demo_id == "demo-a").delete()
            db.commit()
            add_event(db, "event-entry", "demo-a", rule_id="isolated_entry", tick_start=1100)

        listed = self.client.get("/demos/demo-a/coaching", headers=headers(OWNER_A)).json()
        self.assertEqual([event["id"] for event in listed], ["event-entry"])
        self.assertEqual(listed[0]["feedback"]["verdict"], "helpful")
        summary = self.client.get("/coaching/feedback/summary?demo_id=demo-a", headers=headers(OWNER_A)).json()
        self.assertEqual((summary["total"], summary["rated"], summary["helpful"]), (1, 1, 1))


def headers(owner_id: str) -> dict[str, str]:
    return {"X-Dev-User-Id": owner_id}


def add_demo(db, demo_id: str, owner_id: str) -> Demo:
    timestamp = datetime(2026, 9, 18, tzinfo=UTC)
    demo = Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        map_name="de_inferno",
        tick_rate=64,
        round_count=2,
        coaching_event_count=0,
        status="completed",
        archived=False,
        replay_storage_key=f"local://replays/{demo_id}.json",
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
    )
    db.add(demo)
    db.commit()
    return demo


def add_event(db, event_id: str, demo_id: str, *, rule_id: str, tick_start: int) -> CoachingEvent:
    event = CoachingEvent(
        id=event_id,
        demo_id=demo_id,
        round_number=1,
        player_id="76561198000000001",
        player_name="T Entry",
        tick_start=tick_start,
        tick_end=tick_start + 192,
        category="positioning",
        severity="medium",
        title=f"Review {rule_id}",
        message="Sampled distance suggests a review.",
        structured_context_json={"ruleId": rule_id, "involvedPlayerIds": ["76561198000000001"]},
        confidence=0.7,
    )
    db.add(event)
    db.commit()
    return event


def feedback_row(row_id: str, demo_id: str, event_id: str, owner_id: str, verdict: str) -> CoachingFeedback:
    now = datetime(2026, 9, 18, tzinfo=UTC)
    return CoachingFeedback(
        id=row_id,
        demo_id=demo_id,
        event_id=event_id,
        owner_id=owner_id,
        verdict=verdict,
        note=None,
        created_at=now,
        updated_at=now,
    )
