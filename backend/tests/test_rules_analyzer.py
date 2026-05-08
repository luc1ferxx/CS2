import unittest

from app.analysis.analyzer import analyze_replay
from app.analysis.rules import RuleConfig


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
        self.assertEqual(untraded[0]["structured_context_json"]["ruleId"], "untraded_death")
        self.assertEqual(
            untraded[0]["structured_context_json"]["involvedPlayerIds"],
            ["t-entry", "ct-1"],
        )
        self.assertEqual(untraded[0]["structured_context_json"]["windowSeconds"], 5)
        self.assertEqual(untraded[0]["structured_context_json"]["evidenceTicks"], [100])

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
        self.assertEqual(
            analyze_replay(
                replay_fixture(
                    kills=[],
                    frames=[{"tick": 100, "roundNumber": 1, "players": []}],
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

    def test_custom_config_limits_each_rule_per_round(self) -> None:
        replay = replay_fixture(
            kills=[
                kill(100, "ct-1", "CT One", "t-entry", "T Entry"),
                kill(180, "ct-2", "CT Two", "t-entry", "T Entry"),
                kill(260, "ct-1", "CT One", "t-entry", "T Entry"),
            ],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20), player("ct-1", "CT One", "CT", 21, 21)]),
                frame(180, [player("t-entry", "T Entry", "T", 25, 25), player("ct-2", "CT Two", "CT", 26, 26)]),
                frame(260, [player("t-entry", "T Entry", "T", 30, 30), player("ct-1", "CT One", "CT", 31, 31)]),
            ],
            extra_players=[
                {"id": "ct-2", "name": "CT Two", "side": "CT", "color": "#2ed3d0"},
            ],
        )

        events = analyze_replay(
            replay,
            config=RuleConfig(max_events_per_round_per_rule=1, dedupe_tick_window_seconds=0),
        )

        untraded = [event for event in events if event["structured_context_json"]["ruleId"] == "untraded_death"]
        self.assertEqual(len(untraded), 1)

    def test_near_tick_duplicates_are_collapsed(self) -> None:
        replay = replay_fixture(
            kills=[
                kill(100, "ct-1", "CT One", "t-entry", "T Entry"),
                kill(120, "ct-1", "CT One", "t-entry", "T Entry"),
            ],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20), player("ct-1", "CT One", "CT", 21, 21)]),
                frame(120, [player("t-entry", "T Entry", "T", 22, 22), player("ct-1", "CT One", "CT", 23, 23)]),
            ],
        )

        events = analyze_replay(replay, config=RuleConfig(dedupe_tick_window_seconds=1))

        untraded = [event for event in events if event["structured_context_json"]["ruleId"] == "untraded_death"]
        self.assertEqual(len(untraded), 1)

    def test_events_sort_by_severity_then_tick(self) -> None:
        replay = replay_fixture(
            rounds=[
                {"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 640},
                {"roundNumber": 2, "startTick": 700, "freezeEndTick": 700, "endTick": 1300},
            ],
            kills=[
                kill(100, "t-entry", "T Entry", "ct-1", "CT One", round_number=1),
                kill(760, "ct-1", "CT One", "t-entry", "T Entry", round_number=2),
            ],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20), player("ct-1", "CT One", "CT", 21, 21)], 1),
                frame(
                    760,
                    [
                        player("t-entry", "T Entry", "T", 20, 20),
                        player("t-trade", "T Trade", "T", 60, 60),
                        player("ct-1", "CT One", "CT", 21, 21),
                    ],
                    2,
                ),
            ],
        )

        events = analyze_replay(replay)

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["severity"], "high")
        self.assertEqual(events[0]["structured_context_json"]["ruleId"], "isolated_entry")

    def test_post_plant_spread_generates_event(self) -> None:
        replay = replay_fixture(
            kills=[],
            frames=[
                planted_frame(200, cluster_t_players(50, 50), bomb=(52, 52)),
                planted_frame(360, cluster_t_players(50.5, 50.5), bomb=(52, 52)),
                planted_frame(520, cluster_t_players(51, 51), bomb=(52, 52)),
            ],
        )

        events = analyze_replay(
            replay,
            config=RuleConfig(post_plant_min_duration_seconds=4, post_plant_cluster_distance=6),
        )

        post_plant = [event for event in events if event["structured_context_json"]["ruleId"] == "post_plant_spread_issue"]
        self.assertEqual(len(post_plant), 1)
        self.assertIn(post_plant[0]["category"], {"objective", "positioning"})
        self.assertEqual(post_plant[0]["structured_context_json"]["nearbyCount"], 3)
        self.assertEqual(post_plant[0]["structured_context_json"]["evidenceTicks"], [200, 360, 520])

    def test_retake_desync_generates_event(self) -> None:
        replay = replay_fixture(
            kills=[],
            frames=[
                planted_frame(
                    200,
                    [
                        player("ct-1", "CT One", "CT", 54, 52),
                        player("ct-2", "CT Two", "CT", 20, 20),
                        player("ct-3", "CT Three", "CT", 22, 22),
                        player("t-entry", "T Entry", "T", 50, 50),
                    ],
                    bomb=(52, 52),
                ),
                planted_frame(
                    560,
                    [
                        player("ct-1", "CT One", "CT", 54, 52),
                        player("ct-2", "CT Two", "CT", 53, 54),
                        player("ct-3", "CT Three", "CT", 20, 20),
                        player("t-entry", "T Entry", "T", 50, 50),
                    ],
                    bomb=(52, 52),
                ),
            ],
            extra_players=[
                {"id": "ct-2", "name": "CT Two", "side": "CT", "color": "#2ed3d0"},
                {"id": "ct-3", "name": "CT Three", "side": "CT", "color": "#2ed3d0"},
            ],
        )

        events = analyze_replay(
            replay,
            config=RuleConfig(retake_site_distance=5, retake_desync_seconds=4),
        )

        retake = [event for event in events if event["structured_context_json"]["ruleId"] == "retake_desync"]
        self.assertEqual(len(retake), 1)
        self.assertEqual(retake[0]["category"], "timing")
        self.assertEqual(retake[0]["structured_context_json"]["windowSeconds"], 5.625)
        self.assertEqual(retake[0]["structured_context_json"]["involvedPlayerIds"], ["ct-1", "ct-2"])


def replay_fixture(
    *,
    kills: list[dict[str, object]],
    frames: list[dict[str, object]],
    rounds: list[dict[str, object]] | None = None,
    extra_players: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "demoId": "demo-rules",
        "mapName": "de_dust2",
        "tickRate": 64,
        "rounds": rounds or [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 640}],
        "players": [
            {"id": "t-entry", "name": "T Entry", "side": "T", "color": "#f5b542"},
            {"id": "t-trade", "name": "T Trade", "side": "T", "color": "#f5b542"},
            {"id": "ct-1", "name": "CT One", "side": "CT", "color": "#2ed3d0"},
            *(extra_players or []),
        ],
        "frames": frames,
        "kills": kills,
        "deaths": kills,
    }


def kill(
    tick: int,
    attacker_id: str,
    attacker_name: str,
    victim_id: str,
    victim_name: str,
    round_number: int = 1,
) -> dict[str, object]:
    return {
        "tick": tick,
        "roundNumber": round_number,
        "attackerId": attacker_id,
        "attackerName": attacker_name,
        "victimId": victim_id,
        "victimName": victim_name,
    }


def frame(tick: int, players: list[dict[str, object]], round_number: int = 1) -> dict[str, object]:
    return {
        "tick": tick,
        "timeSeconds": tick / 64,
        "roundNumber": round_number,
        "players": players,
        "bombState": {"status": "carried"},
    }


def planted_frame(
    tick: int,
    players: list[dict[str, object]],
    *,
    bomb: tuple[float, float],
) -> dict[str, object]:
    return {
        "tick": tick,
        "timeSeconds": tick / 64,
        "roundNumber": 1,
        "players": players,
        "bombState": {"status": "planted", "x": bomb[0], "y": bomb[1], "site": "A"},
    }


def cluster_t_players(x: float, y: float) -> list[dict[str, object]]:
    return [
        player("t-entry", "T Entry", "T", x, y),
        player("t-trade", "T Trade", "T", x + 1, y + 1),
        player("t-support", "T Support", "T", x + 2, y + 2),
        player("ct-1", "CT One", "CT", 20, 20),
    ]


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
