import unittest

from app.parser.normalizer import normalize_parser_output


class NormalizerTest(unittest.TestCase):
    def test_converts_parser_output_to_replay_contract(self) -> None:
        replay = normalize_parser_output(
            demo_id="demo-1",
            parsed={
                "mapName": "de_ancient",
                "tickRate": 64,
                "rounds": [
                    {
                        "roundNumber": 1,
                        "startTick": 100,
                        "freezeEndTick": 164,
                        "endTick": 300,
                        "winnerSide": "CT",
                    }
                ],
                "players": [
                    {"id": "765", "name": "Player One", "side": "T"},
                    {"id": "766", "name": "Player Two", "side": "CT"},
                ],
                "frames": [
                    {
                        "tick": 100,
                        "roundNumber": 1,
                        "players": [
                            {
                                "id": "765",
                                "name": "Player One",
                                "side": "T",
                                "x": -1000,
                                "y": 500,
                                "alive": True,
                                "hp": 100,
                            },
                            {
                                "id": "766",
                                "name": "Player Two",
                                "side": "CT",
                                "x": 1000,
                                "y": -500,
                                "alive": False,
                                "hp": 0,
                            },
                        ],
                    }
                ],
                "kills": [{"tick": 120, "attackerName": "Player One", "victimName": "Player Two"}],
                "deaths": [{"tick": 120, "victimName": "Player Two"}],
            },
        )

        self.assertEqual(replay["demoId"], "demo-1")
        self.assertEqual(replay["mapName"], "de_ancient")
        self.assertEqual(replay["tickRate"], 64)
        self.assertEqual(replay["video"]["status"], "ready")
        self.assertIsNone(replay["video"]["url"])
        self.assertEqual(replay["video"]["source"], "mock")
        self.assertEqual(len(replay["rounds"]), 1)
        self.assertEqual(len(replay["players"]), 2)
        self.assertEqual(len(replay["frames"]), 1)
        self.assertEqual(len(replay["kills"]), 1)
        self.assertEqual(len(replay["deaths"]), 1)
        self.assertEqual(replay["frames"][0]["bombState"]["status"], "carried")
        for player in replay["frames"][0]["players"]:
            self.assertGreaterEqual(player["x"], 0)
            self.assertLessEqual(player["x"], 100)
            self.assertGreaterEqual(player["y"], 0)
            self.assertLessEqual(player["y"], 100)


if __name__ == "__main__":
    unittest.main()
