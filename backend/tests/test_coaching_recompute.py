"""The background coaching recompute (app/workers/coaching_recompute.py).

A completed demo whose suggestions predate COACHING_RULES_VERSION is re-analyzed
from its stored replay and its suggestions swapped in place: same status, same
completed_at/updated_at, no ledger entry, no new artifact, verdicts keyed by
event id (a surviving id keeps its verdict, a vanished one is kept but neither
listed nor counted), and nothing resurrected when the match is deleted
meanwhile. The analyzer is injected so these tests do not depend on what the
rules emit. Foreign keys are enforced, as in test_data_deletion.
"""

import copy
import dataclasses
import io
import json
import tempfile
import unittest
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.analysis.analyzer import analyze_replay
from app.analysis.version import COACHING_RULES_VERSION
from app.core.config import settings
from app.core.database import Base
from app.models import Demo, DemoJob
from app.models.coaching import CoachingEvent, CoachingFeedback
from app.models.deletion import UploadLedger
from app.services.deletion_service import DeletionService
from app.services.demo_service import DemoService, coaching_event_rows
from app.services.demo_service.coaching_recompute import CLAIM_HOLD_SECONDS, STATE_KEY, VERSION_KEY
from app.services.demo_service.replay_upgrade import STATE_KEY as UPGRADE_STATE_KEY
from app.services.demo_service.replay_upgrade import VERSION_KEY as REPLAY_VERSION_KEY
from app.services.storage import LocalArtifactStore, LocalStorageService
from app.workers import coaching_recompute, replay_upgrade, worker
from app.workers.coaching_recompute import recompute_stale_coaching
from app.workers.replay_upgrade import upgrade_stale_replays
from app.workers.worker import process_mock_parse_job, process_real_parse_job

OLD_RULES = "coaching_rules_v1"
OLD_CONTRACT = "replay_contract_v1"


def recompute_settings(**changes: Any) -> ExitStack:
    """Settings is frozen; swap a copy into every module that reads these knobs."""
    stack = ExitStack()
    replaced = dataclasses.replace(settings, **changes)
    for target in (
        "app.services.demo_service.coaching_recompute.settings",
        "app.workers.coaching_recompute.settings",
        "app.services.demo_service.replay_upgrade.settings",
        "app.workers.replay_upgrade.settings",
    ):
        stack.enter_context(patch(target, replaced))
    return stack


def fk_engine(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine


def _player(pid: str, name: str, side: str, x: float, y: float, *, alive: bool = True) -> dict[str, Any]:
    return {"id": pid, "name": name, "side": side, "x": x, "y": y, "z": 0, "hp": 100 if alive else 0, "alive": alive}


def parsed_match() -> dict[str, Any]:
    """Parser output (world units) for a small Mirage round with one death."""
    death = {
        "tick": 100, "roundNumber": 1,
        "attackerId": "ct-1", "attackerName": "CT One", "attackerSide": "CT",
        "victimId": "t-entry", "victimName": "T Entry", "victimSide": "T",
    }
    return {
        "mapName": "de_mirage",
        "tickRate": 64,
        "rounds": [
            {"roundNumber": 1, "startTick": 0, "freezeEndTick": 64, "endTick": 1400, "winnerSide": "CT"},
        ],
        "players": [
            {"id": "t-entry", "name": "T Entry", "side": "T"},
            {"id": "t-trade", "name": "T Trade", "side": "T"},
            {"id": "ct-1", "name": "CT One", "side": "CT"},
        ],
        "frames": [
            {"tick": 96, "roundNumber": 1, "players": [
                _player("t-entry", "T Entry", "T", -400, -1500),
                _player("t-trade", "T Trade", "T", 1000, 300),
                _player("ct-1", "CT One", "CT", -380, -1480),
            ]},
            {"tick": 112, "roundNumber": 1, "players": [
                _player("t-entry", "T Entry", "T", -400, -1500, alive=False),
                _player("t-trade", "T Trade", "T", 1000, 300),
                _player("ct-1", "CT One", "CT", -380, -1480),
            ]},
        ],
        "kills": [dict(death)],
        "deaths": [dict(death)],
        "events": [],
        "teamNames": {"t-entry": "MOUZ", "ct-1": "Spirit"},
    }


def fake_event(demo_id: str, name: str, *, tick: int = 100) -> dict[str, Any]:
    """An analyzer-shaped event whose id, like the real ones, is derived from the demo."""
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{demo_id}:{name}")),
        "demo_id": demo_id,
        "round_number": 1,
        "player_id": "t-entry",
        "player_name": "T Entry",
        "tick_start": tick,
        "tick_end": tick + 64,
        "category": "trade",
        "severity": "medium",
        "title": f"{name} title",
        "message": f"{name} message",
        "structured_context_json": {"ruleId": f"rule_{name}", "targetPlayerId": "t-entry"},
        "confidence": 0.8,
    }


def event_id(demo_id: str, name: str) -> str:
    return str(fake_event(demo_id, name)["id"])


class FakeAnalyzer:
    """Emits one event per name for whatever replay it is given, recording each call."""

    def __init__(self, *names: str, side_effect: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.names = list(names)
        self.side_effect = side_effect
        self.calls: list[dict[str, Any]] = []

    def __call__(self, replay: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append({"demoId": replay.get("demoId"), "contractVersion": replay.get("contractVersion")})
        if self.side_effect is not None:
            self.side_effect(replay)
        demo_id = str(replay["demoId"])
        return [fake_event(demo_id, name, tick=100 + index) for index, name in enumerate(self.names)]


class CoachingEventRowsTest(unittest.TestCase):
    def test_rows_belong_to_the_claimed_demo_and_bad_output_is_rejected(self) -> None:
        events = [fake_event("other-demo", "a"), {**fake_event("other-demo", "b"), "extra": 1}]
        rows = coaching_event_rows(events, "demo-1")
        self.assertEqual([row["demo_id"] for row in rows], ["demo-1", "demo-1"])
        self.assertNotIn("extra", rows[1])
        with self.assertRaises(ValueError):
            coaching_event_rows([fake_event("d", "a"), fake_event("d", "a")], "d")
        incomplete = fake_event("d", "a")
        incomplete.pop("title")
        with self.assertRaises(ValueError):
            coaching_event_rows([incomplete], "d")
        with self.assertRaises(ValueError):
            coaching_event_rows([{**fake_event("d", "a"), "structured_context_json": "x"}], "d")


class CoachingRecomputeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = fk_engine(root / "recompute.db")
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        self.legacy = LocalStorageService(root / "legacy")

        def internal(db: Any) -> DemoService:
            return DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)

        self.patches = [
            patch.object(DemoService, "for_internal", side_effect=internal),
            patch("app.services.demo_service.get_redis_client"),
        ]
        for item in self.patches:
            item.start()
        coaching_recompute.reset_recompute_state()
        replay_upgrade.reset_upgrade_state()
        self.analyzer = FakeAnalyzer("kept", "new")

    def tearDown(self) -> None:
        coaching_recompute.reset_recompute_state()
        replay_upgrade.reset_upgrade_state()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.scratch.cleanup()

    # -- helpers ----------------------------------------------------------------

    def service(self, db: Any) -> DemoService:
        return DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)

    def owner_service(self, db: Any) -> DemoService:
        return DemoService(db, owner_id=settings.dev_user_id, storage=self.legacy, artifact_store=self.store)

    def upload(self) -> str:
        with self.Session() as db:
            created = self.owner_service(db).create_real_demo(SimpleNamespace(
                filename="spirit-vs-mouz.dem",
                content_type="application/octet-stream",
                file=io.BytesIO(b"HL2DEMO\x00" + b"recompute-payload" * 4),
            ))
            return str(created.id)

    def upload_and_parse(self, *names: str) -> str:
        """A real parse whose (older) rules emitted `names` (default: kept, vanishing)."""
        demo_id = self.upload()
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
            with patch("app.workers.worker.run_parse_subprocess", return_value=parsed_match()), \
                    patch("app.workers.worker.analyze_replay", FakeAnalyzer(*(names or ("kept", "vanishing")))):
                process_real_parse_job(db, demo, job)
        return demo_id

    def edit_metadata(self, demo_id: str, change: Callable[[dict[str, Any]], None]) -> None:
        with self.Session() as db:
            job = self.service(db).latest_parse_job(db.get(Demo, demo_id))
            assert job is not None
            metadata = json.loads(job.metadata_json)
            change(metadata)
            job.metadata_json = json.dumps(metadata, separators=(",", ":"))
            db.commit()

    def make_stale(self, demo_id: str, version: str | None = OLD_RULES) -> None:
        """Mark the stored suggestions as made by older rules (None: before the marker existed)."""

        def change(metadata: dict[str, Any]) -> None:
            if version is None:
                metadata.pop(VERSION_KEY, None)
            else:
                metadata[VERSION_KEY] = version

        self.edit_metadata(demo_id, change)

    def make_old_replay(self, demo_id: str) -> None:
        """Turn the stored replay into one parsed before the current contract."""
        with self.Session() as db:
            service = self.service(db)
            demo = db.get(Demo, demo_id)
            replay = copy.deepcopy(service.load_replay_blob(demo))
            assert replay is not None
            replay["contractVersion"] = OLD_CONTRACT
            replay.pop("playerStates", None)
            replay.pop("utility", None)
            previous = demo.replay_storage_key
            demo.replay_storage_key = service.write_replay_blob(demo_id, replay)
            db.commit()
            service.delete_artifact_safely(previous)
        self.edit_metadata(demo_id, lambda metadata: metadata.pop(REPLAY_VERSION_KEY, None))

    def rewind_retry(self, demo_id: str) -> None:
        def change(metadata: dict[str, Any]) -> None:
            metadata[STATE_KEY]["nextAttemptAt"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

        self.edit_metadata(demo_id, change)

    def run_pass(self, analyze: Any = None, **kwargs: Any) -> str | None:
        return recompute_stale_coaching(
            analyze=analyze or self.analyzer,
            session_factory=self.Session,
            force=True,
            **kwargs,
        )

    def due(self) -> list[str]:
        with self.Session() as db:
            return self.service(db).demo_ids_due_for_coaching_recompute(limit=10)

    def snapshot(self, demo_id: str) -> dict[str, Any]:
        with self.Session() as db:
            service = self.service(db)
            demo = db.get(Demo, demo_id)
            job = service.latest_parse_job(demo)
            events = db.query(CoachingEvent).filter(CoachingEvent.demo_id == demo_id).all()
            return {
                "status": demo.status,
                "archived": demo.archived,
                "name": demo.name,
                "completed_at": demo.completed_at,
                "updated_at": demo.updated_at,
                "replay_key": demo.replay_storage_key,
                "coaching_event_count": demo.coaching_event_count,
                "event_ids": sorted(row.id for row in events),
                "titles": sorted(row.title for row in events),
                "job_id": job.id if job else None,
                "job_attempts": job.attempts if job else None,
                "metadata": json.loads(job.metadata_json) if job else {},
                "feedback_rows": db.query(CoachingFeedback).filter(CoachingFeedback.demo_id == demo_id).count(),
                "ledger": db.query(UploadLedger).count(),
            }

    def artifact_count(self) -> int:
        root = Path(self.scratch.name) / "artifacts"
        return sum(1 for path in root.rglob("*") if path.is_file() and not path.name.endswith(".json.meta"))

    def rate(self, demo_id: str, name: str, verdict: str) -> None:
        with self.Session() as db:
            self.owner_service(db).save_coaching_feedback(db.get(Demo, demo_id), event_id(demo_id, name), verdict=verdict)

    def visible_verdicts(self, demo_id: str) -> tuple[dict[str, str], Any]:
        with self.Session() as db:
            service = self.owner_service(db)
            listed = {item.id: item.feedback.verdict for item in service.list_coaching_events(demo_id) if item.feedback}
            return listed, service.coaching_feedback_summary(demo_id)

    # -- parse writes the marker ----------------------------------------------------

    def test_a_new_parse_records_the_current_rules_and_owes_nothing(self) -> None:
        demo_id = self.upload_and_parse()
        state = self.snapshot(demo_id)
        self.assertEqual(state["metadata"][VERSION_KEY], COACHING_RULES_VERSION)
        self.assertNotIn(STATE_KEY, state["metadata"])
        self.assertEqual(self.due(), [])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.analyzer.calls, [])

    def test_complete_parse_job_writes_the_marker_and_drops_recompute_state(self) -> None:
        demo_id = self.upload_and_parse()

        def stale_with_state(metadata: dict[str, Any]) -> None:
            metadata[VERSION_KEY] = OLD_RULES
            metadata[STATE_KEY] = {"rulesVersion": COACHING_RULES_VERSION, "attempts": 2, "exhausted": True}

        self.edit_metadata(demo_id, stale_with_state)
        with self.Session() as db:
            service = self.service(db)
            demo = db.get(Demo, demo_id)
            job = service.latest_parse_job(demo)
            assert job is not None
            replay = service.load_replay_blob(demo)
            assert replay is not None
            self.assertTrue(service.complete_parse_job(demo, job, dict(replay), [fake_event(demo_id, "fresh")]))
        after = self.snapshot(demo_id)
        self.assertEqual(after["metadata"][VERSION_KEY], COACHING_RULES_VERSION)
        self.assertNotIn(STATE_KEY, after["metadata"])
        self.assertEqual(after["event_ids"], [event_id(demo_id, "fresh")])

    # -- the swap -------------------------------------------------------------------

    def test_recompute_replaces_the_suggestions_without_a_visible_change(self) -> None:
        demo_id = self.upload_and_parse("kept", "vanishing")
        self.make_stale(demo_id, None)
        self.rate(demo_id, "kept", "helpful")
        self.rate(demo_id, "vanishing", "irrelevant")
        before = self.snapshot(demo_id)
        self.assertEqual(before["coaching_event_count"], 2)
        self.assertEqual(self.due(), [demo_id])
        artifacts_before = self.artifact_count()

        self.assertEqual(self.run_pass(), "recomputed")

        after = self.snapshot(demo_id)
        self.assertEqual(self.analyzer.calls, [{"demoId": demo_id, "contractVersion": before["metadata"][REPLAY_VERSION_KEY]}])
        self.assertEqual(after["event_ids"], sorted([event_id(demo_id, "kept"), event_id(demo_id, "new")]))
        self.assertEqual(after["coaching_event_count"], 2)
        for field in (
            "status", "archived", "name", "completed_at", "updated_at", "replay_key", "job_id", "job_attempts", "ledger",
        ):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["metadata"][VERSION_KEY], COACHING_RULES_VERSION)
        self.assertNotIn(STATE_KEY, after["metadata"])
        self.assertEqual(after["metadata"][REPLAY_VERSION_KEY], before["metadata"][REPLAY_VERSION_KEY])
        self.assertEqual(self.artifact_count(), artifacts_before)

        # Verdicts: the surviving id keeps its verdict; the vanished one's row
        # is kept but neither listed nor counted.
        self.assertEqual(after["feedback_rows"], 2)
        listed, summary = self.visible_verdicts(demo_id)
        self.assertEqual(listed, {event_id(demo_id, "kept"): "helpful"})
        self.assertEqual((summary.total, summary.rated, summary.helpful, summary.irrelevant), (2, 1, 1, 0))

        # Idempotent: nothing is owed any more.
        self.assertEqual(self.due(), [])
        self.assertIsNone(self.run_pass())
        self.assertEqual(len(self.analyzer.calls), 1)

        # A later rules version that emits the vanished id again brings its verdict back.
        with patch("app.services.demo_service.coaching_recompute.COACHING_RULES_VERSION", "coaching_rules_v99"):
            self.assertEqual(self.run_pass(FakeAnalyzer("kept", "vanishing")), "recomputed")
            listed, summary = self.visible_verdicts(demo_id)
        self.assertEqual(listed, {event_id(demo_id, "kept"): "helpful", event_id(demo_id, "vanishing"): "irrelevant"})
        self.assertEqual((summary.rated, summary.irrelevant), (2, 1))

    def test_the_replay_is_read_and_analyzed_outside_any_transaction(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        original = DemoService.load_replay_blob
        open_transactions: list[bool] = []

        def spy(service: DemoService, demo: Demo) -> Any:
            open_transactions.append(service.db.in_transaction())
            return original(service, demo)

        def analyzer_side_effect(_replay: dict[str, Any]) -> None:
            open_transactions.append(any(session.in_transaction() for session in sessions))

        sessions: list[Any] = []

        def tracked_session() -> Any:
            session = self.Session()
            sessions.append(session)
            return session

        with patch.object(DemoService, "load_replay_blob", autospec=True, side_effect=spy):
            self.assertEqual(
                recompute_stale_coaching(
                    analyze=FakeAnalyzer("kept", side_effect=analyzer_side_effect),
                    session_factory=tracked_session,
                    force=True,
                ),
                "recomputed",
            )
        self.assertEqual(open_transactions, [False, False])

    def test_an_analyzer_that_now_finds_nothing_empties_the_suggestions(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        self.assertEqual(self.run_pass(FakeAnalyzer()), "recomputed")
        after = self.snapshot(demo_id)
        self.assertEqual(after["event_ids"], [])
        self.assertEqual(after["coaching_event_count"], 0)
        self.assertEqual(after["metadata"][VERSION_KEY], COACHING_RULES_VERSION)

    def test_the_real_analyzer_end_to_end(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        with self.Session() as db:
            replay = self.service(db).load_replay_blob(db.get(Demo, demo_id))
        expected = sorted(str(item["id"]) for item in analyze_replay(dict(replay or {})))
        self.assertEqual(
            recompute_stale_coaching(session_factory=self.Session, force=True),
            "recomputed",
        )
        after = self.snapshot(demo_id)
        self.assertEqual(after["event_ids"], expected)
        self.assertEqual(after["coaching_event_count"], len(expected))
        self.assertEqual(after["metadata"][VERSION_KEY], COACHING_RULES_VERSION)

    def test_one_demo_per_pass_unarchived_then_newest_first(self) -> None:
        older = self.upload_and_parse()
        newer = self.upload_and_parse()
        newest_archived = self.upload_and_parse()
        for demo_id in (older, newer, newest_archived):
            self.make_stale(demo_id)
        with self.Session() as db:
            self.owner_service(db).archive_demo(newest_archived)
        self.assertEqual(self.due(), [newer, older, newest_archived])
        self.assertEqual(self.run_pass(), "recomputed")
        self.assertEqual(self.snapshot(newer)["metadata"][VERSION_KEY], COACHING_RULES_VERSION)
        self.assertEqual(self.snapshot(older)["metadata"][VERSION_KEY], OLD_RULES)
        self.assertEqual(self.run_pass(), "recomputed")
        self.assertEqual(self.run_pass(), "recomputed")
        self.assertTrue(self.snapshot(newest_archived)["archived"])
        self.assertEqual(self.snapshot(newest_archived)["metadata"][VERSION_KEY], COACHING_RULES_VERSION)
        self.assertIsNone(self.run_pass())

    # -- what is skipped --------------------------------------------------------------

    def test_mock_demos_are_never_recomputed(self) -> None:
        with self.Session() as db:
            created = self.owner_service(db).create_mock_demo()
            demo = db.get(Demo, created.id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo.id).one()
            with patch("app.workers.worker.time.sleep"):
                process_mock_parse_job(db, demo, job)
        self.make_stale(str(created.id), None)
        self.assertEqual(self.due(), [])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.analyzer.calls, [])

    def test_demos_that_are_not_completed_are_skipped(self) -> None:
        queued = self.upload()
        parsed = self.upload_and_parse()
        self.make_stale(parsed)
        with self.Session() as db:
            # A retry queued on top of the completed parse: the latest job is not completed.
            db.add(DemoJob(
                id="queued-retry", demo_id=parsed, job_type="real_parse", status="queued", attempts=0,
                metadata_json="{}", created_at=datetime.now(UTC) + timedelta(minutes=1),
            ))
            db.commit()
        self.assertEqual(self.due(), [])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.snapshot(queued)["status"], "queued")
        self.assertEqual(self.analyzer.calls, [])

    def test_a_demo_waits_for_its_replay_upgrade_and_is_recomputed_once_after_it(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        self.make_old_replay(demo_id)
        self.assertEqual(self.due(), [])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.analyzer.calls, [])

        self.assertEqual(
            upgrade_stale_replays(
                run_parse=lambda *_a, **_k: parsed_match(),
                session_factory=self.Session,
                force=True,
                check_interval_seconds=0,
            ),
            "upgraded",
        )
        upgraded = self.snapshot(demo_id)
        # The replay upgrade does not run the analyzer and keeps the marker.
        self.assertEqual(upgraded["metadata"][VERSION_KEY], OLD_RULES)
        self.assertEqual(self.run_pass(), "recomputed")
        self.assertEqual(self.analyzer.calls[0]["contractVersion"], upgraded["metadata"][REPLAY_VERSION_KEY])
        self.assertIsNone(self.run_pass())
        self.assertEqual(len(self.analyzer.calls), 1)

    def test_the_replay_upgrade_keeps_a_current_marker(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old_replay(demo_id)
        self.assertEqual(
            upgrade_stale_replays(
                run_parse=lambda *_a, **_k: parsed_match(),
                session_factory=self.Session,
                force=True,
                check_interval_seconds=0,
            ),
            "upgraded",
        )
        self.assertEqual(self.snapshot(demo_id)["metadata"][VERSION_KEY], COACHING_RULES_VERSION)
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.analyzer.calls, [])

    def test_an_exhausted_or_disabled_replay_upgrade_does_not_hold_the_recompute(self) -> None:
        exhausted = self.upload_and_parse()
        disabled = self.upload_and_parse()
        for demo_id in (exhausted, disabled):
            self.make_stale(demo_id)
            self.make_old_replay(demo_id)
        self.edit_metadata(
            exhausted,
            lambda metadata: metadata.update({UPGRADE_STATE_KEY: {"attempts": 3, "exhausted": True}}),
        )
        self.assertEqual(self.due(), [exhausted])
        with recompute_settings(replay_upgrade_enabled=False):
            self.assertEqual(self.due(), [disabled, exhausted])

    def test_an_exhausted_recompute_does_not_hold_back_a_later_replay_upgrade(self) -> None:
        # Both states write `"exhausted":true`; the replay upgrade must still pick the demo.
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        def crashing(_replay: dict[str, Any]) -> list[dict[str, Any]]:
            raise RuntimeError("boom")

        with recompute_settings(coaching_recompute_max_attempts=1):
            self.assertEqual(self.run_pass(crashing), "failed:ANALYZE_FAILED")
        self.assertTrue(self.snapshot(demo_id)["metadata"][STATE_KEY]["exhausted"])
        self.make_old_replay(demo_id)
        with self.Session() as db:
            self.assertEqual(self.service(db).demo_ids_due_for_replay_upgrade(limit=10), [demo_id])
        self.assertEqual(
            upgrade_stale_replays(
                run_parse=lambda *_a, **_k: parsed_match(),
                session_factory=self.Session,
                force=True,
                check_interval_seconds=0,
            ),
            "upgraded",
        )

    def test_exhausted_not_due_and_current_demos_are_skipped(self) -> None:
        exhausted = self.upload_and_parse()
        waiting = self.upload_and_parse()
        self.upload_and_parse()  # current
        self.make_stale(exhausted)
        self.make_stale(waiting)
        self.edit_metadata(exhausted, lambda metadata: metadata.update({
            STATE_KEY: {"rulesVersion": COACHING_RULES_VERSION, "attempts": 3, "exhausted": True},
        }))
        self.edit_metadata(waiting, lambda metadata: metadata.update({STATE_KEY: {
            "rulesVersion": COACHING_RULES_VERSION,
            "attempts": 1,
            "nextAttemptAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }}))
        self.assertEqual(self.due(), [])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.analyzer.calls, [])

    def test_state_left_by_an_older_rules_version_starts_afresh(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        self.edit_metadata(demo_id, lambda metadata: metadata.update({
            STATE_KEY: {"rulesVersion": OLD_RULES, "attempts": 3, "exhausted": True, "lastErrorCode": "ANALYZE_FAILED"},
        }))
        self.assertEqual(self.due(), [demo_id])
        self.assertEqual(self.run_pass(), "recomputed")

    def test_disabled_recompute_does_nothing(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        with recompute_settings(coaching_recompute_enabled=False):
            self.assertIsNone(self.run_pass())
        self.assertEqual(self.analyzer.calls, [])

    # -- the demo moved on meanwhile -------------------------------------------------

    def test_a_replay_rewritten_meanwhile_wins_and_the_attempt_is_given_back(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)

        def rewrite_replay(_replay: dict[str, Any]) -> None:
            with self.Session() as db:
                self.service(db).update_replay_video(db.get(Demo, demo_id), {"status": "ready", "source": "manual_upload"})

        self.assertEqual(self.run_pass(FakeAnalyzer("kept", "new", side_effect=rewrite_replay)), "changed")
        after = self.snapshot(demo_id)
        self.assertNotEqual(after["replay_key"], before["replay_key"])
        self.assertEqual(after["event_ids"], before["event_ids"])
        self.assertEqual(after["metadata"], before["metadata"])
        # Retried on the next pass, on top of the rewritten replay.
        self.assertEqual(self.run_pass(), "recomputed")

    def test_a_newer_parse_job_wins(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)

        def queue_retry(_replay: dict[str, Any]) -> None:
            with self.Session() as db:
                db.add(DemoJob(
                    id="newer-job", demo_id=demo_id, job_type="real_parse", status="queued", attempts=0,
                    metadata_json="{}", created_at=datetime.now(UTC) + timedelta(minutes=1),
                ))
                db.commit()

        self.assertEqual(self.run_pass(FakeAnalyzer("new", side_effect=queue_retry)), "changed")
        with self.Session() as db:
            old_job = db.get(DemoJob, before["job_id"])
            self.assertEqual(json.loads(old_job.metadata_json), before["metadata"])
        self.assertEqual(self.snapshot(demo_id)["event_ids"], before["event_ids"])

    def test_job_metadata_rewritten_meanwhile_wins(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)

        def rewrite_metadata(_replay: dict[str, Any]) -> None:
            self.edit_metadata(demo_id, lambda metadata: metadata.update({"phase": "ready-elsewhere"}))

        self.assertEqual(self.run_pass(FakeAnalyzer("new", side_effect=rewrite_metadata)), "changed")
        after = self.snapshot(demo_id)
        self.assertEqual(after["event_ids"], before["event_ids"])
        self.assertEqual(after["coaching_event_count"], before["coaching_event_count"])
        self.assertEqual(after["metadata"]["phase"], "ready-elsewhere")
        self.assertEqual(after["metadata"][VERSION_KEY], OLD_RULES)
        # Not counted: the claim's state is taken back out of the rewritten metadata.
        self.assertNotIn(STATE_KEY, after["metadata"])
        self.assertEqual(self.due(), [demo_id])
        self.assertEqual(self.run_pass(), "recomputed")

    def test_a_claim_state_rewritten_meanwhile_is_left_alone(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)

        def rewrite_state(_replay: dict[str, Any]) -> None:
            self.edit_metadata(demo_id, lambda metadata: metadata[STATE_KEY].update({"attempts": 2}))

        self.assertEqual(self.run_pass(FakeAnalyzer("new", side_effect=rewrite_state)), "changed")
        # Someone else's recompute state now: the release does not overwrite it.
        self.assertEqual(self.snapshot(demo_id)["metadata"][STATE_KEY]["attempts"], 2)

    def test_deleted_during_the_analysis_writes_nothing(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        self.rate(demo_id, "kept", "helpful")

        def delete_match(_replay: dict[str, Any]) -> None:
            with self.Session() as db:
                self.assertTrue(
                    DeletionService(db, artifact_store=self.store, legacy_storage=self.legacy)
                    .delete_demo(settings.dev_user_id, demo_id)
                )

        self.assertEqual(self.run_pass(FakeAnalyzer("kept", side_effect=delete_match)), "gone")
        with self.Session() as db:
            self.assertIsNone(db.get(Demo, demo_id))
            self.assertEqual(db.query(CoachingEvent).count(), 0)
            self.assertEqual(db.query(CoachingFeedback).count(), 0)
            self.assertEqual(db.query(DemoJob).count(), 0)
        self.assertEqual(self.artifact_count(), 0)
        self.assertIsNone(self.run_pass())

    def test_deleted_while_the_replay_loads_is_not_a_failure(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)

        def load_then_delete(*_args: Any, **_kwargs: Any) -> None:
            with self.Session() as db:
                DeletionService(db, artifact_store=self.store, legacy_storage=self.legacy).delete_demo(
                    settings.dev_user_id, demo_id,
                )

        with patch.object(DemoService, "load_replay_blob", side_effect=load_then_delete):
            self.assertEqual(self.run_pass(), "gone")
        self.assertEqual(self.analyzer.calls, [])

    # -- failures ----------------------------------------------------------------------

    def test_analyzer_failures_back_off_then_keep_the_old_suggestions_for_good(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)
        crashes: list[str] = []

        def crashing(_replay: dict[str, Any]) -> list[dict[str, Any]]:
            crashes.append("crash")
            raise RuntimeError("boom at C:/secret/path")

        with recompute_settings(coaching_recompute_max_attempts=2):
            self.assertEqual(self.run_pass(crashing), "failed:ANALYZE_FAILED")
            state = self.snapshot(demo_id)["metadata"][STATE_KEY]
            self.assertEqual(state["attempts"], 1)
            self.assertEqual(state["lastErrorCode"], "ANALYZE_FAILED")
            self.assertEqual(state["rulesVersion"], COACHING_RULES_VERSION)
            self.assertNotIn("secret", json.dumps(state))
            self.assertGreater(datetime.fromisoformat(state["nextAttemptAt"]), datetime.now(UTC))
            # Not due yet: no second run.
            self.assertIsNone(self.run_pass(crashing))
            self.assertEqual(len(crashes), 1)

            self.rewind_retry(demo_id)
            self.assertEqual(self.run_pass(crashing), "failed:ANALYZE_FAILED")
            after = self.snapshot(demo_id)
            self.assertTrue(after["metadata"][STATE_KEY]["exhausted"])
            self.rewind_retry(demo_id)
            self.assertEqual(self.due(), [])
            self.assertIsNone(self.run_pass(crashing))
            self.assertEqual(len(crashes), 2)
        for field in ("status", "replay_key", "event_ids", "titles", "coaching_event_count", "completed_at", "updated_at"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["metadata"][VERSION_KEY], OLD_RULES)

    def test_malformed_analyzer_output_is_an_analyzer_failure(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)
        self.assertEqual(self.run_pass(FakeAnalyzer("twice", "twice")), "failed:ANALYZE_FAILED")
        self.assertEqual(self.snapshot(demo_id)["event_ids"], before["event_ids"])

    def test_a_replay_of_another_demo_is_a_recorded_failure(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        with self.Session() as db:
            replay = dict(self.service(db).load_replay_blob(db.get(Demo, demo_id)) or {})
        replay["demoId"] = "someone-else"
        with patch.object(DemoService, "load_replay_blob", return_value=replay):
            self.assertEqual(self.run_pass(), "failed:REPLAY_ID_MISMATCH")
        self.assertEqual(self.snapshot(demo_id)["metadata"][STATE_KEY]["lastErrorCode"], "REPLAY_ID_MISMATCH")
        self.assertEqual(self.analyzer.calls, [])

    def test_an_unreadable_stored_replay_is_a_recorded_failure(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)

        def broken_load(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("corrupt replay at C:/secret/path")

        with recompute_settings(coaching_recompute_max_attempts=1), \
                patch.object(DemoService, "load_replay_blob", side_effect=broken_load):
            self.assertEqual(self.run_pass(), "failed:REPLAY_UNREADABLE")
        after = self.snapshot(demo_id)
        state = after["metadata"][STATE_KEY]
        self.assertEqual(state["lastErrorCode"], "REPLAY_UNREADABLE")
        self.assertTrue(state["exhausted"])
        self.assertNotIn("secret", json.dumps(state))
        self.assertEqual(after["event_ids"], before["event_ids"])
        with patch.object(DemoService, "load_replay_blob", return_value=None):
            self.rewind_retry(demo_id)
            self.assertIsNone(self.run_pass())

    def test_a_missing_replay_is_a_recorded_failure(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        with patch.object(DemoService, "load_replay_blob", return_value=None):
            self.assertEqual(self.run_pass(), "failed:REPLAY_UNREADABLE")

    def test_a_write_failure_keeps_the_old_suggestions(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)
        with patch.object(DemoService, "complete_coaching_recompute", side_effect=RuntimeError("db hiccup")):
            self.assertEqual(self.run_pass(), "failed:RECOMPUTE_WRITE_FAILED")
        after = self.snapshot(demo_id)
        self.assertEqual(after["event_ids"], before["event_ids"])
        self.assertEqual(after["metadata"][STATE_KEY]["lastErrorCode"], "RECOMPUTE_WRITE_FAILED")

    def test_a_worker_dying_mid_recompute_still_spends_the_attempt(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        with self.Session() as db:
            claim = self.service(db).claim_coaching_recompute(demo_id)
        self.assertIsNotNone(claim)
        state = self.snapshot(demo_id)["metadata"][STATE_KEY]
        self.assertEqual(state["attempts"], 1)
        held_until = datetime.fromisoformat(state["nextAttemptAt"])
        self.assertGreaterEqual(held_until, datetime.now(UTC) + timedelta(seconds=CLAIM_HOLD_SECONDS - 60))
        # Another worker (or this one after a restart) leaves it alone until then.
        self.assertIsNone(self.run_pass())
        with self.Session() as db:
            self.assertIsNone(self.service(db).claim_coaching_recompute(demo_id))

    def test_attempts_a_dead_worker_never_finished_still_end_in_giving_up(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        with recompute_settings(coaching_recompute_max_attempts=2):
            for expected_attempt in (1, 2):
                with self.Session() as db:
                    claim = self.service(db).claim_coaching_recompute(demo_id)
                assert claim is not None
                self.assertEqual(claim.attempt, expected_attempt)
                # The worker dies here: no failure is ever recorded.
                self.rewind_retry(demo_id)
            self.assertIsNone(self.run_pass())
            after = self.snapshot(demo_id)
            self.assertEqual(self.due(), [])
        self.assertEqual(self.analyzer.calls, [])
        state = after["metadata"][STATE_KEY]
        self.assertTrue(state["exhausted"])
        self.assertEqual(state["attempts"], 2)
        self.assertEqual(state["lastErrorCode"], "RECOMPUTE_ABANDONED")
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["metadata"][VERSION_KEY], OLD_RULES)

    # -- the idle tick ----------------------------------------------------------------

    def test_waiting_work_stops_the_pass_and_gives_the_attempt_back(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        before = self.snapshot(demo_id)
        # With work already waiting the pass does not even start.
        self.assertIsNone(self.run_pass(should_yield=lambda: True))
        self.assertEqual(self.snapshot(demo_id)["metadata"], before["metadata"])
        # Work arriving while the replay loads: the claim is handed back uncounted.
        answers = iter([False, True])
        self.assertEqual(self.run_pass(should_yield=lambda: next(answers)), "yielded")
        after = self.snapshot(demo_id)
        self.assertEqual(after["metadata"], before["metadata"])
        self.assertEqual(after["event_ids"], before["event_ids"])
        self.assertEqual(self.analyzer.calls, [])
        self.assertEqual(self.run_pass(), "recomputed")

    def test_the_pass_keeps_the_worker_alive_between_steps(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_stale(demo_id)
        ticks: list[int] = []
        self.assertEqual(self.run_pass(on_tick=lambda: ticks.append(1)), "recomputed")
        self.assertGreaterEqual(len(ticks), 3)

    def test_passes_are_rate_limited(self) -> None:
        first = self.upload_and_parse()
        second = self.upload_and_parse()
        self.make_stale(first)
        self.make_stale(second)
        coaching_recompute.reset_recompute_state()
        self.assertEqual(
            recompute_stale_coaching(analyze=self.analyzer, session_factory=self.Session),
            "recomputed",
        )
        self.assertIsNone(recompute_stale_coaching(analyze=self.analyzer, session_factory=self.Session))
        self.assertEqual(len(self.analyzer.calls), 1)


class _StopWorker(BaseException):
    """Ends run_worker's endless loop once the scripted idle ticks are used up."""


class IdleQueue:
    """A queue with nothing to deliver: one idle tick, then the test stops the loop."""

    def __init__(self, idle_ticks: int) -> None:
        self.idle_ticks = idle_ticks
        self.consumer_id = "test-consumer"
        self.queue_name = "test-queue"
        self.redis = MagicMock()
        self.redis.llen.return_value = 2

    def register(self) -> None: ...

    def unregister(self) -> None: ...

    def renew_lease(self) -> None: ...

    def reserve(self, timeout: int) -> None:
        if self.idle_ticks <= 0:
            raise _StopWorker
        self.idle_ticks -= 1


class WorkerIdleHookTest(unittest.TestCase):
    def run_idle_ticks(self, recompute: Any, idle_ticks: int = 2) -> list[str]:
        calls: list[str] = []
        queue = IdleQueue(idle_ticks)

        def record(name: str) -> Callable[..., None]:
            def backstop(*_args: Any, **_kwargs: Any) -> None:
                calls.append(name)

            return backstop

        def coaching(**kwargs: Any) -> Any:
            calls.append("coaching-recompute")
            return recompute(**kwargs)

        with ExitStack() as stack:
            for name in (
                "sweep_stale_render_clip_jobs",
                "sweep_unclaimed_render_clip_jobs",
                "reap_orphaned_parse_messages",
                "sweep_stale_parse_jobs",
                "drain_deletion_outbox",
                "run_hourly_storage_maintenance",
                "backfill_match_summaries",
                "upgrade_stale_replays",
            ):
                stack.enter_context(patch.object(worker, name, side_effect=record(name)))
            stack.enter_context(patch.object(worker, "recompute_stale_coaching", side_effect=coaching))
            stack.enter_context(patch.object(worker, "ParseQueue", return_value=queue))
            stack.enter_context(patch.object(worker, "init_db"))
            stack.enter_context(patch.object(worker, "get_redis_client", return_value=MagicMock()))
            stack.enter_context(patch.object(worker, "write_worker_heartbeat"))
            stack.enter_context(patch("app.core.config.Settings.validate_worker_runtime_configuration"))
            with self.assertRaises(_StopWorker):
                worker.run_worker()
        return calls

    def test_the_recompute_runs_after_the_replay_upgrade_and_yields_to_queued_work(self) -> None:
        seen: list[dict[str, Any]] = []

        def recompute(**kwargs: Any) -> None:
            seen.append(kwargs)

        calls = self.run_idle_ticks(recompute, idle_ticks=1)
        self.assertEqual(calls[-2:], ["upgrade_stale_replays", "coaching-recompute"])
        self.assertEqual(len(seen), 1)
        # The queue reports two waiting messages: the pass must yield.
        self.assertTrue(seen[0]["should_yield"]())
        self.assertTrue(callable(seen[0]["on_tick"]))

    def test_a_failing_recompute_never_takes_the_worker_down(self) -> None:
        def recompute(**_kwargs: Any) -> None:
            raise RuntimeError("analyzer exploded")

        calls = self.run_idle_ticks(recompute, idle_ticks=2)
        # The worker survived the first failure and ran the second idle tick too.
        self.assertEqual(calls.count("coaching-recompute"), 2)


if __name__ == "__main__":
    unittest.main()
