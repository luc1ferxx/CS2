import math
import unittest

from app.parser.demo_parser import _build_bomb_events, _build_frames, _build_rounds, _build_utility_events


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


if __name__ == "__main__":
    unittest.main()
