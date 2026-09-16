import itertools
import math
import unittest

from app.parser.demo_parser import (
    MAX_SAMPLE_FRAMES,
    _build_bomb_events,
    _build_damage_events,
    _build_frames,
    _build_kills,
    _parse_event_records,
    _parse_tick_records,
    _sample_ticks,
)
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import normalize_replay_contract


class ParserPrecisionTest(unittest.TestCase):
    def test_quarter_second_sampling_includes_both_sides_of_events_and_boundaries(self):
        ticks = _sample_ticks(
            [{"roundNumber": 1, "startTick": 100, "freezeEndTick": 130, "endTick": 1100}],
            [{"tick": 137}, {"tick": 487}, {"tick": "bad"}, {"tick": math.inf}], {}, 64,
        )
        self.assertTrue({100, 130, 136, 137, 486, 487, 1100}.issubset(ticks))
        self.assertLessEqual(max(b - a for a, b in itertools.pairwise(ticks)), 16)

    def test_long_match_regular_sampling_is_bounded_without_discarding_event_ticks(self):
        ticks = _sample_ticks(
            [{"startTick": 0, "endTick": 10_000_000}], [{"tick": 1234567}], {}, 64,
        )
        self.assertLessEqual(len(ticks), MAX_SAMPLE_FRAMES + 4)
        self.assertTrue({1234566, 1234567}.issubset(ticks))

    def test_optional_tick_props_failure_does_not_discard_height(self):
        class Parser:
            def parse_ticks(self, props, ticks):
                if "inventory" in props:
                    raise RuntimeError("optional inventory unsupported")
                return [{"tick": ticks[0], **dict.fromkeys(props, 10)}]

        record = _parse_tick_records(Parser(), [100])[0]
        self.assertEqual(record["Z"], 10)
        self.assertIn("health", record)

    def test_optional_event_height_failure_keeps_event_family(self):
        class Parser:
            def parse_event(self, event, **options):
                if "Z" in options.get("player", []):
                    raise RuntimeError("optional height unsupported")
                return [{"tick": 100, "user_X": 10, "user_Y": 20}]

        records = _parse_event_records(Parser(), "bomb_planted", player=["X", "Y", "Z"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["user_X"], 10)

    def test_bomb_transitions_use_observed_ticks_and_reset_next_round(self):
        rounds = [{"roundNumber": 1, "startTick": 100, "endTick": 190},
                  {"roundNumber": 2, "startTick": 200, "endTick": 300}]
        records = [self.record(tick) for tick in [100, 110, 120, 130, 140, 149, 150, 200]]
        events = _build_bomb_events(
            [{"tick": 140, "user_steamid": "p1", "user_X": 300, "user_Y": 400, "user_Z": -700}],
            [{"tick": 150, "user_X": 999, "user_Y": 999, "user_Z": -200}], [],
            [{"tick": 120, "user_X": 200, "user_Y": 250, "user_Z": -500}],
            [{"tick": 110, "user_steamid": "p1"}, {"tick": 130, "user_steamid": "p1"}],
        )
        frames = _build_frames(records, rounds, 64, events)
        self.assertEqual([frame["bombState"]["status"] for frame in frames],
                         ["unknown", "carried", "dropped", "carried", "planted", "planted", "defused", "unknown"])
        self.assertTrue(frames[1]["players"][0]["hasBomb"])
        self.assertFalse(frames[4]["players"][0]["hasBomb"])
        self.assertEqual(frames[6]["bombState"], {"status": "defused", "x": 300, "y": 400, "z": -700})
        self.assertEqual(frames[7]["roundNumber"], 2)

    def test_explosion_retains_plant_position_and_does_not_guess_if_missing(self):
        rounds = [{"roundNumber": 1, "startTick": 100, "endTick": 300}]
        records = [self.record(140), self.record(150)]
        events = _build_bomb_events(
            [{"tick": 140, "user_X": 10, "user_Y": 20, "user_Z": -700}], [],
            [{"tick": 150, "user_X": 900, "user_Y": 800, "user_Z": -200}],
        )
        frames = _build_frames(records, rounds, 64, events)
        self.assertEqual(frames[-1]["bombState"], {"status": "exploded", "x": 10, "y": 20, "z": -700})
        missing_plant = _build_frames(records, rounds, 64, events[1:])
        self.assertEqual(missing_plant[-1]["bombState"], {"status": "exploded"})

    def test_real_inventory_and_global_flags_recover_missing_event_families(self):
        frames = _build_frames(
            [{**self.record(100), "inventory": ["AK-47", "C4 Explosive"]},
             {**self.record(110), "inventory": [], "is_bomb_dropped": True},
             {**self.record(120), "inventory": [], "is_bomb_planted": True}],
            [{"roundNumber": 1, "startTick": 100, "endTick": 300}], 64,
        )
        self.assertEqual([frame["bombState"]["status"] for frame in frames], ["carried", "dropped", "planted"])
        self.assertEqual(frames[0]["bombState"]["z"], -500)
        self.assertNotIn("x", frames[1]["bombState"])

    def test_nan_bomb_flag_and_dead_carrier_do_not_fabricate_carried_state(self):
        frames = _build_frames(
            [{**self.record(100), "has_bomb": math.nan},
             {**self.record(110), "has_bomb": True, "health": 0, "is_alive": False}],
            [{"roundNumber": 1, "startTick": 100, "endTick": 300}], 64,
        )
        self.assertEqual([frame["bombState"]["status"] for frame in frames], ["unknown", "unknown"])

    def test_normalization_retains_world_z_and_normalizes_bomb_radar_position(self):
        raw = {
            "mapName": "de_dust2", "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 100, "endTick": 300}],
            "frames": [{"tick": 100, "players": [{"id": "p1", "x": -1000, "y": 500, "z": -500}],
                        "bombState": {"status": "dropped", "x": -1000, "y": 500, "z": -500, "raw": "discard"}}],
            "events": [{"type": "bomb_dropped", "tick": 100, "x": -1000, "y": 500, "z": -500}],
        }
        replay = normalize_parser_output("precision", raw)
        player = replay["frames"][0]["players"][0]
        bomb = replay["frames"][0]["bombState"]
        self.assertEqual(player["z"], -500)
        self.assertEqual((player["x"], player["y"]), (bomb["x"], bomb["y"]))
        self.assertEqual(bomb["z"], -500)
        self.assertNotIn("raw", bomb)
        self.assertEqual(replay["events"][0]["z"], -500)
        loaded = normalize_replay_contract(replay)
        self.assertEqual(loaded["events"][0]["z"], -500)
        self.assertEqual(loaded["events"][0]["type"], "bomb_dropped")

    def test_nonfinite_optional_height_is_omitted(self):
        parsed = {"mapName": "unknown", "frames": [{"tick": 100,
            "players": [{"id": "p1", "x": 10, "y": 20, "z": math.inf}],
            "bombState": {"status": "planted", "x": 10, "y": 20, "z": math.nan}}],
            "events": [{"type": "smoke", "tick": 100, "x": 10, "y": 20, "z": math.inf}]}
        replay = normalize_parser_output("bad-height", parsed)
        self.assertNotIn("z", replay["frames"][0]["players"][0])
        self.assertNotIn("z", replay["frames"][0]["bombState"])
        self.assertNotIn("z", replay["events"][0])

    def test_stored_legacy_frames_without_height_and_new_malformed_height_load(self):
        replay = normalize_replay_contract({"frames": [
            {"tick": 100, "players": [{"id": "p1", "x": 10, "y": 20}], "bombState": {"status": "carried"}},
            {"tick": 101, "players": [{"id": "p1", "x": 10, "y": 20, "z": math.inf}],
             "bombState": {"status": "defused", "z": math.nan, "x": math.inf, "y": 30}},
        ]})
        self.assertNotIn("z", replay["frames"][0]["players"][0])
        self.assertEqual(replay["frames"][0]["bombState"], {"status": "carried"})
        self.assertNotIn("z", replay["frames"][1]["players"][0])
        self.assertEqual(replay["frames"][1]["bombState"], {"status": "defused"})

    def test_inter_round_frames_stay_in_preceding_round_until_next_start(self):
        frames = _build_frames([self.record(tick) for tick in [150, 199, 200]],
            [{"roundNumber": 2, "startTick": 100, "endTick": 140},
             {"roundNumber": 3, "startTick": 200, "endTick": 300}], 64)
        self.assertEqual([frame["roundNumber"] for frame in frames], [2, 2, 3])

    def test_kill_position_uses_victim_height(self):
        kill = _build_kills([{"tick": 100, "attacker_steamid": "p1", "user_steamid": "p2",
                              "user_X": 10, "user_Y": 20, "user_Z": -700, "attacker_Z": -400}])[0]
        self.assertEqual((kill["x"], kill["y"], kill["z"]), (10, 20, -700))

    def test_damage_side_matches_attacker_while_position_marks_victim(self):
        event = _build_damage_events([{"tick": 100, "attacker_steamid": "p1", "user_steamid": "p2",
            "attacker_team_num": 2, "user_team_num": 3, "user_X": 10, "user_Y": 20, "user_Z": -700}])[0]
        self.assertEqual(event["side"], "T")
        self.assertEqual(event["playerId"], "p1")
        self.assertEqual(event["z"], -700)
        missing_attacker_side = _build_damage_events([{"tick": 100, "attacker_steamid": "p1",
                                                      "user_steamid": "p2", "user_team_num": 3}])[0]
        self.assertNotIn("side", missing_attacker_side)

    @staticmethod
    def record(tick):
        return {"tick": tick, "steamid": "p1", "name": "Player", "X": 10, "Y": 20,
                "Z": -500, "team_num": 2, "health": 100, "is_alive": True}


if __name__ == "__main__":
    unittest.main()
