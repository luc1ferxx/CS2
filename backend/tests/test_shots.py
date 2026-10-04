"""Replay contract v5: ``shots``, each player's gun shots with speed, airborne flag and weapon."""

import copy
import json
import math
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd

from app.parser.demo_parser import parse_demo_file
from app.parser.map_config import get_map_config
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import (
    REPLAY_CONTRACT_VERSION,
    normalize_replay_contract,
    replay_contract_is_current,
)
from app.parser.shots import (
    MAX_SHOT_SPEED,
    SHOT_EVENT,
    SHOT_SOURCE,
    build_shots,
    normalize_shots,
    parse_shots,
    shot_count,
    shot_track_capped,
    weapon_key,
)
from app.services.demo_service.projection import _public_replay_contract

XELEX = "76561198998266210"
OTHER = "76561198000000002"
COLUMNS = ["tick", "user_steamid", "user_name", "user_velocity_X", "user_velocity_Y", "user_is_airborne", "weapon"]
# weapon_fire rows as demoparser2 hands them back (float32 velocity, object steamid).
FIRE_ROWS: list[tuple[Any, ...]] = [
    (1010, XELEX, "xelex", 150.4, 0.0, True, "weapon_ak47"),
    (1000, XELEX, "xelex", 3.0, 4.0, False, "weapon_ak47"),
    (1000, XELEX, "xelex", 3.0, 4.0, False, "weapon_ak47"),  # an identical row: dropped
    (1001, XELEX, "xelex", 0.0, 0.0, False, "weapon_knife_karambit"),
    (1002, XELEX, "xelex", 0.0, 0.0, False, "weapon_knife"),
    (1003, XELEX, "xelex", 0.0, 0.0, False, "weapon_bayonet"),
    (1004, XELEX, "xelex", 0.0, 0.0, False, "weapon_smokegrenade"),
    (1005, XELEX, "xelex", 0.0, 0.0, False, "weapon_flashbang"),
    (1006, XELEX, "xelex", 0.0, 0.0, False, "weapon_hegrenade"),
    (1007, XELEX, "xelex", 0.0, 0.0, False, "weapon_molotov"),
    (1008, XELEX, "xelex", 0.0, 0.0, False, "weapon_incgrenade"),
    (1009, XELEX, "xelex", 0.0, 0.0, False, "weapon_decoy"),
    (1011, XELEX, "xelex", 0.0, 0.0, False, "weapon_c4"),
    (1012, XELEX, "xelex", 0.0, 0.0, False, "weapon_taser"),
    (1013, XELEX, "xelex", math.nan, 10.0, False, "weapon_m4a1_silencer"),  # no velocity: dropped
    (1014, XELEX, "xelex", 10.0, math.inf, False, "weapon_m4a1_silencer"),
    (1020, XELEX, "xelex", 120.0, 160.0, False, "weapon_m4a1_silencer"),
    (1030, OTHER, "other", 2000.0, 0.0, False, "weapon_deagle"),  # clamped to 1000
    (990, OTHER, "other", -70.6, 0.0, True, "weapon_glock"),  # other guns are shots too
]
EXPECTED = {
    XELEX: [[1000, 5, 0, "ak47"], [1010, 150, 1, "ak47"], [1020, 200, 0, "m4a1_silencer"]],
    OTHER: [[990, 71, 1, "glock"], [1030, 1000, 0, "deagle"]],
}


def _fire_frame(rows: list[tuple[Any, ...]] = FIRE_ROWS, *, drop: tuple[str, ...] = ()) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=COLUMNS).astype({
        "tick": "int32", "user_steamid": "object", "user_velocity_X": "float32", "user_velocity_Y": "float32",
        "user_is_airborne": "bool",
    })
    frame["silenced"] = False
    return frame.drop(columns=list(drop))


class FakeFireParser:
    """``parse_event`` over a fixed DataFrame; props named in ``reject`` raise like an unknown prop."""

    def __init__(self, frame: pd.DataFrame | None, reject: tuple[str, ...] = ()):
        self.frame = frame
        self.reject = set(reject)
        self.calls: list[tuple[str, list[str]]] = []

    def parse_event(self, event_name: str, *, player: list[str]) -> pd.DataFrame:
        self.calls.append((event_name, list(player)))
        if self.frame is None or self.reject & set(player):
            raise RuntimeError("unknown prop")
        return self.frame


class ShotExtractionTest(unittest.TestCase):
    def test_gun_shots_come_out_sorted_rounded_and_flagged(self) -> None:
        parser = FakeFireParser(_fire_frame())
        self.assertEqual(parse_shots(parser), EXPECTED)
        self.assertEqual(parser.calls, [(SHOT_EVENT, ["is_airborne", "velocity_X", "velocity_Y"])])
        # Rows from a list of dicts work the same.
        self.assertEqual(build_shots(_fire_frame().to_dict("records")), EXPECTED)

    def test_without_is_airborne_it_retries_with_velocity_alone(self) -> None:
        parser = FakeFireParser(_fire_frame(drop=("user_is_airborne",)), reject=("is_airborne",))
        shots = parse_shots(parser)
        self.assertEqual([props for _, props in parser.calls],
                         [["is_airborne", "velocity_X", "velocity_Y"], ["velocity_X", "velocity_Y"]])
        self.assertEqual(shots, {player: [[row[0], row[1], 0, row[3]] for row in track] for player, track in EXPECTED.items()})

    def test_failures_and_unusable_rows_yield_nothing(self) -> None:
        broken = FakeFireParser(None)
        self.assertEqual(parse_shots(broken), {})
        self.assertEqual(len(broken.calls), 2)
        self.assertEqual(parse_shots(FakeFireParser(_fire_frame([]))), {})
        # Without velocity columns no shot has a speed.
        self.assertEqual(parse_shots(FakeFireParser(_fire_frame(drop=("user_velocity_X",)))), {})
        for junk in (None, [], "rows", 5, [1, "a"]):
            self.assertEqual(build_shots(junk), {})

        class Exploding:
            def parse_event(self, *_: Any, **__: Any) -> Any:
                return pd.DataFrame({"tick": [1], "weapon": ["weapon_ak47"], "user_velocity_X": [object()],
                                     "user_velocity_Y": [1.0], "user_steamid": [XELEX]})

        self.assertEqual(parse_shots(Exploding()), {})

    def test_player_ids_follow_the_frames_rule(self) -> None:
        rows = [
            {"tick": 5, "user_steamid": int(XELEX), "user_name": "xelex", "user_velocity_X": 1.0, "user_velocity_Y": 0.0,
             "weapon": "weapon_ak47"},
            {"tick": 6, "user_steamid": None, "user_name": "  bot   Kev ", "user_velocity_X": 1.0, "user_velocity_Y": 0.0,
             "weapon": "weapon_ak47"},
            # A SteamID64 that went through float64 matches nobody: the name stands in.
            {"tick": 7, "user_steamid": float(XELEX), "user_name": "xelex", "user_velocity_X": 1.0,
             "user_velocity_Y": 0.0, "weapon": "weapon_ak47"},
            {"tick": 8, "user_steamid": "0", "user_name": None, "user_velocity_X": 1.0, "user_velocity_Y": 0.0,
             "weapon": "weapon_ak47"},
        ]
        self.assertEqual(build_shots(rows), {XELEX: [[5, 1, 0, "ak47"]], "bot Kev": [[6, 1, 0, "ak47"]],
                                             "xelex": [[7, 1, 0, "ak47"]]})

    def test_rows_without_a_usable_tick_or_weapon_are_dropped(self) -> None:
        base = {"user_steamid": XELEX, "user_velocity_X": 1.0, "user_velocity_Y": 0.0, "weapon": "weapon_ak47"}
        rows = [{**base, "tick": -1}, {**base, "tick": None}, {**base, "tick": math.nan}, {**base, "tick": True},
                {**base, "tick": 1, "weapon": None}, {**base, "tick": 2, "weapon": "weapon_ak-47"},
                {**base, "tick": 3, "weapon": "weapon_" + "a" * 33}, {**base, "tick": 4, "weapon": ""},
                {**base, "tick": 5, "user_velocity_Y": True}, {**base, "tick": 9}]
        self.assertEqual(build_shots(rows), {XELEX: [[9, 1, 0, "ak47"]]})

    def test_caps_rows_per_player_and_players(self) -> None:
        rows = [{"tick": tick, "user_steamid": XELEX, "user_velocity_X": 0.0, "user_velocity_Y": 0.0,
                 "weapon": "weapon_ak47"} for tick in range(10, 0, -1)]
        with patch("app.parser.shots.MAX_SHOTS_PER_PLAYER", 4):
            track = build_shots(rows)[XELEX]
            self.assertEqual([row[0] for row in track], [1, 2, 3, 4])  # the earliest shots
            self.assertTrue(shot_track_capped(track))
        players = [{"tick": 1, "user_steamid": str(index + 1), "user_velocity_X": 0.0, "user_velocity_Y": 0.0,
                    "weapon": "weapon_ak47"} for index in range(5)]
        with patch("app.parser.shots.MAX_SHOT_PLAYERS", 3):
            self.assertEqual(sorted(build_shots(players)), ["1", "2", "3"])

    def test_weapon_keys(self) -> None:
        for raw, expected in (("weapon_ak47", "ak47"), ("weapon_m4a1_silencer", "m4a1_silencer"), ("deagle", "deagle"),
                              (" WEAPON_AWP ", "awp"), ("weapon_revolver", "revolver"), ("weapon_knife_m9_bayonet", None),
                              ("weapon_knife_t", None), ("weapon_bayonet", None), ("weapon_taser", None),
                              ("weapon_c4", None), ("weapon_decoy", None), ("weapon_", None), (None, None), (7, None)):
            self.assertEqual(weapon_key(raw), expected, raw)


class NormalizeShotsTest(unittest.TestCase):
    def test_well_formed_tracks_pass_through(self) -> None:
        self.assertEqual(normalize_shots(copy.deepcopy(EXPECTED)), EXPECTED)
        # Two different shots at one tick both stay.
        same_tick = {"p": [[5, 10, 0, "ak47"], [5, 10, 0, "deagle"]]}
        self.assertEqual(normalize_shots(same_tick), same_tick)

    def test_sorts_dedupes_clamps_and_drops_malformed_rows(self) -> None:
        raw = {"p": [
            [30, 5000, 3, "awp"], [10, -5, 0, "ak47"], [20, 100, 1, "ak47"], [10, -5, 0, "ak47"], (15, 1, 0, "ak47"),
            [True, 1, 0, "ak47"], [1, 1.5, 0, "ak47"], [1, 1, "0", "ak47"], [-1, 1, 0, "ak47"], [1, 1, -1, "ak47"],
            [1, 1, 0, "AK47"], [1, 1, 0, "knife_karambit"], [1, 1, 0, "smokegrenade"], [1, 1, 0, "a" * 33],
            [1, 1, 0], [1, 1, 0, "ak47", "extra"], "junk", None, {"tick": 1},
        ]}
        self.assertEqual(normalize_shots(raw), {"p": [[10, 0, 0, "ak47"], [15, 1, 0, "ak47"], [20, 100, 1, "ak47"],
                                                      [30, MAX_SHOT_SPEED, 1, "awp"]]})

    def test_junk_yields_empty_and_bad_players_are_skipped(self) -> None:
        for junk in (None, [], "shots", 5, [[1, 1, 0, "ak47"]]):
            self.assertEqual(normalize_shots(junk), {})
        value = {"": [[1, 1, 0, "ak47"]], " p ": [[1, 1, 0, "ak47"]], "q": "junk", "r": [], "s": [[1, 1, 0, "c4"]]}
        self.assertEqual(normalize_shots(value), {"p": [[1, 1, 0, "ak47"]]})

    def test_is_idempotent(self) -> None:
        once = normalize_shots({"p": [[3, 2000, 7, "ak47"], [1, 5, 0, "deagle"], [1, 5, 0, "deagle"]]})
        self.assertEqual(normalize_shots(copy.deepcopy(once)), once)
        self.assertEqual(normalize_shots(json.loads(json.dumps(once))), once)

    def test_caps_players_and_rows_and_flags_capped_tracks(self) -> None:
        many = {f"p{index}": [[1, 1, 0, "ak47"]] for index in range(70)}
        self.assertEqual(len(normalize_shots(many)), 64)
        track = [[tick, 0, 0, "ak47"] for tick in range(10)]
        with patch("app.parser.shots.MAX_SHOTS_PER_PLAYER", 4):
            self.assertEqual(normalize_shots({"p": track}), {"p": track[:4]})
            self.assertEqual(normalize_shots({"p": list(reversed(track))}), {"p": track[:4]})
            loaded = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "shots": {"p": track}})
            self.assertEqual(loaded["shots"], {"p": track[:4]})
            self.assertIn("shotsCapped", loaded["diagnostics"]["degradedFields"])
            again = normalize_replay_contract(copy.deepcopy(loaded))
            self.assertIn("shotsCapped", again["diagnostics"]["degradedFields"])
        self.assertEqual(shot_count({"a": [[1, 1, 0, "ak47"]], "b": [[1, 1, 0, "ak47"], [2, 1, 0, "ak47"]]}), 3)
        self.assertEqual(shot_count(None), 0)


def _parsed_match(shots: Any = None) -> dict[str, Any]:
    player = {"id": XELEX, "name": "xelex", "side": "T", "x": -500.0, "y": 300.0, "alive": True, "hp": 100}
    parsed: dict[str, Any] = {
        "mapName": "de_mirage",
        "tickRate": 64,
        "rounds": [{"roundNumber": 1, "startTick": 900, "freezeEndTick": 950, "endTick": 2000}],
        "players": [{"id": XELEX, "name": "xelex", "side": "T"}],
        "frames": [{"tick": 960, "roundNumber": 1, "players": [player]},
                   {"tick": 976, "roundNumber": 1, "players": [player]}],
        "kills": [], "deaths": [], "events": [],
        "playerStates": {XELEX: [{"tick": 960, "money": 800}]},
        "utility": [{"id": "u", "type": "smoke", "throwerId": XELEX, "roundNumber": 1, "throwTick": 960,
                     "points": [{"tick": 960, "x": -500.0, "y": 300.0}]}],
    }
    if shots is not None:
        parsed["shots"] = shots
    return parsed


class ContractV5Test(unittest.TestCase):
    def test_version_is_v5_and_v4_replays_are_owed_the_upgrade(self) -> None:
        self.assertEqual(REPLAY_CONTRACT_VERSION, "replay_contract_v5")
        self.assertTrue(replay_contract_is_current("replay_contract_v5"))
        self.assertFalse(replay_contract_is_current("replay_contract_v4"))

    def test_older_replays_load_with_empty_shots_and_no_flag(self) -> None:
        for version in ("replay_contract_v1", "replay_contract_v2", "replay_contract_v3", "replay_contract_v4", None):
            replay = normalize_replay_contract({"contractVersion": version, "rounds": [], "frames": [],
                                                "players": [], "events": [], "video": {}})
            self.assertEqual(replay["shots"], {})
            diagnostics = replay["diagnostics"]
            self.assertEqual((diagnostics["shotSource"], diagnostics["shotCount"]), (None, 0))
            self.assertNotIn("shots", diagnostics["degradedFields"])

    def test_a_v4_blob_loads_unchanged_with_empty_shots(self) -> None:
        v4 = normalize_parser_output("demo-1", _parsed_match())
        v4["contractVersion"] = "replay_contract_v4"
        v4.pop("shots")
        stored = json.loads(json.dumps(v4))
        loaded = normalize_replay_contract(copy.deepcopy(stored))
        self.assertEqual(loaded["contractVersion"], "replay_contract_v4")
        self.assertEqual(loaded["shots"], {})
        self.assertEqual({key: loaded[key] for key in stored if key not in ("diagnostics", "video")},
                         {key: stored[key] for key in stored if key not in ("diagnostics", "video")})
        self.assertEqual(loaded["diagnostics"]["degradedFields"], [])

    def test_a_v5_parse_without_shots_is_not_degraded(self) -> None:
        loaded = normalize_replay_contract(normalize_parser_output("demo-1", _parsed_match()))
        self.assertEqual(loaded["shots"], {})
        self.assertEqual(loaded["diagnostics"]["degradedFields"], [])
        self.assertEqual((loaded["diagnostics"]["shotSource"], loaded["diagnostics"]["shotCount"]), (None, 0))

    def test_malformed_shots_are_degraded_and_dropped(self) -> None:
        replay = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "shots": [[1, 1, 0, "ak47"]]})
        self.assertEqual(replay["shots"], {})
        self.assertIn("shots", replay["diagnostics"]["degradedFields"])

    def test_parser_output_carries_shots_through_load_and_the_public_replay(self) -> None:
        raw = {XELEX: [[1020, 200, 0, "m4a1_silencer"], [1000, 5, 0, "ak47"], [1000, 5, 0, "ak47"], [1, 1, 0, "c4"]]}
        replay = normalize_parser_output("demo-1", _parsed_match(raw))
        self.assertEqual(replay["contractVersion"], REPLAY_CONTRACT_VERSION)
        self.assertEqual(replay["shots"], {XELEX: [[1000, 5, 0, "ak47"], [1020, 200, 0, "m4a1_silencer"]]})
        loaded = normalize_replay_contract(json.loads(json.dumps(replay)))
        self.assertEqual(loaded["shots"], replay["shots"])
        self.assertEqual((loaded["diagnostics"]["shotSource"], loaded["diagnostics"]["shotCount"]), (SHOT_SOURCE, 2))
        self.assertEqual(loaded["diagnostics"]["degradedFields"], [])
        public = _public_replay_contract(loaded, loaded["video"])
        self.assertEqual(public["shots"], replay["shots"])
        diagnostics = public["diagnostics"]
        assert isinstance(diagnostics, dict)
        self.assertEqual((diagnostics["shotSource"], diagnostics["shotCount"]), ("weapon_fire", 2))
        # The public body is plain JSON.
        self.assertEqual(json.loads(json.dumps(public))["shots"], {XELEX: [[1000, 5, 0, "ak47"],
                                                                         [1020, 200, 0, "m4a1_silencer"]]})

    def test_the_projection_rebuilds_rows_and_never_passes_junk_through(self) -> None:
        loaded = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "shots": EXPECTED})
        loaded["shots"] = {**loaded["shots"], "junk": "x", "short": [[1, 2]]}
        public = _public_replay_contract({**loaded, "demoId": "d", "mapName": "m", "tickRate": 64}, loaded["video"])
        self.assertEqual(public["shots"], {**EXPECTED, "short": []})
        self.assertEqual(_public_replay_contract({**loaded, "shots": None}, loaded["video"])["shots"], {})

    def test_legacy_radar_reprojection_leaves_shots_alone(self) -> None:
        legacy = {"type": "bounds", "minX": -3230.0, "maxX": 1890.0, "minY": -3410.0, "maxY": 1710.0}
        current = get_map_config("de_mirage")
        assert current is not None
        replay = {
            "contractVersion": REPLAY_CONTRACT_VERSION, "mapName": "de_mirage",
            "mapMetadata": {**current, "transform": legacy},
            "frames": [{"tick": 1000, "players": [{"id": XELEX, "x": 50.0, "y": 50.0}]}],
            "shots": copy.deepcopy(EXPECTED),
        }
        loaded = normalize_replay_contract(copy.deepcopy(replay))
        self.assertNotEqual(loaded["frames"][0]["players"][0]["x"], 50.0)  # the frames moved
        self.assertEqual(loaded["shots"], EXPECTED)


class ParseDemoFileShotsTest(unittest.TestCase):
    def _module(self, *, fire: bool) -> Any:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_mirage", "tick_rate": 64, "playback_ticks": 3000}

            def parse_player_info(self) -> list[dict[str, object]]:
                return [{"steamid": int(XELEX), "name": "xelex", "team_num": 2}]

            def parse_event(self, event_name: str, **options: Any) -> Any:
                if event_name == SHOT_EVENT:
                    if not fire:
                        raise RuntimeError("unknown event")
                    if "velocity_X" not in options.get("player", []):
                        raise AssertionError("weapon_fire must be read with the velocity props")
                    return _fire_frame()
                return {
                    "round_start": [{"tick": 900, "total_rounds_played": 0}],
                    "round_freeze_end": [{"tick": 950, "total_rounds_played": 0}],
                    "round_end": [{"tick": 2000, "total_rounds_played": 1, "winner": 3}],
                }.get(event_name, [])

            def parse_ticks(self, props: list[str], *, ticks: list[int] | None = None) -> Any:
                if ticks is None:
                    raise RuntimeError("no usercmd")
                return [{"tick": tick, "steamid": int(XELEX), "name": "xelex", "team_num": 2, "X": -500.0, "Y": 300.0,
                         "health": 100, "is_alive": True} for tick in ticks]

            def parse_grenades(self, **_: object) -> Any:
                raise RuntimeError("no grenades")

        return types.SimpleNamespace(DemoParser=FakeDemoParser)

    def _parse(self, module: Any) -> dict[str, Any]:
        with patch.dict("sys.modules", {"demoparser2": module}), patch(
            "app.parser.demo_parser._validate_demo_file", return_value=None,
        ):
            return parse_demo_file(Path("match.dem"))

    def test_a_full_parse_carries_the_shots_in_the_frames_ids(self) -> None:
        parsed = self._parse(self._module(fire=True))
        self.assertEqual(parsed["shots"], EXPECTED)
        replay = normalize_parser_output("demo-1", parsed)
        self.assertEqual(replay["shots"], EXPECTED)
        frame_ids = {player["id"] for frame in replay["frames"] for player in frame["players"]}
        self.assertIn(XELEX, frame_ids)
        self.assertEqual(normalize_replay_contract(replay)["diagnostics"]["shotCount"], 5)

    def test_a_demo_without_weapon_fire_still_parses(self) -> None:
        parsed = self._parse(self._module(fire=False))
        self.assertEqual(parsed["shots"], {})
        self.assertTrue(parsed["frames"])
        loaded = normalize_replay_contract(normalize_parser_output("demo-1", parsed))
        self.assertIsNone(loaded["diagnostics"]["shotSource"])


if __name__ == "__main__":
    unittest.main()
