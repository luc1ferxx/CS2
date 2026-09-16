import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import Account, Demo, DemoJob, SteamConnection, SteamMatch
from app.services.demo_service import DemoService
from app.services.storage import LocalArtifactStore
from app.workers.worker import process_real_parse_job

OWNER_A = "owner_v1_parse_status_a"
OWNER_B = "owner_v1_parse_status_b"


class SteamParseStatusTest(unittest.TestCase):
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
        self.scratch = tempfile.TemporaryDirectory()
        self.store = LocalArtifactStore(Path(self.scratch.name))

        now = datetime.now(UTC)
        with self.Session() as db:
            for owner_id, suffix in ((OWNER_A, "a"), (OWNER_B, "b")):
                db.add(Account(owner_id=owner_id, created_at=now, updated_at=now))
                db.add(
                    SteamConnection(
                        id=f"connection-{suffix}",
                        owner_id=owner_id,
                        steam_id64=f"7656120225523302{1 if suffix == 'a' else 2}",
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
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.scratch.cleanup()

    def test_claim_marks_only_owner_and_demo_bound_match_as_parsing(self) -> None:
        with self.Session() as db:
            demo, job, match = self._add_lifecycle(db)
            match.map_name = "fabricated"
            match.duration_seconds = 999
            match.ct_round_wins = 9
            match.t_round_wins = 9
            match.players_json = '[{"name":"fabricated"}]'
            db.commit()

            DemoService.for_internal(db, artifact_store=self.store).claim_parse_job(
                demo,
                job,
            )

            db.refresh(match)
            self.assertEqual(match.status, "parsing")
            self.assertEqual(match.parser_dispatched_job_id, job.id)
            self.assertIsNotNone(match.parser_dispatched_at)
            self.assertIsNone(match.map_name)
            self.assertIsNone(match.duration_seconds)
            self.assertIsNone(match.ct_round_wins)
            self.assertIsNone(match.t_round_wins)
            self.assertIsNone(match.players_json)

    def test_owner_mismatch_cannot_change_match_status(self) -> None:
        with self.Session() as db:
            demo, job, _match = self._add_lifecycle(
                db,
                match_owner_id=OWNER_B,
            )

            DemoService.for_internal(db, artifact_store=self.store).claim_parse_job(
                demo,
                job,
            )

            match = db.query(SteamMatch).one()
            self.assertEqual(match.owner_id, OWNER_B)
            self.assertEqual(match.demo_id, demo.id)
            self.assertEqual(match.status, "discovered")

    def test_success_backfills_only_bounded_parser_summary_and_marks_ready(self) -> None:
        replay = _real_replay()
        replay["players"] = [
            {
                "id": f"player-{index}",
                "name": ("\x00  Alice\n" if index == 0 else f"Player {index}"),
                "side": "CT" if index % 2 == 0 else "T",
                "color": "not-persisted",
                "private": "not-persisted",
            }
            for index in range(25)
        ]
        replay["players"].insert(
            1,
            {"id": "player-0", "name": "Duplicate", "side": "T"},
        )
        replay["players"].append(
            {"id": "invalid-side", "name": "Skipped", "side": "spectator"}
        )

        with self.Session() as db:
            demo, job, match = self._add_lifecycle(db)
            service = DemoService.for_internal(db, artifact_store=self.store)
            service.claim_parse_job(demo, job)

            db.refresh(match)
            self.assertIsNone(match.map_name)
            self.assertIsNone(match.players_json)

            service.complete_parse_job(demo, job, replay, [])

            db.refresh(match)
            self.assertEqual(match.status, "ready")
            self.assertEqual(match.map_name, "de_mirage")
            self.assertEqual(match.duration_seconds, 96)
            self.assertEqual(match.ct_round_wins, 2)
            self.assertEqual(match.t_round_wins, 1)
            players = json.loads(match.players_json or "null")
            self.assertEqual(len(players), 20)
            self.assertEqual(
                players[0],
                {"id": "player-0", "name": "Alice", "side": "CT"},
            )
            self.assertEqual(len({player["id"] for player in players}), 20)
            self.assertTrue(
                all(set(player) == {"id", "name", "side"} for player in players)
            )
            self.assertIsNone(match.last_import_error_code)
            self.assertIsNone(match.last_import_error_message)
            self.assertIsNotNone(match.import_completed_at)

    def test_parse_failure_marks_unavailable_without_exposing_internal_error(self) -> None:
        with self.Session() as db:
            demo, job, match = self._add_lifecycle(db)
            service = DemoService.for_internal(db, artifact_store=self.store)
            service.claim_parse_job(demo, job)

            service.fail_parse_job(
                demo,
                job,
                "private parser path /srv/secrets/demo.dem\ntraceback contents",
                error_code="UNEXPECTED_PARSER_EXCEPTION",
            )

            db.refresh(match)
            self.assertEqual(match.status, "unavailable")
            self.assertEqual(match.last_import_error_code, "parser_failed")
            self.assertEqual(
                match.last_import_error_message,
                "Imported Demo could not be parsed. Upload a valid .dem file manually or retry later.",
            )
            self.assertNotIn("secret", match.last_import_error_message)
            self.assertIsNone(match.map_name)
            self.assertIsNone(match.duration_seconds)
            self.assertIsNone(match.ct_round_wins)
            self.assertIsNone(match.t_round_wins)
            self.assertIsNone(match.players_json)
            self.assertIsNotNone(match.import_completed_at)

    def test_round_wins_remain_unknown_without_parser_round_end_provenance(self) -> None:
        replay = _real_replay()
        replay["events"] = []
        with self.Session() as db:
            demo, job, match = self._add_lifecycle(db)
            service = DemoService.for_internal(db, artifact_store=self.store)
            service.claim_parse_job(demo, job)

            service.complete_parse_job(demo, job, replay, [])

            db.refresh(match)
            self.assertEqual(match.status, "ready")
            self.assertIsNone(match.ct_round_wins)
            self.assertIsNone(match.t_round_wins)

    def test_mock_parse_does_not_change_linked_steam_match(self) -> None:
        with self.Session() as db:
            demo, job, match = self._add_lifecycle(db, job_type="mock_parse")

            DemoService.for_internal(db, artifact_store=self.store).claim_parse_job(
                demo,
                job,
            )

            db.refresh(match)
            self.assertEqual(match.status, "discovered")

    def test_duplicate_queue_delivery_does_not_run_real_parser_twice(self) -> None:
        with self.Session() as db:
            demo, job, match = self._add_lifecycle(db)
            service = DemoService.for_internal(db, artifact_store=self.store)
            self.assertTrue(service.claim_parse_job(demo, job))

            with patch("app.workers.worker.parse_demo_file") as parse_demo:
                process_real_parse_job(db, demo, job)

            parse_demo.assert_not_called()
            db.refresh(job)
            db.refresh(match)
            self.assertEqual(job.status, "processing")
            self.assertEqual(job.attempts, 1)
            self.assertEqual(match.status, "parsing")

    def test_disconnect_during_parse_cannot_rollback_retained_demo_completion(self) -> None:
        with self.Session() as db:
            demo, job, _match = self._add_lifecycle(db)
            service = DemoService.for_internal(db, artifact_store=self.store)
            self.assertTrue(service.claim_parse_job(demo, job))

            with self.Session() as disconnect_db:
                connection = (
                    disconnect_db.query(SteamConnection)
                    .filter(SteamConnection.id == "connection-a")
                    .one()
                )
                disconnect_db.delete(connection)
                disconnect_db.commit()

            service.complete_parse_job(demo, job, _real_replay(), [])

            db.expire_all()
            retained_demo = db.query(Demo).filter(Demo.id == demo.id).one()
            retained_job = db.query(DemoJob).filter(DemoJob.id == job.id).one()
            self.assertEqual(retained_demo.status, "completed")
            self.assertEqual(retained_job.status, "completed")
            self.assertEqual(db.query(SteamMatch).count(), 0)

    def _add_lifecycle(
        self,
        db,
        *,
        match_owner_id: str = OWNER_A,
        job_type: str = "real_parse",
    ) -> tuple[Demo, DemoJob, SteamMatch]:
        now = datetime.now(UTC)
        demo = Demo(
            id="demo-parse-status",
            owner_id=OWNER_A,
            legacy_user_id=OWNER_A,
            name="Imported match",
            original_filename="match.dem",
            map_name="unknown",
            tick_rate=64,
            round_count=0,
            coaching_event_count=0,
            status="queued",
            archived=False,
            created_at=now,
            updated_at=now,
        )
        job = DemoJob(
            id="job-parse-status",
            demo_id=demo.id,
            job_type=job_type,
            status="queued",
            attempts=0,
            metadata_json="{}",
            created_at=now,
        )
        match = SteamMatch(
            id="match-parse-status",
            connection_id=(
                "connection-a" if match_owner_id == OWNER_A else "connection-b"
            ),
            owner_id=match_owner_id,
            share_code_hash="a" * 64,
            share_code_ciphertext=b"ciphertext-and-tag",
            share_code_nonce=b"3" * 12,
            encryption_key_version="v1",
            status="discovered",
            demo_id=demo.id,
            discovered_at=now,
            updated_at=now,
        )
        db.add_all([demo, job, match])
        db.commit()
        return demo, job, match


def _real_replay() -> dict[str, object]:
    return {
        "contractVersion": "replay_contract_v1",
        "demoId": "demo-parse-status",
        "mapName": "de_mirage",
        "tickRate": 64,
        "video": {
            "durationSeconds": 95.6,
            "tickStart": 100,
            "tickEnd": 6218,
            "tickRate": 64,
            "status": "ready",
            "url": None,
            "source": "mock",
        },
        "rounds": [
            {"roundNumber": 1, "startTick": 100, "endTick": 2100, "winnerSide": "CT"},
            {"roundNumber": 2, "startTick": 2101, "endTick": 4100, "winnerSide": "T"},
            {"roundNumber": 3, "startTick": 4101, "endTick": 6218, "winnerSide": "CT"},
        ],
        "players": [],
        "frames": [],
        "events": [
            {
                "id": "round-end-1",
                "type": "round_end",
                "tick": 2100,
                "roundNumber": 1,
                "source": "parser",
                "metadata": {"winnerSide": "CT"},
            },
            {
                "id": "round-end-2",
                "type": "round_end",
                "tick": 4100,
                "roundNumber": 2,
                "source": "parser",
                "metadata": {"winnerSide": "T"},
            },
            {
                "id": "round-end-3",
                "type": "round_end",
                "tick": 6218,
                "roundNumber": 3,
                "source": "parser",
                "metadata": {"winnerSide": "CT"},
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
