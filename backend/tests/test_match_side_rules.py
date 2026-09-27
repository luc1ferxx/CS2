"""The shared side-rule cases in fixtures/match-rules/, run against the backend.

frontend/lib/match-side-rules.test.mjs runs the same files against the review
page's implementation; both must produce every case's `expected` exactly.
"""

import json
import unittest
from pathlib import Path
from typing import Any

from app.parser.replay_contract import normalize_replay_contract
from app.services.demo_service.match_summary import (
    MATCH_SUMMARY_VERSION,
    build_match_summary,
    match_side_rules,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "match-rules"
KILL_METADATA_KEYS = ("attackerId", "attackerSide", "victimId", "victimSide")


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    ]


class MatchSideRulesFixtureTest(unittest.TestCase):
    def test_the_shared_fixture_directory_has_every_case(self) -> None:
        names = [case["name"] for case in load_cases()]
        self.assertGreaterEqual(len(names), 11)
        self.assertEqual(len(names), len(set(names)))
        for path in FIXTURE_DIR.glob("*.json"):
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], path.stem)

    def test_every_case_matches_its_expected_outcome(self) -> None:
        for case in load_cases():
            with self.subTest(case=case["name"]):
                self.assertEqual(match_side_rules(case["replay"]).outcome(), case["expected"])

    def test_the_stored_summary_follows_the_same_rule(self) -> None:
        for case in load_cases():
            with self.subTest(case=case["name"]):
                summary = build_match_summary(case["replay"])
                expected = case["expected"]["teams"]
                if not expected:
                    self.assertIsNone(summary)
                    continue
                assert summary is not None
                self.assertEqual(
                    [(team["key"], team["startSide"], team["score"]) for team in summary["teams"]],
                    [(team["key"], team["startSide"], team["score"]) for team in expected],
                )
                self.assertEqual(summary["rounds"], len(case["expected"]["sidesByRound"]))
                self.assertEqual(summary["version"], MATCH_SUMMARY_VERSION)

    def test_every_replay_is_already_in_the_served_form(self) -> None:
        # Parity is defined on what GET /demos/{id}/replay serves. A case whose
        # rounds or events that normalization would change would test the
        # backend on different input than the review page.
        for case in load_cases():
            with self.subTest(case=case["name"]):
                replay = case["replay"]
                served = normalize_replay_contract(json.loads(json.dumps(replay)))
                self.assertEqual(
                    [{key: item[key] for key in raw} for raw, item in zip(replay["rounds"], served["rounds"], strict=True)],
                    replay["rounds"],
                )
                self.assertEqual(_event_keys(served["events"]), _event_keys(replay["events"]))
                self.assertEqual(
                    [{key: value for key, value in frame.items() if key != "bombState"} for frame in served["frames"]],
                    replay["frames"],
                )


def _event_keys(events: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            event["id"],
            event["type"],
            event["tick"],
            event["roundNumber"],
            tuple(event.get("metadata", {}).get(key) for key in KILL_METADATA_KEYS),
        )
        for event in events
    ]


if __name__ == "__main__":
    unittest.main()
