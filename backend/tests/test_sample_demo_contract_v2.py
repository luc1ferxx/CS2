"""Opt-in check of replay contract v2/v3 against real demos.

Skipped unless ``REPLAY_V2_SAMPLE_CHECK=1``. It parses every ``*.dem`` in the
repo root (git-ignored samples) plus ``SAMPLE_DEMO_PATH`` when set, and checks
the v2 extras against the demo's own events: one track per detonation, landing
points on the detonate position, money/weapon ranges and the size budget; and
the v3 key inputs' shape and size.
"""

import json
import math
import os
import unittest
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SIZE_BUDGET = 1.25
# v3 inputs on top of that: ~4 % on a full Mirage match.
INPUTS_BUDGET = 0.08


def _sample_demos() -> list[Path]:
    paths = sorted(REPO_ROOT.glob("*.dem"))
    extra = os.getenv("SAMPLE_DEMO_PATH")
    if extra and Path(extra).is_file():
        paths.append(Path(extra))
    return paths


def _between_rounds(tick: int, replay: dict[str, Any]) -> bool:
    """A detonation in a pause between rounds, from a throw normalize_utility leaves out."""
    from app.parser.utility_tracks import POST_ROUND_THROW_SECONDS

    started = [item for item in replay["rounds"] if item["startTick"] <= tick]
    if not started:
        return False
    current = max(started, key=lambda item: item["startTick"])
    fuse_seconds = 5
    limit = current["endTick"] + (POST_ROUND_THROW_SECONDS + fuse_seconds) * replay["tickRate"]
    return bool(current["endTick"] > current["startTick"] and tick > limit)


def _size(value: Any) -> int:
    return len(json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8"))


@unittest.skipUnless(os.getenv("REPLAY_V2_SAMPLE_CHECK") == "1", "set REPLAY_V2_SAMPLE_CHECK=1 to parse real demos")
class SampleDemoContractV2Test(unittest.TestCase):
    def test_sample_demos(self) -> None:
        from app.parser.demo_parser import parse_demo_file
        from app.parser.normalizer import normalize_parser_output

        demos = _sample_demos()
        if not demos:
            self.skipTest("no .dem in the repo root")
        for path in demos:
            with self.subTest(demo=path.name):
                replay = normalize_parser_output("sample", parse_demo_file(path))
                self._check(replay)

    def _check(self, replay: dict[str, Any]) -> None:
        utility = replay["utility"]
        states = replay["playerStates"]
        self.assertTrue(utility)
        self.assertTrue(states)

        throws = Counter(throw["type"] for throw in utility)
        detonations = Counter(
            event["type"] for event in replay["events"]
            if event["type"] in throws and not _between_rounds(event["tick"], replay)
        )
        for utility_type in ("smoke", "flash", "he"):
            self.assertEqual(throws[utility_type], detonations[utility_type], utility_type)
        # Fire: one track per molotov; the few extra ones burst in the air.
        self.assertGreaterEqual(throws["molotov"], detonations["molotov"])
        self.assertLessEqual(throws["molotov"], detonations["molotov"] * 1.1 + 2)

        scale = replay["mapMetadata"].get("worldUnitsPerPercent") or {"x": 1.0, "y": 1.0}
        events = {
            (event["type"], event.get("playerId"), event["tick"]): event
            for event in replay["events"]
            if "x" in event and event["type"] in throws
        }
        matched = 0
        for throw in utility:
            self.assertLessEqual(len(throw["points"]), 120)
            self.assertLessEqual(throw["throwTick"], throw["detonateTick"])
            self.assertLessEqual(throw["detonateTick"], throw["endTick"])
            event = events.get((throw["type"], throw["throwerId"], throw["detonateTick"]))
            if event is None:
                continue
            matched += 1
            landing = throw["points"][-1]
            distance = math.hypot((landing["x"] - event["x"]) * scale["x"], (landing["y"] - event["y"]) * scale["y"])
            self.assertLess(distance, 2.0, throw["id"])
        self.assertGreaterEqual(matched, len(utility) * 0.95)
        self.assertGreaterEqual(sum(1 for throw in utility if throw["throwerSide"]), len(utility) * 0.99)

        entries = [entry for player_entries in states.values() for entry in player_entries]
        money = [entry["money"] for entry in entries if "money" in entry]
        self.assertTrue(money)
        self.assertGreaterEqual(min(money), 0)
        self.assertLessEqual(max(money), 16000)
        weapons = {entry["weapon"] for entry in entries if entry.get("weapon")}
        self.assertTrue(weapons)
        self.assertTrue(all(0 < len(weapon) <= 32 for weapon in weapons))

        v1 = {key: value for key, value in replay.items() if key not in ("playerStates", "utility", "inputs")}
        self.assertLessEqual(_size({key: value for key, value in replay.items() if key != "inputs"}), _size(v1) * SIZE_BUDGET)

        # v3 key inputs: the sample demos are GOTV recordings, which carry usercmd data.
        inputs = replay["inputs"]
        self.assertTrue(inputs)
        frame_ids = {player["id"] for frame in replay["frames"] for player in frame["players"]}
        self.assertLessEqual(set(inputs), frame_ids)
        for track in inputs.values():
            self.assertTrue(all(earlier[0] < later[0] and earlier[1] != later[1] for earlier, later in pairwise(track)))
        self.assertLessEqual(_size(inputs), _size(v1) * INPUTS_BUDGET)


if __name__ == "__main__":
    unittest.main()
