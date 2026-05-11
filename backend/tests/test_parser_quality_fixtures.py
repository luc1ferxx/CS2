import unittest

from app.analysis.analyzer import analyze_replay
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import normalize_replay_contract
from fixtures.parser_quality import (
    coaching_evidence_replay_blob,
    legacy_replay_blob_without_events,
    malformed_optional_parser_fields,
    missing_event_families_replay_blob,
    replay_contract_v1_blob_with_parser_events,
)


class ParserQualityFixturesTest(unittest.TestCase):
    def test_legacy_replay_blob_normalizes_without_events(self) -> None:
        replay = normalize_replay_contract(legacy_replay_blob_without_events())

        self.assertEqual(replay["events"], [])
        self.assertEqual(replay["diagnostics"]["contractVersion"], "legacy")
        self.assertTrue(replay["diagnostics"]["normalizedLegacy"])
        self.assertEqual(replay["diagnostics"]["parserEventCount"], 0)
        self.assertEqual(replay["diagnostics"]["roundCount"], 1)
        self.assertIn("events", replay["diagnostics"]["missingFields"])

    def test_replay_contract_v1_fixture_keeps_parser_event_families_stable(self) -> None:
        replay = normalize_replay_contract(replay_contract_v1_blob_with_parser_events())

        self.assertEqual(replay["contractVersion"], "replay_contract_v1")
        self.assertEqual(
            [event["type"] for event in replay["events"]],
            ["kill", "bomb_planted", "smoke", "flash"],
        )
        self.assertEqual(replay["diagnostics"]["parserEventCount"], 4)
        self.assertEqual(replay["diagnostics"]["eventFamilyCounts"]["combat"], 1)
        self.assertEqual(replay["diagnostics"]["eventFamilyCounts"]["objective"], 1)
        self.assertEqual(replay["diagnostics"]["eventFamilyCounts"]["utility"], 2)
        self.assertEqual(replay["diagnostics"]["missingEventFamilies"], ["damage"])

    def test_malformed_optional_parser_fields_are_best_effort(self) -> None:
        replay = normalize_parser_output("malformed-parser", malformed_optional_parser_fields())

        self.assertEqual(replay["demoId"], "malformed-parser")
        self.assertEqual(replay["rounds"][0]["startTick"], 128)
        self.assertEqual(replay["rounds"][0]["endTick"], 256)
        self.assertEqual([player["id"] for player in replay["players"]], ["ct-1", "t-entry"])
        self.assertEqual([event["type"] for event in replay["events"]], ["smoke"])
        self.assertEqual(replay["events"][0]["id"], "smoke-220-event")

    def test_missing_event_families_report_compact_diagnostics(self) -> None:
        replay = normalize_replay_contract(missing_event_families_replay_blob())

        self.assertEqual(replay["diagnostics"]["parserEventCount"], 2)
        self.assertEqual(replay["diagnostics"]["eventFamilyCounts"]["objective"], 1)
        self.assertEqual(replay["diagnostics"]["eventFamilyCounts"]["utility"], 1)
        self.assertEqual(replay["diagnostics"]["missingEventFamilies"], ["combat", "damage"])

    def test_analyzer_evidence_links_parser_events(self) -> None:
        events = analyze_replay(coaching_evidence_replay_blob())
        weak_utility = [
            event for event in events
            if event["structured_context_json"]["ruleId"] == "weak_utility_before_execute"
        ]

        self.assertEqual(len(weak_utility), 1)
        context = weak_utility[0]["structured_context_json"]
        self.assertEqual(context["ruleId"], "weak_utility_before_execute")
        self.assertEqual(context["involvedPlayerIds"], ["t-entry", "t-support"])
        self.assertEqual(context["evidenceTicks"], [900, 1200])
        self.assertEqual(context["relatedEventIds"], ["plant-a", "smoke-execute"])


if __name__ == "__main__":
    unittest.main()
