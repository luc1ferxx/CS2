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

    def test_replay_events_include_kills_and_best_effort_parser_events(self) -> None:
        replay = normalize_parser_output(
            demo_id="demo-events",
            parsed={
                "mapName": "de_dust2",
                "tickRate": 64,
                "rounds": [
                    {
                        "roundNumber": 1,
                        "startTick": 100,
                        "freezeEndTick": 164,
                        "endTick": 500,
                        "winnerSide": "T",
                        "winnerReason": "bomb_exploded",
                    }
                ],
                "players": [
                    {"id": "t-1", "name": "T One", "side": "T"},
                    {"id": "ct-1", "name": "CT One", "side": "CT"},
                ],
                "frames": [
                    {
                        "tick": 100,
                        "roundNumber": 1,
                        "players": [
                            {
                                "id": "t-1",
                                "name": "T One",
                                "side": "T",
                                "x": -1200,
                                "y": 400,
                                "alive": True,
                                "hp": 100,
                            },
                            {
                                "id": "ct-1",
                                "name": "CT One",
                                "side": "CT",
                                "x": -1120,
                                "y": 420,
                                "alive": True,
                                "hp": 100,
                            },
                        ],
                        "bombState": {"status": "planted"},
                    }
                ],
                "kills": [
                    {
                        "tick": 180,
                        "roundNumber": 1,
                        "attackerId": "t-1",
                        "attackerName": "T One",
                        "attackerSide": "T",
                        "victimId": "ct-1",
                        "victimName": "CT One",
                        "victimSide": "CT",
                        "weapon": "ak47",
                    }
                ],
                "deaths": [],
                "events": [
                    {
                        "type": "bomb_planted",
                        "tick": 220,
                        "roundNumber": 1,
                        "playerId": "t-1",
                        "playerName": "T One",
                        "side": "T",
                        "site": "A",
                    },
                    {
                        "type": "smoke",
                        "tick": 260,
                        "roundNumber": 1,
                        "playerId": "t-1",
                        "playerName": "T One",
                        "side": "T",
                        "x": -1000,
                        "y": 500,
                    },
                    {
                        "type": "flash",
                        "tick": 300,
                        "roundNumber": 1,
                    },
                ],
            },
        )

        self.assertEqual(replay["rounds"][0]["winnerReason"], "bomb_exploded")
        events = replay["events"]
        self.assertEqual([event["type"] for event in events], ["kill", "bomb_planted", "smoke", "flash"])
        for event in events:
            self.assertEqual(event["source"], "parser")
            self.assertIsInstance(event["playerIds"], list)
        kill = events[0]
        self.assertEqual(kill["tick"], 180)
        self.assertEqual(kill["playerId"], "t-1")
        self.assertEqual(kill["playerIds"], ["t-1", "ct-1"])
        self.assertEqual(kill["side"], "T")
        self.assertEqual(kill["metadata"]["victimId"], "ct-1")
        self.assertEqual(kill["metadata"]["weapon"], "ak47")

        plant = events[1]
        self.assertEqual(plant["label"], "Bomb planted A")
        self.assertEqual(plant["metadata"]["site"], "A")
        self.assertNotIn("x", plant)

        smoke = events[2]
        self.assertEqual(smoke["label"], "Smoke")
        self.assertGreaterEqual(smoke["x"], 0)
        self.assertLessEqual(smoke["x"], 100)
        self.assertGreaterEqual(smoke["y"], 0)
        self.assertLessEqual(smoke["y"], 100)

        flash = events[3]
        self.assertEqual(flash["label"], "Flash")
        self.assertNotIn("x", flash)

    def test_parser_event_v1_accepts_damage_and_round_markers(self) -> None:
        replay = normalize_parser_output(
            demo_id="demo-event-v1",
            parsed={
                "mapName": "de_dust2",
                "tickRate": 64,
                "rounds": [
                    {
                        "roundNumber": 1,
                        "startTick": 100,
                        "freezeEndTick": 164,
                        "endTick": 500,
                        "winnerSide": "CT",
                    }
                ],
                "players": [
                    {"id": "t-1", "name": "T One", "side": "T"},
                    {"id": "ct-1", "name": "CT One", "side": "CT"},
                ],
                "frames": [
                    {
                        "tick": 100,
                        "roundNumber": 1,
                        "players": [
                            {
                                "id": "t-1",
                                "name": "T One",
                                "side": "T",
                                "x": -1200,
                                "y": 400,
                                "alive": True,
                                "hp": 100,
                            },
                            {
                                "id": "ct-1",
                                "name": "CT One",
                                "side": "CT",
                                "x": -1120,
                                "y": 420,
                                "alive": True,
                                "hp": 100,
                            },
                        ],
                    }
                ],
                "events": [
                    {"type": "round_start", "tick": 100, "roundNumber": 1},
                    {
                        "type": "damage",
                        "tick": 180,
                        "roundNumber": 1,
                        "playerIds": ["t-1", "ct-1"],
                        "playerId": "t-1",
                        "playerName": "T One",
                        "side": "T",
                        "metadata": {"victimId": "ct-1", "damageHealth": 42},
                    },
                    {"type": "round_end", "tick": 500, "roundNumber": 1, "metadata": {"winnerSide": "CT"}},
                ],
            },
        )

        events = replay["events"]
        self.assertEqual([event["type"] for event in events], ["round_start", "damage", "round_end"])
        self.assertEqual(events[0]["source"], "parser")
        self.assertEqual(events[0]["playerIds"], [])
        self.assertEqual(events[1]["playerIds"], ["t-1", "ct-1"])
        self.assertEqual(events[1]["label"], "Damage")
        self.assertEqual(events[1]["metadata"]["damageHealth"], 42)
        self.assertEqual(events[2]["label"], "Round ended")


if __name__ == "__main__":
    unittest.main()
