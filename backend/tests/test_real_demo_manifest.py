"""The real-demo manifest: aggregates only, and what the local demos parse to.

`backend/tests/fixtures/real_demo_manifest.json` pins aggregate counts for the
four local sample demos (app/cli/real_demo_manifest.py). The shape test always
runs: it keeps the file to counts, enumerations, versions and hashes, so no
`update` can ever commit player ids, names or other demo content.

The demo test parses every `*.dem` in the repository root whose size and
sha256 prefix match an entry (about 10 s each) and compares the whole entry. It
is opt-in with REAL_DEMO_MANIFEST_CHECK=1; scripts/verify.sh sets it whenever
the repository root holds a `.dem`. A demo the manifest does not know is
skipped, never failed.
"""

import os
import re
import unittest
from pathlib import Path
from typing import Any

from app.analysis.rules import _REVIEW_GUIDANCE
from app.cli import real_demo_manifest as manifest_cli
from app.parser.replay_contract import PARSER_EVENT_TYPES
from app.parser.utility_tracks import SIDES, UTILITY_TYPES

REGENERATE = "PYTHONPATH=backend python -m app.cli.real_demo_manifest update"

# Count dicts keyed by an enumeration, by their path inside an entry.
ENUMERATED_KEYS: dict[str, set[str]] = {
    "rounds.winsBySide": set(SIDES),
    "frames.rowsBySide": set(SIDES),
    "events.byType": set(PARSER_EVENT_TYPES),
    "events.withPositionByType": set(PARSER_EVENT_TYPES),
    "events.withPlayerByType": set(PARSER_EVENT_TYPES),
    "utility.byType": set(UTILITY_TYPES),
    "coaching.byRule": set(_REVIEW_GUIDANCE),
    "coaching.bySeverity": {"low", "medium", "high"},
}
# Every string value an entry may hold, by path.
STRING_VALUES: dict[str, re.Pattern[str]] = {
    "key.sha256Prefix": re.compile(r"[0-9a-f]{16}"),
    "coaching.sha256": re.compile(r"[0-9a-f]{64}"),
    "mapName": re.compile(r"de_[a-z0-9_]{1,32}|unknown"),
    "generatedWith.coachingRulesVersion": re.compile(r"coaching_rules_v\d{1,4}"),
    "generatedWith.replayContractVersion": re.compile(r"replay_contract_v\d{1,4}"),
    "generatedWith.demoparser2": re.compile(r"\d{1,4}(\.\d{1,4}){1,3}|missing"),
    "matchSummary.teams.startSide": re.compile("|".join(SIDES)),
}


def _skeleton() -> dict[str, Any]:
    """An entry's fields with every count dict empty: the shape each entry must have."""
    return {
        "key": {"bytes": 0, "sha256Prefix": ""},
        "generatedWith": manifest_cli.generated_with(),
        **manifest_cli.replay_aggregates({}),
        "matchSummary": manifest_cli.match_summary_aggregates(None, {}),
        "coaching": manifest_cli.coaching_aggregates([]),
    }


class RealDemoManifestShapeTest(unittest.TestCase):
    """Runs everywhere, demos or not."""

    def setUp(self) -> None:
        self.manifest = manifest_cli.load_manifest()

    def test_manifest_has_unique_keyed_entries(self) -> None:
        self.assertEqual(self.manifest["schema"], manifest_cli.MANIFEST_SCHEMA)
        self.assertEqual(set(self.manifest), {"schema", "demos"})
        demos = self.manifest["demos"]
        self.assertTrue(demos)
        keys = [(entry["key"]["bytes"], entry["key"]["sha256Prefix"]) for entry in demos]
        self.assertEqual(len(keys), len(set(keys)))

    def test_entries_hold_only_aggregates(self) -> None:
        skeleton = _skeleton()
        for entry in self.manifest["demos"]:
            with self.subTest(key=entry.get("key")):
                self._check_shape(entry, skeleton, "")

    def _check_shape(self, value: Any, template: Any, path: str) -> None:
        if path in ENUMERATED_KEYS:
            self.assertIsInstance(value, dict, path)
            self.assertLessEqual(set(value), ENUMERATED_KEYS[path], path)
            for count in value.values():
                self.assertIsInstance(count, int, path)
            return
        if isinstance(template, dict):
            self.assertIsInstance(value, dict, path)
            self.assertEqual(set(value), set(template), path or "(entry)")
            for name, child in value.items():
                self._check_shape(child, template[name], f"{path}.{name}" if path else name)
            return
        if path == "matchSummary.teams":
            self.assertIsInstance(value, list, path)
            for team in value:
                self.assertEqual(set(team), {"startSide", "score"}, path)
                self._check_leaf(team["startSide"], f"{path}.startSide")
                self.assertIsInstance(team["score"], int, path)
            return
        self._check_leaf(value, path)

    def _check_leaf(self, value: Any, path: str) -> None:
        if isinstance(value, str):
            pattern = STRING_VALUES.get(path)
            self.assertIsNotNone(pattern, f"{path} holds a string: {value!r}")
            assert pattern is not None
            self.assertRegex(value, rf"\A(?:{pattern.pattern})\Z", path)
            return
        self.assertTrue(value is None or (isinstance(value, int) and not isinstance(value, bool)), path)


@unittest.skipUnless(
    os.getenv("REAL_DEMO_MANIFEST_CHECK") == "1",
    "set REAL_DEMO_MANIFEST_CHECK=1 to parse the repo-root demos against the manifest",
)
class RealDemoManifestTest(unittest.TestCase):
    def test_repo_root_demos_match_their_entries(self) -> None:
        demos: list[Path] = manifest_cli.repo_root_demos()
        if not demos:
            self.skipTest("no .dem in the repo root")
        manifest = manifest_cli.load_manifest()
        for path in demos:
            with self.subTest(demo=path.name):
                expected = manifest_cli.find_entry(manifest, path)
                if expected is None:
                    self.skipTest(f"{path.name} is not in the manifest")
                differences = manifest_cli.diff_entries(expected, manifest_cli.build_entry(path))
                if differences:
                    self.fail(
                        f"{path.name} ({expected['mapName']}) no longer matches its manifest entry:\n  "
                        + "\n  ".join(differences)
                        + f"\nIf the change is intended, regenerate the manifest: {REGENERATE}"
                    )


if __name__ == "__main__":
    unittest.main()
