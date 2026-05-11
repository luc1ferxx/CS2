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

    def test_malformed_optional_event_fields_are_compacted(self) -> None:
        replay = normalize_replay_contract(
            {
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


if __name__ == "__main__":
    unittest.main()
