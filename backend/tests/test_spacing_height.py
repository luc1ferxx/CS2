from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.analysis.rules import RuleConfig, find_poor_spacing, scan_spacing_stretches

# Stacked cards need the pair to hold for poor_spacing_min_duration_seconds (3 s);
# the samples repeat the same positions every second for exactly that long.
STRETCH_OFFSETS = (0, 64, 128, 192)


class StackedHeightEvidenceTest(unittest.TestCase):
    def sample_replay(self, pair, tick=1024, round_number=1, map_name="de_nuke"):
        first = pair[0]
        players = [
            *[{**p, "alive": True, "hp": 100} for p in pair],
            {**first, "id": "synthetic-support", "name": "Synthetic support", "x": first["x"] + 8,
             "y": first["y"] + 8, "alive": True, "hp": 100},
        ]
        return {
            "demoId": "height-regression", "mapName": map_name, "tickRate": 64, "players": players,
            "rounds": [{"roundNumber": round_number, "startTick": 0, "freezeEndTick": 0, "endTick": tick + 320}],
            "frames": [
                {"tick": tick + offset, "roundNumber": round_number, "players": players} for offset in STRETCH_OFFSETS
            ],
            "kills": [],
        }

    def flat_pair(self, difference=0):
        return [
            {"id": "one", "name": "One", "side": "T", "x": 40, "y": 40, "z": -400},
            {"id": "two", "name": "Two", "side": "T", "x": 41, "y": 40, "z": -400 + difference},
        ]

    def test_real_nuke_platform_pairs_do_not_become_close_spacing(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/nuke_stacked_height_samples.json").read_text())
        # Measured in world units these pairs are 169-178 units apart, just wider
        # than the default stacked threshold, so both configs widen it by the same
        # amount. The vertical threshold is then the only difference between them,
        # which is what this test is about.
        stacked = RuleConfig(poor_spacing_min_distance=200)
        for sample in fixture["samples"]:
            with self.subTest(tick=sample["tick"]):
                replay = self.sample_replay(sample["players"], sample["tick"], sample["roundNumber"])
                # Both players are in the upper-map bucket; the old floor check alone accepts them.
                permissive = find_poor_spacing(
                    replay, RuleConfig(poor_spacing_min_distance=200, max_stacked_vertical_distance=512)
                )
                self.assertEqual(len(permissive), 1)
                self.assertEqual(permissive[0]["structured_context_json"]["spacingType"], "stacked")
                self.assertEqual(find_poor_spacing(replay, stacked), [])
                self.assertEqual(find_poor_spacing(replay), [])

    def test_flat_pair_and_threshold_boundary_still_produce_explicit_evidence(self):
        for difference in (0, 128):
            with self.subTest(difference=difference):
                events = find_poor_spacing(self.sample_replay(self.flat_pair(difference)))
                self.assertEqual(len(events), 1)
                context = events[0]["structured_context_json"]
                self.assertEqual(context["verticalDistanceWorldUnits"], difference)
                self.assertEqual(context["maxStackedVerticalDistanceWorldUnits"], 128)
                self.assertEqual(context["assessment"], "review_candidate")
                self.assertIn("vertical separation", context["limitation"])
        self.assertEqual(find_poor_spacing(self.sample_replay(self.flat_pair(128.01))), [])

    def test_nuke_cross_floor_and_missing_height_remain_degraded(self):
        for height in (-600, None, float("nan")):
            with self.subTest(height=height):
                pair = self.flat_pair()
                pair[1]["z"] = height
                self.assertEqual(find_poor_spacing(self.sample_replay(pair)), [])

    def test_world_height_filter_applies_without_map_projection_scale(self):
        for map_name in ("de_dust2", "de_mirage", "unknown-map"):
            with self.subTest(map_name=map_name):
                self.assertEqual(find_poor_spacing(self.sample_replay(self.flat_pair(200), map_name=map_name)), [])
                self.assertEqual(len(find_poor_spacing(self.sample_replay(self.flat_pair(), map_name=map_name))), 1)

    def test_same_world_distance_gets_the_same_verdict_on_every_map(self):
        # Radar percent means a different real distance per map: one point is
        # 4.4 * 1024 / 100 world units on Dust II and 7.0 * 1024 / 100 on Nuke.
        # A threshold in world units has to survive that difference, so 160 units
        # apart must read as "not stacked" on both even though the same gap is
        # 3.55 radar points on Dust II and only 2.23 on Nuke.
        for map_name, units_per_percent in (("de_dust2", 4.4 * 1024 / 100), ("de_nuke", 7.0 * 1024 / 100)):
            for world_distance, expected_events in ((60, 1), (160, 0)):
                with self.subTest(map_name=map_name, world_distance=world_distance):
                    pair = self.flat_pair()
                    pair[1]["x"] = pair[0]["x"] + world_distance / units_per_percent
                    events = find_poor_spacing(self.sample_replay(pair, map_name=map_name))
                    self.assertEqual(len(events), expected_events)

    def test_legacy_non_multilevel_replays_keep_best_effort_2d_spacing(self):
        for height in (None, float("nan")):
            with self.subTest(height=height):
                pair = self.flat_pair()
                pair[1]["z"] = height
                events = find_poor_spacing(self.sample_replay(pair, map_name="de_dust2"))
                self.assertEqual(len(events), 1)
                self.assertNotIn("verticalDistanceWorldUnits", events[0]["structured_context_json"])

    def test_height_filter_does_not_change_far_spacing(self):
        pair = self.flat_pair(300)
        pair[1]["x"] = 90
        replay = self.sample_replay(pair)
        # too_far is never a card of its own; the stretch only becomes a reason on a death card.
        self.assertEqual(find_poor_spacing(replay), [])
        stretches = scan_spacing_stretches(replay)
        self.assertEqual([stretch.spacing_type for stretch in stretches], ["too_far"])
        self.assertEqual(stretches[0].focus_id, "two")
        self.assertIsNone(stretches[0].vertical_distance)
        self.assertEqual(stretches[0].last_tick - stretches[0].start_tick, 192)


if __name__ == "__main__":
    unittest.main()
