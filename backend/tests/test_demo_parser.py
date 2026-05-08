import math
import unittest

from app.parser.demo_parser import _build_frames


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


if __name__ == "__main__":
    unittest.main()
