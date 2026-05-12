import math
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.parser.demo_parser import (
    _build_bomb_events,
    _build_frames,
    _build_rounds,
    _build_utility_events,
    parse_demo_file,
)


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
