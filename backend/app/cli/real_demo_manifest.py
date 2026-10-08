"""Pin what the parser, normalizer and analyzer make of the local real demos.

`backend/tests/fixtures/real_demo_manifest.json` holds one entry per local
sample `.dem`, keyed by file size and a sha256 prefix. An entry holds only
aggregate numbers -- map, round/frame/event counts, per-event-type counts and
how many of them carry a position, equipment/utility/input/shot counts, the
final score, per-rule suggestion counts and a sha256 of the analyzer output --
never player ids, names, positions or any other demo content.

The pipeline is the worker's: `parse_demo_file`, the JSON hand-off from the
parse child, `normalize_parser_output` and `analyze_replay`. A parser fallback
that silently drops a prop (the tick-prop or event-prop retry chains) moves the
"with position" and frame-row counts here even when every event count stays
the same.

    python -m app.cli.real_demo_manifest check [DEMO ...]
    python -m app.cli.real_demo_manifest update [--prune] [DEMO ...]

Run from the repository root with PYTHONPATH=backend; without DEMO it uses the
`*.dem` files in the repository root. `check` skips a demo the manifest does not
know. Regenerate with `update` (and review the diff) after a demoparser2
upgrade, a parser/normalizer or map-transform change, or a bump of the coaching
rules, replay contract or match summary version.
Exit status: 0 when every known demo matches (or the update was written),
1 on a mismatch or when no demo was given or found, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "real_demo_manifest.json"
MANIFEST_SCHEMA = 1
SHA256_PREFIX_LENGTH = 16
# Every uuid5 coaching event id is derived from the demo id; a fixed one keeps
# the analyzer hash independent of where the demo came from.
MANIFEST_DEMO_ID = "real-demo-manifest"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2


def repo_root_demos() -> list[Path]:
    return sorted(path for path in REPO_ROOT.glob("*.dem") if path.is_file())


def demo_key(path: Path, *, sha256_prefix: bool = True) -> dict[str, Any]:
    """The entry key: file size, plus the sha256 prefix unless only the size is wanted."""
    key: dict[str, Any] = {"bytes": path.stat().st_size}
    if sha256_prefix:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        key["sha256Prefix"] = digest.hexdigest()[:SHA256_PREFIX_LENGTH]
    return key


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": MANIFEST_SCHEMA, "demos": []}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"{path.name} is not a schema {MANIFEST_SCHEMA} real-demo manifest")
    return manifest


def write_manifest(manifest: Mapping[str, Any], path: Path = MANIFEST_PATH) -> None:
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def find_entry(manifest: Mapping[str, Any], path: Path) -> dict[str, Any] | None:
    """The manifest entry for this file; hashes it only when its size matches an entry."""
    entries = [entry for entry in manifest.get("demos", []) if isinstance(entry, dict)]
    size = path.stat().st_size
    if not any(entry.get("key", {}).get("bytes") == size for entry in entries):
        return None
    key = demo_key(path)
    return next((entry for entry in entries if entry.get("key") == key), None)


def generated_with() -> dict[str, Any]:
    from app.analysis.version import COACHING_RULES_VERSION
    from app.parser.replay_contract import REPLAY_CONTRACT_VERSION
    from app.services.demo_service.match_summary import MATCH_SUMMARY_VERSION

    try:
        parser_version = metadata.version("demoparser2")
    except metadata.PackageNotFoundError:
        parser_version = "missing"
    return {
        "coachingRulesVersion": COACHING_RULES_VERSION,
        "demoparser2": parser_version,
        "matchSummaryVersion": MATCH_SUMMARY_VERSION,
        "replayContractVersion": REPLAY_CONTRACT_VERSION,
    }


def build_entry(path: Path) -> dict[str, Any]:
    """Parse, normalize and analyze one demo exactly as the worker does; aggregates only."""
    from app.analysis.analyzer import analyze_replay
    from app.parser.demo_parser import parse_demo_file
    from app.parser.normalizer import normalize_parser_output
    from app.services.demo_service.match_summary import build_match_summary

    # The worker reads the parse child's output back from JSON: the same round
    # trip here, so the normalizer sees exactly what it sees in production.
    parsed = json.loads(json.dumps(parse_demo_file(path)))
    replay = normalize_parser_output(MANIFEST_DEMO_ID, parsed)
    coaching = analyze_replay(replay)
    team_names = parsed.get("teamNames") if isinstance(parsed.get("teamNames"), dict) else {}
    return {
        "key": demo_key(path),
        "generatedWith": generated_with(),
        **replay_aggregates(replay),
        "matchSummary": match_summary_aggregates(build_match_summary(replay, team_names), team_names),
        "coaching": coaching_aggregates(coaching),
    }


def replay_aggregates(replay: Mapping[str, Any]) -> dict[str, Any]:
    frames = _dicts(replay.get("frames"))
    rows = [row for frame in frames for row in _dicts(frame.get("players"))]
    events = _dicts(replay.get("events"))
    kills = _dicts(replay.get("kills"))
    rounds = _dicts(replay.get("rounds"))
    states = [entry for track in _mapping(replay.get("playerStates")).values() for entry in _dicts(track)]
    utility = _dicts(replay.get("utility"))
    inputs = _mapping(replay.get("inputs"))
    shots = _mapping(replay.get("shots"))
    shot_rows = [row for track in shots.values() if isinstance(track, list) for row in track if isinstance(row, list)]
    return {
        "mapName": str(replay.get("mapName")),
        "tickRate": replay.get("tickRate"),
        "players": len(_dicts(replay.get("players"))),
        "rounds": {
            "count": len(rounds),
            "withFreezeEnd": sum(1 for item in rounds if item.get("freezeEndTick") is not None),
            "winsBySide": _counts(item.get("winnerSide") for item in rounds),
        },
        "frames": {
            "count": len(frames),
            "withBombState": sum(1 for frame in frames if frame.get("bombState")),
            "playerRows": len(rows),
            "rowsBySide": _counts(row.get("side") for row in rows),
            "rowsWithZ": sum(1 for row in rows if _finite(row.get("z"))),
            "rowsDead": sum(1 for row in rows if row.get("alive") is False),
            "rowsHurt": sum(1 for row in rows if isinstance(row.get("hp"), int) and 0 < row["hp"] < 100),
            "rowsWithBomb": sum(1 for row in rows if row.get("hasBomb") is True),
        },
        "events": {
            "count": len(events),
            "byType": _counts(event.get("type") for event in events),
            "withPositionByType": _counts(event.get("type") for event in events if _has_position(event)),
            "withPlayerByType": _counts(event.get("type") for event in events if event.get("playerId")),
        },
        "kills": {
            "count": len(kills),
            "withPosition": sum(1 for kill in kills if _has_position(kill)),
            "withAttacker": sum(1 for kill in kills if kill.get("attackerId")),
            "withWeapon": sum(1 for kill in kills if kill.get("weapon")),
            "headshots": sum(1 for kill in kills if kill.get("headshot") is True),
        },
        "playerStates": {
            "players": len(_mapping(replay.get("playerStates"))),
            "entries": len(states),
            "withMoney": sum(1 for entry in states if "money" in entry),
            "withArmor": sum(1 for entry in states if "armor" in entry),
            "withWeapon": sum(1 for entry in states if entry.get("weapon")),
            "withGrenades": sum(1 for entry in states if entry.get("grenades")),
            "withEquipValue": sum(1 for entry in states if "equipValue" in entry),
        },
        "utility": {
            "count": len(utility),
            "byType": _counts(throw.get("type") for throw in utility),
            "points": sum(len(_dicts(throw.get("points"))) for throw in utility),
            "withThrowerSide": sum(1 for throw in utility if throw.get("throwerSide")),
            "withThrowOrigin": sum(1 for throw in utility if throw.get("throwOrigin")),
        },
        "inputs": {
            "players": len(inputs),
            "changePoints": sum(len(track) for track in inputs.values() if isinstance(track, list)),
        },
        "shots": {
            "players": len(shots),
            "count": len(shot_rows),
            "airborne": sum(1 for row in shot_rows if len(row) > 2 and isinstance(row[2], int) and row[2] & 1),
            "weapons": len({row[3] for row in shot_rows if len(row) > 3 and isinstance(row[3], str)}),
        },
    }


def coaching_aggregates(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        events,
        key=lambda event: (int(event.get("round_number") or 0), int(event.get("tick_start") or 0), str(event.get("id"))),
    )
    canonical = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return {
        "count": len(events),
        "byRule": _counts(_mapping(event.get("structured_context_json")).get("ruleId") for event in events),
        "bySeverity": _counts(event.get("severity") for event in events),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def match_summary_aggregates(summary: Mapping[str, Any] | None, team_names: Mapping[str, Any]) -> dict[str, Any]:
    """Scores and how many names resolved -- never the team or player names themselves."""
    teams = _dicts(summary.get("teams")) if summary else []
    return {
        "rounds": summary.get("rounds") if summary else None,
        "teams": [{"startSide": team.get("startSide"), "score": team.get("score")} for team in teams],
        "teamsWithName": sum(1 for team in teams if team.get("name")),
        "playersWithTeamName": len(team_names),
    }


def diff_entries(expected: Any, actual: Any, path: str = "") -> list[str]:
    """One line per differing leaf, as `path: expected -> actual`."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines: list[str] = []
        for name in sorted(set(expected) | set(actual)):
            child = f"{path}.{name}" if path else str(name)
            if name not in actual:
                lines.append(f"{child}: {expected[name]!r} -> (missing)")
            elif name not in expected:
                lines.append(f"{child}: (missing) -> {actual[name]!r}")
            else:
                lines.extend(diff_entries(expected[name], actual[name], child))
        return lines
    if expected != actual:
        return [f"{path or '(entry)'}: {expected!r} -> {actual!r}"]
    return []


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _counts(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _has_position(item: Mapping[str, Any]) -> bool:
    return _finite(item.get("x")) and _finite(item.get("y"))


# -- command line ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.real_demo_manifest",
        description="Check or regenerate the aggregate manifest of the local real demos.",
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH, help="Manifest file (default: the test fixture).")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Parse each known demo and compare it with its entry.")
    check.add_argument("demos", nargs="*", type=Path, metavar="DEMO")
    update = commands.add_parser("update", help="Parse each demo and write its entry.")
    update.add_argument("demos", nargs="*", type=Path, metavar="DEMO")
    update.add_argument("--prune", action="store_true", help="Drop entries for demos not given.")
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    demos = _unique_demos(args.demos or repo_root_demos())
    missing = [path for path in demos if not path.is_file()]
    if missing:
        out.write(f"Not a file: {', '.join(str(path) for path in missing)}\n")
        return EXIT_USAGE
    if not demos:
        out.write("No .dem given and none in the repository root.\n")
        return EXIT_MISMATCH
    try:
        manifest = load_manifest(args.manifest)
    except ValueError as exc:
        out.write(f"{exc}\n")
        return EXIT_USAGE
    if args.command == "update":
        return _update(manifest, demos, args.manifest, prune=args.prune, out=out)
    return _check(manifest, demos, out=out)


def _check(manifest: Mapping[str, Any], demos: Sequence[Path], *, out: TextIO) -> int:
    status = EXIT_OK
    checked = 0
    for path in demos:
        expected = find_entry(manifest, path)
        if expected is None:
            out.write(f"skip  {path.name}: not in the manifest\n")
            continue
        checked += 1
        differences = diff_entries(expected, build_entry(path))
        if differences:
            status = EXIT_MISMATCH
            out.write(f"FAIL  {path.name} ({expected.get('mapName')}):\n")
            out.writelines(f"        {line}\n" for line in differences)
        else:
            out.write(f"ok    {path.name} ({expected.get('mapName')})\n")
    if status != EXIT_OK:
        out.write(
            "Review the change; if it is intended, regenerate with\n"
            "  PYTHONPATH=backend python -m app.cli.real_demo_manifest update\n"
        )
    elif not checked:
        out.write("No given demo is in the manifest; nothing checked.\n")
    return status


def _update(
    manifest: Mapping[str, Any],
    demos: Sequence[Path],
    manifest_path: Path,
    *,
    prune: bool,
    out: TextIO,
) -> int:
    fresh = []
    for path in demos:
        entry = build_entry(path)
        fresh.append(entry)
        out.write(f"built {path.name} ({entry['mapName']}, {entry['rounds']['count']} rounds)\n")
    fresh_keys = [entry["key"] for entry in fresh]
    kept = [entry for entry in manifest.get("demos", []) if isinstance(entry, dict) and entry.get("key") not in fresh_keys]
    if kept and not prune:
        for entry in kept:
            out.write(f"kept  {entry.get('mapName')} {entry.get('key')}: not refreshed (its demo was not given)\n")
    entries = fresh if prune else [*kept, *fresh]
    entries.sort(key=lambda entry: (str(entry.get("mapName")), entry["key"]["bytes"]))
    write_manifest({"schema": MANIFEST_SCHEMA, "demos": entries}, manifest_path)
    out.write(f"wrote {len(entries)} entries to {manifest_path.name}\n")
    return EXIT_OK


def _unique_demos(paths: Iterable[Path]) -> list[Path]:
    unique: dict[Path, Path] = {}
    for path in paths:
        unique.setdefault(path.resolve(), path)
    return list(unique.values())


if __name__ == "__main__":
    sys.exit(main())
