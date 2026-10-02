"""The shared buy-type cases in fixtures/round-economy/, run against the backend port.

frontend/lib/round-economy-fixtures.test.mjs runs the same files against the
review page's lib/round-economy.ts; both must produce every case's `expected`
exactly.
"""

import json
import unittest
from pathlib import Path
from typing import Any

from app.analysis.round_economy import buy_kinds_by_side, round_economies
from app.parser.replay_contract import normalize_replay_contract

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "round-economy"
REQUIRED_CASES = {
    "pistol-round-one",
    "half-time-switch-in-a-later-demo",
    "overtime-switch-is-not-a-pistol",
    "switch-at-round-16-is-a-pistol",
    "buy-kind-thresholds",
    "fewer-than-five-players",
    "unknown-states",
    "buy-tick-fallback",
    "v1-replay-without-player-states",
    "no-known-kind",
    "no-sides-anywhere",
}


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    ]


class RoundEconomyFixtureTest(unittest.TestCase):
    def test_the_shared_fixture_directory_has_every_case(self) -> None:
        names = [case["name"] for case in load_cases()]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(REQUIRED_CASES, set(names))
        for path in FIXTURE_DIR.glob("*.json"):
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], path.stem)

    def test_every_case_matches_its_expected_outcome(self) -> None:
        for case in load_cases():
            with self.subTest(case=case["name"]):
                self.assertEqual(round_economies(case["replay"]), case["expected"])

    def test_every_replay_is_already_in_the_served_form(self) -> None:
        # Parity is defined on what GET /demos/{id}/replay serves. A case whose
        # rounds or player states that normalization would change would test the
        # backend on different input than the review page.
        for case in load_cases():
            with self.subTest(case=case["name"]):
                replay = case["replay"]
                served = normalize_replay_contract(json.loads(json.dumps(replay)))
                self.assertEqual(served["rounds"], replay["rounds"])
                self.assertEqual(served["tickRate"], replay["tickRate"])
                self.assertEqual(served["playerStates"], replay.get("playerStates", {}))

    def test_the_input_replay_is_not_modified(self) -> None:
        for case in load_cases():
            with self.subTest(case=case["name"]):
                replay = case["replay"]
                before = json.dumps(replay, sort_keys=True)
                round_economies(replay)
                self.assertEqual(json.dumps(replay, sort_keys=True), before)

    def test_buy_kinds_by_side_reads_each_round_by_side(self) -> None:
        cases = {case["name"]: case for case in load_cases()}
        kinds = buy_kinds_by_side(cases["half-time-switch-in-a-later-demo"]["replay"])
        self.assertEqual(kinds[12], {"T": "full", "CT": "eco"})
        # Team A switched to CT at 13: the T kind is now team B's.
        self.assertEqual(kinds[14], {"CT": "half", "T": "full"})
        self.assertEqual(buy_kinds_by_side(cases["unknown-states"]["replay"])[2], {"T": "full", "CT": None})
        self.assertEqual(buy_kinds_by_side(cases["v1-replay-without-player-states"]["replay"]), {})

    def test_malformed_input_never_raises(self) -> None:
        cases = {case["name"]: case for case in load_cases()}
        base = cases["pistol-round-one"]["replay"]
        for broken in (
            {},
            {"rounds": None, "frames": None, "playerStates": None},
            {**base, "frames": [None, 3, {"roundNumber": "1", "players": "x"}]},
            {**base, "playerStates": {"a1": [None, {"tick": "x"}, {"tick": 1150, "equipValue": True}]}},
            {**base, "rounds": [{"roundNumber": None}, {"roundNumber": 2, "startTick": None, "freezeEndTick": None}]},
            {**base, "tickRate": -5},
        ):
            with self.subTest(broken=sorted(broken)):
                self.assertIsInstance(round_economies(broken), list)
                self.assertIsInstance(buy_kinds_by_side(broken), dict)


if __name__ == "__main__":
    unittest.main()
