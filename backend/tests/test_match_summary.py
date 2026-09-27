import io
import tempfile
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import demos as demos_api
from app.core.config import settings
from app.core.database import Base, get_db
from app.migrations.runner import run_schema_migrations
from app.models import Demo, DemoJob
from app.parser.demo_parser import parse_demo_file, parse_team_names, team_name_sample_ticks
from app.parser.normalizer import normalize_parser_output
from app.services.demo_service import DemoService
from app.services.demo_service.match_summary import build_match_summary, public_match_summary
from app.services.storage import LocalArtifactStore
from app.workers import match_summary_backfill
from app.workers.match_summary_backfill import backfill_match_summaries
from app.workers.worker import process_mock_parse_job, process_real_parse_job

A_PLAYERS = ("a1", "a2")
B_PLAYERS = ("b1", "b2")


def replay_fixture(
    winners: list[str | None],
    a_side_by_round: dict[int, str | None],
    *,
    kills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Two-a-side match; `a_side_by_round[n]` None means round n has no frames."""
    rounds = []
    frames = []
    for index, winner in enumerate(winners):
        number = index + 1
        rounds.append({
            "roundNumber": number,
            "startTick": number * 1000,
            "freezeEndTick": number * 1000 + 100,
            "endTick": number * 1000 + 900,
            "winnerSide": winner,
        })
        a_side = a_side_by_round.get(number)
        if a_side is None:
            continue
        b_side = "CT" if a_side == "T" else "T"
        for offset in (200, 400):
            frames.append({
                "tick": number * 1000 + offset,
                "roundNumber": number,
                "players": [
                    *({"id": pid, "side": a_side, "x": 1, "y": 1} for pid in A_PLAYERS),
                    *({"id": pid, "side": b_side, "x": 2, "y": 2} for pid in B_PLAYERS),
                ],
            })
    return {"rounds": rounds, "frames": frames, "kills": kills or [], "events": []}


def regulation_sides(total_rounds: int) -> dict[int, str | None]:
    return {number: ("T" if number <= 12 else "CT") for number in range(1, total_rounds + 1)}


class ParserTeamNamesTest(unittest.TestCase):
    def test_clan_names_are_keyed_by_the_frame_player_id(self) -> None:
        calls: list[tuple[list[str], list[int]]] = []

        class FakeParser:
            def parse_ticks(self, props: list[str], *, ticks: list[int]) -> list[dict[str, Any]]:
                calls.append((props, ticks))
                return [
                    {"tick": 900, "steamid": "7656-a", "name": "a", "team_clan_name": "Late Name"},
                    {"tick": 100, "steamid": "7656-a", "name": "a", "team_clan_name": "  Team   Spirit "},
                    {"tick": 100, "steamid": "7656-b", "name": "b", "team_clan_name": "MOUZ"},
                    {"tick": 100, "steamid": "7656-c", "name": "c", "team_clan_name": ""},
                    {"tick": 100, "steamid": None, "name": "BOT Kurt", "team_clan_name": "MOUZ"},
                    {"tick": 100, "steamid": "7656-d", "name": "d", "team_clan_name": None},
                ]

        names = parse_team_names(FakeParser(), [100, 900])

        self.assertEqual(calls, [(["team_clan_name"], [100, 900])])
        self.assertEqual(
            names,
            {"7656-a": "Team Spirit", "7656-b": "MOUZ", "BOT Kurt": "MOUZ"},
        )

    def test_missing_prop_or_no_ticks_yields_no_names(self) -> None:
        class BrokenParser:
            def parse_ticks(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
                raise KeyError("team_clan_name")

        self.assertEqual(parse_team_names(BrokenParser(), [1]), {})
        self.assertEqual(parse_team_names(object(), []), {})

    def test_sample_ticks_take_first_middle_and_last_round(self) -> None:
        rounds = [
            {"roundNumber": 1, "startTick": 0, "freezeEndTick": 64},
            {"roundNumber": 2, "startTick": 1000},
            {"roundNumber": 3, "startTick": 2000, "freezeEndTick": 2064},
        ]
        self.assertEqual(team_name_sample_ticks(rounds), [64, 1000, 2064])
        self.assertEqual(team_name_sample_ticks(rounds[:1]), [64])
        self.assertEqual(team_name_sample_ticks(None), [])
        self.assertEqual(team_name_sample_ticks(["junk", {"startTick": "x"}]), [])

    def test_parse_output_carries_team_names_but_the_replay_does_not(self) -> None:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_mirage", "tick_rate": 64, "playback_ticks": 640}

            def parse_player_info(self) -> list[dict[str, object]]:
                return [
                    {"steamid": "t-1", "name": "T One", "team_num": 2},
                    {"steamid": "ct-1", "name": "CT One", "team_num": 3},
                ]

            def parse_event(self, event_name: str, **_: object) -> list[dict[str, object]]:
                return {
                    "round_start": [{"tick": 0, "total_rounds_played": 0}],
                    "round_freeze_end": [{"tick": 64, "total_rounds_played": 0}],
                    "round_end": [{"tick": 640, "total_rounds_played": 1, "winner": 2}],
                }.get(event_name, [])

            def parse_ticks(self, props: list[str], *, ticks: list[int]) -> list[dict[str, object]]:
                if props == ["team_clan_name"]:
                    return [
                        {"tick": ticks[0], "steamid": "t-1", "name": "T One", "team_clan_name": "MOUZ"},
                        {"tick": ticks[0], "steamid": "ct-1", "name": "CT One", "team_clan_name": "Spirit"},
                    ]
                return [
                    record
                    for tick in ticks
                    for record in (
                        {"tick": tick, "steamid": "t-1", "name": "T One", "team_num": 2,
                         "X": 100.0, "Y": 200.0, "health": 100},
                        {"tick": tick, "steamid": "ct-1", "name": "CT One", "team_num": 3,
                         "X": 300.0, "Y": 400.0, "health": 100},
                    )
                ]

        fake_module = types.SimpleNamespace(DemoParser=FakeDemoParser)
        with patch.dict("sys.modules", {"demoparser2": fake_module}), patch(
            "app.parser.demo_parser._validate_demo_file",
            return_value=None,
        ):
            parsed = parse_demo_file(Path("match.dem"))

        self.assertEqual(parsed["teamNames"], {"t-1": "MOUZ", "ct-1": "Spirit"})
        replay = normalize_parser_output("demo-names", parsed)
        self.assertNotIn("teamNames", replay)
        summary = build_match_summary(replay, parsed["teamNames"])
        assert summary is not None
        self.assertEqual(
            [(team["key"], team["name"], team["startSide"], team["score"]) for team in summary["teams"]],
            [("A", "MOUZ", "T", 1), ("B", "Spirit", "CT", 0)],
        )


class BuildMatchSummaryTest(unittest.TestCase):
    def test_side_swap_at_half_time_follows_the_frames(self) -> None:
        # Team A (started T) wins rounds 1-4 on T, then 13-21 on CT: 13 to 11.
        winners = ["T"] * 4 + ["CT"] * 8 + ["CT"] * 9 + ["T"] * 3
        summary = build_match_summary(replay_fixture(winners, regulation_sides(24)))

        self.assertEqual(summary, {
            "teams": [
                {"key": "A", "name": None, "startSide": "T", "score": 13},
                {"key": "B", "name": None, "startSide": "CT", "score": 11},
            ],
            "rounds": 24,
            "version": 1,
        })

    def test_overtime_sides_come_from_frames_not_round_numbers(self) -> None:
        winners = ["T"] * 6 + ["CT"] * 6 + ["CT"] * 6 + ["T"] * 6  # 12:12
        sides = regulation_sides(24)
        # Overtime: A on T for 25-27, CT for 28-30; A wins 25, 26, 28, 29.
        winners += ["T", "T", "CT", "CT", "CT", "T"]
        sides.update({25: "T", 26: "T", 27: "T", 28: "CT", 29: "CT", 30: "CT"})

        summary = build_match_summary(replay_fixture(winners, sides))
        assert summary is not None

        self.assertEqual([team["score"] for team in summary["teams"]], [16, 14])
        self.assertEqual(summary["rounds"], 30)

    def test_a_round_without_frames_falls_back_to_kills_then_the_nearest_placed_round(self) -> None:
        sides = regulation_sides(14)
        sides[13] = None
        sides[14] = None
        kills = [{
            "roundNumber": 13, "attackerId": "b1", "attackerSide": "T",
            "victimId": "a1", "victimSide": "CT",
        }]
        winners = ["T"] * 12 + ["CT", "CT"]

        summary = build_match_summary(replay_fixture(winners, sides, kills=kills))
        assert summary is not None

        # Round 13: kills put A on CT, so A wins it; round 14 keeps round 13's sides
        # (the same guess the review page makes), so A wins it too.
        self.assertEqual([team["score"] for team in summary["teams"]], [14, 0])
        self.assertEqual(summary["rounds"], 14)

    def test_frames_a_round_keeps_through_the_half_time_break_do_not_vote(self) -> None:
        winners = ["T"] * 12 + ["CT"]
        replay = replay_fixture(winners, regulation_sides(13))
        # Round 12 keeps frames long after its endTick, already on the swapped sides,
        # and more of them than it has inside the round.
        for offset in range(1000, 1600, 20):
            replay["frames"].append({
                "tick": 12_000 + offset,
                "roundNumber": 12,
                "players": [
                    *({"id": pid, "side": "CT", "x": 1, "y": 1} for pid in A_PLAYERS),
                    *({"id": pid, "side": "T", "x": 2, "y": 2} for pid in B_PLAYERS),
                ],
            })

        summary = build_match_summary(replay)
        assert summary is not None

        # A won all twelve rounds on T and round 13 on CT.
        self.assertEqual([team["score"] for team in summary["teams"]], [13, 0])

    def test_missing_winner_or_reason_is_not_a_round_win(self) -> None:
        summary = build_match_summary(replay_fixture(["T", None, "CT"], regulation_sides(3)))
        assert summary is not None
        self.assertEqual([team["score"] for team in summary["teams"]], [1, 1])
        self.assertEqual(summary["rounds"], 3)

    def test_team_name_is_the_most_common_clan_name_among_members(self) -> None:
        names = {"a1": "MOUZ", "a2": "MOUZ", "b1": "Spirit", "b2": "  ", "x9": "Other"}
        summary = build_match_summary(replay_fixture(["T"], {1: "T"}), names)
        assert summary is not None
        self.assertEqual([team["name"] for team in summary["teams"]], ["MOUZ", "Spirit"])

        unnamed = build_match_summary(replay_fixture(["T"], {1: "T"}), ["not", "a", "mapping"])  # type: ignore[arg-type]
        assert unnamed is not None
        self.assertEqual([team["name"] for team in unnamed["teams"]], [None, None])

    def test_frameless_or_malformed_replays_have_no_summary_and_never_raise(self) -> None:
        self.assertIsNone(build_match_summary(replay_fixture(["T"], {})))
        self.assertIsNone(build_match_summary({}))
        self.assertIsNone(build_match_summary({
            "rounds": "junk",
            "frames": [None, 3, {"roundNumber": "x", "players": "y"}, {"roundNumber": 1, "players": [None, {"id": "p", "side": "spectator"}]}],
            "kills": {"not": "a list"},
            "events": [{"type": "kill", "metadata": None}],
        }))

    def test_public_summary_accepts_only_the_version_one_shape(self) -> None:
        stored = build_match_summary(replay_fixture(["T"], {1: "T"}), {"a1": "MOUZ"})
        public = public_match_summary(stored)
        assert public is not None
        self.assertEqual(public.teams[0].name, "MOUZ")
        self.assertIsNone(public_match_summary(None))
        self.assertIsNone(public_match_summary({**(stored or {}), "version": 2}))
        self.assertIsNone(public_match_summary({"teams": [], "rounds": 1, "version": 1}))
        self.assertIsNone(public_match_summary("13:11"))


class MatchSummaryMigrationTest(unittest.TestCase):
    def test_existing_demos_table_gets_a_nullable_summary_column(self) -> None:
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE demos (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255))"))
                connection.execute(text("INSERT INTO demos (id, name) VALUES ('d1', 'kept')"))
            with engine.begin() as connection:
                run_schema_migrations(connection)
            with engine.begin() as connection:
                run_schema_migrations(connection)

            columns = {column["name"] for column in inspect(engine).get_columns("demos")}
            self.assertIn("match_summary", columns)
            with engine.connect() as connection:
                row = connection.execute(text("SELECT name, match_summary FROM demos")).one()
            self.assertEqual(tuple(row), ("kept", None))
        finally:
            engine.dispose()

    def test_fresh_database_gets_the_column_from_the_model(self) -> None:
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                run_schema_migrations(connection)
            Base.metadata.create_all(bind=engine)
            columns = {column["name"] for column in inspect(engine).get_columns("demos")}
            self.assertIn("match_summary", columns)
        finally:
            engine.dispose()


def parsed_match(team_names: dict[str, str] | None = None) -> dict[str, Any]:
    frame_players = [
        {"id": "a1", "name": "a1", "side": "T", "x": 100, "y": 200, "z": 0, "hp": 100, "alive": True},
        {"id": "b1", "name": "b1", "side": "CT", "x": 300, "y": 400, "z": 0, "hp": 100, "alive": True},
    ]
    parsed: dict[str, Any] = {
        "mapName": "de_mirage", "tickRate": 64,
        "rounds": [
            {"roundNumber": 1, "startTick": 0, "freezeEndTick": 10, "endTick": 128, "winnerSide": "T"},
            {"roundNumber": 2, "startTick": 200, "freezeEndTick": 210, "endTick": 328, "winnerSide": "T"},
        ],
        "players": [{"id": "a1", "name": "a1", "side": "T"}, {"id": "b1", "name": "b1", "side": "CT"}],
        "frames": [
            {"tick": 20, "roundNumber": 1, "players": frame_players},
            {"tick": 220, "roundNumber": 2, "players": frame_players},
        ],
        "events": [], "kills": [], "deaths": [],
    }
    if team_names is not None:
        parsed["teamNames"] = team_names
    return parsed


class MatchSummaryStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = create_engine(
            f"sqlite:///{(root / 'summary.db').as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        self.patches = [
            patch(
                "app.workers.worker.DemoService.for_internal",
                side_effect=lambda db: DemoService(db, artifact_store=self.store, internal=True),
            ),
            patch(
                "app.workers.match_summary_backfill.DemoService.for_internal",
                side_effect=lambda db: DemoService(db, artifact_store=self.store, internal=True),
            ),
            patch("app.services.demo_service.get_redis_client"),
        ]
        for item in self.patches:
            item.start()
        match_summary_backfill.reset_backfill_state()

    def tearDown(self) -> None:
        match_summary_backfill.reset_backfill_state()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.scratch.cleanup()

    def upload_and_parse(self, parsed: dict[str, Any], owner_id: str = settings.dev_user_id) -> str:
        with self.Session() as db:
            service = DemoService(db, owner_id=owner_id, artifact_store=self.store)
            created = service.create_real_demo(SimpleNamespace(
                filename="spirit-vs-mouz.dem",
                content_type="application/octet-stream",
                file=io.BytesIO(b"HL2DEMO\x00" + b"summary-payload" * 4),
            ))
            demo = db.get(Demo, created.id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == created.id).one()
            with patch("app.workers.worker.run_parse_subprocess", return_value=parsed):
                process_real_parse_job(db, demo, job)
            return created.id

    def artifact_files(self) -> dict[str, bytes]:
        root = Path(self.scratch.name) / "artifacts"
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def clear_summary(self, demo_id: str) -> datetime:
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            demo.match_summary = None
            db.commit()
            db.refresh(demo)
            return demo.updated_at

    def test_parse_completion_stores_the_summary_with_team_names(self) -> None:
        demo_id = self.upload_and_parse(parsed_match({"a1": "MOUZ", "b1": "Spirit"}))
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            self.assertEqual(demo.status, "completed")
            self.assertEqual(demo.match_summary["teams"][0], {
                "key": "A", "name": "MOUZ", "startSide": "T", "score": 2,
            })
            self.assertEqual(demo.match_summary["teams"][1]["name"], "Spirit")

    def test_mock_parse_stores_a_summary_without_names(self) -> None:
        with self.Session() as db:
            created = DemoService(db, owner_id="mock-owner", artifact_store=self.store).create_mock_demo()
            demo = db.get(Demo, created.id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo.id).one()
            with patch("app.workers.worker.time.sleep"):
                process_mock_parse_job(db, demo, job)
            db.refresh(demo)
            summary = public_match_summary(demo.match_summary)
            assert summary is not None
            self.assertEqual([team.name for team in summary.teams], [None, None])
            self.assertEqual(summary.rounds, demo.round_count)

    def test_backfill_reads_names_from_the_source_and_is_idempotent(self) -> None:
        demo_id = self.upload_and_parse(parsed_match())
        updated_at = self.clear_summary(demo_id)
        with self.Session() as db:
            replay_key = db.get(Demo, demo_id).replay_storage_key
        artifacts_before = self.artifact_files()
        reads: list[tuple[bytes, list[int]]] = []

        def read_names(source_path: Path, ticks: list[int]) -> dict[str, str]:
            reads.append((source_path.read_bytes()[:8], ticks))
            return {"a1": "MOUZ", "b1": "Spirit"}

        stored = backfill_match_summaries(
            force=True, session_factory=self.Session, read_team_names=read_names,
        )
        self.assertEqual(stored, [demo_id])
        self.assertEqual(reads, [(b"HL2DEMO\x00", [10, 210])])
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            self.assertEqual([team["name"] for team in demo.match_summary["teams"]], ["MOUZ", "Spirit"])
            self.assertEqual([team["score"] for team in demo.match_summary["teams"]], [2, 0])
            self.assertEqual(demo.updated_at, updated_at)
            self.assertEqual(demo.replay_storage_key, replay_key)
        # Read-only on storage: the replay blob and the source are untouched.
        self.assertEqual(self.artifact_files(), artifacts_before)

        again = backfill_match_summaries(
            force=True, session_factory=self.Session, read_team_names=read_names,
        )
        self.assertEqual(again, [])
        self.assertEqual(len(reads), 1)

    def test_backfill_without_readable_names_still_stores_the_score(self) -> None:
        demo_id = self.upload_and_parse(parsed_match())
        self.clear_summary(demo_id)

        def failing_reader(_path: Path, _ticks: list[int]) -> dict[str, str]:
            raise RuntimeError("names child timed out")

        stored = backfill_match_summaries(
            force=True, session_factory=self.Session, read_team_names=failing_reader,
        )
        self.assertEqual(stored, [demo_id])
        with self.Session() as db:
            summary = db.get(Demo, demo_id).match_summary
            self.assertEqual([team["name"] for team in summary["teams"]], [None, None])
            self.assertEqual([team["score"] for team in summary["teams"]], [2, 0])

    def test_backfill_skips_a_demo_without_replay_and_remembers_it(self) -> None:
        missing_id = self.upload_and_parse(parsed_match())
        self.clear_summary(missing_id)
        with self.Session() as db:
            db.get(Demo, missing_id).replay_storage_key = "local://replays/nowhere.json"
            db.commit()
        stored = backfill_match_summaries(
            force=True, session_factory=self.Session, read_team_names=lambda *_: {},
        )
        self.assertEqual(stored, [])
        with self.Session() as db:
            self.assertIsNone(db.get(Demo, missing_id).match_summary)
            service = DemoService.for_internal(db)
            self.assertEqual(
                service.demo_ids_missing_match_summary(
                    limit=5, exclude=match_summary_backfill._unavailable_demo_ids,
                ),
                [],
            )

    def test_backfill_is_rate_limited_between_passes(self) -> None:
        demo_id = self.upload_and_parse(parsed_match())
        self.clear_summary(demo_id)
        backfill_match_summaries(force=True, session_factory=self.Session, read_team_names=lambda *_: {})
        self.clear_summary(demo_id)
        self.assertEqual(
            backfill_match_summaries(session_factory=self.Session, read_team_names=lambda *_: {}),
            [],
        )


class MatchSummaryApiTest(unittest.TestCase):
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
        self.app.include_router(demos_api.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(self.app)
        summary = build_match_summary(
            replay_fixture(["T"] * 4 + ["CT"] * 17 + ["T"] * 3, regulation_sides(24)),
            {"a1": "MOUZ", "b1": "Spirit"},
        )
        with self.Session() as db:
            add_demo(db, "with-summary", match_summary=summary)
            add_demo(db, "without-summary")
            add_demo(db, "junk-summary", match_summary={"teams": "13:11", "version": 1})

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

    def test_list_and_status_expose_the_optional_match_summary(self) -> None:
        headers = {"X-Dev-User-Id": settings.dev_user_id}
        listed = self.client.get("/demos", headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        by_id = {item["id"]: item for item in listed.json()}
        self.assertEqual(by_id["with-summary"]["matchSummary"], {
            "teams": [
                {"key": "A", "name": "MOUZ", "startSide": "T", "score": 13},
                {"key": "B", "name": "Spirit", "startSide": "CT", "score": 11},
            ],
            "rounds": 24,
            "version": 1,
        })
        self.assertIsNone(by_id["without-summary"]["matchSummary"])
        self.assertIsNone(by_id["junk-summary"]["matchSummary"])

        status = self.client.get("/demos/with-summary/status", headers=headers)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["matchSummary"]["teams"][0]["score"], 13)
        empty = self.client.get("/demos/without-summary/status", headers=headers)
        self.assertIsNone(empty.json()["matchSummary"])


def add_demo(db, demo_id: str, *, match_summary: dict[str, Any] | None = None) -> Demo:
    timestamp = datetime(2026, 9, 27, tzinfo=UTC)
    demo = Demo(
        id=demo_id,
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        map_name="de_mirage",
        tick_rate=64,
        round_count=24,
        coaching_event_count=0,
        status="completed",
        archived=False,
        replay_storage_key=f"local://replays/{demo_id}.json",
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
        match_summary=match_summary,
    )
    db.add(demo)
    db.commit()
    return demo


if __name__ == "__main__":
    unittest.main()
