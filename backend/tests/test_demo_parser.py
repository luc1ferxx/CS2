import math
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.parser.demo_parser import (
    _align_round_numbers,
    _build_bomb_events,
    _build_frames,
    _build_kills,
    _build_round_events,
    _build_rounds,
    _build_utility_events,
    parse_demo_file,
)


class DemoParserRoundAssignmentTest(unittest.TestCase):
    def test_restarted_initial_round_keeps_match_numbers_and_boundaries(self) -> None:
        starts = [
            {"tick": 0, "round": 1, "total_rounds_played": 0},
            {"tick": 326, "round": 1, "total_rounds_played": 0},
            {"tick": 14889, "round": 2, "total_rounds_played": 1},
        ]
        ends = [
            {"tick": 0, "round": 0, "total_rounds_played": 0, "winner": None},
            {"tick": 14441, "round": 1, "total_rounds_played": 1, "winner": "CT"},
            {"tick": 20778, "round": 2, "total_rounds_played": 2, "winner": "T"},
        ]
        rounds = _build_rounds(
            starts,
            [{"tick": 7832, "total_rounds_played": 0}, {"tick": 16169, "total_rounds_played": 1}],
            ends, {}, 64,
        )

        self.assertEqual(
            [(item["roundNumber"], item["startTick"], item["freezeEndTick"], item["endTick"], item["winnerSide"]) for item in rounds],
            [(1, 326, 7832, 14441, "CT"), (2, 14889, 16169, 20778, "T")],
        )
        markers = _build_round_events(starts, ends, rounds)
        self.assertEqual(
            [(item["type"], item["tick"], item["roundNumber"]) for item in markers],
            [("round_start", 326, 1), ("round_end", 14441, 1), ("round_start", 14889, 2), ("round_end", 20778, 2)],
        )
        kills = _build_kills([{"tick": 11702, "total_rounds_played": 0}, {"tick": 19000, "total_rounds_played": 1}])
        self.assertEqual([item["roundNumber"] for item in _align_round_numbers(kills, rounds)], [1, 2])

    def test_missing_end_does_not_borrow_next_rounds_winner(self) -> None:
        rounds = _build_rounds(
            [{"tick": 100}, {"tick": 300}],
            [{"tick": 150}, {"tick": 350}],
            [{"tick": 500, "winner": "T"}], {}, 64,
        )
        self.assertEqual([item["roundNumber"] for item in rounds], [1, 2])
        self.assertEqual([item["endTick"] for item in rounds], [299, 500])
        self.assertEqual([item["freezeEndTick"] for item in rounds], [150, 350])
        self.assertIsNone(rounds[0]["winnerSide"])
        self.assertEqual(rounds[1]["winnerSide"], "T")

    def test_round_end_counter_is_completed_count_and_explicit_round_wins(self) -> None:
        markers = _build_round_events([], [
            {"tick": 500, "total_rounds_played": 2},
            {"tick": 700, "round": 3, "total_rounds_played": 4},
        ])
        self.assertEqual([item["roundNumber"] for item in markers], [2, 3])
        kills = _build_kills([{"tick": 600, "round": 3, "total_rounds_played": 4}])
        self.assertEqual(kills[0]["roundNumber"], 3)

    def test_missing_starts_use_real_end_numbers_and_ignore_initial_sentinel(self) -> None:
        rounds = _build_rounds([], [], [
            {"tick": 0, "round": 0},
            {"tick": 500, "round": 1, "winner": "CT"},
            {"tick": 900, "round": 2, "winner": "T"},
        ], {}, 64)
        self.assertEqual([(item["roundNumber"], item["startTick"], item["endTick"]) for item in rounds], [(1, 0, 500), (2, 501, 900)])

    def test_tick_window_aligns_missing_metadata_without_reassigning_post_round_events(self) -> None:
        rounds = [{"roundNumber": 2, "startTick": 300, "endTick": 500}]
        events = _align_round_numbers([
            {"tick": 400, "roundNumber": 1},
            {"tick": 550, "roundNumber": 3},
        ], rounds)
        self.assertEqual([item["roundNumber"] for item in events], [2, 3])


class DemoParserFrameBuildTest(unittest.TestCase):
    def test_nan_health_defaults_to_alive_player_health(self) -> None:
        frames = _build_frames(
            [
                {
                    "tick": 64,
                    "steamid": 765,
                    "name": "Player",
                    "team_num": 2,
                    "X": 100.0,
                    "Y": 200.0,
                    "health": math.nan,
                }
            ],
            [{"roundNumber": 1, "startTick": 0, "endTick": 128}],
            64,
        )

        self.assertEqual(frames[0]["players"][0]["hp"], 100)
        self.assertTrue(frames[0]["players"][0]["alive"])

    def test_rounds_use_freeze_end_and_winner_reason_when_available(self) -> None:
        rounds = _build_rounds(
            starts=[{"tick": 100}],
            freeze_ends=[{"tick": 180, "total_rounds_played": 0}],
            ends=[{"tick": 700, "winner": 2, "reason": "bomb_exploded"}],
            header={},
            tick_rate=64,
        )

        self.assertEqual(
            rounds,
            [
                {
                    "roundNumber": 1,
                    "startTick": 100,
                    "freezeEndTick": 180,
                    "endTick": 700,
                    "winnerSide": "T",
                    "winnerReason": "bomb_exploded",
                }
            ],
        )

    def test_bomb_and_utility_events_are_best_effort(self) -> None:
        bomb_events = _build_bomb_events(
            planted=[
                {
                    "tick": 300,
                    "total_rounds_played": 0,
                    "user_steamid": "t-1",
                    "user_name": "T One",
                    "team_num": 2,
                    "site": "A",
                }
            ],
            defused=[],
            exploded=[{"tick": 620, "total_rounds_played": 0}],
        )
        utility_events = _build_utility_events(
            {
                "smoke": [
                    {
                        "tick": 240,
                        "total_rounds_played": 0,
                        "user_steamid": "t-1",
                        "user_name": "T One",
                        "team_num": 2,
                        "X": 12.5,
                        "Y": 22.5,
                    }
                ],
                "flash": [{"tick": 260, "total_rounds_played": 0}],
            }
        )

        self.assertEqual([event["type"] for event in bomb_events], ["bomb_planted", "bomb_exploded"])
        self.assertEqual(bomb_events[0]["playerId"], "t-1")
        self.assertEqual(bomb_events[0]["side"], "T")
        self.assertEqual(bomb_events[0]["metadata"]["site"], "A")
        self.assertEqual(bomb_events[1]["label"], "Bomb exploded")
        self.assertEqual([event["type"] for event in utility_events], ["smoke", "flash"])
        self.assertEqual(utility_events[0]["x"], 12.5)
        self.assertEqual(utility_events[0]["y"], 22.5)
        self.assertNotIn("x", utility_events[1])

    def test_parse_demo_continues_when_one_event_family_fails(self) -> None:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_dust2", "tick_rate": 64, "playback_ticks": 640}

            def parse_player_info(self) -> list[dict[str, object]]:
                return [
                    {"steamid": "t-1", "name": "T One", "team_num": 2},
                    {"steamid": "ct-1", "name": "CT One", "team_num": 3},
                ]

            def parse_event(self, event_name: str, **_: object) -> list[dict[str, object]]:
                if event_name == "player_hurt":
                    raise RuntimeError("event family unavailable")
                records_by_name = {
                    "round_start": [{"tick": 0, "total_rounds_played": 0}],
                    "round_freeze_end": [{"tick": 64, "total_rounds_played": 0}],
                    "round_end": [{"tick": 640, "total_rounds_played": 0, "winner": 2}],
                    "bomb_planted": [
                        {
                            "tick": 500,
                            "total_rounds_played": 0,
                            "user_steamid": "t-1",
                            "user_name": "T One",
                            "team_num": 2,
                            "site": "A",
                        }
                    ],
                }
                return records_by_name.get(event_name, [])

            def parse_ticks(self, _: list[str], *, ticks: list[int]) -> list[dict[str, object]]:
                records: list[dict[str, object]] = []
                for tick in ticks:
                    records.extend(
                        [
                            {
                                "tick": tick,
                                "steamid": "t-1",
                                "name": "T One",
                                "team_num": 2,
                                "X": 100.0,
                                "Y": 200.0,
                                "health": 100,
                            },
                            {
                                "tick": tick,
                                "steamid": "ct-1",
                                "name": "CT One",
                                "team_num": 3,
                                "X": 300.0,
                                "Y": 400.0,
                                "health": 100,
                            },
                        ]
                    )
                return records

        fake_module = types.SimpleNamespace(DemoParser=FakeDemoParser)
        with patch.dict("sys.modules", {"demoparser2": fake_module}), patch(
            "app.parser.demo_parser._validate_demo_file",
            return_value=None,
        ):
            parsed = parse_demo_file(Path("match.dem"))

        event_types = [event["type"] for event in parsed["events"]]
        self.assertIn("round_start", event_types)
        self.assertIn("round_end", event_types)
        self.assertIn("bomb_planted", event_types)
        self.assertNotIn("damage", event_types)


if __name__ == "__main__":
    unittest.main()
