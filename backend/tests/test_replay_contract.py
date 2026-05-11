import unittest

from app.parser.replay_contract import normalize_replay_contract


class ReplayContractTest(unittest.TestCase):
    def test_old_replay_without_events_loads_with_empty_events(self) -> None:
        replay = normalize_replay_contract(
            {
                "demoId": "legacy-demo",
                "tickRate": 64,
                "rounds": [{"roundNumber": 1, "startTick": 100, "endTick": 500}],
                "players": [],
                "frames": [],
            }
        )

        self.assertEqual(replay["events"], [])
        self.assertEqual(replay["video"]["tickStart"], 100)
        self.assertEqual(replay["video"]["tickEnd"], 500)
        self.assertEqual(replay["diagnostics"]["contractVersion"], "legacy")
        self.assertTrue(replay["diagnostics"]["normalizedLegacy"])
        self.assertEqual(replay["diagnostics"]["parserEventCount"], 0)
        self.assertEqual(replay["diagnostics"]["roundCount"], 1)
        self.assertEqual(replay["diagnostics"]["playerCount"], 0)
        self.assertIn("events", replay["diagnostics"]["missingFields"])
        self.assertIn("combat", replay["diagnostics"]["missingEventFamilies"])

    def test_malformed_optional_event_fields_are_compacted(self) -> None:
        replay = normalize_replay_contract(
            {
                "contractVersion": "replay_contract_v1",
                "demoId": "legacy-events",
                "tickRate": 64,
                "rounds": [{"roundNumber": 1, "startTick": 100, "endTick": 500}],
                "players": [],
                "frames": [],
                "events": [
                    {
                        "type": "bomb_planted",
                        "tick": 220,
                        "playerId": "t-1",
                        "metadata": {
                            "site": "A",
                            "notes": "x" * 500,
                            "rawFrame": {"too": "large"},
                        },
                    },
                    {"type": "smoke", "tick": "bad"},
                    {"tick": 240},
                ],
            }
        )

        self.assertEqual(len(replay["events"]), 1)
        event = replay["events"][0]
        self.assertEqual(event["id"], "bomb_planted-220-t-1")
        self.assertEqual(event["roundNumber"], 1)
        self.assertEqual(event["source"], "parser")
        self.assertEqual(event["playerIds"], ["t-1"])
        self.assertEqual(event["label"], "Bomb planted A")
        self.assertEqual(event["metadata"]["site"], "A")
        self.assertLessEqual(len(event["metadata"]["notes"]), 200)
        self.assertNotIn("rawFrame", event["metadata"])
        self.assertEqual(replay["diagnostics"]["contractVersion"], "replay_contract_v1")
        self.assertEqual(replay["diagnostics"]["parserEventCount"], 1)
        self.assertEqual(replay["diagnostics"]["eventFamilyCounts"]["objective"], 1)

    def test_malformed_optional_replay_fields_report_compact_diagnostics(self) -> None:
        replay = normalize_replay_contract(
            {
                "demoId": "malformed-detail",
                "tickRate": "bad",
                "rounds": "not-a-list",
                "players": {"bad": "shape"},
                "frames": "not-a-list",
                "events": {"bad": "shape"},
                "video": "not-a-dict",
            }
        )

        self.assertEqual(replay["tickRate"], 64)
        self.assertEqual(replay["rounds"], [])
        self.assertEqual(replay["players"], [])
        self.assertEqual(replay["frames"], [])
        self.assertEqual(replay["events"], [])
        self.assertEqual(replay["diagnostics"]["parserEventCount"], 0)
        self.assertEqual(replay["diagnostics"]["roundCount"], 0)
        self.assertEqual(replay["diagnostics"]["playerCount"], 0)
        self.assertTrue(replay["diagnostics"]["normalizedLegacy"])
        self.assertEqual(
            replay["diagnostics"]["degradedFields"],
            ["rounds", "players", "frames", "events", "video"],
        )


if __name__ == "__main__":
    unittest.main()
