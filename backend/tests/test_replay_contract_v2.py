"""Replay contract v2: playerStates change points and utility trajectories."""

import copy
import math
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd

from app.parser.demo_parser import parse_demo_file
from app.parser.map_config import get_map_config, transform_percent_to_world, world_to_radar_percent
from app.parser.normalizer import normalize_parser_output
from app.parser.player_states import build_player_states, grenade_keys, normalize_player_states
from app.parser.replay_contract import (
    REPLAY_CONTRACT_VERSION,
    normalize_replay_contract,
    replay_contract_is_current,
)
from app.parser.utility_tracks import (
    MAX_POINTS_PER_THROW,
    build_utility_tracks,
    normalize_utility,
    parse_utility_tracks,
)
from app.services.demo_service.projection import _public_replay_contract

ROUNDS = [
    {"roundNumber": 1, "startTick": 1000, "freezeEndTick": 1100, "endTick": 5000},
    {"roundNumber": 2, "startTick": 6000, "freezeEndTick": 6100, "endTick": 9000},
]
THROWER = 76561198000000001
OTHER = 76561198000000002


def _rows(kind: str, entity: int, ticks: range, *, steamid: int = THROWER, name: str = "xelex",
          start: tuple[float, float, float] = (0.0, 0.0, 0.0), velocity: tuple[float, float, float] = (10.0, 5.0, 1.0),
          rest_after: int | None = None) -> list[dict[str, Any]]:
    rows = []
    for tick in ticks:
        moving = tick - ticks.start if rest_after is None else min(tick, rest_after) - ticks.start
        rows.append({
            "grenade_type": kind,
            "grenade_entity_id": entity,
            "x": start[0] + velocity[0] * moving,
            "y": start[1] + velocity[1] * moving,
            "z": start[2] + velocity[2] * moving,
            "tick": tick,
            "steamid": steamid,
            "name": name,
        })
    return rows


def _held(kind: str, tick: int) -> dict[str, Any]:
    return {"grenade_type": kind, "grenade_entity_id": 1, "x": math.nan, "y": math.nan, "z": math.nan,
            "tick": tick, "steamid": THROWER, "name": "xelex"}


def _event(tick: int, entity: int, steamid: int = THROWER, x: float = 1.0, y: float = 2.0, z: float = 3.0) -> dict[str, Any]:
    return {"tick": tick, "entityid": entity, "user_steamid": str(steamid), "user_name": "xelex", "x": x, "y": y, "z": z}


class PlayerStatesTest(unittest.TestCase):
    def test_grenade_keys_map_display_names_one_entry_per_grenade(self) -> None:
        self.assertEqual(
            grenade_keys(["Karambit", "Flashbang", "USP-S", "Incendiary Grenade", "Smoke Grenade", "Flashbang",
                          "High Explosive Grenade", "Decoy Grenade", "C4 Explosive", 7]),
            ["smoke", "flash", "flash", "he", "molotov", "decoy"],
        )
        self.assertEqual(grenade_keys(["Molotov", "weapon_incgrenade"]), ["molotov", "molotov"])
        self.assertEqual(grenade_keys(["AK-47"]), [])
        self.assertIsNone(grenade_keys(None))

    def test_change_points_only_first_entry_at_first_sample(self) -> None:
        def record(tick: int, **extra: Any) -> dict[str, Any]:
            base = {"tick": tick, "steamid": THROWER, "balance": 800, "armor_value": 0, "has_helmet": False,
                    "has_defuser": False, "active_weapon_name": "Glock-18", "inventory": ["Glock-18"],
                    "current_equip_value": 200}
            return {**base, **extra}

        records = [
            record(1016, balance=100, armor_value=100, inventory=["Glock-18", "Flashbang", "Flashbang"]),
            record(1000),
            record(1032, balance=100, armor_value=100, inventory=["Glock-18", "Flashbang", "Flashbang"]),
            record(1048, balance=100, armor_value=100, inventory=["Glock-18", "Flashbang"],
                   active_weapon_name="Flashbang"),
            record(1064, balance=100, armor_value=0, active_weapon_name=None, inventory=[], health=0),
            record(1064, balance=999),  # a duplicate sample of one tick: the first wins
        ]
        states = build_player_states(records)
        self.assertEqual(list(states), [str(THROWER)])
        entries = states[str(THROWER)]
        self.assertEqual([entry["tick"] for entry in entries], [1000, 1016, 1048, 1064])
        self.assertEqual(entries[0], {"tick": 1000, "money": 800, "armor": 0, "helmet": False, "defuser": False,
                                      "weapon": "Glock-18", "grenades": [], "equipValue": 200})
        self.assertEqual(entries[1]["grenades"], ["flash", "flash"])
        self.assertEqual(entries[2]["weapon"], "Flashbang")
        self.assertIsNone(entries[3]["weapon"])
        self.assertEqual(entries[3]["money"], 100)

    def test_props_the_demo_lacks_are_absent_not_zero(self) -> None:
        states = build_player_states([
            {"tick": 10, "steamid": "p1", "balance": 650},
            {"tick": 26, "steamid": "p1", "balance": 650},
            {"tick": 10, "steamid": "p2", "X": 1.0},  # no state props at all
        ])
        self.assertEqual(states, {"p1": [{"tick": 10, "money": 650}]})

    def test_normalize_sanitizes_and_recollapses(self) -> None:
        raw = {
            "p1": [
                {"tick": 30, "money": 100, "weapon": "W" * 80},
                {"tick": 10, "money": math.nan, "armor": 900, "helmet": "yes", "grenades": ["smoke", "rocket", 3]},
                {"tick": 20, "money": -5, "armor": 900, "grenades": ["smoke"]},
                {"tick": "bad"},
                "junk",
            ],
            "p2": "not a list",
            "": [{"tick": 1, "money": 1}],
        }
        states = normalize_player_states(raw)
        self.assertEqual(list(states), ["p1"])
        self.assertEqual(states["p1"][0], {"tick": 10, "armor": 255, "grenades": ["smoke"]})
        self.assertEqual(states["p1"][1], {"tick": 20, "money": 0, "armor": 255, "grenades": ["smoke"]})
        self.assertEqual(states["p1"][2]["weapon"], "W" * 32)
        self.assertEqual(normalize_player_states([1, 2]), {})
        self.assertEqual(normalize_player_states(states), states)


class UtilityTracksTest(unittest.TestCase):
    def _smoke_rows(self) -> list[dict[str, Any]]:
        # Flies 1000..1100, rests from 1100, detonates at 1180, persists to 2600.
        return _rows("CSmokeGrenadeProjectile", 633, range(1000, 2600), rest_after=1100)

    def test_smoke_cut_at_detonation_with_expire_event(self) -> None:
        grenades = pd.DataFrame([_held("CSmokeGrenade", tick) for tick in range(990, 1000)] + self._smoke_rows())
        tracks = build_utility_tracks(
            grenades,
            {"smoke": [_event(1180, 633, x=1000.0, y=500.0, z=100.0)]},
            {"smoke": [_event(2588, 633)]},
            ROUNDS,
            64,
        )
        self.assertEqual(len(tracks), 1)
        smoke = tracks[0]
        self.assertEqual(smoke["id"], "utility-smoke-633-1000")
        self.assertEqual(smoke["type"], "smoke")
        self.assertEqual((smoke["throwerId"], smoke["throwerName"]), (str(THROWER), "xelex"))
        self.assertEqual((smoke["roundNumber"], smoke["throwTick"], smoke["detonateTick"], smoke["endTick"]),
                         (1, 1000, 1180, 2588))
        points = smoke["points"]
        self.assertEqual(points[0], {"tick": 1000, "x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(points[-1]["tick"], 1180)
        self.assertEqual((points[-1]["x"], points[-1]["y"]), (1000.0, 500.0))  # the landing
        in_flight = [point["tick"] for point in points if point["tick"] <= 1100]
        self.assertEqual(in_flight, list(range(1000, 1101, 4)))
        # At rest between 1100 and the detonation: no repeated points.
        self.assertEqual([point["tick"] for point in points if point["tick"] > 1100], [1180])

    def test_missing_expire_event_falls_back_to_fixed_effect_lengths(self) -> None:
        grenades = self._smoke_rows() + _rows("CMolotovProjectile", 700, range(2000, 2100), velocity=(3.0, 0.0, 0.0))
        tracks = build_utility_tracks(grenades, {"smoke": [_event(1180, 633)]}, {}, ROUNDS, 64)
        by_type = {track["type"]: track for track in tracks}
        self.assertEqual(by_type["smoke"]["endTick"], 1180 + 18 * 64)
        # No fire events at all: the last moving tick detonates, +7 s of fire.
        self.assertEqual(by_type["molotov"]["detonateTick"], 2099)
        self.assertEqual(by_type["molotov"]["endTick"], 2099 + 7 * 64)

    def test_fire_matches_by_thrower_and_air_bursts_get_no_fire(self) -> None:
        grenades = (
            _rows("CMolotovProjectile", 700, range(2000, 2080))
            + _rows("CMolotovProjectile", 701, range(3000, 3128), steamid=OTHER, name="donk")
        )
        tracks = build_utility_tracks(
            grenades,
            # The inferno is its own entity (9001): matched by thrower + time.
            {"molotov": [_event(2080, 9001, x=55.0, y=66.0, z=-7.0)]},
            {"molotov": [_event(2080 + 440, 9001), _event(9999, 700)]},
            ROUNDS,
            64,
        )
        fire, burst = tracks
        self.assertEqual((fire["detonateTick"], fire["endTick"]), (2080, 2520))
        self.assertEqual(fire["points"][-1], {"tick": 2080, "x": 55.0, "y": 66.0, "z": -7.0})
        self.assertEqual(burst["throwerName"], "donk")
        self.assertEqual(burst["detonateTick"], 3127)
        self.assertEqual(burst["endTick"], burst["detonateTick"])

    def test_flash_he_decoy_end_at_detonation_and_entity_reuse_splits_tracks(self) -> None:
        grenades = (
            _rows("CFlashbangProjectile", 450, range(1200, 1260))
            + _rows("CFlashbangProjectile", 450, range(6200, 6240), steamid=OTHER, name="donk")
            + _rows("CHEGrenadeProjectile", 451, range(1300, 1500))
            + _rows("CDecoyProjectile", 452, range(1400, 1600), rest_after=1450)
        )
        tracks = build_utility_tracks(
            grenades,
            {"flash": [_event(1260, 450), _event(6240, 450, steamid=OTHER)], "he": [_event(1400, 451)]},
            {},
            ROUNDS,
            64,
        )
        self.assertEqual(
            [(t["id"], t["roundNumber"], t["detonateTick"], t["endTick"]) for t in tracks],
            [
                ("utility-flash-450-1200", 1, 1260, 1260),
                ("utility-he-451-1300", 1, 1400, 1400),
                ("utility-decoy-452-1400", 1, 1450, 1450),
                ("utility-flash-450-6200", 2, 6240, 6240),
            ],
        )
        self.assertEqual(tracks[3]["throwerId"], str(OTHER))

    def test_long_flights_are_capped_and_warmup_throws_dropped(self) -> None:
        grenades = (
            _rows("CSmokeGrenadeProjectile", 800, range(1000, 1900))
            + _rows("CFlashbangProjectile", 801, range(100, 150))  # before round 1
        )
        tracks = build_utility_tracks(grenades, {}, {}, ROUNDS, 64)
        self.assertEqual(len(tracks), 1)
        points = tracks[0]["points"]
        self.assertLessEqual(len(points), MAX_POINTS_PER_THROW)
        self.assertEqual((points[0]["tick"], points[-1]["tick"]), (1000, 1899))
        self.assertEqual(points, sorted(points, key=lambda point: point["tick"]))

    def test_accepts_plain_records_and_junk(self) -> None:
        self.assertEqual(len(build_utility_tracks(self._smoke_rows(), {}, {}, ROUNDS, 64)), 1)
        self.assertEqual(build_utility_tracks([], {}, {}, ROUNDS, 64), [])
        self.assertEqual(build_utility_tracks(pd.DataFrame({"a": [1]}), {}, {}, ROUNDS, 64), [])
        self.assertEqual(build_utility_tracks(None, {}, {}, ROUNDS, 64), [])
        self.assertEqual(build_utility_tracks(self._smoke_rows(), {}, {}, [], 64), [])

    def test_parse_failures_are_a_partial_success(self) -> None:
        class Broken:
            def parse_grenades(self, **_: Any) -> Any:
                raise RuntimeError("no grenade data")

        self.assertEqual(parse_utility_tracks(Broken(), ROUNDS, 64), [])

        class NoEvents:
            def parse_grenades(self, **_: Any) -> Any:
                return pd.DataFrame(_rows("CSmokeGrenadeProjectile", 633, range(1000, 1200), rest_after=1100))

            def parse_event(self, name: str, **_: Any) -> Any:
                raise RuntimeError(name)

        tracks = parse_utility_tracks(NoEvents(), ROUNDS, 64)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["detonateTick"], 1100)
        self.assertEqual(tracks[0]["endTick"], 1100 + 18 * 64)

    def test_normalize_utility_sanitizes(self) -> None:
        raw = [
            {
                "id": "utility-smoke-1-100", "type": "smoke", "throwerId": "p1", "throwerName": " a  b ",
                "throwerSide": "CT", "roundNumber": 0, "throwTick": 1500, "detonateTick": 10, "endTick": 5,
                "points": [{"tick": 1510, "x": 150, "y": -3, "z": math.inf}, {"tick": 1500, "x": 10, "y": 20, "z": 1.26},
                           {"tick": 1505, "x": math.nan, "y": 1}, "junk"],
            },
            {"id": "utility-smoke-1-100", "type": "smoke", "throwTick": 1, "points": [{"tick": 1, "x": 1, "y": 1}]},
            {"type": "rocket", "throwTick": 1, "points": [{"tick": 1, "x": 1, "y": 1}]},
            {"type": "flash", "throwTick": 1, "points": []},
            {"type": "he", "throwTick": 7000, "throwerSide": "X", "points": [{"tick": 7000, "x": 1, "y": 1}]},
        ]
        throws = normalize_utility(raw, ROUNDS)
        self.assertEqual([throw["type"] for throw in throws], ["smoke", "he"])
        smoke = throws[0]
        self.assertEqual(smoke["points"], [{"tick": 1500, "x": 10.0, "y": 20.0, "z": 1.3},
                                           {"tick": 1510, "x": 100.0, "y": 0.0}])
        self.assertEqual((smoke["roundNumber"], smoke["detonateTick"], smoke["endTick"]), (1, 1510, 1510))
        self.assertEqual(smoke["throwerName"], "a b")
        he = throws[1]
        self.assertEqual((he["roundNumber"], he["throwerSide"], he["throwerId"]), (2, None, None))
        self.assertTrue(he["id"].startswith("utility-he-7000"))
        self.assertEqual(normalize_utility("junk"), [])
        self.assertEqual(normalize_utility(throws, ROUNDS), throws)

    def test_normalize_utility_ends_effects_at_the_round_reset_and_drops_between_round_throws(self) -> None:
        def smoke(throw_id: str, throw_tick: int, detonate: int, end: int) -> dict[str, Any]:
            return {"id": throw_id, "type": "smoke", "roundNumber": 1, "throwTick": throw_tick,
                    "detonateTick": detonate, "endTick": end,
                    "points": [{"tick": throw_tick, "x": 1, "y": 1}, {"tick": detonate, "x": 2, "y": 2}]}

        raw = [
            smoke("late-smoke", 4800, 4900, 4900 + 18 * 64),  # its fallback end would outlast the reset at 6000
            smoke("expired-smoke", 1500, 1600, 2900),  # a matched expire event: untouched
            smoke("post-round", 5100, 5150, 5150 + 18 * 64),  # 1.6 s after the round's end: kept
            smoke("paused", 5000 + 11 * 64, 5000 + 11 * 64 + 50, 5000 + 11 * 64 + 50),  # mid-pause: dropped
        ]
        throws = normalize_utility(raw, ROUNDS, 64)
        by_id = {throw["id"]: throw for throw in throws}
        self.assertEqual(sorted(by_id), ["expired-smoke", "late-smoke", "post-round"])
        self.assertEqual(by_id["late-smoke"]["endTick"], 6000)
        self.assertEqual(by_id["post-round"]["endTick"], 6000)
        self.assertEqual(by_id["expired-smoke"]["endTick"], 2900)
        self.assertEqual(normalize_utility(throws, ROUNDS, 64), throws)
        # A round without a known end never loses its throws.
        no_end = [{"roundNumber": 1, "startTick": 1000, "endTick": 1000}]
        self.assertEqual(len(normalize_utility([smoke("minute-in", 1000 + 60 * 64, 1000 + 61 * 64, 1000 + 70 * 64)], no_end, 64)), 1)
        # Stored replays get the same treatment on load.
        stored = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "tickRate": 64,
                                            "rounds": ROUNDS, "frames": [], "utility": raw})
        self.assertEqual([throw["id"] for throw in stored["utility"]], [throw["id"] for throw in throws])
        self.assertEqual({throw["id"]: throw["endTick"] for throw in stored["utility"]}["late-smoke"], 6000)


class ContractV2Test(unittest.TestCase):
    def test_version_helpers(self) -> None:
        # v3 (key inputs) is current, so v2 replays are owed a re-parse.
        self.assertEqual(REPLAY_CONTRACT_VERSION, "replay_contract_v3")
        self.assertTrue(replay_contract_is_current("replay_contract_v3"))
        self.assertTrue(replay_contract_is_current(" replay_contract_v4 "))
        for version in ("replay_contract_v2", "replay_contract_v1", "legacy", None, 3, "replay_contract_v",
                        "replay_contract_v3x"):
            self.assertFalse(replay_contract_is_current(version), version)

    def test_v1_replay_loads_with_empty_v2_fields_and_no_degradation(self) -> None:
        replay = normalize_replay_contract({"contractVersion": "replay_contract_v1", "rounds": [], "frames": [],
                                            "players": [], "events": [], "video": {}})
        self.assertEqual((replay["playerStates"], replay["utility"]), ({}, []))
        diagnostics = replay["diagnostics"]
        self.assertEqual((diagnostics["utilityCount"], diagnostics["playerStateCount"]), (0, 0))
        self.assertEqual(diagnostics["degradedFields"], [])
        self.assertFalse(diagnostics["normalizedLegacy"])

    def test_v2_without_utility_is_flagged_as_partial_not_legacy(self) -> None:
        replay = normalize_replay_contract({"contractVersion": REPLAY_CONTRACT_VERSION, "rounds": [], "frames": [],
                                            "players": [], "events": [], "video": {},
                                            "playerStates": {"p1": [{"tick": 1, "money": 1}]}, "utility": []})
        self.assertEqual(replay["diagnostics"]["degradedFields"], ["utility"])
        self.assertFalse(replay["diagnostics"]["normalizedLegacy"])
        malformed = normalize_replay_contract({"contractVersion": "replay_contract_v1", "playerStates": [1],
                                               "utility": {"a": 1}})
        self.assertEqual((malformed["playerStates"], malformed["utility"]), ({}, []))
        self.assertIn("playerStates", malformed["diagnostics"]["degradedFields"])
        self.assertIn("utility", malformed["diagnostics"]["degradedFields"])

    def test_legacy_radar_reprojection_moves_utility_points(self) -> None:
        legacy = {"type": "bounds", "minX": -1120.0, "maxX": 3800.0, "minY": -2060.0, "maxY": 2920.0}
        current = get_map_config("de_inferno")
        assert current is not None
        replay = {
            "contractVersion": REPLAY_CONTRACT_VERSION,
            "mapName": "de_inferno",
            "mapMetadata": {**current, "transform": legacy},
            "utility": [{
                "id": "utility-smoke-1-10", "type": "smoke", "throwTick": 10, "detonateTick": 20, "endTick": 30,
                "roundNumber": 1, "throwerId": None, "throwerName": None, "throwerSide": None,
                "points": [{"tick": 10, "x": 50.0, "y": 50.0, "z": 5.0}, {"tick": 20, "x": 0.0, "y": 40.0}],
            }],
        }
        loaded = normalize_replay_contract(copy.deepcopy(replay))
        expected = world_to_radar_percent("de_inferno", *transform_percent_to_world(legacy, 50.0, 50.0))
        assert expected is not None
        points = loaded["utility"][0]["points"]
        # The point clamped to the old edge is dropped, the other re-projected.
        self.assertEqual(points, [{"tick": 10, "x": expected["x"], "y": expected["y"], "z": 5.0}])
        self.assertEqual(loaded["mapMetadata"]["legacyEdgePositionsHidden"], 1)
        self.assertEqual(normalize_replay_contract(copy.deepcopy(loaded))["utility"], loaded["utility"])


def _frame(tick: int, round_number: int, t_side_player: str, ct_side_player: str) -> dict[str, Any]:
    return {
        "tick": tick,
        "roundNumber": round_number,
        "players": [
            {"id": t_side_player, "name": t_side_player, "side": "T", "x": -500.0, "y": 300.0, "z": 10.0,
             "alive": True, "hp": 100, "hasBomb": False},
            {"id": ct_side_player, "name": ct_side_player, "side": "CT", "x": 400.0, "y": -200.0,
             "alive": True, "hp": 100, "hasBomb": False},
        ],
        "bombState": {"status": "unknown"},
    }


class NormalizerV2Test(unittest.TestCase):
    def test_utility_uses_the_frame_projection_and_the_round_side_rule(self) -> None:
        thrower = str(THROWER)
        parsed = {
            "mapName": "de_mirage",
            "tickRate": 64,
            "rounds": ROUNDS,
            "players": [],
            # The thrower plays T in round 1 and CT in round 2 (half-time swap).
            "frames": [_frame(1100, 1, thrower, "p2"), _frame(1116, 1, thrower, "p2"),
                       _frame(6100, 2, "p2", thrower), _frame(6116, 2, "p2", thrower)],
            "events": [],
            "kills": [],
            "playerStates": {thrower: [{"tick": 1100, "money": 800, "weapon": "Glock-18"}]},
            "utility": [
                {"id": "utility-smoke-1-1200", "type": "smoke", "throwerId": thrower, "throwerName": "xelex",
                 "roundNumber": 1, "throwTick": 1200, "detonateTick": 1300, "endTick": 2452,
                 "points": [{"tick": 1200, "x": -500.0, "y": 300.0, "z": 10.0},
                            {"tick": 1300, "x": 99999.0, "y": 300.0}]},
                {"id": "utility-flash-2-6200", "type": "flash", "throwerId": thrower, "throwerName": "xelex",
                 "roundNumber": 2, "throwTick": 6200, "detonateTick": 6260, "endTick": 6260,
                 "points": [{"tick": 6200, "x": 400.0, "y": -200.0}]},
                {"id": "utility-he-3-6300", "type": "he", "throwerId": "ghost", "throwerName": "ghost",
                 "roundNumber": 2, "throwTick": 6300, "detonateTick": 6300, "endTick": 6300,
                 "points": [{"tick": 6300, "x": 0.0, "y": 0.0}]},
            ],
        }
        replay = normalize_parser_output("demo-1", parsed)
        self.assertEqual(replay["contractVersion"], REPLAY_CONTRACT_VERSION)
        self.assertEqual(replay["playerStates"], {thrower: [{"tick": 1100, "money": 800, "weapon": "Glock-18"}]})
        smoke, flash, he = replay["utility"]
        frame_player = replay["frames"][0]["players"][0]
        self.assertEqual(smoke["points"][0], {"tick": 1200, "x": frame_player["x"], "y": frame_player["y"], "z": 10.0})
        self.assertEqual(smoke["points"][1]["x"], 100.0)  # clamped like the frames
        self.assertEqual((smoke["throwerSide"], flash["throwerSide"], he["throwerSide"]), ("T", "CT", None))
        # Stored and served forms agree: loading the stored blob changes nothing.
        loaded = normalize_replay_contract(copy.deepcopy(replay))
        self.assertEqual((loaded["utility"], loaded["playerStates"]), (replay["utility"], replay["playerStates"]))
        self.assertEqual(loaded["diagnostics"]["utilityCount"], 3)

    def test_public_projection_serves_v2_fields(self) -> None:
        replay = normalize_replay_contract({
            "contractVersion": REPLAY_CONTRACT_VERSION,
            "demoId": "d", "mapName": "de_mirage", "mapMetadata": get_map_config("de_mirage"),
            "playerStates": {"p1": [{"tick": 1, "money": 5, "internal": "x"}]},
            "utility": [{"id": "u", "type": "smoke", "throwTick": 1, "roundNumber": 1,
                         "points": [{"tick": 1, "x": 1, "y": 2, "secret": True}], "raw": "x"}],
        })
        public = _public_replay_contract(replay, replay["video"])
        self.assertEqual(public["playerStates"], {"p1": [{"tick": 1, "money": 5}]})
        utility = public["utility"]
        assert isinstance(utility, list)
        self.assertEqual(utility[0]["points"], [{"tick": 1, "x": 1.0, "y": 2.0}])
        self.assertNotIn("raw", utility[0])
        metadata = public["mapMetadata"]
        assert isinstance(metadata, dict)
        self.assertEqual(set(metadata["worldUnitsPerPercent"]), {"x", "y"})
        diagnostics = public["diagnostics"]
        assert isinstance(diagnostics, dict)
        self.assertEqual((diagnostics["utilityCount"], diagnostics["playerStateCount"]), (1, 1))


class ParseDemoFileV2Test(unittest.TestCase):
    def _parser_module(self, *, grenades: Any, state_props: bool) -> Any:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_mirage", "tick_rate": 64, "playback_ticks": 2000}

            def parse_player_info(self) -> list[dict[str, object]]:
                return [{"steamid": THROWER, "name": "xelex", "team_num": 2}]

            def parse_event(self, event_name: str, **_: object) -> list[dict[str, object]]:
                return {
                    "round_start": [{"tick": 1000, "total_rounds_played": 0}],
                    "round_freeze_end": [{"tick": 1100, "total_rounds_played": 0}],
                    "round_end": [{"tick": 1900, "total_rounds_played": 1, "winner": 2}],
                    "smokegrenade_detonate": [_event(1180, 633)],
                }.get(event_name, [])

            def parse_ticks(self, props: list[str], *, ticks: list[int]) -> list[dict[str, object]]:
                if "balance" in props and not state_props:
                    raise RuntimeError("unknown prop")
                return [
                    {"tick": tick, "steamid": THROWER, "name": "xelex", "team_num": 2, "X": -500.0, "Y": 300.0,
                     "health": 100, "is_alive": True, "inventory": ["Glock-18", "Smoke Grenade"],
                     **({"balance": 800 if tick < 1100 else 600, "active_weapon_name": "Glock-18"} if "balance" in props else {})}
                    for tick in ticks
                ]

            def parse_grenades(self, **_: object) -> Any:
                if isinstance(grenades, Exception):
                    raise grenades
                return grenades

        return types.SimpleNamespace(DemoParser=FakeDemoParser)

    def _parse(self, module: Any) -> dict[str, Any]:
        with patch.dict("sys.modules", {"demoparser2": module}), patch(
            "app.parser.demo_parser._validate_demo_file", return_value=None,
        ):
            return parse_demo_file(Path("match.dem"))

    def test_states_and_tracks_come_out_of_a_full_parse(self) -> None:
        grenades = pd.DataFrame(_rows("CSmokeGrenadeProjectile", 633, range(1000, 1300), rest_after=1100))
        parsed = self._parse(self._parser_module(grenades=grenades, state_props=True))
        entries = parsed["playerStates"][str(THROWER)]
        self.assertEqual([entry["money"] for entry in entries], [800, 600])
        self.assertEqual(entries[0]["grenades"], ["smoke"])
        self.assertEqual([track["id"] for track in parsed["utility"]], ["utility-smoke-633-1000"])
        self.assertEqual(parsed["utility"][0]["detonateTick"], 1180)

    def test_missing_grenades_and_state_props_are_a_partial_success(self) -> None:
        parsed = self._parse(self._parser_module(grenades=RuntimeError("boom"), state_props=False))
        self.assertEqual(parsed["utility"], [])
        # Without the state props only the inventory-derived grenades remain.
        self.assertEqual(parsed["playerStates"][str(THROWER)][0], {"tick": 1000, "grenades": ["smoke"]})
        self.assertTrue(parsed["frames"])


if __name__ == "__main__":
    unittest.main()
