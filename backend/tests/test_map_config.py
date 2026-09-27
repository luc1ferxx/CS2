import re
import struct
import unittest
from pathlib import Path

from app.parser.map_config import (
    SUPPORTED_MAP_NAMES,
    get_map_config,
    is_current_transform,
    legacy_radar_reprojection,
    transform_percent_to_world,
    transform_world_to_percent,
    world_to_radar_percent,
)
from app.parser.normalizer import normalize_parser_output
from app.parser.replay_contract import normalize_replay_contract

REPO_ROOT = Path(__file__).resolve().parents[2]

# The transforms Inferno and Anubis were normalized with before their radars were
# re-rendered from the nav mesh; replays stored then still carry them.
LEGACY_INFERNO_BOUNDS = {"type": "bounds", "minX": -1120.0, "maxX": 3800.0, "minY": -2060.0, "maxY": 2920.0}
LEGACY_ANUBIS_BOUNDS = {"type": "bounds", "minX": -3300.0, "maxX": 1560.0, "minY": -3150.0, "maxY": 1850.0}


class MapConfigTest(unittest.TestCase):
    def test_nuke_overview_and_floor_threshold(self) -> None:
        config = get_map_config("de_nuke")
        self.assertEqual(config["lowerLevelMaxZ"], -495)
        self.assertEqual(config["secondaryRadarImagePath"], "/maps/de_nuke_lower_radar.png")
        self.assertEqual(world_to_radar_percent("de_nuke", -3453, 2887), {"x": 0, "y": 0, "confidence": "calibrated"})
        self.assertEqual(world_to_radar_percent("de_nuke", 3715, -4281), {"x": 100, "y": 100, "confidence": "calibrated"})

    def test_supported_maps_have_radar_metadata(self) -> None:
        self.assertEqual(
            SUPPORTED_MAP_NAMES,
            (
                "de_dust2",
                "de_mirage",
                "de_inferno",
                "de_ancient",
                "de_nuke",
                "de_anubis",
            ),
        )

        for map_name in SUPPORTED_MAP_NAMES:
            with self.subTest(map_name=map_name):
                config = get_map_config(map_name)
                self.assertIsNotNone(config)
                self.assertEqual(config["mapName"], map_name)
                self.assertTrue(config["radarImagePath"].startswith(f"/maps/{map_name}"))
                # Every radar is rendered with the map's own transform, so all are aligned by construction.
                self.assertTrue(config["calibrated"])
                self.assertEqual(config["confidence"], "calibrated")
                self.assertEqual(config["source"], "scripts/maps/build_radars.py")
                self.assertNotIn("rabume", config["attribution"])
                self.assertIn("transform", config)

    def test_radar_images_are_shipped_1024_square_pngs(self) -> None:
        for map_name in SUPPORTED_MAP_NAMES:
            config = get_map_config(map_name)
            for path in filter(None, (config["radarImagePath"], config.get("secondaryRadarImagePath"))):
                with self.subTest(path=path):
                    data = (REPO_ROOT / "frontend" / "public" / path.lstrip("/")).read_bytes()
                    self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack(">II", data[16:24]), (1024, 1024))
                    self.assertLess(len(data), 150_000)

    def test_inferno_and_anubis_use_valve_overview_transforms(self) -> None:
        for map_name, pos_x, pos_y, scale in (("de_inferno", -2087, 3870, 4.9), ("de_anubis", -2796, 3328, 5.22)):
            with self.subTest(map_name=map_name):
                size = scale * 1024
                self.assertEqual(world_to_radar_percent(map_name, pos_x, pos_y), {"x": 0, "y": 0, "confidence": "calibrated"})
                self.assertEqual(
                    world_to_radar_percent(map_name, pos_x + size, pos_y - size),
                    {"x": 100, "y": 100, "confidence": "calibrated"},
                )

    def test_percent_to_world_inverts_every_transform(self) -> None:
        transforms = [get_map_config(name)["transform"] for name in SUPPORTED_MAP_NAMES]
        for transform in [*transforms, LEGACY_INFERNO_BOUNDS]:
            for world in ((0.0, 0.0), (-1234.5, 987.25), (2500.0, -2500.0)):
                with self.subTest(transform=transform, world=world):
                    percent = transform_world_to_percent(transform, *world)
                    back = transform_percent_to_world(transform, *percent)
                    self.assertAlmostEqual(back[0], world[0], places=6)
                    self.assertAlmostEqual(back[1], world[1], places=6)
        self.assertIsNone(transform_world_to_percent({"type": "dynamicBounds"}, 0, 0))
        self.assertIsNone(transform_world_to_percent({"type": "overview", "posX": 0, "posY": 0, "scale": 0, "imageSize": 1024}, 0, 0))
        self.assertIsNone(transform_percent_to_world({"type": "bounds", "minX": 1, "maxX": 1, "minY": 0, "maxY": 1}, 0, 0))

    def test_backend_and_frontend_configs_share_transforms(self) -> None:
        source = (REPO_ROOT / "frontend" / "lib" / "map-config.ts").read_text(encoding="utf-8")
        for map_name in SUPPORTED_MAP_NAMES:
            with self.subTest(map_name=map_name):
                block = re.search(rf"\n  {map_name}: \{{(.*?)\n  \}}", source, re.S)
                self.assertIsNotNone(block)
                body = block.group(1)
                config = get_map_config(map_name)
                transform = config["transform"]
                self.assertIn(f'type: "{transform["type"]}"', body)
                for key, value in transform.items():
                    if key != "type":
                        found = re.search(rf"\b{key}: (-?[\d.]+)", body)
                        self.assertIsNotNone(found, key)
                        self.assertEqual(float(found.group(1)), value, key)
                self.assertIn(f'radarImagePath: "{config["radarImagePath"]}"', body)
                if config.get("calibrationSource"):
                    self.assertIn(f'calibrationSource: "{config["calibrationSource"]}"', body)
                self.assertIn(f"calibrated: {str(config['calibrated']).lower()}", body)

    def test_unknown_map_has_uncalibrated_metadata_and_no_dust2_resource(self) -> None:
        replay = _normalized_replay("de_cache")

        self.assertEqual(replay["mapName"], "de_cache")
        self.assertEqual(replay["mapMetadata"]["mapName"], "de_cache")
        self.assertEqual(replay["mapMetadata"]["displayName"], "de_cache")
        self.assertFalse(replay["mapMetadata"]["calibrated"])
        self.assertEqual(replay["mapMetadata"]["confidence"], "fallback")
        self.assertIsNone(replay["mapMetadata"]["radarImagePath"])
        self.assertNotEqual(replay["mapMetadata"]["radarImagePath"], "/maps/de_dust2_radar.png")

    def test_normalizer_attaches_map_metadata_for_supported_map(self) -> None:
        replay = _normalized_replay("de_mirage")

        self.assertEqual(replay["mapMetadata"]["mapName"], "de_mirage")
        self.assertEqual(replay["mapMetadata"]["displayName"], "Mirage")
        self.assertEqual(replay["mapMetadata"]["radarImagePath"], "/maps/de_mirage_radar.png")
        self.assertTrue(replay["mapMetadata"]["calibrated"])
        self.assertEqual(replay["mapMetadata"]["confidence"], "calibrated")

    def test_world_to_radar_percent_clamps_supported_map_coordinates(self) -> None:
        for map_name in SUPPORTED_MAP_NAMES:
            with self.subTest(map_name=map_name):
                point = world_to_radar_percent(map_name, -999999, 999999)
                self.assertGreaterEqual(point["x"], 0)
                self.assertLessEqual(point["x"], 100)
                self.assertGreaterEqual(point["y"], 0)
                self.assertLessEqual(point["y"], 100)

    def test_world_to_radar_percent_rejects_non_finite_coordinates(self) -> None:
        self.assertIsNone(world_to_radar_percent("de_dust2", float("nan"), 0))
        self.assertIsNone(world_to_radar_percent("de_mirage", 0, float("inf")))

    def test_supported_map_transforms_are_map_specific(self) -> None:
        dust2_point = world_to_radar_percent("de_dust2", 0, 0)
        mirage_point = world_to_radar_percent("de_mirage", 0, 0)
        inferno_point = world_to_radar_percent("de_inferno", 0, 0)

        self.assertNotEqual((dust2_point["x"], dust2_point["y"]), (mirage_point["x"], mirage_point["y"]))
        self.assertNotEqual((mirage_point["x"], mirage_point["y"]), (inferno_point["x"], inferno_point["y"]))


class LegacyRadarReprojectionTest(unittest.TestCase):
    def test_positions_stored_under_old_bounds_move_to_the_current_transform(self) -> None:
        for map_name, legacy in (("de_inferno", LEGACY_INFERNO_BOUNDS), ("de_anubis", LEGACY_ANUBIS_BOUNDS)):
            with self.subTest(map_name=map_name):
                world = (400.0, 1200.0)
                stored = transform_world_to_percent(legacy, *world)
                convert = legacy_radar_reprojection(map_name, legacy)
                self.assertIsNotNone(convert)
                current = world_to_radar_percent(map_name, *world)
                moved = convert(round(stored[0], 2), round(stored[1], 2))
                self.assertAlmostEqual(moved[0], current["x"], delta=0.02)
                self.assertAlmostEqual(moved[1], current["y"], delta=0.02)

    def test_current_unknown_or_unusable_transforms_need_no_conversion(self) -> None:
        for map_name in SUPPORTED_MAP_NAMES:
            with self.subTest(map_name=map_name):
                self.assertIsNone(legacy_radar_reprojection(map_name, get_map_config(map_name)["transform"]))
        self.assertIsNone(legacy_radar_reprojection("de_cache", LEGACY_INFERNO_BOUNDS))
        self.assertIsNone(legacy_radar_reprojection("de_inferno", {"type": "dynamicBounds"}))
        self.assertIsNone(legacy_radar_reprojection("de_inferno", None))
        self.assertIsNone(legacy_radar_reprojection("de_inferno", {"type": "bounds", "minX": "x"}))

    def test_loading_a_legacy_replay_reprojects_frames_bomb_and_events_once(self) -> None:
        legacy_replay = {
            "demoId": "legacy-inferno",
            "mapName": "de_inferno",
            "mapMetadata": {
                "mapName": "de_inferno",
                "calibrated": False,
                "confidence": "approximate",
                "radarImagePath": "/maps/de_inferno_radar.png",
                "transform": dict(LEGACY_INFERNO_BOUNDS),
            },
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 10, "endTick": 100, "winnerSide": "CT"}],
            "players": [{"id": "p1", "name": "One", "side": "T"}],
            "frames": [
                {
                    "tick": 10,
                    "timeSeconds": 0.1,
                    "roundNumber": 1,
                    "players": [{"id": "p1", "name": "One", "side": "T", "x": 50.0, "y": 50.0, "z": 12.0, "alive": True, "hp": 100}],
                    "bombState": {"status": "planted", "x": 60.0, "y": 40.0, "z": 5.0},
                }
            ],
            "kills": [],
            "events": [{"id": "e1", "type": "smoke", "tick": 10, "roundNumber": 1, "source": "parser", "x": 50.0, "y": 50.0}],
            "generatedAt": "2026-09-01T00:00:00+00:00",
        }
        loaded = normalize_replay_contract(legacy_replay)

        expected = world_to_radar_percent("de_inferno", *transform_percent_to_world(LEGACY_INFERNO_BOUNDS, 50.0, 50.0))
        player = loaded["frames"][0]["players"][0]
        self.assertEqual((player["x"], player["y"]), (expected["x"], expected["y"]))
        self.assertEqual(player["z"], 12.0)
        self.assertEqual((loaded["events"][0]["x"], loaded["events"][0]["y"]), (expected["x"], expected["y"]))
        bomb_expected = world_to_radar_percent("de_inferno", *transform_percent_to_world(LEGACY_INFERNO_BOUNDS, 60.0, 40.0))
        bomb = loaded["frames"][0]["bombState"]
        self.assertEqual((bomb["x"], bomb["y"]), (bomb_expected["x"], bomb_expected["y"]))
        current = get_map_config("de_inferno")
        self.assertEqual(loaded["mapMetadata"]["transform"], current["transform"])
        self.assertTrue(loaded["mapMetadata"]["calibrated"])
        self.assertEqual(loaded["mapMetadata"]["worldUnitsPerPercent"], current["worldUnitsPerPercent"])
        # The stored blob is left untouched, and a second load changes nothing.
        self.assertEqual(legacy_replay["frames"][0]["players"][0]["x"], 50.0)
        self.assertEqual(legacy_replay["mapMetadata"]["transform"], LEGACY_INFERNO_BOUNDS)
        again = normalize_replay_contract(loaded)
        self.assertEqual(again["frames"], loaded["frames"])
        self.assertEqual(again["events"], loaded["events"])

    def test_replays_with_the_current_transform_load_unchanged(self) -> None:
        replay = _normalized_replay("de_mirage")
        loaded = normalize_replay_contract(replay)
        self.assertEqual(loaded["frames"][0]["players"], replay["frames"][0]["players"])
        self.assertEqual(loaded["mapMetadata"], replay["mapMetadata"])
        self.assertNotIn("legacyRadarEdgePositions", loaded["diagnostics"]["degradedFields"])

    def test_positions_clamped_to_the_old_edge_are_hidden_not_moved(self) -> None:
        convert = legacy_radar_reprojection("de_anubis", LEGACY_ANUBIS_BOUNDS)
        for stored in ((0.0, 40.0), (100.0, 40.0), (40.0, 0.0), (40.0, 100.0)):
            with self.subTest(stored=stored):
                self.assertIsNone(convert(*stored))
        self.assertIsNotNone(convert(0.01, 99.99))

        legacy_replay = {
            "demoId": "legacy-anubis",
            "mapName": "de_anubis",
            "mapMetadata": {"mapName": "de_anubis", "calibrated": False, "confidence": "approximate",
                            "transform": dict(LEGACY_ANUBIS_BOUNDS)},
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 10, "endTick": 100, "winnerSide": "CT"}],
            "players": [{"id": "p1", "name": "One", "side": "CT"}, {"id": "p2", "name": "Two", "side": "T"}],
            "frames": [
                {
                    "tick": 10,
                    "timeSeconds": 0.1,
                    "roundNumber": 1,
                    "players": [
                        # CT spawn lies north of the old maxY, so the old code stored y clamped to 0.
                        {"id": "p1", "name": "One", "side": "CT", "x": 55.0, "y": 0.0, "z": 20.0, "alive": True, "hp": 100},
                        {"id": "p2", "name": "Two", "side": "T", "x": 40.0, "y": 60.0, "z": 5.0, "alive": True, "hp": 100},
                    ],
                    "bombState": {"status": "dropped", "x": 100.0, "y": 30.0, "z": 0.0},
                }
            ],
            "kills": [],
            "events": [{"id": "e1", "type": "smoke", "tick": 10, "roundNumber": 1, "source": "parser", "x": 100.0, "y": 50.0}],
            "generatedAt": "2026-09-01T00:00:00+00:00",
        }
        loaded = normalize_replay_contract(legacy_replay)

        clamped, inside = loaded["frames"][0]["players"]
        self.assertNotIn("x", clamped)
        self.assertNotIn("y", clamped)
        self.assertEqual((clamped["id"], clamped["z"], clamped["alive"]), ("p1", 20.0, True))
        expected = world_to_radar_percent("de_anubis", *transform_percent_to_world(LEGACY_ANUBIS_BOUNDS, 40.0, 60.0))
        self.assertEqual((inside["x"], inside["y"]), (expected["x"], expected["y"]))
        self.assertEqual(loaded["frames"][0]["bombState"], {"status": "dropped", "z": 0.0})
        self.assertNotIn("x", loaded["events"][0])
        self.assertEqual(loaded["mapMetadata"]["legacyEdgePositionsHidden"], 3)
        self.assertIn("legacyRadarEdgePositions", loaded["diagnostics"]["degradedFields"])
        # The stored blob keeps its values, and a reload (for example after a re-save) is stable.
        self.assertEqual(legacy_replay["frames"][0]["players"][0]["y"], 0.0)
        again = normalize_replay_contract(loaded)
        self.assertEqual(again["frames"], loaded["frames"])
        self.assertEqual(again["mapMetadata"]["legacyEdgePositionsHidden"], 3)
        self.assertIn("legacyRadarEdgePositions", again["diagnostics"]["degradedFields"])

    def test_stale_display_metadata_is_refreshed_when_the_transform_is_current(self) -> None:
        for map_name in ("de_ancient", "de_mirage", "de_dust2", "de_nuke"):
            with self.subTest(map_name=map_name):
                replay = _normalized_replay(map_name)
                self.assertTrue(is_current_transform(map_name, replay["mapMetadata"]["transform"]))
                # What replays parsed before the radars were re-rendered carry.
                replay["mapMetadata"].update(
                    calibrated=map_name in ("de_dust2", "de_nuke"),
                    confidence="calibrated" if map_name in ("de_dust2", "de_nuke") else "approximate",
                    attribution="CS2 radar asset from rabume/cs2-dma-radar; ...",
                    source="https://github.com/rabume/cs2-dma-radar",
                )
                loaded = normalize_replay_contract(replay)

                current = get_map_config(map_name)
                for field in ("calibrated", "confidence", "attribution", "source", "transform", "radarImagePath"):
                    self.assertEqual(loaded["mapMetadata"][field], current[field], field)
                self.assertEqual(loaded["frames"], replay["frames"])
                self.assertNotIn("legacyEdgePositionsHidden", loaded["mapMetadata"])
        self.assertFalse(is_current_transform("de_inferno", LEGACY_INFERNO_BOUNDS))
        self.assertFalse(is_current_transform("de_cache", LEGACY_INFERNO_BOUNDS))
        self.assertFalse(is_current_transform("de_inferno", None))


def _normalized_replay(map_name: str) -> dict:
    return normalize_parser_output(
        demo_id="demo-map",
        parsed={
            "mapName": map_name,
            "tickRate": 64,
            "rounds": [
                {
                    "roundNumber": 1,
                    "startTick": 100,
                    "freezeEndTick": 164,
                    "endTick": 300,
                    "winnerSide": "CT",
                }
            ],
            "players": [
                {"id": "p1", "name": "Player One", "side": "T"},
                {"id": "p2", "name": "Player Two", "side": "CT"},
            ],
            "frames": [
                {
                    "tick": 100,
                    "roundNumber": 1,
                    "players": [
                        {
                            "id": "p1",
                            "name": "Player One",
                            "side": "T",
                            "x": -1000,
                            "y": 500,
                            "alive": True,
                            "hp": 100,
                        },
                        {
                            "id": "p2",
                            "name": "Player Two",
                            "side": "CT",
                            "x": 1000,
                            "y": -500,
                            "alive": True,
                            "hp": 100,
                        },
                    ],
                }
            ],
        },
    )


if __name__ == "__main__":
    unittest.main()
