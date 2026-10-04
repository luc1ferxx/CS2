"""Replay contract v3: per-player key inputs (`inputs`) from the demo's usercmd button state."""

import copy
import json
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.parser.demo_parser import parse_demo_file
from app.parser.normalizer import normalize_parser_output
from app.parser.player_inputs import (
    BUTTON_ATTACK,
    BUTTON_ATTACK2,
    BUTTON_BACK,
    BUTTON_DUCK,
    BUTTON_FORWARD,
    BUTTON_JUMP,
    BUTTON_LEFT,
    BUTTON_RIGHT,
    BUTTON_WALK,
    DISPLAY_MASK,
    INPUT_PROP,
    MAX_INPUT_PLAYERS,
    build_player_inputs,
    normalize_player_inputs,
    parse_player_inputs,
)
from app.parser.replay_contract import REPLAY_CONTRACT_VERSION, normalize_replay_contract
from app.services.demo_service.projection import _public_replay_contract

XELEX = 76561198998266210
OTHER = 76561198000000002
W, A, S, D = BUTTON_FORWARD, BUTTON_LEFT, BUTTON_BACK, BUTTON_RIGHT
DUCK, JUMP, FIRE = BUTTON_DUCK, BUTTON_JUMP, BUTTON_ATTACK
USE = 32  # held but not displayed
SCOREBOARD = 1 << 33  # held but not displayed
# xelex, Spirit vs MOUZ on Mirage, round 10: the key state from each tick on (he dies at 77874).
GROUND_TRUTH = [
    (77756, W | D), (77805, D), (77831, A | D), (77832, A), (77837, A | FIRE), (77841, A | D | FIRE),
    (77842, A | D), (77849, D | DUCK | FIRE), (77855, A | D | DUCK | FIRE), (77857, A | D | DUCK),
    (77858, D | DUCK), (77866, D | DUCK | FIRE), (77870, D | DUCK), (77872, D),
]
DEATH_TICK = 77874
ROUNDS = [
    {"roundNumber": 10, "startTick": 77700, "freezeEndTick": 77700, "endTick": 78270},
    {"roundNumber": 11, "startTick": 78718, "freezeEndTick": 78718, "endTick": 78780},
]
DEATHS = [{"tick": DEATH_TICK, "user_steamid": str(XELEX), "user_name": "xelex"}]
EXPECTED_XELEX = [
    [77700, 0], *[[tick, mask] for tick, mask in GROUND_TRUTH], [DEATH_TICK, 0], [78718, W | JUMP],
]


def _xelex_raw(tick: int) -> int:
    """xelex's raw usercmd button state, with undisplayed bits mixed in."""
    if tick < 77700:
        return W | FIRE  # before the first round: never read
    if tick >= 78718:
        return W | JUMP
    if tick >= DEATH_TICK:
        return W | (FIRE if tick % 2 else 0)  # spectating: drives the camera, not a player
    held = 0
    for start, mask in GROUND_TRUTH:
        if tick >= start:
            held = mask
    if 77720 <= tick <= 77730:
        held |= USE
    if 77810 <= tick <= 77815 or tick == 77740:
        held |= SCOREBOARD
    return held


def _usercmd_frame(*, float_masks: bool = False, drop_ticks: tuple[int, ...] = ()) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for tick in range(77600, 78800):
        if tick not in drop_ticks:
            rows.append({"tick": tick, "steamid": XELEX, "name": "xelex", INPUT_PROP: _xelex_raw(tick)})
        rows.append({"tick": tick, "steamid": OTHER, "name": "torzsi", INPUT_PROP: S})
        rows.append({"tick": tick, "steamid": 0, "name": "BOT Kate", INPUT_PROP: BUTTON_ATTACK2 if tick < 77750 else 0})
    frame = pd.DataFrame(rows)
    frame["tick"] = frame["tick"].astype("int32")
    frame["steamid"] = frame["steamid"].astype("uint64")
    frame[INPUT_PROP] = frame[INPUT_PROP].astype("float64" if float_masks else "uint64")
    return frame


def _mask_at(track: list[list[int]], tick: int) -> int | None:
    held = None
    for entry_tick, mask in track:
        if entry_tick > tick:
            break
        held = mask
    return held


class ExtractionTest(unittest.TestCase):
    def test_ground_truth_change_points_from_a_usercmd_frame(self) -> None:
        inputs = build_player_inputs(_usercmd_frame(), ROUNDS, DEATHS)
        self.assertEqual(inputs[str(XELEX)], EXPECTED_XELEX)
        for tick, mask in GROUND_TRUTH:
            self.assertEqual(_mask_at(inputs[str(XELEX)], tick), mask, tick)
        # Alive the whole time, one key held from the first round start; nothing after the last round's end.
        self.assertEqual(inputs[str(OTHER)], [[77700, S]])
        # A bot (SteamID 0) is keyed by name, like its frames.
        self.assertEqual(inputs["BOT Kate"], [[77700, BUTTON_ATTACK2], [77750, 0]])

    def test_float_masks_with_gaps_and_a_missing_death_row(self) -> None:
        frame = _usercmd_frame(float_masks=True, drop_ticks=(77790, DEATH_TICK))
        frame.loc[(frame["steamid"] == XELEX) & (frame["tick"] == 77791), INPUT_PROP] = np.nan
        inputs = build_player_inputs(frame, ROUNDS, DEATHS)
        # The death still gets its [tick, 0] point although the demo has no row for it.
        self.assertEqual(inputs[str(XELEX)], EXPECTED_XELEX)

    def test_list_rows_and_the_display_mask(self) -> None:
        rows = [
            {"tick": 10, "steamid": XELEX, "name": "xelex", INPUT_PROP: SCOREBOARD | W | BUTTON_WALK | 8192},
            {"tick": 11, "steamid": XELEX, "name": "xelex", INPUT_PROP: W | BUTTON_WALK},
            {"tick": 12, "steamid": XELEX, "name": "xelex", INPUT_PROP: 0},
            {"tick": 12, "steamid": XELEX, "name": "xelex", INPUT_PROP: W},  # second row at a tick: ignored
            {"tick": 13, "steamid": XELEX, "name": "xelex", INPUT_PROP: None},  # no sample
            {"tick": -1, "steamid": XELEX, "name": "xelex", INPUT_PROP: W},
            {"tick": 14, "steamid": 0, "name": None, INPUT_PROP: W},  # no SteamID and no name: nobody
        ]
        self.assertEqual(build_player_inputs(rows, [], []), {str(XELEX): [[10, W | BUTTON_WALK], [12, 0]]})

    def test_unusable_frames_yield_nothing(self) -> None:
        for frame in (
            [],
            pd.DataFrame(),
            pd.DataFrame([{"tick": 1, "steamid": XELEX, "name": "xelex", "X": 1.0}]),
            pd.DataFrame([{"tick": 1, "steamid": XELEX, INPUT_PROP: True}]),
            pd.DataFrame([{"tick": 1, "steamid": XELEX, INPUT_PROP: "junk"}]),
            "junk",
        ):
            self.assertEqual(build_player_inputs(frame, ROUNDS, DEATHS), {})

    def test_one_every_tick_call_with_the_one_prop(self) -> None:
        calls: list[tuple[list[str], dict[str, Any]]] = []

        class Parser:
            def parse_ticks(self, props: list[str], **kwargs: Any) -> pd.DataFrame:
                calls.append((props, kwargs))
                return _usercmd_frame()

        self.assertEqual(parse_player_inputs(Parser(), ROUNDS, DEATHS)[str(XELEX)], EXPECTED_XELEX)
        self.assertEqual(calls, [([INPUT_PROP], {})])

    def test_a_parser_without_usercmd_is_a_partial_success(self) -> None:
        class Raises:
            def parse_ticks(self, props: list[str], **_: Any) -> Any:
                raise RuntimeError("unknown prop usercmd_buttonstate_1")

        class NeedsTicks:
            def parse_ticks(self, props: list[str], *, ticks: list[int]) -> Any:
                return []

        for parser in (Raises(), NeedsTicks(), object()):
            self.assertEqual(parse_player_inputs(parser, ROUNDS, DEATHS), {})


class NormalizeInputsTest(unittest.TestCase):
    def test_sorts_masks_dedupes_and_drops_junk(self) -> None:
        raw = {
            "p1": [
                [30, D], [10, W | SCOREBOARD | USE], [20, W], [20, A],  # unsorted, masked, then a repeat at 20
                [40, D], (50, 0), [60, True], [70, 1.0], ["80", 1], [90, -1], [-5, 1], [100], {"tick": 1},
                [110, 2**40 | D | DUCK],
            ],
            " p2 ": [[1, 0]],
            "": [[1, 1]],
            "p3": "junk",
            "p4": [],
        }
        self.assertEqual(normalize_player_inputs(raw), {
            "p1": [[10, W], [30, D], [50, 0], [110, D | DUCK]],
            "p2": [[1, 0]],
        })
        self.assertEqual(normalize_player_inputs(normalize_player_inputs(raw)), normalize_player_inputs(raw))
        for junk in (None, [], "x", 3):
            self.assertEqual(normalize_player_inputs(junk), {})

    def test_well_formed_tracks_pass_through(self) -> None:
        track = [[1, W], [2, W | D], [5, 0], [9, DISPLAY_MASK]]
        self.assertEqual(normalize_player_inputs({"p": track}), {"p": track})

    def test_caps_players_and_entries(self) -> None:
        many = {f"p{index}": [[1, W]] for index in range(MAX_INPUT_PLAYERS + 5)}
        self.assertEqual(len(normalize_player_inputs(many)), MAX_INPUT_PLAYERS)
        track = [[tick, W if tick % 2 else 0] for tick in range(10)]
        with patch("app.parser.player_inputs.MAX_INPUT_ENTRIES_PER_PLAYER", 4):
            self.assertEqual(normalize_player_inputs({"p": track}), {"p": track[:4]})
            loaded = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "inputs": {"p": track}})
            self.assertEqual(loaded["inputs"], {"p": track[:4]})
            self.assertIn("inputsCapped", loaded["diagnostics"]["degradedFields"])
            again = normalize_replay_contract(copy.deepcopy(loaded))
            self.assertEqual(again["inputs"], loaded["inputs"])
            self.assertIn("inputsCapped", again["diagnostics"]["degradedFields"])
            # The extraction stops at the cap too.
            rows = [{"tick": tick, "steamid": XELEX, INPUT_PROP: mask} for tick, mask in track]
            self.assertEqual(build_player_inputs(rows, [], []), {str(XELEX): track[:4]})


class ContractV3Test(unittest.TestCase):
    def test_older_replays_load_with_empty_inputs(self) -> None:
        for version in ("replay_contract_v1", "replay_contract_v2", None):
            replay = normalize_replay_contract({"contractVersion": version, "rounds": [], "frames": [],
                                                "players": [], "events": [], "video": {}})
            self.assertEqual(replay["inputs"], {})
            diagnostics = replay["diagnostics"]
            self.assertEqual((diagnostics["inputSource"], diagnostics["inputPlayerCount"]), (None, 0))
            self.assertNotIn("inputs", diagnostics["degradedFields"])

    def test_a_v2_replay_keeps_its_partial_flags(self) -> None:
        replay = normalize_replay_contract({"contractVersion": "replay_contract_v2", "rounds": [], "frames": [],
                                            "players": [], "events": [], "video": {}})
        self.assertEqual(replay["diagnostics"]["degradedFields"], ["utility", "playerStates"])
        self.assertFalse(replay["diagnostics"]["normalizedLegacy"])

    def test_a_v3_replay_without_inputs_is_not_degraded(self) -> None:
        replay = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "rounds": [], "frames": [],
                                            "players": [], "events": [], "video": {},
                                            "playerStates": {"p1": [{"tick": 1, "money": 1}]},
                                            "utility": [{"id": "u", "type": "smoke", "throwTick": 1, "roundNumber": 1,
                                                         "points": [{"tick": 1, "x": 1, "y": 2}]}],
                                            "inputs": {}})
        diagnostics = replay["diagnostics"]
        self.assertEqual(diagnostics["degradedFields"], [])
        self.assertIsNone(diagnostics["inputSource"])

    def test_malformed_inputs_are_degraded_and_dropped(self) -> None:
        replay = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "inputs": [[1, 8]]})
        self.assertEqual(replay["inputs"], {})
        self.assertIn("inputs", replay["diagnostics"]["degradedFields"])

    def test_parser_output_carries_inputs_and_reloads_unchanged(self) -> None:
        parsed = {
            "mapName": "de_mirage",
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 10, "endTick": 500}],
            "players": [{"id": "p1", "name": "xelex", "side": "T"}],
            "frames": [{"tick": 10, "roundNumber": 1,
                        "players": [{"id": "p1", "name": "xelex", "side": "T", "x": -500.0, "y": 300.0}]}],
            "kills": [], "deaths": [], "events": [],
            "inputs": {"p1": [[20, D | USE], [10, W], [30, D]]},
        }
        replay = normalize_parser_output("demo-1", parsed)
        self.assertEqual(replay["contractVersion"], REPLAY_CONTRACT_VERSION)
        self.assertEqual(replay["inputs"], {"p1": [[10, W], [20, D]]})
        loaded = normalize_replay_contract(json.loads(json.dumps(replay)))
        self.assertEqual(loaded["inputs"], replay["inputs"])
        self.assertEqual((loaded["diagnostics"]["inputSource"], loaded["diagnostics"]["inputPlayerCount"]), ("usercmd", 1))
        public = _public_replay_contract(loaded, loaded["video"])
        self.assertEqual(public["inputs"], {"p1": [[10, W], [20, D]]})
        diagnostics = public["diagnostics"]
        assert isinstance(diagnostics, dict)
        self.assertEqual((diagnostics["inputSource"], diagnostics["inputPlayerCount"]), ("usercmd", 1))


class ParseDemoFileInputsTest(unittest.TestCase):
    def _module(self, *, usercmd: bool, calls: list[str]) -> Any:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_mirage", "tick_rate": 64, "playback_ticks": 79000}

            def parse_player_info(self) -> list[dict[str, object]]:
                return [{"steamid": XELEX, "name": "xelex", "team_num": 2}]

            def parse_event(self, event_name: str, **_: object) -> list[dict[str, object]]:
                return {
                    "round_start": [{"tick": 77700, "total_rounds_played": 9}, {"tick": 78718, "total_rounds_played": 10}],
                    "round_freeze_end": [{"tick": 77700, "total_rounds_played": 9}],
                    "round_end": [{"tick": 78270, "total_rounds_played": 10, "winner": 3},
                                  {"tick": 78780, "total_rounds_played": 11, "winner": 3}],
                    "player_death": DEATHS,
                }.get(event_name, [])

            def parse_ticks(self, props: list[str], *, ticks: list[int] | None = None) -> Any:
                if ticks is None:
                    calls.append("usercmd")
                    if not usercmd or props != [INPUT_PROP]:
                        raise RuntimeError("unknown prop")
                    return _usercmd_frame()
                calls.append("frames")
                if INPUT_PROP in props:
                    raise AssertionError("the frame sample must not read usercmd")
                return [
                    {"tick": tick, "steamid": XELEX, "name": "xelex", "team_num": 2, "X": -500.0, "Y": 300.0,
                     "health": 100 if tick < DEATH_TICK or tick >= 78718 else 0,
                     "is_alive": tick < DEATH_TICK or tick >= 78718}
                    for tick in ticks
                ]

            def parse_grenades(self, **_: object) -> Any:
                raise RuntimeError("no grenades")

        return types.SimpleNamespace(DemoParser=FakeDemoParser)

    def _parse(self, module: Any) -> dict[str, Any]:
        with patch.dict("sys.modules", {"demoparser2": module}), patch(
            "app.parser.demo_parser._validate_demo_file", return_value=None,
        ):
            return parse_demo_file(Path("match.dem"))

    def test_inputs_come_out_of_a_full_parse_before_the_frame_sample(self) -> None:
        calls: list[str] = []
        parsed = self._parse(self._module(usercmd=True, calls=calls))
        self.assertEqual(parsed["inputs"][str(XELEX)], EXPECTED_XELEX)
        # The every-tick read happens (and is released) before the sampled frames are parsed.
        self.assertEqual(calls[0], "usercmd")
        self.assertIn("frames", calls)
        replay = normalize_parser_output("demo-1", parsed)
        self.assertEqual(replay["inputs"][str(XELEX)], EXPECTED_XELEX)

    def test_a_demo_without_usercmd_still_parses(self) -> None:
        parsed = self._parse(self._module(usercmd=False, calls=[]))
        self.assertEqual(parsed["inputs"], {})
        self.assertTrue(parsed["frames"])
        replay = normalize_parser_output("demo-1", parsed)
        self.assertEqual(replay["inputs"], {})
        self.assertIsNone(normalize_replay_contract(replay)["diagnostics"]["inputSource"])


if __name__ == "__main__":
    unittest.main()
