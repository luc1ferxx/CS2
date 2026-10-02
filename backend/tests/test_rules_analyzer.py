import unittest
import uuid

from app.analysis.analyzer import analyze_replay
from app.analysis.rules import (
    ReplayContext,
    RuleConfig,
    find_isolated_entries,
    find_poor_spacing,
    find_post_plant_spread_issues,
    find_retake_desyncs,
    find_untraded_deaths,
    merge_death_cards,
    scan_spacing_stretches,
)
from app.parser.normalizer import normalize_parser_output


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

    def test_missing_or_empty_replay_events_do_not_crash_analysis(self) -> None:
        replay_without_events = replay_fixture(
            kills=[],
            frames=[frame(100, [player("t-entry", "T Entry", "T", 20, 20)])],
        )
        replay_without_events.pop("events")
        replay_with_empty_events = {
            **replay_without_events,
            "events": [],
        }

        self.assertIsInstance(analyze_replay(replay_without_events), list)
        self.assertIsInstance(analyze_replay(replay_with_empty_events), list)
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
        self.assertEqual(events[0]["severity"], "medium")
        self.assertEqual([event["tick_start"] for event in events], sorted(event["tick_start"] for event in events))

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
            config=RuleConfig(post_plant_min_duration_seconds=4, post_plant_cluster_distance=270),
        )

        post_plant = [event for event in events if event["structured_context_json"]["ruleId"] == "post_plant_spread_issue"]
        self.assertEqual({event["player_id"] for event in post_plant}, {"t-entry", "t-trade", "t-support"})
        self.assertIn(post_plant[0]["category"], {"objective", "positioning"})
        self.assertEqual(post_plant[0]["structured_context_json"]["nearbyCount"], 3)
        self.assertEqual(post_plant[0]["structured_context_json"]["evidenceTicks"], [200, 360, 520])

    def test_retake_desync_generates_event(self) -> None:
        replay = replay_fixture(
            kills=[],
            frames=[
                planted_frame(
                    150,
                    [
                        player("ct-1", "CT One", "CT", 30, 30),
                        player("ct-2", "CT Two", "CT", 20, 20),
                        player("ct-3", "CT Three", "CT", 22, 22),
                        player("t-entry", "T Entry", "T", 50, 50),
                    ],
                    bomb=(52, 52),
                ),
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
            config=RuleConfig(retake_site_distance=225, retake_desync_seconds=4),
        )

        retake = [event for event in events if event["structured_context_json"]["ruleId"] == "retake_desync"]
        self.assertEqual({event["player_id"] for event in retake}, {"ct-1", "ct-2"})
        self.assertEqual(retake[0]["category"], "timing")
        self.assertEqual(retake[0]["structured_context_json"]["windowSeconds"], 5.625)
        self.assertEqual(retake[0]["structured_context_json"]["involvedPlayerIds"], ["ct-1", "ct-2"])

    def test_weak_utility_before_execute_uses_bomb_and_utility_events(self) -> None:
        replay = replay_fixture(
            kills=[],
            rounds=[{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 1400}],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20)]),
                frame(1200, [player("t-entry", "T Entry", "T", 50, 50)]),
            ],
            events=[
                replay_event("smoke-early", "smoke", 120, player_id="t-entry", player_name="T Entry", side="T"),
                replay_event("plant-a", "bomb_planted", 1200, player_id="t-entry", player_name="T Entry", side="T"),
            ],
        )

        events = analyze_replay(replay)

        weak_utility = [
            event for event in events
            if event["structured_context_json"]["ruleId"] == "weak_utility_before_execute"
        ]
        self.assertEqual(len(weak_utility), 1)
        context = weak_utility[0]["structured_context_json"]
        self.assertEqual(context["relatedEventIds"], ["plant-a"])
        self.assertEqual(context["evidenceTicks"], [1200])
        self.assertEqual(context["utilityCount"], 0)
        self.assertEqual(context["requiredUtilityCount"], 2)
        self.assertEqual(context["windowSeconds"], 12)

    def test_utility_dependent_rules_do_not_false_positive_when_utility_events_missing(self) -> None:
        replay = replay_fixture(
            kills=[],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20)]),
                frame(520, [player("t-entry", "T Entry", "T", 50, 50)]),
            ],
            events=[
                replay_event("plant-a", "bomb_planted", 520, player_id="t-entry", player_name="T Entry", side="T"),
            ],
        )

        events = analyze_replay(replay)
        rule_ids = {event["structured_context_json"]["ruleId"] for event in events}

        self.assertNotIn("weak_utility_before_execute", rule_ids)
        self.assertNotIn("late_post_plant_utility", rule_ids)

    def test_bomb_event_dependent_rules_skip_when_objective_events_missing(self) -> None:
        replay = replay_fixture(
            kills=[],
            frames=[
                planted_frame(500, cluster_t_players(50, 50), bomb=(52, 52)),
                planted_frame(700, cluster_t_players(50.5, 50.5), bomb=(52, 52)),
                planted_frame(900, cluster_t_players(51, 51), bomb=(52, 52)),
            ],
            events=[],
        )

        events = analyze_replay(
            replay,
            config=RuleConfig(post_plant_min_duration_seconds=4, post_plant_cluster_distance=270),
        )
        rule_ids = {event["structured_context_json"]["ruleId"] for event in events}

        self.assertNotIn("post_plant_spacing_with_bomb_event", rule_ids)

    def test_late_post_plant_utility_rule_is_removed(self) -> None:
        import app.analysis.rules as rules

        replay = replay_fixture(
            kills=[],
            rounds=[{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 1400}],
            frames=[
                frame(500, [player("t-entry", "T Entry", "T", 20, 20)]),
                frame(1200, [player("t-support", "T Support", "T", 45, 45)]),
            ],
            events=[
                replay_event("plant-a", "bomb_planted", 500, player_id="t-entry", player_name="T Entry", side="T"),
                replay_event("smoke-late", "smoke", 1200, player_id="t-support", player_name="T Support", side="T"),
            ],
        )

        rule_ids = {event["structured_context_json"]["ruleId"] for event in analyze_replay(replay)}
        self.assertNotIn("late_post_plant_utility", rule_ids)
        self.assertFalse(hasattr(rules, "find_late_post_plant_utility"))
        self.assertNotIn("post_plant_utility_grace_seconds", RuleConfig.__dataclass_fields__)

    def test_parser_event_v1_player_ids_feed_evidence_metadata(self) -> None:
        replay = replay_fixture(
            kills=[],
            rounds=[{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 1400}],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20)]),
                frame(1200, [player("t-entry", "T Entry", "T", 50, 50)]),
            ],
            events=[
                {
                    "id": "smoke-v1",
                    "type": "smoke",
                    "tick": 120,
                    "roundNumber": 1,
                    "source": "parser",
                    "playerIds": ["t-entry"],
                    "label": "Smoke",
                    "metadata": {},
                },
                {
                    "id": "plant-v1",
                    "type": "bomb_planted",
                    "tick": 1200,
                    "roundNumber": 1,
                    "source": "parser",
                    "playerIds": ["t-entry"],
                    "label": "Bomb planted",
                    "metadata": {"site": "A"},
                }
            ],
        )

        events = analyze_replay(replay)
        weak_utility = [
            event for event in events
            if event["structured_context_json"]["ruleId"] == "weak_utility_before_execute"
        ]

        self.assertEqual(len(weak_utility), 1)
        self.assertEqual(weak_utility[0]["player_id"], "t-entry")
        context = weak_utility[0]["structured_context_json"]
        self.assertEqual(context["ruleId"], "weak_utility_before_execute")
        self.assertEqual(context["involvedPlayerIds"], ["t-entry"])
        self.assertEqual(context["evidenceTicks"], [1200])
        self.assertEqual(context["relatedEventIds"], ["plant-v1"])

    def test_post_plant_spacing_with_bomb_event_uses_bomb_tick_and_event_id(self) -> None:
        replay = replay_fixture(
            kills=[],
            rounds=[{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 1000}],
            frames=[
                planted_frame(500, cluster_t_players(50, 50), bomb=(52, 52)),
                planted_frame(700, cluster_t_players(50.5, 50.5), bomb=(52, 52)),
                planted_frame(900, cluster_t_players(51, 51), bomb=(52, 52)),
            ],
            events=[
                replay_event("plant-a", "bomb_planted", 500, player_id="t-entry", player_name="T Entry", side="T"),
            ],
        )

        events = analyze_replay(
            replay,
            config=RuleConfig(post_plant_min_duration_seconds=4, post_plant_cluster_distance=270),
        )

        spacing = [
            event for event in events
            if event["structured_context_json"]["ruleId"] == "post_plant_spacing_with_bomb_event"
        ]
        self.assertEqual({event["player_id"] for event in spacing}, {"t-entry", "t-trade", "t-support"})
        context = spacing[0]["structured_context_json"]
        self.assertEqual(context["relatedEventIds"], ["plant-a"])
        self.assertEqual(context["bombTick"], 500)
        self.assertEqual(context["evidenceTicks"], [500, 700, 900])
        self.assertEqual(context["nearbyCount"], 3)

    def test_mock_replay_analysis_remains_stable_with_v3_rules(self) -> None:
        from app.services.mock_replay_service import build_mock_replay

        replay, _ = build_mock_replay("mock-v3")

        events = analyze_replay(replay)

        self.assertLessEqual(len(events), RuleConfig().max_events_total)
        self.assertTrue(all(event["structured_context_json"].get("ruleId") for event in events))


def replay_fixture(
    *,
    kills: list[dict[str, object]],
    frames: list[dict[str, object]],
    rounds: list[dict[str, object]] | None = None,
    extra_players: list[dict[str, object]] | None = None,
    events: list[dict[str, object]] | None = None,
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
        "events": events or [],
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


def replay_event(
    event_id: str,
    event_type: str,
    tick: int,
    *,
    player_id: str | None = None,
    player_name: str | None = None,
    side: str | None = None,
    round_number: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "type": event_type,
        "tick": tick,
        "roundNumber": round_number,
        "playerId": player_id,
        "playerName": player_name,
        "side": side,
        "label": event_type.replace("_", " ").title(),
        "metadata": {},
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


class CoachingEvidenceTest(unittest.TestCase):
    def death_replay(self, tick=100):
        return replay_fixture(
            kills=[kill(tick, "ct-1", "CT One", "t-entry", "T Entry")],
            frames=[frame(tick, [
                player("t-entry", "T Entry", "T", 20, 20),
                player("t-trade", "T Trade", "T", 60, 60),
                player("ct-1", "CT One", "CT", 21, 21),
            ])],
        )

    def test_killing_another_nearby_enemy_is_not_a_trade_on_the_killer(self):
        replay = self.death_replay()
        replay["players"].append({"id": "ct-2", "name": "CT Two", "side": "CT"})
        replay["frames"].append(frame(150, [
            player("t-trade", "T Trade", "T", 21, 21),
            player("ct-2", "CT Two", "CT", 21, 21),
        ]))
        replay["kills"].append(kill(150, "t-trade", "T Trade", "ct-2", "CT Two"))
        events = find_untraded_deaths(replay)
        self.assertIn("t-entry", [event["player_id"] for event in events])

    def test_non_live_deaths_are_excluded_even_with_a_round_label(self):
        for tick in (50, 650):
            with self.subTest(tick=tick):
                replay = self.death_replay(tick)
                replay["rounds"][0]["freezeEndTick"] = 75
                self.assertEqual(find_untraded_deaths(replay), [])
                self.assertEqual(find_isolated_entries(replay), [])

    def test_round_end_does_not_become_a_missed_trade_window(self):
        self.assertEqual(find_untraded_deaths(self.death_replay(600)), [])

    def test_world_suicide_and_friendly_fire_do_not_become_trade_advice(self):
        for attacker in (None, "t-entry", "t-trade"):
            with self.subTest(attacker=attacker):
                replay = self.death_replay()
                replay["kills"][0]["attackerId"] = attacker
                replay["kills"][0]["attackerName"] = None
                self.assertEqual(find_untraded_deaths(replay), [])
                self.assertEqual(find_isolated_entries(replay), [])

    def test_kill_side_metadata_handles_halftime(self):
        replay = self.death_replay()
        replay["kills"][0].update(attackerSide="T", victimSide="CT")
        replay["kills"].append({
            **kill(140, "t-trade", "T Trade", "ct-1", "CT One"),
            "attackerSide": "CT", "victimSide": "T",
        })
        self.assertNotIn("t-entry", [event["player_id"] for event in find_untraded_deaths(replay)])

    def test_spatial_evidence_never_uses_a_future_or_stale_sample(self):
        for frame_tick in (101, 0):
            with self.subTest(frame_tick=frame_tick):
                replay = self.death_replay()
                replay["frames"][0]["tick"] = frame_tick
                self.assertEqual(find_isolated_entries(replay), [])

    def test_multiple_players_in_one_round_receive_their_own_death_review(self):
        replay = self.death_replay()
        replay["kills"].append(kill(200, "ct-1", "CT One", "t-trade", "T Trade"))
        self.assertEqual({event["player_id"] for event in find_untraded_deaths(replay)}, {"t-entry", "t-trade"})

    def test_personal_limits_cover_later_rounds_and_every_player(self):
        replay = self.death_replay()
        replay["kills"].clear()
        replay["frames"].clear()
        replay["rounds"].clear()
        for round_number in range(1, 31):
            start = round_number * 2000
            replay["rounds"].append({"roundNumber": round_number, "startTick": start, "freezeEndTick": start, "endTick": start + 1500})
            for offset, victim in ((100, "t-entry"), (200, "t-trade")):
                replay["kills"].append({
                    **kill(start + offset, "ct-1", "CT One", victim, victim, round_number),
                    "attackerSide": "CT", "victimSide": "T",
                })
            replay["frames"].append(frame(start + 100, [
                player("t-entry", "T Entry", "T", 20, 20),
                player("t-trade", "T Trade", "T", 25, 25),
                player("ct-1", "CT One", "CT", 21, 21),
            ], round_number))
        events = analyze_replay(replay, RuleConfig(max_events_total=60, max_events_per_player=30))
        self.assertEqual(len(events), 60)
        for player_id in ("t-entry", "t-trade"):
            self.assertEqual({event["round_number"] for event in events if event["player_id"] == player_id}, set(range(1, 31)))

    def test_events_are_deterministic_and_explicit_review_candidates(self):
        replay = self.death_replay()
        events = analyze_replay(replay)
        self.assertEqual(events, analyze_replay(replay))
        for event in events:
            context = event["structured_context_json"]
            self.assertEqual(context["targetPlayerId"], event["player_id"])
            self.assertEqual(context["assessment"], "review_candidate")
            self.assertTrue(context["action"])
            self.assertTrue(context["limitation"])

    def test_nuke_mixed_or_unknown_floors_do_not_generate_spacing_claims(self):
        for heights in ((-600, -300, -300), (None, None, None)):
            with self.subTest(heights=heights):
                replay = self.death_replay()
                replay["mapName"] = "de_nuke"
                replay["mapMetadata"] = {"lowerLevelMaxZ": -495}
                replay["frames"][0]["tick"] = 600
                replay["frames"][0]["players"] = [
                    {**player(f"t-{index}", f"T {index}", "T", 20 + index, 20), "z": z}
                    for index, z in enumerate(heights)
                ]
                single_sample = RuleConfig(poor_spacing_eval_delay_seconds=0, poor_spacing_min_duration_seconds=0)
                self.assertEqual(find_poor_spacing(replay, single_sample), [])
                self.assertEqual(scan_spacing_stretches(replay, single_sample), [])

    def test_ct_utility_does_not_count_as_terrorist_utility(self):
        replay = self.death_replay()
        replay["kills"].clear()
        replay["rounds"][0]["endTick"] = 1400
        replay["events"] = [
            replay_event("ct-smoke", "smoke", 120, player_id="ct-1", side="CT"),
            replay_event("plant", "bomb_planted", 1200, player_id="t-entry", side="T"),
        ]
        utility = [event for event in analyze_replay(replay) if event["category"] == "utility"]
        # The CT smoke is not T utility: the plant reads as an execute with none at all.
        self.assertEqual(len(utility), 1)
        self.assertEqual(utility[0]["structured_context_json"]["utilityCount"], 0)
        self.assertEqual(utility[0]["structured_context_json"]["relatedEventIds"], ["plant"])

    def test_parser_kill_reference_points_at_the_victim_event(self):
        replay = self.death_replay()
        replay["events"] = [{
            "id": "kill-100", "type": "kill", "tick": 100, "roundNumber": 1,
            "playerIds": ["ct-1", "t-entry"], "metadata": {"victimId": "t-entry"},
        }]
        event = find_untraded_deaths(replay)[0]
        self.assertEqual(event["structured_context_json"]["relatedEventIds"], ["kill-100"])
        self.assertEqual(event["player_id"], "t-entry")


class MalformedRoundRecordTest(unittest.TestCase):
    """Round records carrying a null where a number or a tick belongs.

    worker.py always normalizes parser output before analyzing, so these cannot
    arrive from the parser today. What they pin is the contract the three
    round-number lookups share: a record the index refuses is a record no
    lookup hands back, and a null tick reads as an absent one rather than
    reaching int().
    """

    def replay_with_rounds(self, rounds, *, tick=100):
        return replay_fixture(
            kills=[kill(tick, "ct-1", "CT One", "t-entry", "T Entry")],
            frames=[frame(tick, [
                player("t-entry", "T Entry", "T", 20, 20),
                player("t-trade", "T Trade", "T", 60, 60),
                player("ct-1", "CT One", "CT", 21, 21),
            ])],
            rounds=rounds,
        )

    def test_a_round_with_no_usable_number_is_skipped_by_every_rule(self):
        replay = self.replay_with_rounds(
            [{"roundNumber": None, "startTick": 0, "freezeEndTick": 0, "endTick": 640}]
        )

        context = ReplayContext(replay)
        self.assertEqual(context.round_by_number, {})
        self.assertIsNone(context.live_round_at(100))
        self.assertEqual(analyze_replay(replay), [])

    def test_null_round_ticks_read_as_absent_ones(self):
        replay = self.replay_with_rounds(
            [{"roundNumber": 1, "startTick": None, "freezeEndTick": None, "endTick": None}],
            tick=0,
        )

        context = ReplayContext(replay)
        # Every tick field falls back to 0, so tick 0 is the only live tick this
        # round has -- and it still has no observation window to judge a trade in.
        self.assertEqual(context.live_round_at(0), 1)
        self.assertIsNone(context.live_round_at(1))
        self.assertEqual(find_untraded_deaths(replay), [])
        # The round number is the one usable field, so the opening-death rule
        # still fires off it; the trade rule cannot, its window has no end.
        rule_ids = {event["structured_context_json"]["ruleId"] for event in analyze_replay(replay)}
        self.assertEqual(rule_ids, {"isolated_entry"})

    def test_a_round_number_taken_off_a_frame_label_need_not_be_indexed(self):
        replay = self.replay_with_rounds(
            [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 640}],
            tick=5000,
        )
        replay["frames"][0]["roundNumber"] = 9
        replay["kills"][0]["roundNumber"] = 9

        context = ReplayContext(replay)
        self.assertEqual(context.round_for_tick(5000, 9), 9)
        self.assertNotIn(9, context.round_by_number)
        self.assertEqual(analyze_replay(replay), [])

    def test_a_round_number_a_lookup_returns_is_always_indexed(self):
        replay = self.replay_with_rounds([
            {"roundNumber": None, "startTick": 0, "freezeEndTick": 0, "endTick": 200},
            {"roundNumber": "2", "startTick": 201, "freezeEndTick": 220, "endTick": 400},
            {"roundNumber": 3, "startTick": None, "endTick": None},
        ])

        context = ReplayContext(replay)
        for tick in range(0, 420, 20):
            with self.subTest(tick=tick):
                self.assertIn(context.round_for_tick(tick), context.round_by_number)
                live_round = context.live_round_at(tick)
                self.assertTrue(live_round is None or live_round in context.round_by_number)


NAMES = {"t-a": "T A", "t-b": "T B", "t-c": "T C", "ct-1": "CT One", "ct-2": "CT Two", "ct-3": "CT Three"}
ROSTER = [{"id": pid, "name": name, "side": "CT" if pid.startswith("ct") else "T"} for pid, name in NAMES.items()]
# de_dust2: 45.056 world units per radar percent. Stacked <= 112 units (2.49 %),
# too_far >= 1260 units (27.97 %), isolated entry > 990 units (21.97 %).
STACKED = {"t-a": (20, 20), "t-b": (21, 20), "t-c": (30, 20), "ct-1": (70, 70)}
APART = {"t-a": (20, 20), "t-b": (24, 20), "t-c": (30, 20), "ct-1": (70, 70)}
FAR = {"t-a": (20, 20), "t-b": (24, 20), "t-c": (80, 80), "ct-1": (70, 70)}
EVAL_START = 960  # 15 s after freeze end (tick 0) at 64 tick


def plan_a_replay(frames, *, kills=(), events=(), rounds=None, player_states=None):
    replay = {
        "demoId": "demo-plan-a",
        "mapName": "de_dust2",
        "tickRate": 64,
        "rounds": rounds or [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 6400, "winnerSide": "CT"}],
        "players": [dict(item) for item in ROSTER],
        "frames": frames,
        "kills": list(kills),
        "deaths": list(kills),
        "events": list(events),
    }
    if player_states is not None:
        replay["playerStates"] = player_states
    return replay


def layout_frame(tick, layout, round_number=1, dead=()):
    players = []
    for player_id, spot in layout.items():
        side = "CT" if player_id.startswith("ct") else "T"
        entry = player(player_id, NAMES[player_id], side, *spot)
        # A dead player stays where he fell.
        players.append({**entry, "alive": False, "hp": 0} if player_id in dead else entry)
    return frame(tick, players, round_number)


def sampled(start, end, layout, *, step=16, round_number=1, dead=()):
    return [layout_frame(tick, layout, round_number, dead) for tick in range(start, end + 1, step)]


def death(tick, attacker_id, victim_id, *, weapon="ak47", attacker_side="CT", victim_side="T"):
    return {
        "tick": tick, "roundNumber": 1,
        "attackerId": attacker_id, "attackerName": NAMES[attacker_id], "attackerSide": attacker_side,
        "victimId": victim_id, "victimName": NAMES[victim_id], "victimSide": victim_side,
        "weapon": weapon, "headshot": False,
    }


def kill_event(kill):
    return {
        "id": f"kill-{kill['tick']}-{kill['attackerId']}-{kill['victimId']}", "type": "kill",
        "tick": kill["tick"], "roundNumber": kill["roundNumber"],
        "playerIds": [kill["attackerId"], kill["victimId"]], "playerId": kill["attackerId"],
        "metadata": {key: kill[key] for key in ("attackerId", "attackerSide", "victimId", "victimSide")},
    }


def raw_id(demo_id, rule_id, player_id, round_number, tick_start, tick_end):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{demo_id}:{rule_id}:{player_id}:{round_number}:{tick_start}:{tick_end}"))


def personal_id(raw, player_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{raw}:{player_id}"))


def of_rule(events, rule_id, spacing_type=None):
    return [
        event for event in events
        if event["structured_context_json"]["ruleId"] == rule_id
        and (spacing_type is None or event["structured_context_json"].get("spacingType") == spacing_type)
    ]


class StackedStretchTest(unittest.TestCase):
    def test_a_stacked_pair_needs_three_seconds(self):
        short = plan_a_replay(sampled(EVAL_START, EVAL_START + 176, STACKED) + sampled(EVAL_START + 192, 1600, APART))
        self.assertEqual(find_poor_spacing(short), [])

        held = plan_a_replay(sampled(EVAL_START, EVAL_START + 192, STACKED) + sampled(EVAL_START + 208, 1600, APART))
        events = find_poor_spacing(held)
        self.assertEqual(len(events), 1)
        context = events[0]["structured_context_json"]
        self.assertEqual((events[0]["player_id"], events[0]["tick_start"], events[0]["tick_end"]), ("t-a", 960, 1152))
        self.assertEqual(context["involvedPlayerIds"], ["t-a", "t-b"])
        self.assertEqual(context["evidenceTicks"], [960])
        self.assertEqual(context["durationSeconds"], 3)
        self.assertNotIn("stackedMultikill", context)
        # The shape of the single-sample card is kept.
        for key in ("side", "spacingType", "distance", "nearbyCount", "poorSpacingMinDistance",
                    "poorSpacingMaxDistance", "maxNearestDistance", "minPairDistance"):
            self.assertIn(key, context)

    def test_evaluation_starts_fifteen_seconds_after_freeze_end(self):
        replay = plan_a_replay(sampled(640, 1600, STACKED))
        events = find_poor_spacing(replay)
        self.assertEqual([event["tick_start"] for event in events], [EVAL_START])
        self.assertEqual(events[0]["structured_context_json"]["durationSeconds"], 10)
        # Spawn walks: a pair together only before 15 s gives nothing.
        self.assertEqual(find_poor_spacing(plan_a_replay(sampled(512, 944, STACKED) + sampled(960, 1600, APART))), [])

    def test_a_gap_of_more_than_one_second_breaks_the_stretch(self):
        broken = sampled(960, 1088, STACKED) + sampled(1168, 1296, STACKED)
        self.assertEqual(find_poor_spacing(plan_a_replay(broken)), [])
        joined = sampled(960, 1088, STACKED) + sampled(1152, 1296, STACKED)
        self.assertEqual([event["tick_start"] for event in find_poor_spacing(plan_a_replay(joined))], [960])

    def test_the_first_qualifying_stretch_wins_and_a_short_one_takes_no_slot(self):
        frames = (
            sampled(960, 1024, STACKED) + sampled(1040, 1200, APART)
            + sampled(1216, 1600, STACKED) + sampled(1616, 2000, APART)
            + sampled(2016, 2400, STACKED)
        )
        events = find_poor_spacing(plan_a_replay(frames))
        self.assertEqual([event["tick_start"] for event in events], [1216])
        self.assertEqual(events[0]["structured_context_json"]["durationSeconds"], 6)

    def test_a_card_whose_first_stretch_qualifies_keeps_its_id(self):
        replay = plan_a_replay(sampled(EVAL_START, 1600, STACKED))
        raw = raw_id("demo-plan-a", "poor_spacing", "t-a", 1, 960, 960 + 192)
        self.assertEqual([event["id"] for event in find_poor_spacing(replay)], [raw])
        personal = of_rule(analyze_replay(replay), "poor_spacing", "stacked")
        self.assertEqual(
            sorted(event["id"] for event in personal),
            sorted(personal_id(raw, player_id) for player_id in ("t-a", "t-b")),
        )

    def test_a_short_stack_counts_when_one_enemy_kills_both_within_three_seconds(self):
        frames = sampled(EVAL_START, 1008, STACKED) + sampled(1024, 1600, APART)
        kills = [death(1100, "ct-1", "t-a"), death(1200, "ct-1", "t-b")]
        events = find_poor_spacing(plan_a_replay(frames, kills=kills))
        self.assertEqual(len(events), 1)
        context = events[0]["structured_context_json"]
        self.assertTrue(context["stackedMultikill"])
        self.assertEqual(context["multikillAttackerId"], "ct-1")
        self.assertEqual(context["multikillAttackerName"], "CT One")
        self.assertEqual(context["durationSeconds"], 0.75)
        self.assertEqual(events[0]["tick_start"], 960)

    def test_a_stack_that_already_lasted_three_seconds_does_not_carry_the_multikill_fields(self):
        frames = sampled(EVAL_START, EVAL_START + 320, STACKED) + sampled(EVAL_START + 336, 1800, APART)
        kills = [death(1300, "ct-1", "t-a"), death(1400, "ct-1", "t-b")]
        events = find_poor_spacing(plan_a_replay(frames, kills=kills))
        self.assertEqual(len(events), 1)
        context = events[0]["structured_context_json"]
        self.assertEqual(context["durationSeconds"], 5)
        for key in ("stackedMultikill", "multikillAttackerId", "multikillAttackerName"):
            self.assertNotIn(key, context)

    def test_the_multikill_path_needs_one_enemy_close_deaths_and_a_first_death_near_the_stretch(self):
        frames = sampled(EVAL_START, 1008, STACKED) + sampled(1024, 1800, APART)
        for label, kills in (
            ("two killers", [death(1100, "ct-1", "t-a"), death(1200, "ct-2", "t-b")]),
            ("more than 3 s apart", [death(1100, "ct-1", "t-a"), death(1100 + 193, "ct-1", "t-b")]),
            ("first death after stretch end + 3 s", [death(1008 + 193, "ct-1", "t-a"), death(1250, "ct-1", "t-b")]),
            ("first death before the stretch", [death(900, "ct-1", "t-a"), death(1000, "ct-1", "t-b")]),
            ("a teamkill", [
                death(1100, "t-c", "t-a", attacker_side="T"), death(1200, "t-c", "t-b", attacker_side="T"),
            ]),
        ):
            with self.subTest(label):
                self.assertEqual(find_poor_spacing(plan_a_replay(frames, kills=kills)), [])


class DeathCardTest(unittest.TestCase):
    def far_then_death(self, death_tick, far_until, *, also_far_at_death=False):
        layout_after = FAR if also_far_at_death else APART
        frames = sampled(EVAL_START, far_until, FAR) + sampled(far_until + 16, death_tick - 16, layout_after)
        frames += sampled(death_tick + 16, 2800, layout_after, dead=("t-c",))
        kill = death(death_tick, "ct-1", "t-c", weapon="awp")
        return plan_a_replay(frames, kills=[kill], events=[kill_event(kill)])

    def test_too_far_is_no_card_and_becomes_a_reason_within_five_seconds_before_the_death(self):
        replay = self.far_then_death(1216 + 4 * 64, 1216)
        events = analyze_replay(replay)
        self.assertEqual(of_rule(events, "poor_spacing", "too_far"), [])
        cards = of_rule(events, "untraded_death")
        self.assertEqual(len(cards), 1)
        reasons = cards[0]["structured_context_json"]["extraReasons"]
        self.assertEqual(len(reasons), 1)
        self.assertEqual(
            {key: reasons[0][key] for key in ("ruleId", "spacingType", "durationSeconds", "tick")},
            {"ruleId": "poor_spacing", "spacingType": "too_far", "durationSeconds": 4, "tick": 960},
        )
        self.assertIsInstance(reasons[0]["distance"], int)
        self.assertGreaterEqual(reasons[0]["distance"], 1260)
        # The reason never enters the id.
        untraded = find_untraded_deaths(replay)[0]
        self.assertEqual(cards[0]["id"], personal_id(untraded["id"], "t-c"))

    def test_too_far_outside_the_window_or_too_short_adds_no_reason(self):
        for label, replay in (
            ("ended 6 s before the death", self.far_then_death(1216 + 6 * 64, 1216)),
            ("lasted 2.75 s", self.far_then_death(1136 + 64, 1136)),
        ):
            with self.subTest(label):
                cards = of_rule(analyze_replay(replay), "untraded_death")
                self.assertEqual(len(cards), 1)
                self.assertNotIn("extraReasons", cards[0]["structured_context_json"])

    def test_an_isolated_opening_death_folds_into_its_untraded_card(self):
        replay = self.far_then_death(1600, 1584, also_far_at_death=True)
        events = analyze_replay(replay)
        self.assertEqual(of_rule(events, "isolated_entry"), [])
        cards = of_rule(events, "untraded_death")
        self.assertEqual(len(cards), 1)
        context = cards[0]["structured_context_json"]
        self.assertEqual([reason["ruleId"] for reason in context["extraReasons"]], ["poor_spacing", "isolated_entry"])
        isolated = context["extraReasons"][1]
        self.assertEqual(isolated["tick"], 1584)
        self.assertGreater(isolated["distance"], 990)
        self.assertEqual(context["relatedEventIds"], ["kill-1600-ct-1-t-c"])
        self.assertEqual(cards[0]["id"], personal_id(find_untraded_deaths(replay)[0]["id"], "t-c"))

    def test_without_an_untraded_card_the_isolated_entry_is_the_card(self):
        replay = self.far_then_death(1600, 1584, also_far_at_death=True)
        trade = death(1650, "t-a", "ct-1", attacker_side="T", victim_side="CT")
        replay["kills"].append(trade)
        replay["events"].append(kill_event(trade))
        events = analyze_replay(replay)
        self.assertNotIn("t-c", [event["player_id"] for event in of_rule(events, "untraded_death")])
        cards = of_rule(events, "isolated_entry")
        self.assertEqual([card["player_id"] for card in cards], ["t-c"])
        context = cards[0]["structured_context_json"]
        self.assertEqual([reason["ruleId"] for reason in context["extraReasons"]], ["poor_spacing"])
        self.assertEqual(context["attackerName"], "CT One")
        self.assertEqual(context["weapon"], "awp")
        self.assertTrue(context["impact"]["firstDeath"])

    def test_cards_merge_by_kill_id_never_by_tick(self):
        def card(rule_id, event_id, tick, related):
            return {
                "id": event_id, "round_number": 1, "player_id": "t-c", "tick_start": tick,
                "structured_context_json": {
                    "ruleId": rule_id, "relatedEventIds": related, "distance": 1500.4, "positionSampleTick": tick - 10,
                },
            }

        merged = merge_death_cards([card("untraded_death", "u", 100, ["kill-a"]), card("isolated_entry", "i", 104, ["kill-a"])])
        self.assertEqual([event["id"] for event in merged], ["u"])
        self.assertEqual(merged[0]["structured_context_json"]["extraReasons"], [{"ruleId": "isolated_entry", "distance": 1500, "tick": 94}])
        apart = merge_death_cards([card("untraded_death", "u", 100, ["kill-a"]), card("isolated_entry", "i", 100, ["kill-b"])])
        self.assertEqual([event["id"] for event in apart], ["u", "i"])
        fallback = merge_death_cards([card("untraded_death", "u", 100, []), card("isolated_entry", "i", 100, [])])
        self.assertEqual([event["id"] for event in fallback], ["u"])
        no_match = merge_death_cards([card("untraded_death", "u", 100, []), card("isolated_entry", "i", 120, [])])
        self.assertEqual([event["id"] for event in no_match], ["u", "i"])


def two_death_round(*, winner="CT", second_victim_alone=False):
    """t-a dies at 1200 (ct-1, ak47), then t-b at 1600 (ct-2): 3v2 -> 2v2, then 2v2 -> 1v2.

    With `second_victim_alone`, t-c is already dead before t-b's death, so t-b dies as the last T.
    """
    layout = {"t-a": (20, 20), "t-b": (24, 20), "t-c": (30, 20), "ct-1": (70, 70), "ct-2": (72, 70)}
    dead_between = ("t-a", "t-c") if second_victim_alone else ("t-a",)
    frames = (
        sampled(EVAL_START, 1184, layout)
        + sampled(1200, 1584, layout, dead=dead_between)
        + sampled(1600, 2400, layout, dead=(*dead_between, "t-b"))
    )
    kills = [death(1200, "ct-1", "t-a"), death(1600, "ct-2", "t-b", weapon="m4a1_silencer")]
    rounds = [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 6400}]
    if winner is not None:
        # A round the parser saw end carries both.
        rounds[0].update(winnerSide=winner, winnerReason="t_killed" if winner == "CT" else "ct_killed")
    return plan_a_replay(frames, kills=kills, events=[kill_event(kill) for kill in kills], rounds=rounds)


class DeathImpactTest(unittest.TestCase):
    def round_with_two_deaths(self, **options):
        return two_death_round(**options)

    def test_every_death_card_carries_its_impact_weapon_and_attacker(self):
        cards = {event["player_id"]: event["structured_context_json"] for event in of_rule(
            analyze_replay(self.round_with_two_deaths()), "untraded_death"
        )}
        self.assertEqual(cards["t-a"]["impact"], {
            "roundLost": True, "firstDeath": True,
            "aliveBefore": {"own": 3, "enemy": 2}, "aliveAfter": {"own": 2, "enemy": 2}, "manDisadvantage": False,
        })
        self.assertEqual(cards["t-b"]["impact"], {
            "roundLost": True, "firstDeath": False,
            "aliveBefore": {"own": 2, "enemy": 2}, "aliveAfter": {"own": 1, "enemy": 2}, "manDisadvantage": True,
        })
        self.assertEqual((cards["t-b"]["weapon"], cards["t-b"]["attackerName"]), ("m4a1_silencer", "CT Two"))

    def test_round_result_reads_null_when_the_winner_is_unknown_and_false_on_a_win(self):
        unknown = of_rule(analyze_replay(self.round_with_two_deaths(winner=None)), "untraded_death")
        self.assertIsNone(unknown[0]["structured_context_json"]["impact"]["roundLost"])
        won = of_rule(analyze_replay(self.round_with_two_deaths(winner="T")), "untraded_death")
        self.assertFalse(won[0]["structured_context_json"]["impact"]["roundLost"])

    def test_a_round_that_never_ended_reads_null_after_normalization(self):
        # A demo cut off before round_end: the parser has no winner and no reason,
        # and the normalizer fills winnerSide with "CT". That is not a known winner.
        parsed = self.round_with_two_deaths(winner=None)
        replay = normalize_parser_output("demo-plan-a", parsed)
        self.assertEqual([item.get("winnerSide") for item in replay["rounds"]], ["CT"])
        self.assertNotIn("winnerReason", replay["rounds"][0])
        cards = of_rule(analyze_replay(replay), "untraded_death")
        self.assertTrue(cards)
        for card in cards:
            self.assertIsNone(card["structured_context_json"]["impact"]["roundLost"])

    def test_a_winner_without_a_reason_is_not_trusted(self):
        replay = self.round_with_two_deaths(winner="CT")
        del replay["rounds"][0]["winnerReason"]
        cards = of_rule(analyze_replay(replay), "untraded_death")
        self.assertTrue(cards)
        for card in cards:
            self.assertIsNone(card["structured_context_json"]["impact"]["roundLost"])

    def test_missing_alive_data_leaves_the_counts_out(self):
        replay = self.round_with_two_deaths()
        # No frame before the first death: counts unknown, never guessed.
        replay["frames"] = [item for item in replay["frames"] if item["tick"] >= 1200]
        cards = {event["player_id"]: event["structured_context_json"] for event in of_rule(analyze_replay(replay), "untraded_death")}
        self.assertEqual(cards["t-a"]["impact"], {"roundLost": True, "firstDeath": True})
        self.assertIn("aliveBefore", cards["t-b"]["impact"])

    def test_the_last_player_alive_is_not_reported_as_untraded(self):
        events = of_rule(analyze_replay(self.round_with_two_deaths(second_victim_alone=True)), "untraded_death")
        self.assertEqual([event["player_id"] for event in events], ["t-a"])


class RetakeAndUtilityTest(unittest.TestCase):
    def retake_replay(self, *, ts_alive_at_last_arrival=True, ct1_at_bomb_on_plant=False):
        def planted(tick, ct_spots, t_alive=True):
            players = [player(pid, NAMES[pid], "CT", *spot) for pid, spot in ct_spots.items()]
            t_player = player("t-a", "T A", "T", 50, 50)
            if not t_alive:
                t_player = {**t_player, "alive": False, "hp": 0}
            return planted_frame(tick, [*players, t_player], bomb=(52, 52))

        frames = [
            planted(150, {"ct-1": (52, 53) if ct1_at_bomb_on_plant else (30, 30), "ct-2": (20, 20), "ct-3": (22, 22)}),
            planted(200, {"ct-1": (52, 53), "ct-2": (20, 20), "ct-3": (22, 22)}),
            planted(300, {"ct-1": (52, 53), "ct-2": (53, 52), "ct-3": (22, 22)}),
            planted(600, {"ct-1": (52, 53), "ct-2": (53, 52), "ct-3": (52, 51)}, t_alive=ts_alive_at_last_arrival),
        ]
        return replay_fixture(kills=[], frames=frames, extra_players=[
            {"id": "ct-2", "name": "CT Two", "side": "CT"}, {"id": "ct-3", "name": "CT Three", "side": "CT"},
        ])

    def retake(self, replay):
        return find_retake_desyncs(replay, RuleConfig(retake_site_distance=225))

    def test_retake_desync_counts_arrivals_only(self):
        events = self.retake(self.retake_replay())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["structured_context_json"]["involvedPlayerIds"], ["ct-1", "ct-2", "ct-3"])
        self.assertEqual((events[0]["tick_start"], events[0]["tick_end"]), (200, 600))
        # ct-1 was already at the bomb in the first planted sample: he did not arrive.
        events = self.retake(self.retake_replay(ct1_at_bomb_on_plant=True))
        self.assertEqual(events[0]["structured_context_json"]["involvedPlayerIds"], ["ct-2", "ct-3"])
        self.assertEqual((events[0]["tick_start"], events[0]["tick_end"]), (300, 600))

    def test_retake_desync_skips_a_round_whose_ts_are_all_dead_at_the_last_arrival(self):
        self.assertEqual(self.retake(self.retake_replay(ts_alive_at_last_arrival=False)), [])

    def utility_replay(self, *, t_equip=None, round_number=2, ct_smoke=True):
        rounds = [
            {"roundNumber": 1, "startTick": 0, "freezeEndTick": 100, "endTick": 1400, "winnerSide": "T"},
            {"roundNumber": 2, "startTick": 1500, "freezeEndTick": 1600, "endTick": 2900, "winnerSide": "T"},
        ]
        layout = {"t-a": (20, 20), "t-b": (24, 20), "ct-1": (70, 70), "ct-2": (72, 70)}
        frames = sampled(0, 1400, layout, step=200, round_number=1) + sampled(1500, 2900, layout, step=200, round_number=2)
        start = 0 if round_number == 1 else 1500
        events = [replay_event("plant", "bomb_planted", start + 1200, player_id="t-a", player_name="T A", side="T",
                               round_number=round_number)]
        if ct_smoke:
            events.append(replay_event("ct-smoke", "smoke", start + 300, player_id="ct-1", side="CT", round_number=round_number))
        states = None
        if t_equip is not None:
            buy_tick = start + 50
            states = {
                pid: [{"tick": buy_tick, "money": money, "equipValue": equip}]
                for pid, (equip, money) in {
                    "t-a": t_equip, "t-b": t_equip, "ct-1": (4500, 1000), "ct-2": (4500, 1000),
                }.items()
            }
        return plan_a_replay(frames, events=events, rounds=rounds, player_states=states)

    def weak_utility(self, replay):
        return of_rule(analyze_replay(replay), "weak_utility_before_execute")

    def test_a_plant_without_any_t_utility_reads_as_a_weak_execute_unless_the_ts_were_on_an_eco(self):
        unknown = self.weak_utility(self.utility_replay())
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["structured_context_json"]["utilityCount"], 0)
        self.assertNotIn("tBuyKind", unknown[0]["structured_context_json"])

        full = self.weak_utility(self.utility_replay(t_equip=(4500, 500)))
        self.assertEqual([event["structured_context_json"]["tBuyKind"] for event in full], ["full"])
        self.assertEqual(self.weak_utility(self.utility_replay(t_equip=(700, 3000))), [])
        force = self.weak_utility(self.utility_replay(t_equip=(1500, 200)))
        self.assertEqual([event["structured_context_json"]["tBuyKind"] for event in force], ["force"])
        pistol = self.weak_utility(self.utility_replay(t_equip=(700, 100), round_number=1))
        self.assertEqual([event["structured_context_json"]["tBuyKind"] for event in pistol], ["pistol"])

    def test_a_replay_without_any_utility_event_has_the_family_missing(self):
        self.assertEqual(self.weak_utility(self.utility_replay(ct_smoke=False)), [])

    def test_clustering_in_a_round_with_a_recorded_plant_is_only_the_bomb_event_version(self):
        frames = [
            planted_frame(500, cluster_t_players(50, 50), bomb=(52, 52)),
            planted_frame(700, cluster_t_players(50.5, 50.5), bomb=(52, 52)),
            planted_frame(900, cluster_t_players(51, 51), bomb=(52, 52)),
        ]
        config = RuleConfig(post_plant_min_duration_seconds=4, post_plant_cluster_distance=270)
        without_event = replay_fixture(kills=[], frames=frames, rounds=[
            {"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 1000},
        ])
        self.assertEqual(len(find_post_plant_spread_issues(without_event, config)), 1)
        with_event = replay_fixture(kills=[], frames=frames, rounds=without_event["rounds"], events=[
            replay_event("plant-a", "bomb_planted", 500, player_id="t-entry", player_name="T Entry", side="T"),
        ])
        self.assertEqual(find_post_plant_spread_issues(with_event, config), [])
        rule_ids = {event["structured_context_json"]["ruleId"] for event in analyze_replay(with_event, config)}
        self.assertIn("post_plant_spacing_with_bomb_event", rule_ids)
        self.assertNotIn("post_plant_spread_issue", rule_ids)


class StableIdTest(unittest.TestCase):
    def test_ids_of_rules_this_change_leaves_alone_are_unchanged(self):
        replay = replay_fixture(
            kills=[kill(100, "ct-1", "CT One", "t-entry", "T Entry")],
            rounds=[{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 1400}],
            frames=[
                frame(100, [player("t-entry", "T Entry", "T", 20, 20), player("t-trade", "T Trade", "T", 60, 60),
                            player("ct-1", "CT One", "CT", 21, 21)]),
                frame(1200, [player("t-entry", "T Entry", "T", 50, 50)]),
            ],
            events=[
                replay_event("smoke-early", "smoke", 120, player_id="t-entry", player_name="T Entry", side="T"),
                replay_event("plant-a", "bomb_planted", 1200, player_id="t-entry", player_name="T Entry", side="T"),
            ],
        )
        ids = {event["structured_context_json"]["ruleId"]: event["id"] for event in analyze_replay(replay)}
        self.assertEqual(ids["untraded_death"], personal_id(raw_id("demo-rules", "untraded_death", "t-entry", 1, 100, 420), "t-entry"))
        self.assertEqual(
            ids["weak_utility_before_execute"],
            personal_id(raw_id("demo-rules", "weak_utility_before_execute", "t-entry", 1, 1200, 1200), "t-entry"),
        )

    def test_partial_and_malformed_replays_never_raise(self):
        base = two_death_round()
        broken_cases = {
            "v1 without playerStates or events": {key: value for key, value in base.items() if key != "events"},
            "frames without ticks": {**base, "frames": [{**item, "tick": None} for item in base["frames"]][:3] + base["frames"]},
            "players not a list": {**base, "frames": [{**item, "players": None} for item in base["frames"][:3]] + base["frames"]},
            "deaths missing": {**base, "deaths": None},
            "junk player states": {**base, "playerStates": {"t-a": "x", "t-b": [None, {"tick": "x"}]}},
            "junk map metadata": {**base, "mapMetadata": ["x"]},
        }
        for label, replay in broken_cases.items():
            with self.subTest(label):
                self.assertIsInstance(analyze_replay(replay), list)


if __name__ == "__main__":
    unittest.main()
