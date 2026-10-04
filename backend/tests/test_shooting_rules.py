"""moving_shots and no_counter_strafe, from the replay's recorded gun shots (contract v5 `shots`)."""

import unittest
import uuid

from app.analysis.analyzer import analyze_replay
from app.analysis.rules import DEFAULT_RULE_CONFIG, RuleConfig, find_shooting_issues
from app.analysis.version import COACHING_RULES_VERSION

SHOOTER = "76561198000000001"
ENEMY = "76561198000000002"
MATE = "76561198000000003"
# Round 1 is live from tick 640 to 6400, round 2 from 7000 to 12000 (64 ticks a second).
ROUNDS = [
    {"roundNumber": 1, "startTick": 0, "freezeEndTick": 640, "endTick": 6400, "winnerSide": "CT", "winnerReason": "t_killed"},
    {"roundNumber": 2, "startTick": 6500, "freezeEndTick": 7000, "endTick": 12000, "winnerSide": "T", "winnerReason": "ct_killed"},
]
# AK-47: max speed 215, accurate at or below round(0.34 * 215) = 73.
AK_ACCURATE = 73


def shot(tick, speed=200, weapon="ak47", airborne=False):
    return [tick, speed, 1 if airborne else 0, weapon]


def damage(tick, attacker=SHOOTER, victim=ENEMY, weapon="ak47"):
    return {
        "id": f"damage-{tick}-{attacker}-{victim}",
        "type": "damage",
        "tick": tick,
        "playerId": attacker,
        "metadata": {"attackerId": attacker, "victimId": victim, "weapon": weapon, "damageHealth": 27},
    }


def death_of_shooter(tick, attacker=ENEMY, attacker_name="donk"):
    return {
        "tick": tick, "roundNumber": 1 if tick <= 6400 else 2,
        "attackerId": attacker, "attackerName": attacker_name, "attackerSide": "CT",
        "victimId": SHOOTER, "victimName": "xelex", "victimSide": "T", "weapon": "m4a1",
    }


def frame(tick):
    return {
        "tick": tick,
        "timeSeconds": tick / 64,
        "roundNumber": 1 if tick <= 6400 else 2,
        "players": [
            {"id": SHOOTER, "name": "xelex", "side": "T", "x": 40, "y": 40, "alive": True, "hp": 100},
            {"id": MATE, "name": "mate", "side": "T", "x": 42, "y": 42, "alive": True, "hp": 100},
            {"id": ENEMY, "name": "donk", "side": "CT", "x": 60, "y": 60, "alive": True, "hp": 100},
        ],
        "bombState": {"status": "carried"},
    }


def shooting_replay(shots, *, events=None, kills=(), inputs=None, extra_shots=None):
    # A far-away damage event of another player: the damage family is recorded but hits nothing here.
    events = [damage(6000, attacker=MATE), *(events or [])]
    replay = {
        "demoId": "demo-shots",
        "mapName": "de_mirage",
        "tickRate": 64,
        "rounds": ROUNDS,
        "players": [
            {"id": SHOOTER, "name": "xelex", "side": "T"},
            {"id": MATE, "name": "mate", "side": "T"},
            {"id": ENEMY, "name": "donk", "side": "CT"},
        ],
        "frames": [frame(tick) for tick in [*range(640, 6401, 160), *range(7000, 12001, 160)]],
        "kills": list(kills),
        "deaths": list(kills),
        "events": events,
        "shots": {SHOOTER: shots, **(extra_shots or {})},
    }
    if inputs is not None:
        replay["inputs"] = {SHOOTER: inputs}
    return replay


def cards(replay, config=DEFAULT_RULE_CONFIG):
    return find_shooting_issues(replay, config)


def context(card):
    return card["structured_context_json"]


def raw_id(demo_id, rule_id, player_id, round_number, tick_start, tick_end):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{demo_id}:{rule_id}:{player_id}:{round_number}:{tick_start}:{tick_end}"))


def personal_id(raw, player_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{raw}:{player_id}"))


class MovingShotsTest(unittest.TestCase):
    def test_three_moving_shots_without_a_hit_make_one_card(self):
        found = cards(shooting_replay([shot(1000, 200), shot(1020, 190), shot(1040, 180)]))
        self.assertEqual(len(found), 1)
        card = found[0]
        self.assertEqual(context(card)["ruleId"], "moving_shots")
        self.assertEqual(card["category"], "mechanics")
        self.assertEqual(card["severity"], "low")
        self.assertEqual(card["round_number"], 1)
        self.assertEqual(card["player_id"], SHOOTER)
        self.assertEqual(card["tick_start"], 1000 - 32)
        self.assertEqual(card["tick_end"], 1040 + 16)
        self.assertEqual(context(card)["evidenceTicks"], [1000, 1020, 1040])
        self.assertEqual(context(card)["involvedPlayerIds"], [SHOOTER])
        self.assertEqual(context(card)["evidenceSource"], "recorded_shots")
        expected = {
            "weapon": "ak47", "weaponLabel": "AK-47", "accurateSpeed": AK_ACCURATE, "speed": 200,
            "shotCount": 3, "movingShotCount": 3, "airborne": False, "hit": False, "died": False,
            "side": "T", "occurrencesInRound": 1,
        }
        self.assertEqual({key: context(card)[key] for key in expected}, expected)
        self.assertNotIn("attackerName", context(card))
        self.assertNotIn("keysAtShot", context(card))
        self.assertNotIn("counterStrafe", context(card))

    def test_the_guidance_is_about_stopping_and_carries_no_distance_note(self):
        card = cards(shooting_replay([shot(1000), shot(1020), shot(1040)]))[0]
        self.assertTrue(context(card)["action"].startswith("Stop before you shoot"))
        self.assertTrue(context(card)["limitation"].startswith("Speed comes from the velocity recorded with each shot"))
        self.assertNotIn("straight-line", context(card)["limitation"])
        self.assertNotIn("calibration", context(card)["limitation"])

    def test_two_moving_shots_are_not_enough(self):
        # The first shot opens the fight too fast, so it reads as no_counter_strafe instead.
        found = cards(shooting_replay([shot(1000), shot(1020), shot(1040, 40)]))
        self.assertEqual([context(card)["ruleId"] for card in found], ["no_counter_strafe"])

    def test_an_airborne_shot_is_moving_whatever_its_speed(self):
        found = cards(shooting_replay([shot(1000, 10, airborne=True), shot(1020, 10, airborne=True), shot(1040, 10, airborne=True)]))
        self.assertEqual(context(found[0])["ruleId"], "moving_shots")
        self.assertTrue(context(found[0])["airborne"])
        self.assertEqual(context(found[0])["speed"], 10)

    def test_a_shot_at_the_accurate_speed_is_not_moving(self):
        accurate = [shot(1000, AK_ACCURATE), shot(1020, AK_ACCURATE), shot(1040, AK_ACCURATE)]
        self.assertEqual(cards(shooting_replay(accurate)), [])
        just_above = [shot(1000, AK_ACCURATE + 1), shot(1020, AK_ACCURATE + 1), shot(1040, AK_ACCURATE + 1)]
        self.assertEqual(context(cards(shooting_replay(just_above))[0])["ruleId"], "moving_shots")

    def test_a_hit_within_two_ticks_of_a_moving_shot_clears_the_burst(self):
        shots = [shot(1000), shot(1020), shot(1040)]
        self.assertEqual(cards(shooting_replay(shots, events=[damage(1022)])), [])
        late = cards(shooting_replay(shots, events=[damage(1023)]))
        self.assertEqual(context(late[0])["ruleId"], "moving_shots")
        self.assertFalse(context(late[0])["hit"])

    def test_a_hit_by_a_still_shot_keeps_the_card_and_names_the_victim(self):
        shots = [shot(1000), shot(1020), shot(1040), shot(1060, 20)]
        card = cards(shooting_replay(shots, events=[damage(1061)]))[0]
        self.assertEqual(context(card)["ruleId"], "moving_shots")
        self.assertTrue(context(card)["hit"])
        self.assertEqual(context(card)["shotCount"], 4)
        self.assertEqual(context(card)["movingShotCount"], 3)
        self.assertEqual(context(card)["involvedPlayerIds"], [SHOOTER, ENEMY])

    def test_grenade_fire_and_self_damage_are_no_hits(self):
        shots = [shot(1000), shot(1020), shot(1040)]
        events = [damage(1020, weapon="hegrenade"), damage(1021, weapon="inferno"), damage(1040, victim=SHOOTER)]
        self.assertEqual(context(cards(shooting_replay(shots, events=events))[0])["ruleId"], "moving_shots")


class BurstTest(unittest.TestCase):
    def test_a_gap_over_half_a_second_splits_the_burst(self):
        # 1000-1020 and 1053-1073 are two bursts of two moving shots: no moving_shots card.
        found = cards(shooting_replay([shot(1000), shot(1020), shot(1053), shot(1073)]))
        self.assertEqual([context(card)["ruleId"] for card in found], ["no_counter_strafe"])
        self.assertEqual(context(found[0])["shotCount"], 2)
        # Exactly half a second still continues it.
        found = cards(shooting_replay([shot(1000), shot(1020), shot(1052)]))
        self.assertEqual(context(found[0])["ruleId"], "moving_shots")

    def test_a_weapon_change_or_another_gun_splits_the_burst(self):
        self.assertEqual(
            [context(card)["ruleId"] for card in cards(shooting_replay([shot(1000), shot(1020), shot(1040, weapon="deagle")]))],
            ["no_counter_strafe"],
        )
        self.assertEqual(
            [context(card)["ruleId"] for card in cards(shooting_replay([shot(1000), shot(1010, weapon="glock"), shot(1020), shot(1040)]))],
            ["no_counter_strafe"],
        )

    def test_unjudged_guns_are_never_judged(self):
        for weapon in ("mp9", "p250", "nova", "negev", "glock", "usp_silencer"):
            with self.subTest(weapon):
                self.assertEqual(cards(shooting_replay([shot(1000, 240, weapon), shot(1010, 240, weapon), shot(1020, 240, weapon)])), [])

    def test_each_judged_weapon_uses_its_own_accurate_speed(self):
        # AWP 200 -> 68; a lone fast AWP opener that misses is a no_counter_strafe card.
        card = cards(shooting_replay([shot(1000, 150, "awp")]))[0]
        self.assertEqual((context(card)["ruleId"], context(card)["weaponLabel"], context(card)["accurateSpeed"]), ("no_counter_strafe", "AWP", 68))
        self.assertEqual(cards(shooting_replay([shot(1000, 108, "awp")])), [], "68 + 40 is not above the margin")
        card = cards(shooting_replay([shot(1000, 120, "m4a1_silencer")]))[0]
        self.assertEqual((context(card)["weaponLabel"], context(card)["accurateSpeed"]), ("M4A1-S", 76))
        card = cards(shooting_replay([shot(1000, 160, "deagle")]))[0]
        self.assertEqual((context(card)["weaponLabel"], context(card)["accurateSpeed"]), ("Desert Eagle", 78))

    def test_shots_outside_a_live_round_are_ignored(self):
        freeze_time = [shot(600), shot(610), shot(620)]
        after_the_round = [shot(6420), shot(6440), shot(6460)]
        self.assertEqual(cards(shooting_replay([*freeze_time, *after_the_round])), [])

    def test_the_card_window_starts_no_earlier_than_freeze_end(self):
        card = cards(shooting_replay([shot(650), shot(670), shot(690)]))[0]
        self.assertEqual(card["tick_start"], 640)
        card = cards(shooting_replay([shot(6370), shot(6380), shot(6390)]))[0]
        self.assertEqual(card["tick_end"], 6400, "clamped to the round end")


class NoCounterStrafeTest(unittest.TestCase):
    def test_a_fast_first_shot_that_missed_opens_a_card(self):
        card = cards(shooting_replay([shot(1000, 150), shot(1020, 30)]))[0]
        self.assertEqual(context(card)["ruleId"], "no_counter_strafe")
        self.assertEqual(context(card)["speed"], 150)
        self.assertEqual(context(card)["movingShotCount"], 1)
        self.assertEqual(context(card)["evidenceTicks"], [1000])
        self.assertTrue(context(card)["action"].startswith("Practise counter-strafing"))

    def test_the_first_shot_needs_the_margin_or_the_air(self):
        self.assertEqual(cards(shooting_replay([shot(1000, AK_ACCURATE + 40)])), [])
        self.assertEqual(len(cards(shooting_replay([shot(1000, AK_ACCURATE + 41)]))), 1)
        airborne = cards(shooting_replay([shot(1000, 20, airborne=True)]))[0]
        self.assertTrue(context(airborne)["airborne"])

    def test_only_an_opener_counts(self):
        # Any gun fired in the second before the burst means it does not open the fight.
        self.assertEqual(cards(shooting_replay([shot(940, 10, "glock"), shot(1000, 150)])), [])
        self.assertEqual(cards(shooting_replay([shot(936, 10, "glock"), shot(1000, 150)])), [], "exactly one second before")
        self.assertEqual(len(cards(shooting_replay([shot(935, 10, "glock"), shot(1000, 150)]))), 1)

    def test_a_hit_clears_it_unless_the_player_died_within_two_seconds(self):
        shots = [shot(1000, 150), shot(1020, 20)]
        self.assertEqual(cards(shooting_replay(shots, events=[damage(1020)])), [])
        died = cards(shooting_replay(shots, events=[damage(1020)], kills=[death_of_shooter(1020 + 128)]))[0]
        self.assertEqual(context(died)["ruleId"], "no_counter_strafe")
        self.assertTrue(context(died)["hit"])
        self.assertTrue(context(died)["died"])
        self.assertEqual(context(died)["attackerName"], "donk")
        self.assertEqual(context(died)["involvedPlayerIds"], [SHOOTER, ENEMY])
        self.assertEqual(cards(shooting_replay(shots, events=[damage(1020)], kills=[death_of_shooter(1020 + 129)])), [])

    def test_a_missed_burst_records_a_later_death_only_inside_the_window(self):
        card = cards(shooting_replay([shot(1000, 150)], kills=[death_of_shooter(1300)]))[0]
        self.assertFalse(context(card)["died"])
        self.assertNotIn("attackerName", context(card))
        card = cards(shooting_replay([shot(1000, 150)], kills=[death_of_shooter(1100, attacker_name=None)]))[0]
        self.assertTrue(context(card)["died"])
        self.assertNotIn("attackerName", context(card))


class OnePerRoundTest(unittest.TestCase):
    def test_moving_shots_wins_over_an_earlier_no_counter_strafe_and_counts_both(self):
        shots = [shot(1000, 150), shot(2000), shot(2020), shot(2040)]
        found = cards(shooting_replay(shots))
        self.assertEqual(len(found), 1)
        self.assertEqual(context(found[0])["ruleId"], "moving_shots")
        self.assertEqual(found[0]["tick_start"], 2000 - 32)
        self.assertEqual(context(found[0])["occurrencesInRound"], 2)

    def test_a_moving_shots_burst_is_never_also_a_no_counter_strafe(self):
        # Opener with a fast first shot and three missed moving shots: one finding, counted once.
        found = cards(shooting_replay([shot(1000), shot(1020), shot(1040)]))
        self.assertEqual([context(card)["ruleId"] for card in found], ["moving_shots"])
        self.assertEqual(context(found[0])["occurrencesInRound"], 1)

    def test_the_earliest_no_counter_strafe_is_kept(self):
        found = cards(shooting_replay([shot(1000, 150), shot(2000, 170)]))
        self.assertEqual(len(found), 1)
        self.assertEqual(context(found[0])["speed"], 150)
        self.assertEqual(context(found[0])["occurrencesInRound"], 2)

    def test_each_round_and_each_player_gets_its_own_card(self):
        found = cards(shooting_replay(
            [shot(1000, 150), shot(7500, 150)],
            extra_shots={ENEMY: [shot(1500, 160, "m4a1")]},
        ))
        self.assertEqual(
            sorted((card["player_id"], card["round_number"]) for card in found),
            sorted([(SHOOTER, 1), (SHOOTER, 2), (ENEMY, 1)]),
        )
        self.assertEqual(next(context(card)["side"] for card in found if card["player_id"] == ENEMY), "CT")


class PerMatchCapTest(unittest.TestCase):
    def kept(self, shots, *, limit, kills=()):
        found = cards(shooting_replay(shots, kills=kills), RuleConfig(no_counter_strafe_max_per_match=limit))
        return sorted((card["round_number"], context(card)["ruleId"]) for card in found)

    def test_the_default_keeps_three_no_counter_strafe_cards_a_match(self):
        self.assertEqual(DEFAULT_RULE_CONFIG.no_counter_strafe_max_per_match, 3)

    def test_a_card_where_the_player_died_is_kept_before_a_faster_one(self):
        shots = [shot(1000, 170), shot(7500, 120)]
        self.assertEqual(self.kept(shots, limit=1, kills=[death_of_shooter(7600)]), [(2, "no_counter_strafe")])

    def test_then_the_fastest_first_shot_then_the_earliest(self):
        self.assertEqual(self.kept([shot(1000, 130), shot(7500, 170)], limit=1), [(2, "no_counter_strafe")])
        self.assertEqual(self.kept([shot(1000, 150), shot(7500, 150)], limit=1), [(1, "no_counter_strafe")])
        self.assertEqual(self.kept([shot(1000, 130), shot(7500, 170)], limit=2), [(1, "no_counter_strafe"), (2, "no_counter_strafe")])

    def test_moving_shots_is_not_capped(self):
        moving = [shot(1000), shot(1020), shot(1040), shot(7500), shot(7520), shot(7540)]
        self.assertEqual(self.kept(moving, limit=0), [(1, "moving_shots"), (2, "moving_shots")])
        self.assertEqual(self.kept([shot(1000, 150), shot(7500), shot(7520), shot(7540)], limit=0), [(2, "moving_shots")])

    def test_the_cap_is_per_player(self):
        found = cards(
            shooting_replay([shot(1000, 150)], extra_shots={ENEMY: [shot(1500, 160, "m4a1")]}),
            RuleConfig(no_counter_strafe_max_per_match=1),
        )
        self.assertEqual(sorted(card["player_id"] for card in found), sorted([SHOOTER, ENEMY]))


class InputsTest(unittest.TestCase):
    def keys(self, inputs, shots=None):
        card = cards(shooting_replay(shots or [shot(1000, 150)], inputs=inputs))[0]
        return context(card).get("keysAtShot"), context(card).get("counterStrafe")

    def test_held_keys_are_listed_in_w_a_s_d_order(self):
        self.assertEqual(self.keys([[900, 512]]), (["A"], False))
        self.assertEqual(self.keys([[900, 1024 | 8 | 1]]), (["W", "D"], False))
        self.assertEqual(self.keys([[900, 0]]), ([], False))

    def test_an_opposite_key_pressed_just_before_the_shot_is_a_counter_strafe(self):
        # 0.15 s is 10 ticks: D pressed at 995 while A was held at the window's start.
        self.assertEqual(self.keys([[900, 512], [995, 1024]]), (["D"], True))
        self.assertEqual(self.keys([[900, 512], [995, 1024], [998, 0]]), ([], True))
        # Pressed too early to count.
        self.assertEqual(self.keys([[900, 512], [950, 1024]]), (["D"], False))
        # A new key that is not the opposite one.
        self.assertEqual(self.keys([[900, 512], [995, 512 | 8]]), (["W", "A"], False))

    def test_no_track_or_a_track_starting_after_the_shot_leaves_the_fields_out(self):
        self.assertEqual(self.keys(None), (None, None))
        self.assertEqual(self.keys([[1100, 512]]), (None, None))
        self.assertEqual(self.keys(["junk", [None, 4]]), (None, None))


class BestEffortTest(unittest.TestCase):
    def test_no_shots_gives_no_cards(self):
        for shots in (None, {}, [], "x"):
            with self.subTest(shots=shots):
                replay = shooting_replay([])
                replay["shots"] = shots
                self.assertEqual(cards(replay), [])

    def test_a_replay_without_damage_events_has_the_family_missing(self):
        replay = shooting_replay([shot(1000), shot(1020), shot(1040)])
        replay["events"] = [event for event in replay["events"] if event["type"] != "damage"]
        self.assertEqual(cards(replay), [])

    def test_malformed_rows_are_skipped_and_rows_are_sorted(self):
        rows = [
            shot(1040), "junk", [1030], [None, 200, 0, "ak47"], [1035, float("nan"), 0, "ak47"],
            [1036, 200, 0, ""], [True, 200, 0, "ak47"], [1037, -5, 0, "ak47"], shot(1000), shot(1020),
        ]
        found = cards(shooting_replay(rows))
        self.assertEqual(context(found[0])["evidenceTicks"], [1000, 1020, 1040])

    def test_junk_never_raises(self):
        base = shooting_replay([shot(1000), shot(1020), shot(1040)])
        broken = {
            "shots keyed oddly": {**base, "shots": {None: [shot(1000)], SHOOTER: "x"}},
            "inputs not a dict": {**base, "inputs": ["x"]},
            "rounds missing": {**base, "rounds": None},
            "events junk": {**base, "events": [None, {"type": "damage"}, {"type": "damage", "tick": 1000, "metadata": "x"}]},
            "deaths junk": {**base, "deaths": [None, {"victimId": SHOOTER}]},
        }
        for label, replay in broken.items():
            with self.subTest(label):
                self.assertIsInstance(cards(replay), list)
                self.assertIsInstance(analyze_replay(replay), list)

    def test_thresholds_come_from_the_rule_config(self):
        shots = [shot(1000), shot(1020), shot(1040)]
        self.assertEqual(cards(shooting_replay(shots), RuleConfig(moving_shots_min=4))[0]["structured_context_json"]["ruleId"], "no_counter_strafe")
        self.assertEqual(cards(shooting_replay(shots), RuleConfig(shot_weapon_max_speeds=(("deagle", 230),))), [])


class AnalyzerIntegrationTest(unittest.TestCase):
    def test_the_analyzer_emits_a_personal_card_with_a_deterministic_id(self):
        replay = shooting_replay([shot(1000), shot(1020), shot(1040)])
        first = [event for event in analyze_replay(replay) if event["category"] == "mechanics"]
        second = [event for event in analyze_replay(replay) if event["category"] == "mechanics"]
        self.assertEqual(len(first), 1)
        card = first[0]
        self.assertEqual(card["id"], personal_id(raw_id("demo-shots", "moving_shots", SHOOTER, 1, 968, 1056), SHOOTER))
        self.assertEqual([event["id"] for event in second], [card["id"]])
        self.assertEqual(card["player_name"], "xelex")
        self.assertEqual(context(card)["targetPlayerId"], SHOOTER)

    def test_a_death_card_and_a_shooting_card_of_one_player_and_round_both_survive(self):
        replay = shooting_replay([shot(1000, 150)], kills=[death_of_shooter(1100)])
        rules = sorted(context(event)["ruleId"] for event in analyze_replay(replay) if event["player_id"] == SHOOTER)
        self.assertEqual(rules, ["no_counter_strafe", "untraded_death"])

    def test_the_rules_version_was_bumped_for_the_new_rules(self):
        self.assertEqual(COACHING_RULES_VERSION, "coaching_rules_v3")


if __name__ == "__main__":
    unittest.main()
