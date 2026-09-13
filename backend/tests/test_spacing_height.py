from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.analysis.rules import RuleConfig, find_poor_spacing


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
            "frames": [{"tick": tick, "roundNumber": round_number, "players": players}], "kills": [],
        }

    def flat_pair(self, difference=0):
        return [
            {"id": "one", "name": "One", "side": "T", "x": 40, "y": 40, "z": -400},
            {"id": "two", "name": "Two", "side": "T", "x": 41, "y": 40, "z": -400 + difference},
        ]

    def test_real_nuke_platform_pairs_do_not_become_close_spacing(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/nuke_stacked_height_samples.json").read_text())
        for sample in fixture["samples"]:
            with self.subTest(tick=sample["tick"]):
                replay = self.sample_replay(sample["players"], sample["tick"], sample["roundNumber"])
                # Both players are in the upper-map bucket; the old floor check alone accepts them.
                permissive = find_poor_spacing(replay, RuleConfig(max_stacked_vertical_distance=512))
                self.assertEqual(len(permissive), 1)
                self.assertEqual(permissive[0]["structured_context_json"]["spacingType"], "stacked")
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
        events = find_poor_spacing(self.sample_replay(pair))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["structured_context_json"]["spacingType"], "too_far")
        self.assertNotIn("verticalDistanceWorldUnits", events[0]["structured_context_json"])
        self.assertIn("route and timing for a trade", events[0]["structured_context_json"]["action"])
        self.assertNotIn("leave enough room", events[0]["structured_context_json"]["action"])


if __name__ == "__main__":
    unittest.main()
