import unittest

from app.parser.map_config import SUPPORTED_MAP_NAMES, get_map_config, world_to_radar_percent
from app.parser.normalizer import normalize_parser_output


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
                self.assertIn(config["confidence"], {"calibrated", "approximate"})
                self.assertIn("transform", config)

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
        self.assertFalse(replay["mapMetadata"]["calibrated"])
        self.assertEqual(replay["mapMetadata"]["confidence"], "approximate")

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
