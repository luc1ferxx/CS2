import unittest

from app.analysis.analyzer import analyze_replay
from app.analysis.rules import RuleConfig, find_isolated_entries, find_poor_spacing, find_untraded_deaths


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

    def test_late_post_plant_utility_includes_related_event_metadata(self) -> None:
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

        events = analyze_replay(replay)

        late_utility = [
            event for event in events
            if event["structured_context_json"]["ruleId"] == "late_post_plant_utility"
        ]
        self.assertEqual(len(late_utility), 1)
        context = late_utility[0]["structured_context_json"]
        self.assertEqual(context["relatedEventIds"], ["plant-a", "smoke-late"])
        self.assertEqual(context["utilityType"], "smoke")
        self.assertEqual(context["utilityLabel"], "Smoke")
        self.assertGreater(context["windowSeconds"], context["graceWindowSeconds"])

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
                self.assertEqual(find_poor_spacing(replay), [])

    def test_ct_utility_does_not_count_as_terrorist_utility(self):
        replay = self.death_replay()
        replay["kills"].clear()
        replay["rounds"][0]["endTick"] = 1400
        replay["events"] = [
            replay_event("ct-smoke", "smoke", 120, player_id="ct-1", side="CT"),
            replay_event("plant", "bomb_planted", 1200, player_id="t-entry", side="T"),
        ]
        self.assertFalse(any(event["category"] == "utility" for event in analyze_replay(replay)))

    def test_parser_kill_reference_points_at_the_victim_event(self):
        replay = self.death_replay()
        replay["events"] = [{
            "id": "kill-100", "type": "kill", "tick": 100, "roundNumber": 1,
            "playerIds": ["ct-1", "t-entry"], "metadata": {"victimId": "t-entry"},
        }]
        event = find_untraded_deaths(replay)[0]
        self.assertEqual(event["structured_context_json"]["relatedEventIds"], ["kill-100"])
        self.assertEqual(event["player_id"], "t-entry")


if __name__ == "__main__":
    unittest.main()
