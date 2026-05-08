import unittest

from app.analysis.analyzer import analyze_replay


class RulesAnalyzerTest(unittest.TestCase):
    def test_untraded_death_generates_event(self) -> None:
        replay = replay_fixture(
            kills=[
                {
                    "tick": 100,
                    "roundNumber": 1,
                    "attackerId": "ct-1",
                    "attackerName": "CT One",
                    "victimId": "t-entry",
                    "victimName": "T Entry",
                }
            ],
            frames=[
                frame(
                    100,
                    [
                        player("t-entry", "T Entry", "T", 20, 20),
                        player("t-trade", "T Trade", "T", 55, 55),
                        player("ct-1", "CT One", "CT", 21, 21),
                    ],
                )
            ],
        )

        events = analyze_replay(replay)

        untraded = [event for event in events if event["structured_context_json"]["rule"] == "untraded_death"]
        self.assertEqual(len(untraded), 1)
        self.assertEqual(untraded[0]["demo_id"], "demo-rules")
        self.assertEqual(untraded[0]["round_number"], 1)
        self.assertEqual(untraded[0]["player_id"], "t-entry")
        self.assertEqual(untraded[0]["tick_start"], 100)
        self.assertEqual(untraded[0]["category"], "trading")

    def test_timely_trade_suppresses_untraded_death(self) -> None:
        replay = replay_fixture(
            kills=[
                {
                    "tick": 100,
                    "roundNumber": 1,
                    "attackerId": "ct-1",
                    "attackerName": "CT One",
                    "victimId": "t-entry",
                    "victimName": "T Entry",
                },
                {
                    "tick": 180,
                    "roundNumber": 1,
                    "attackerId": "t-trade",
                    "attackerName": "T Trade",
                    "victimId": "ct-1",
                    "victimName": "CT One",
                },
            ],
            frames=[
                frame(
                    100,
                    [
                        player("t-entry", "T Entry", "T", 20, 20),
                        player("t-trade", "T Trade", "T", 25, 20),
                        player("ct-1", "CT One", "CT", 21, 21),
                    ],
                ),
                frame(
                    180,
                    [
                        player("t-trade", "T Trade", "T", 25, 20),
                        player("ct-1", "CT One", "CT", 21, 21),
                    ],
                ),
            ],
        )

        events = analyze_replay(replay)

        self.assertFalse(
            any(
                event["structured_context_json"]["rule"] == "untraded_death"
                and event["player_id"] == "t-entry"
                for event in events
            )
        )

    def test_empty_replay_or_missing_kill_data_does_not_crash(self) -> None:
        self.assertEqual(analyze_replay({"demoId": "empty"}), [])
        self.assertEqual(
            analyze_replay(
                replay_fixture(
                    kills=[],
                    frames=[frame(100, [player("t-entry", "T Entry", "T", 20, 20)])],
                )
            ),
            [],
        )

    def test_output_fields_match_coaching_event_insert_shape(self) -> None:
        replay = replay_fixture(
            kills=[
                {
                    "tick": 100,
                    "roundNumber": 1,
                    "attackerId": "ct-1",
                    "attackerName": "CT One",
                    "victimId": "t-entry",
                    "victimName": "T Entry",
                }
            ],
            frames=[
                frame(
                    100,
                    [
                        player("t-entry", "T Entry", "T", 20, 20),
                        player("t-trade", "T Trade", "T", 55, 55),
                        player("ct-1", "CT One", "CT", 21, 21),
                    ],
                )
            ],
        )

        event = analyze_replay(replay)[0]

        self.assertEqual(
            set(event),
            {
                "id",
                "demo_id",
                "round_number",
                "player_id",
                "player_name",
                "tick_start",
                "tick_end",
                "category",
                "severity",
                "title",
                "message",
                "structured_context_json",
                "confidence",
            },
        )
        self.assertIsInstance(event["structured_context_json"], dict)
        self.assertGreaterEqual(event["confidence"], 0)
        self.assertLessEqual(event["confidence"], 1)


def replay_fixture(
    *,
    kills: list[dict[str, object]],
    frames: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "demoId": "demo-rules",
        "mapName": "de_dust2",
        "tickRate": 64,
        "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 640}],
        "players": [
            {"id": "t-entry", "name": "T Entry", "side": "T", "color": "#f5b542"},
            {"id": "t-trade", "name": "T Trade", "side": "T", "color": "#f5b542"},
            {"id": "ct-1", "name": "CT One", "side": "CT", "color": "#2ed3d0"},
        ],
        "frames": frames,
        "kills": kills,
        "deaths": kills,
    }


def frame(tick: int, players: list[dict[str, object]]) -> dict[str, object]:
    return {
        "tick": tick,
        "timeSeconds": tick / 64,
        "roundNumber": 1,
        "players": players,
        "bombState": {"status": "carried"},
    }


def player(
    player_id: str,
    name: str,
    side: str,
    x: float,
    y: float,
) -> dict[str, object]:
    return {
        "id": player_id,
        "name": name,
        "side": side,
        "x": x,
        "y": y,
        "alive": True,
        "hp": 100,
        "hasBomb": False,
    }


if __name__ == "__main__":
    unittest.main()
