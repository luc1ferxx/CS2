"""The background replay upgrade (app/workers/replay_upgrade.py).

A completed demo whose replay predates the current contract is re-parsed from
its stored .dem and switched over in place: same status, the same coaching
suggestions and verdicts (never re-analysed), same summary, no ledger entry,
and nothing resurrected when the match is deleted meanwhile. Foreign keys are enforced, as
in test_data_deletion.
"""

import copy
import dataclasses
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.analysis.analyzer import analyze_replay
from app.core.config import settings
from app.core.database import Base
from app.models import Demo, DemoJob
from app.models.coaching import CoachingEvent, CoachingFeedback
from app.models.deletion import UploadLedger
from app.parser.demo_parser import DemoParserError
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import REPLAY_CONTRACT_VERSION
from app.services.deletion_service import DeletionService
from app.services.demo_service import DemoService
from app.services.demo_service.replay_upgrade import STATE_KEY, VERSION_KEY
from app.services.storage import LocalArtifactStore, LocalStorageService
from app.workers import replay_upgrade
from app.workers.replay_upgrade import upgrade_stale_replays
from app.workers.worker import process_mock_parse_job, process_real_parse_job

OLD_VERSION = "replay_contract_v1"


def upgrade_settings(**changes: Any) -> ExitStack:
    """Settings is frozen; swap a copy into both modules that read the knobs."""
    stack = ExitStack()
    replaced = dataclasses.replace(settings, **changes)
    for target in ("app.services.demo_service.replay_upgrade.settings", "app.workers.replay_upgrade.settings"):
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
    """Parser output (world units) for a small Mirage round with an untraded, isolated death."""
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


class CoachingIdsAcrossContractsTest(unittest.TestCase):
    def test_reparsing_the_same_demo_under_the_new_contract_keeps_every_event_id(self) -> None:
        old_parse = parsed_match()
        new_parse = parsed_match()
        new_parse["playerStates"] = {"t-entry": [{"tick": 96, "money": 800, "weapon": "Glock-18"}]}
        new_parse["utility"] = []
        old_events = analyze_replay(normalize_parser_output("demo-ids", old_parse))
        new_events = analyze_replay(normalize_parser_output("demo-ids", new_parse))
        # One card per death: the isolated entry rides on the untraded-death card.
        self.assertGreaterEqual(len(old_events), 1)
        for events in (old_events, new_events):
            self.assertIn(
                "isolated_entry",
                [reason.get("ruleId") for reason in events[0]["structured_context_json"].get("extraReasons", [])],
            )
        self.assertEqual([item["id"] for item in new_events], [item["id"] for item in old_events])


class ReplayUpgradeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = fk_engine(root / "upgrade.db")
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        self.legacy = LocalStorageService(root / "legacy")

        def internal(db: Any) -> DemoService:
            return DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)

        self.patches = [
            patch("app.workers.worker.DemoService.for_internal", side_effect=internal),
            patch("app.workers.replay_upgrade.DemoService.for_internal", side_effect=internal),
            patch("app.services.demo_service.get_redis_client"),
        ]
        for item in self.patches:
            item.start()
        replay_upgrade.reset_upgrade_state()
        self.parse_calls: list[bytes] = []

    def tearDown(self) -> None:
        replay_upgrade.reset_upgrade_state()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.scratch.cleanup()

    # -- helpers ----------------------------------------------------------------

    def owner_service(self, db: Any) -> DemoService:
        return DemoService(db, owner_id=settings.dev_user_id, storage=self.legacy, artifact_store=self.store)

    def upload_and_parse(self) -> str:
        with self.Session() as db:
            created = self.owner_service(db).create_real_demo(SimpleNamespace(
                filename="spirit-vs-mouz.dem",
                content_type="application/octet-stream",
                file=io.BytesIO(b"HL2DEMO\x00" + b"upgrade-payload" * 4),
            ))
            demo = db.get(Demo, created.id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == created.id).one()
            with patch("app.workers.worker.run_parse_subprocess", return_value=parsed_match()):
                process_real_parse_job(db, demo, job)
            return created.id

    def make_old(self, demo_id: str, *, video: dict[str, Any] | None = None) -> str:
        """Turn a freshly parsed demo into one parsed before the current contract."""
        with self.Session() as db:
            service = DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)
            demo = db.get(Demo, demo_id)
            replay = copy.deepcopy(service.load_replay_blob(demo))
            assert replay is not None
            replay["contractVersion"] = OLD_VERSION
            replay.pop("playerStates", None)
            replay.pop("utility", None)
            if video is not None:
                replay["video"] = {**replay["video"], **video}
            previous = demo.replay_storage_key
            demo.replay_storage_key = service.write_replay_blob(demo_id, replay)
            job = service.latest_parse_job(demo)
            assert job is not None
            metadata = json.loads(job.metadata_json)
            metadata.pop(VERSION_KEY, None)
            job.metadata_json = json.dumps(metadata, separators=(",", ":"))
            db.commit()
            service.delete_artifact_safely(previous)
            return str(demo.replay_storage_key)

    def run_pass(self, run_parse: Any = None, **kwargs: Any) -> str | None:
        def default_parse(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            self.parse_calls.append(source_path.read_bytes()[:8])
            return parsed_match()

        return upgrade_stale_replays(
            run_parse=run_parse or default_parse,
            session_factory=self.Session,
            force=True,
            check_interval_seconds=0,
            **kwargs,
        )

    def snapshot(self, demo_id: str) -> dict[str, Any]:
        with self.Session() as db:
            service = DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)
            demo = db.get(Demo, demo_id)
            job = service.latest_parse_job(demo)
            replay = service.load_replay_blob(demo)
            return {
                "status": demo.status,
                "archived": demo.archived,
                "name": demo.name,
                "completed_at": demo.completed_at,
                "updated_at": demo.updated_at,
                "replay_key": demo.replay_storage_key,
                "round_count": demo.round_count,
                "coaching_event_count": demo.coaching_event_count,
                "match_summary": demo.match_summary,
                "version": replay.get("contractVersion") if replay else None,
                "replay": replay,
                "event_ids": sorted(
                    row.id for row in db.query(CoachingEvent.id).filter(CoachingEvent.demo_id == demo_id)
                ),
                "job_status": job.status if job else None,
                "job_attempts": job.attempts if job else None,
                "metadata": json.loads(job.metadata_json) if job else {},
                "pending": service.demo_status(demo).ingestion.replayUpgradePending,
                "ledger": db.query(UploadLedger).count(),
            }

    def artifact_count(self) -> int:
        root = Path(self.scratch.name) / "artifacts"
        return sum(1 for path in root.rglob("*") if path.is_file() and not path.name.endswith(".json.meta"))

    def rewind_retry(self, demo_id: str) -> None:
        with self.Session() as db:
            job = DemoService.for_internal(db).latest_parse_job(db.get(Demo, demo_id))
            assert job is not None
            metadata = json.loads(job.metadata_json)
            metadata[STATE_KEY]["nextAttemptAt"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            job.metadata_json = json.dumps(metadata, separators=(",", ":"))
            db.commit()

    # -- tests --------------------------------------------------------------------

    def test_a_new_parse_records_the_current_contract_and_owes_nothing(self) -> None:
        demo_id = self.upload_and_parse()
        state = self.snapshot(demo_id)
        self.assertEqual(state["metadata"][VERSION_KEY], REPLAY_CONTRACT_VERSION)
        self.assertFalse(state["pending"])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.parse_calls, [])

    def test_upgrade_swaps_the_replay_and_summary_without_a_status_change(self) -> None:
        demo_id = self.upload_and_parse()
        old_key = self.make_old(demo_id, video={"timeOriginSeconds": 12.5, "source": "manual_upload"})
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            event_id = db.query(CoachingEvent.id).filter(CoachingEvent.demo_id == demo_id).first()[0]
            self.owner_service(db).save_coaching_feedback(demo, event_id, verdict="helpful", note="kept")
            # A suggestion only older rules made, with a verdict: today's analyzer would not emit it.
            older = db.query(CoachingEvent).filter(CoachingEvent.id == event_id).one()
            db.add(CoachingEvent(
                id="older-rule-event", demo_id=demo_id, round_number=older.round_number,
                player_id=older.player_id, player_name=older.player_name, tick_start=older.tick_start,
                tick_end=older.tick_end, category=older.category, severity=older.severity,
                title="旧规则的建议", message="旧规则的建议", structured_context_json={}, confidence=0.5,
            ))
            demo.coaching_event_count = (demo.coaching_event_count or 0) + 1
            db.commit()
            self.owner_service(db).save_coaching_feedback(
                db.get(Demo, demo_id), "older-rule-event", verdict="irrelevant",
            )
        before = self.snapshot(demo_id)
        self.assertEqual(before["version"], OLD_VERSION)
        self.assertTrue(before["pending"])
        self.assertEqual(before["replay_key"], old_key)
        artifacts_before = self.artifact_count()

        self.assertEqual(self.run_pass(), "upgraded")

        after = self.snapshot(demo_id)
        self.assertEqual(self.parse_calls, [b"HL2DEMO\x00"])
        self.assertEqual(after["version"], REPLAY_CONTRACT_VERSION)
        self.assertIn("utility", after["replay"])
        self.assertNotEqual(after["replay_key"], old_key)
        for field in ("status", "archived", "name", "completed_at", "updated_at", "job_status", "job_attempts"):
            self.assertEqual(after[field], before[field], field)
        # The suggestions are left alone, including the one today's rules would not make.
        self.assertEqual(after["event_ids"], before["event_ids"])
        self.assertIn("older-rule-event", after["event_ids"])
        self.assertEqual(after["coaching_event_count"], before["coaching_event_count"])
        self.assertEqual(after["round_count"], 1)
        self.assertEqual(after["match_summary"], before["match_summary"])
        self.assertEqual(after["replay"]["video"]["timeOriginSeconds"], 12.5)
        self.assertEqual(after["replay"]["video"]["source"], "manual_upload")
        self.assertEqual(after["metadata"][VERSION_KEY], REPLAY_CONTRACT_VERSION)
        self.assertNotIn(STATE_KEY, after["metadata"])
        self.assertFalse(after["pending"])
        self.assertEqual(after["ledger"], before["ledger"])
        # The old replay went through the storage package; one replay remains.
        self.assertEqual(self.artifact_count(), artifacts_before)
        with self.Session() as db:
            coaching = self.owner_service(db).list_coaching_events(demo_id)
            verdicts = {item.id: item.feedback.verdict for item in coaching if item.feedback is not None}
            self.assertEqual(verdicts, {event_id: "helpful", "older-rule-event": "irrelevant"})

        # Idempotent: nothing is owed any more.
        self.assertIsNone(self.run_pass())
        self.assertEqual(len(self.parse_calls), 1)

    def test_a_replay_already_current_only_records_the_marker(self) -> None:
        demo_id = self.upload_and_parse()
        with self.Session() as db:
            job = DemoService.for_internal(db).latest_parse_job(db.get(Demo, demo_id))
            assert job is not None
            metadata = json.loads(job.metadata_json)
            metadata.pop(VERSION_KEY)
            job.metadata_json = json.dumps(metadata, separators=(",", ":"))
            db.commit()
        before = self.snapshot(demo_id)
        self.assertTrue(before["pending"])
        self.assertEqual(self.run_pass(), "current")
        after = self.snapshot(demo_id)
        self.assertEqual(self.parse_calls, [])
        self.assertEqual(after["replay_key"], before["replay_key"])
        self.assertEqual(after["metadata"][VERSION_KEY], REPLAY_CONTRACT_VERSION)
        self.assertFalse(after["pending"])

    def test_mock_demos_are_never_upgraded(self) -> None:
        with self.Session() as db:
            created = self.owner_service(db).create_mock_demo()
            demo = db.get(Demo, created.id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo.id).one()
            with patch("app.workers.worker.time.sleep"):
                process_mock_parse_job(db, demo, job)
        self.assertFalse(self.snapshot(created.id)["pending"])
        self.assertIsNone(self.run_pass())
        self.assertEqual(self.parse_calls, [])

    def test_deleted_during_the_parse_stops_it_and_resurrects_nothing(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        ticks: list[str] = []

        def parse_then_delete(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            with self.Session() as db:
                self.assertTrue(DeletionService(db, artifact_store=self.store, legacy_storage=self.legacy)
                                .delete_demo(settings.dev_user_id, demo_id))
            try:
                on_tick()
            except BaseException as exc:
                ticks.append(type(exc).__name__)
                raise
            return parsed_match()

        self.assertEqual(self.run_pass(parse_then_delete), "gone")
        self.assertEqual(ticks, ["_DemoDeleted"])
        with self.Session() as db:
            self.assertIsNone(db.get(Demo, demo_id))
            self.assertEqual(db.query(CoachingEvent).filter(CoachingEvent.demo_id == demo_id).count(), 0)
            self.assertEqual(db.query(DemoJob).filter(DemoJob.demo_id == demo_id).count(), 0)
        self.assertEqual(self.artifact_count(), 0)

    def test_deleted_after_the_parse_writes_nothing(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)

        def parse_then_delete(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            with self.Session() as db:
                DeletionService(db, artifact_store=self.store, legacy_storage=self.legacy).delete_demo(
                    settings.dev_user_id, demo_id,
                )
            return parsed_match()

        self.assertEqual(self.run_pass(parse_then_delete), "gone")
        with self.Session() as db:
            self.assertIsNone(db.get(Demo, demo_id))
            self.assertEqual(db.query(CoachingEvent).count(), 0)
            self.assertEqual(db.query(CoachingFeedback).count(), 0)
        self.assertEqual(self.artifact_count(), 0)

    def test_archived_during_the_parse_is_upgraded_and_stays_archived(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)

        def parse_then_archive(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            with self.Session() as db:
                self.owner_service(db).archive_demo(demo_id)
            return parsed_match()

        self.assertEqual(self.run_pass(parse_then_archive), "upgraded")
        after = self.snapshot(demo_id)
        self.assertTrue(after["archived"])
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["version"], REPLAY_CONTRACT_VERSION)

    def test_a_replay_rewritten_meanwhile_wins_and_the_attempt_is_given_back(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        rewritten: list[str] = []

        def parse_while_video_updates(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            with self.Session() as db:
                service = DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)
                service.update_replay_video(db.get(Demo, demo_id), {"status": "ready", "source": "manual_upload"})
                rewritten.append(str(db.get(Demo, demo_id).replay_storage_key))
            return parsed_match()

        artifacts = self.artifact_count()
        self.assertEqual(self.run_pass(parse_while_video_updates), "changed")
        after = self.snapshot(demo_id)
        self.assertEqual(after["replay_key"], rewritten[0])
        self.assertEqual(after["version"], OLD_VERSION)
        self.assertEqual(after["metadata"].get(STATE_KEY), None)
        self.assertTrue(after["pending"])
        self.assertEqual(self.artifact_count(), artifacts)
        # Retried on the next pass, on top of the rewritten replay.
        self.assertEqual(self.run_pass(), "upgraded")
        self.assertEqual(self.snapshot(demo_id)["replay"]["video"]["source"], "manual_upload")

    def test_failures_back_off_then_leave_the_old_replay_for_good(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        before = self.snapshot(demo_id)

        def crashing_parse(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            self.parse_calls.append(b"crash")
            raise DemoParserError("boom at C:/secret/path", error_code="PARSER_CRASHED", user_message="x")

        with upgrade_settings(replay_upgrade_max_attempts=2):
            self.assertEqual(self.run_pass(crashing_parse), "failed:PARSER_CRASHED")
            state = self.snapshot(demo_id)["metadata"][STATE_KEY]
            self.assertEqual(state["attempts"], 1)
            self.assertEqual(state["lastErrorCode"], "PARSER_CRASHED")
            self.assertNotIn("secret", json.dumps(state))
            self.assertGreater(datetime.fromisoformat(state["nextAttemptAt"]), datetime.now(UTC))
            self.assertTrue(self.snapshot(demo_id)["pending"])
            # Not due yet: no second parse.
            self.assertIsNone(self.run_pass(crashing_parse))
            self.assertEqual(len(self.parse_calls), 1)

            self.rewind_retry(demo_id)
            self.assertEqual(self.run_pass(crashing_parse), "failed:PARSER_CRASHED")
            after = self.snapshot(demo_id)
            self.assertTrue(after["metadata"][STATE_KEY]["exhausted"])
            self.assertFalse(after["pending"])
            self.rewind_retry(demo_id)
            self.assertIsNone(self.run_pass(crashing_parse))
            self.assertEqual(len(self.parse_calls), 2)
        for field in ("status", "replay_key", "version", "event_ids", "completed_at", "updated_at", "job_attempts"):
            self.assertEqual(after[field], before[field], field)

    def test_a_worker_dying_mid_upgrade_still_spends_the_attempt(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        with self.Session() as db:
            claim = DemoService.for_internal(db).claim_replay_upgrade(demo_id)
        self.assertIsNotNone(claim)
        state = self.snapshot(demo_id)["metadata"][STATE_KEY]
        self.assertEqual(state["attempts"], 1)
        held_until = datetime.fromisoformat(state["nextAttemptAt"])
        self.assertGreaterEqual(
            held_until, datetime.now(UTC) + timedelta(seconds=settings.parse_timeout_seconds),
        )
        # Another worker (or this one after a restart) leaves it alone until then.
        self.assertIsNone(self.run_pass())
        with self.Session() as db:
            self.assertIsNone(DemoService.for_internal(db).claim_replay_upgrade(demo_id))

    def test_attempts_a_dead_worker_never_finished_still_end_in_giving_up(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        with upgrade_settings(replay_upgrade_max_attempts=2):
            for expected_attempt in (1, 2):
                with self.Session() as db:
                    claim = DemoService.for_internal(db).claim_replay_upgrade(demo_id)
                self.assertIsNotNone(claim)
                assert claim is not None
                self.assertEqual(claim.attempt, expected_attempt)
                # The worker dies here: no failure is ever recorded.
                self.rewind_retry(demo_id)
            self.assertTrue(self.snapshot(demo_id)["pending"])
            self.assertIsNone(self.run_pass())
            after = self.snapshot(demo_id)
        self.assertEqual(self.parse_calls, [])
        self.assertTrue(after["metadata"][STATE_KEY]["exhausted"])
        self.assertEqual(after["metadata"][STATE_KEY]["attempts"], 2)
        self.assertEqual(after["metadata"][STATE_KEY]["lastErrorCode"], "UPGRADE_ABANDONED")
        self.assertFalse(after["pending"])
        self.assertEqual(after["version"], OLD_VERSION)
        self.assertEqual(after["status"], "completed")
        with self.Session() as db:
            self.assertEqual(DemoService.for_internal(db).demo_ids_due_for_replay_upgrade(limit=5), [])

    def test_an_unreadable_stored_replay_is_a_recorded_failure(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)

        def broken_load(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("corrupt replay at C:/secret/path")

        with upgrade_settings(replay_upgrade_max_attempts=1), \
                patch("app.services.demo_service.DemoService.load_replay_blob", side_effect=broken_load):
            self.assertEqual(self.run_pass(), "failed:REPLAY_UNREADABLE")
        after = self.snapshot(demo_id)
        state = after["metadata"][STATE_KEY]
        self.assertEqual(state["lastErrorCode"], "REPLAY_UNREADABLE")
        self.assertTrue(state["exhausted"])
        self.assertNotIn("secret", json.dumps(state))
        self.assertFalse(after["pending"])
        self.assertEqual(self.parse_calls, [])

    def test_a_video_write_from_before_the_swap_lands_on_the_new_replay(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        with self.Session() as stale_db:
            # A dev-tools video write that loaded the match before the upgrade committed.
            stale_demo = stale_db.get(Demo, demo_id)
            old_key = stale_demo.replay_storage_key
            self.assertEqual(self.run_pass(), "upgraded")
            self.assertEqual(stale_demo.replay_storage_key, old_key)
            service = DemoService(stale_db, storage=self.legacy, artifact_store=self.store, internal=True)
            service.update_replay_video(stale_demo, {"status": "ready", "source": "manual_upload"})
        after = self.snapshot(demo_id)
        self.assertEqual(after["version"], REPLAY_CONTRACT_VERSION)
        self.assertIn("utility", after["replay"])
        self.assertEqual(after["replay"]["video"]["source"], "manual_upload")
        self.assertFalse(after["pending"])

    def test_upgraded_demos_with_older_unmarked_parse_jobs_are_not_candidates(self) -> None:
        demo_id = self.upload_and_parse()
        with self.Session() as db:
            marked = DemoService.for_internal(db).latest_parse_job(db.get(Demo, demo_id))
            assert marked is not None
            db.add(DemoJob(
                id="older-unmarked-job", demo_id=demo_id, job_type="real_parse", status="failed",
                attempts=1, metadata_json="{}",
                created_at=(marked.created_at or datetime.now(UTC)) - timedelta(hours=1),
            ))
            db.commit()
            self.assertEqual(DemoService.for_internal(db).demo_ids_due_for_replay_upgrade(limit=5), [])
        self.make_old(demo_id)
        with self.Session() as db:
            self.assertEqual(DemoService.for_internal(db).demo_ids_due_for_replay_upgrade(limit=5), [demo_id])

    def test_a_v2_replay_is_owed_the_v3_upgrade_and_gets_its_inputs(self) -> None:
        demo_id = self.upload_and_parse()
        # A demo parsed under contract v2: the job carries the v2 marker and the replay has no inputs.
        with self.Session() as db:
            service = DemoService(db, storage=self.legacy, artifact_store=self.store, internal=True)
            demo = db.get(Demo, demo_id)
            replay = copy.deepcopy(service.load_replay_blob(demo))
            assert replay is not None
            replay["contractVersion"] = "replay_contract_v2"
            replay.pop("inputs", None)
            previous = demo.replay_storage_key
            demo.replay_storage_key = service.write_replay_blob(demo_id, replay)
            job = service.latest_parse_job(demo)
            assert job is not None
            metadata = json.loads(job.metadata_json)
            metadata[VERSION_KEY] = "replay_contract_v2"
            job.metadata_json = json.dumps(metadata, separators=(",", ":"))
            db.commit()
            service.delete_artifact_safely(previous)
        self.assertTrue(self.snapshot(demo_id)["pending"])
        with self.Session() as db:
            self.assertEqual(DemoService.for_internal(db).demo_ids_due_for_replay_upgrade(limit=5), [demo_id])

        inputs = {"t-entry": [[0, 0], [50, 1032], [100, 0]]}

        def parse_with_inputs(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            return {**parsed_match(), "inputs": inputs}

        self.assertEqual(self.run_pass(parse_with_inputs), "upgraded")
        after = self.snapshot(demo_id)
        self.assertEqual(after["version"], REPLAY_CONTRACT_VERSION)
        self.assertEqual(after["metadata"][VERSION_KEY], REPLAY_CONTRACT_VERSION)
        self.assertEqual(after["replay"]["inputs"], inputs)
        self.assertEqual(after["replay"]["diagnostics"]["inputSource"], "usercmd")
        self.assertFalse(after["pending"])
        self.assertIsNone(self.run_pass())

    def test_waiting_work_stops_the_upgrade_and_gives_the_attempt_back(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        before = self.snapshot(demo_id)
        waiting = [False]

        def parse_until_an_upload_arrives(source_path: Path, on_tick: Any = None) -> dict[str, Any]:
            waiting[0] = True
            on_tick()
            return parsed_match()

        self.assertEqual(
            self.run_pass(parse_until_an_upload_arrives, should_yield=lambda: waiting[0]),
            "yielded",
        )
        after = self.snapshot(demo_id)
        self.assertEqual(after["metadata"], before["metadata"])
        self.assertEqual(after["replay_key"], before["replay_key"])
        # With work already waiting the pass does not even start.
        self.assertIsNone(self.run_pass(should_yield=lambda: True))
        self.assertEqual(self.run_pass(), "upgraded")

    def test_disabled_upgrade_does_nothing_and_reports_nothing_pending(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        with upgrade_settings(replay_upgrade_enabled=False):
            self.assertFalse(self.snapshot(demo_id)["pending"])
            self.assertIsNone(self.run_pass())
        self.assertEqual(self.parse_calls, [])

    def test_one_demo_per_pass_newest_first(self) -> None:
        first = self.upload_and_parse()
        second = self.upload_and_parse()
        self.make_old(first)
        self.make_old(second)
        self.assertEqual(self.run_pass(), "upgraded")
        self.assertEqual(self.snapshot(first)["version"], OLD_VERSION)
        self.assertEqual(self.snapshot(second)["version"], REPLAY_CONTRACT_VERSION)
        self.assertEqual(self.run_pass(), "upgraded")
        self.assertEqual(self.snapshot(first)["version"], REPLAY_CONTRACT_VERSION)

    def test_passes_are_rate_limited(self) -> None:
        demo_id = self.upload_and_parse()
        self.make_old(demo_id)
        replay_upgrade.reset_upgrade_state()
        self.assertEqual(self.run_pass(), "upgraded")
        self.make_old(demo_id)
        self.assertIsNone(upgrade_stale_replays(run_parse=lambda *_a, **_k: parsed_match(), session_factory=self.Session))


if __name__ == "__main__":
    unittest.main()
