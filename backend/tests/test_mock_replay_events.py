import unittest

from app.services.mock_replay_service import build_mock_replay


class MockReplayEventsTest(unittest.TestCase):
    def test_mock_replay_has_compatible_parser_events(self) -> None:
        replay, _ = build_mock_replay("demo-mock-events")

        self.assertIn("events", replay)
        self.assertGreaterEqual(len(replay["events"]), 3)
        event_types = {event["type"] for event in replay["events"]}
        self.assertIn("kill", event_types)
        self.assertIn("bomb_planted", event_types)
        self.assertIn("smoke", event_types)

        for event in replay["events"]:
            self.assertIsInstance(event["id"], str)
            self.assertIsInstance(event["tick"], int)
            self.assertIsInstance(event["roundNumber"], int)
            self.assertIsInstance(event["label"], str)


if __name__ == "__main__":
    unittest.main()
