"""Replay contract v4: ``throwOrigin``, the thrower's pose at release, per throw."""

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
from app.parser.utility_tracks import (
    THROW_POSE_TICK_OFFSET,
    build_throw_origins,
    normalize_throw_origin,
    normalize_utility,
    throw_pose_ticks,
    with_throw_origins,
)
from app.services.demo_service.projection import _public_replay_contract

# xertioN's round-10 smoke on the Mirage sample: the projectile's first tick is
# 77300; the rows below are demoparser2's at 77299 and 77300 (float32 values).
XERTION = 76561198193174134
OTHER = 76561198000000002
THROW_TICK = 77300
POSE_ROWS: list[dict[str, Any]] = [
    {"tick": 77299, "steamid": XERTION, "name": "xertioN", "X": 1377.260620, "Y": -115.654846, "Z": -132.609375,
     "pitch": -20.32814, "yaw": 163.278137, "is_airborne": True},
    {"tick": 77300, "steamid": XERTION, "name": "xertioN", "X": 1376.158813, "Y": -119.321106, "Z": -129.343750,
     "pitch": -20.32814, "yaw": 163.278137, "is_airborne": True},
    {"tick": 77299, "steamid": OTHER, "name": "other", "X": 10.0, "Y": 20.0, "Z": 30.0,
     "pitch": 0.0, "yaw": 0.0, "is_airborne": False},
]
# The pose one tick before the projectile; speed = the 3.83 u it moved in that tick x 64.
EXPECTED_ORIGIN = {"x": 1377.26, "y": -115.65, "z": -132.61, "pitch": -20.33, "yaw": 163.28,
                   "speed": 245.0, "airborne": True}


def _frame(rows: list[dict[str, Any]], *, drop: tuple[str, ...] = ()) -> pd.DataFrame:
    frame = pd.DataFrame(rows).astype({"steamid": "uint64", "X": "float32", "Y": "float32", "Z": "float32",
                                       "pitch": "float32", "yaw": "float32"})
    return frame.drop(columns=list(drop))


def _throw(throw_id: str = "utility-smoke-431-77300", thrower: Any = str(XERTION), tick: int = THROW_TICK,
           **extra: Any) -> dict[str, Any]:
    return {"id": throw_id, "type": "smoke", "throwerId": thrower, "throwerName": "xertioN", "roundNumber": 10,
            "throwTick": tick, "detonateTick": tick + 100, "endTick": tick + 1152,
            "points": [{"tick": tick, "x": 1353.8, "y": -112.3, "z": -53.0},
                       {"tick": tick + 100, "x": 300.0, "y": -400.0, "z": -100.0}], **extra}


class FakePoseParser:
    """``parse_ticks`` over a fixed DataFrame; props named in ``reject`` raise like an unknown prop."""

    def __init__(self, frame: pd.DataFrame | None, reject: tuple[str, ...] = ()):
        self.frame = frame
        self.reject = set(reject)
        self.calls: list[tuple[list[str], list[int]]] = []

    def parse_ticks(self, props: list[str], *, ticks: list[int]) -> pd.DataFrame:
        self.calls.append((list(props), list(ticks)))
        if self.frame is None or self.reject & set(props):
            raise RuntimeError("unknown prop")
        columns = [column for column in [*props, "tick", "steamid", "name"] if column in self.frame.columns]
        return self.frame.loc[self.frame["tick"].isin(ticks), columns]


class ThrowOriginExtractionTest(unittest.TestCase):
    def test_pose_ticks_are_each_throw_tick_and_the_one_before(self) -> None:
        self.assertEqual(THROW_POSE_TICK_OFFSET, 1)
        throws = [_throw(), _throw("b", tick=500), _throw("c", tick=500), _throw("d", thrower=None, tick=900),
                  _throw("e", tick=0), {"junk": True}]
        self.assertEqual(throw_pose_ticks(throws), [0, 499, 500, 77299, 77300])  # no 899/900: no thrower
        self.assertEqual(throw_pose_ticks([]), [])

    def test_the_tick_before_the_throw_is_preferred_and_gives_the_speed(self) -> None:
        parser = FakePoseParser(_frame(POSE_ROWS))
        throws = [_throw()]
        result = with_throw_origins(parser, throws, 64)
        self.assertEqual(result[0]["throwOrigin"], EXPECTED_ORIGIN)
        self.assertEqual(parser.calls, [(["X", "Y", "Z", "pitch", "yaw", "is_airborne"], [77299, 77300])])
        self.assertNotIn("throwOrigin", throws[0])  # the input list is not mutated
        self.assertEqual({key: value for key, value in result[0].items() if key != "throwOrigin"}, throws[0])

    def test_without_the_row_before_it_falls_back_to_the_throw_tick_without_a_speed(self) -> None:
        rows = [row for row in POSE_ROWS if row["tick"] == THROW_TICK]
        origin = build_throw_origins(_frame(rows), [_throw()], 64)[0]["throwOrigin"]
        self.assertEqual(origin, {"x": 1376.16, "y": -119.32, "z": -129.34, "pitch": -20.33, "yaw": 163.28,
                                  "airborne": True})
        # A row before the throw without a usable pose counts as missing too.
        unusable = [{**POSE_ROWS[0], "Z": math.nan}, POSE_ROWS[1]]
        self.assertEqual(build_throw_origins(_frame(unusable), [_throw()], 64)[0]["throwOrigin"], origin)

    def test_throws_without_a_thrower_or_without_rows_stay_without_an_origin(self) -> None:
        throws = [_throw("nobody", thrower=None), _throw("absent", thrower="76561198000000099"), _throw()]
        result = build_throw_origins(_frame(POSE_ROWS), throws, 64)
        self.assertIs(result[0], throws[0])
        self.assertIs(result[1], throws[1])
        self.assertEqual(result[2]["throwOrigin"], EXPECTED_ORIGIN)
        # Rows from a list of dicts work the same; a SteamID column that lost its precision matches nobody.
        self.assertEqual(build_throw_origins(POSE_ROWS, [_throw()], 64)[0]["throwOrigin"], EXPECTED_ORIGIN)
        floats = [{**row, "steamid": float(row["steamid"])} for row in POSE_ROWS]
        self.assertNotIn("throwOrigin", build_throw_origins(floats, [_throw()], 64)[0])

    def test_missing_optional_columns_retry_then_leave_the_field_out(self) -> None:
        # The parser rejects is_airborne: one retry without it, the pose and speed still come out.
        parser = FakePoseParser(_frame(POSE_ROWS, drop=("is_airborne",)), reject=("is_airborne",))
        origin = with_throw_origins(parser, [_throw()], 64)[0]["throwOrigin"]
        self.assertEqual(origin, {key: value for key, value in EXPECTED_ORIGIN.items() if key != "airborne"})
        self.assertEqual([props for props, _ in parser.calls],
                         [["X", "Y", "Z", "pitch", "yaw", "is_airborne"], ["X", "Y", "Z", "pitch", "yaw"]])
        # A frame without an angle column: no origin at all (all five are required).
        no_yaw = build_throw_origins(_frame(POSE_ROWS, drop=("yaw",)), [_throw()], 64)
        self.assertNotIn("throwOrigin", no_yaw[0])

    def test_a_failing_parser_returns_the_throws_unchanged(self) -> None:
        throws = [_throw()]
        parser = FakePoseParser(None)
        self.assertIs(with_throw_origins(parser, throws, 64), throws)
        self.assertEqual(len(parser.calls), 2)
        # No throws with a thrower: no parse at all.
        idle = FakePoseParser(_frame(POSE_ROWS))
        self.assertEqual(with_throw_origins(idle, [_throw(thrower=None)], 64), [_throw(thrower=None)])
        self.assertEqual(idle.calls, [])


class NormalizeThrowOriginTest(unittest.TestCase):
    def test_rounds_clamps_and_wraps(self) -> None:
        base = {"x": 1.234, "y": -5.678, "z": 0.0, "pitch": -20.32814, "yaw": 163.278137}
        self.assertEqual(normalize_throw_origin(base), {"x": 1.23, "y": -5.68, "z": 0.0, "pitch": -20.33, "yaw": 163.28})
        for raw, expected in ((180.0, 180.0), (-180.0, 180.0), (540.0, 180.0), (-190.0, 170.0), (190.0, -170.0),
                              (359.999, 0.0), (-179.999, 180.0), (-0.001, 0.0), (720, 0.0)):
            yaw = normalize_throw_origin({**base, "yaw": raw})
            assert yaw is not None
            self.assertEqual(yaw["yaw"], expected, raw)
            self.assertEqual(math.copysign(1.0, yaw["yaw"]), 1.0 if expected >= 0 else -1.0, raw)  # no -0.0
        for raw, expected in ((120.0, 90.0), (-95.0, -90.0), (89.996, 90.0)):
            pitch = normalize_throw_origin({**base, "pitch": raw})
            assert pitch is not None
            self.assertEqual(pitch["pitch"], expected)

    def test_drops_the_field_unless_position_and_angles_are_finite_numbers(self) -> None:
        base = {"x": 1.0, "y": 2.0, "z": 3.0, "pitch": 4.0, "yaw": 5.0}
        self.assertIsNone(normalize_throw_origin(None))
        self.assertIsNone(normalize_throw_origin([1, 2, 3]))
        for key in base:
            for junk in (None, math.nan, math.inf, "1.5", True):
                self.assertIsNone(normalize_throw_origin({**base, key: junk}), (key, junk))
            self.assertIsNone(normalize_throw_origin({k: v for k, v in base.items() if k != key}), key)

    def test_speed_and_airborne_are_dropped_alone_when_bad(self) -> None:
        base = {"x": 1.0, "y": 2.0, "z": 3.0, "pitch": 4.0, "yaw": 5.0}
        self.assertEqual(normalize_throw_origin({**base, "speed": 245.0081, "airborne": False}),
                         {**base, "speed": 245.0, "airborne": False})
        self.assertEqual(normalize_throw_origin({**base, "speed": 0, "airborne": True}),
                         {**base, "speed": 0.0, "airborne": True})
        for speed in (-1.0, math.nan, math.inf, "5", True, None):
            self.assertEqual(normalize_throw_origin({**base, "speed": speed}), base, speed)
        for airborne in (1, 0, "true", None):
            self.assertEqual(normalize_throw_origin({**base, "airborne": airborne}), base, airborne)
        # Unknown keys never survive; a clean origin passes through unchanged.
        clean = normalize_throw_origin({**base, "speed": 120.0, "airborne": False, "secret": "x"})
        self.assertEqual(clean, {**base, "speed": 120.0, "airborne": False})
        self.assertEqual(normalize_throw_origin(clean), clean)

    def test_normalize_utility_copies_a_valid_origin_and_drops_a_bad_one(self) -> None:
        raw = [_throw("a", throwOrigin=EXPECTED_ORIGIN), _throw("b", tick=THROW_TICK + 10, throwOrigin={"x": 1}),
               _throw("c", tick=THROW_TICK + 20)]
        throws = normalize_utility(raw, [], 64)
        self.assertEqual([("throwOrigin" in throw) for throw in throws], [True, False, False])
        self.assertEqual(throws[0]["throwOrigin"], EXPECTED_ORIGIN)
        self.assertEqual(normalize_utility(copy.deepcopy(throws), [], 64), throws)


def _parsed_match(origin: dict[str, Any] | None) -> dict[str, Any]:
    thrower = str(XERTION)
    player = {"id": thrower, "name": "xertioN", "side": "T", "x": 1377.26, "y": -115.65, "z": -132.6,
              "alive": True, "hp": 100}
    return {
        "mapName": "de_mirage",
        "tickRate": 64,
        "rounds": [{"roundNumber": 10, "startTick": 72771, "freezeEndTick": 74051, "endTick": 78270}],
        "players": [{"id": thrower, "name": "xertioN", "side": "T"}],
        "frames": [{"tick": 77296, "roundNumber": 10, "players": [player]},
                   {"tick": 77312, "roundNumber": 10, "players": [player]}],
        "kills": [], "deaths": [], "events": [],
        "playerStates": {thrower: [{"tick": 77296, "money": 800}]},
        "utility": [_throw() if origin is None else _throw(throwOrigin=origin)],
    }


class ContractV4Test(unittest.TestCase):
    def test_version_is_v4_and_v3_replays_are_owed_the_upgrade(self) -> None:
        self.assertEqual(REPLAY_CONTRACT_VERSION, "replay_contract_v4")
        self.assertTrue(replay_contract_is_current("replay_contract_v4"))
        self.assertFalse(replay_contract_is_current("replay_contract_v3"))

    def test_parser_output_keeps_the_origin_in_world_units_and_counts_it(self) -> None:
        replay = normalize_parser_output("demo-1", _parsed_match(EXPECTED_ORIGIN))
        self.assertEqual(replay["contractVersion"], "replay_contract_v4")
        throw = replay["utility"][0]
        self.assertEqual(throw["throwOrigin"], EXPECTED_ORIGIN)  # not projected like the points
        self.assertLessEqual(throw["points"][0]["x"], 100.0)
        loaded = normalize_replay_contract(json.loads(json.dumps(replay)))
        self.assertEqual(loaded["utility"], replay["utility"])
        self.assertEqual(loaded["diagnostics"]["throwOriginCount"], 1)
        self.assertNotIn("throwOrigin", loaded["diagnostics"]["degradedFields"])
        public = _public_replay_contract(loaded, loaded["video"])
        utility = public["utility"]
        assert isinstance(utility, list)
        self.assertEqual(utility[0]["throwOrigin"], EXPECTED_ORIGIN)
        diagnostics = public["diagnostics"]
        assert isinstance(diagnostics, dict)
        self.assertEqual(diagnostics["throwOriginCount"], 1)

    def test_a_v4_parse_without_origins_is_not_degraded(self) -> None:
        loaded = normalize_replay_contract(normalize_parser_output("demo-1", _parsed_match(None)))
        self.assertNotIn("throwOrigin", loaded["utility"][0])
        self.assertEqual(loaded["diagnostics"]["throwOriginCount"], 0)
        self.assertEqual(loaded["diagnostics"]["degradedFields"], [])

    def test_a_v3_blob_loads_unchanged(self) -> None:
        v3 = normalize_parser_output("demo-1", _parsed_match(None))
        v3["contractVersion"] = "replay_contract_v3"
        stored = json.loads(json.dumps(v3))
        loaded = normalize_replay_contract(copy.deepcopy(stored))
        self.assertEqual(loaded["contractVersion"], "replay_contract_v3")
        self.assertEqual(loaded["utility"], stored["utility"])
        self.assertEqual((loaded["diagnostics"]["utilityCount"], loaded["diagnostics"]["throwOriginCount"]), (1, 0))
        self.assertEqual(loaded["diagnostics"]["degradedFields"], [])

    def test_legacy_radar_reprojection_leaves_the_origin_alone(self) -> None:
        legacy = {"type": "bounds", "minX": -3230.0, "maxX": 1890.0, "minY": -3410.0, "maxY": 1710.0}
        current = get_map_config("de_mirage")
        assert current is not None
        replay = {
            "contractVersion": REPLAY_CONTRACT_VERSION, "mapName": "de_mirage",
            "mapMetadata": {**current, "transform": legacy},
            "utility": [_throw(throwOrigin=EXPECTED_ORIGIN,
                               points=[{"tick": THROW_TICK, "x": 50.0, "y": 50.0}])],
        }
        loaded = normalize_replay_contract(copy.deepcopy(replay))
        self.assertNotEqual(loaded["utility"][0]["points"][0]["x"], 50.0)  # the points moved
        self.assertEqual(loaded["utility"][0]["throwOrigin"], EXPECTED_ORIGIN)  # world units stay


class ParseDemoFileThrowOriginTest(unittest.TestCase):
    def _module(self, *, poses: bool) -> Any:
        thrower = XERTION
        projectile = pd.DataFrame([
            {"grenade_type": "CSmokeGrenadeProjectile", "grenade_entity_id": 431, "x": 1353.8 - 10 * step,
             "y": -112.3, "z": -53.0, "tick": THROW_TICK + step, "steamid": thrower, "name": "xertioN"}
            for step in range(40)
        ]).astype({"steamid": "uint64"})

        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_mirage", "tick_rate": 64, "playback_ticks": 79000}

            def parse_player_info(self) -> list[dict[str, object]]:
                return [{"steamid": thrower, "name": "xertioN", "team_num": 2}]

            def parse_event(self, event_name: str, **_: object) -> Any:
                return {
                    "round_start": [{"tick": 72771, "total_rounds_played": 9}],
                    "round_freeze_end": [{"tick": 74051, "total_rounds_played": 9}],
                    "round_end": [{"tick": 78270, "total_rounds_played": 10, "winner": 3}],
                }.get(event_name, [])

            def parse_ticks(self, props: list[str], *, ticks: list[int] | None = None) -> Any:
                if ticks is None:
                    raise RuntimeError("no usercmd")
                if "pitch" in props:
                    if not poses:
                        raise RuntimeError("unknown prop")
                    return _frame(POSE_ROWS).loc[lambda frame: frame["tick"].isin(ticks)]
                return [{"tick": tick, "steamid": thrower, "name": "xertioN", "team_num": 2, "X": 1377.0, "Y": -115.0,
                         "health": 100, "is_alive": True} for tick in ticks]

            def parse_grenades(self, **_: object) -> Any:
                return projectile

        return types.SimpleNamespace(DemoParser=FakeDemoParser)

    def _parse(self, module: Any) -> dict[str, Any]:
        with patch.dict("sys.modules", {"demoparser2": module}), patch(
            "app.parser.demo_parser._validate_demo_file", return_value=None,
        ):
            return parse_demo_file(Path("match.dem"))

    def test_a_full_parse_attaches_the_origin(self) -> None:
        parsed = self._parse(self._module(poses=True))
        self.assertEqual([throw["throwTick"] for throw in parsed["utility"]], [THROW_TICK])
        self.assertEqual(parsed["utility"][0]["throwOrigin"], EXPECTED_ORIGIN)
        replay = normalize_parser_output("demo-1", parsed)
        self.assertEqual(replay["utility"][0]["throwOrigin"], EXPECTED_ORIGIN)

    def test_a_demo_without_pose_props_still_parses_its_throws(self) -> None:
        parsed = self._parse(self._module(poses=False))
        self.assertEqual(len(parsed["utility"]), 1)
        self.assertNotIn("throwOrigin", parsed["utility"][0])
        self.assertTrue(parsed["frames"])


if __name__ == "__main__":
    unittest.main()
