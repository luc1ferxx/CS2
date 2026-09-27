from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from typing import Any

SUPPORTED_MAP_NAMES = (
    "de_dust2",
    "de_mirage",
    "de_inferno",
    "de_ancient",
    "de_nuke",
    "de_anubis",
)

# Every radar image is rendered by this project from the navigation mesh in the
# operator's own CS2 install, with exactly the transform below, so positions line
# up with the image by construction. See scripts/maps/build_radars.py.
ASSET_ATTRIBUTION = (
    "Radar rendered by this project from the CS2 navigation mesh with "
    "scripts/maps/build_radars.py; no third-party radar artwork."
)
RADAR_SOURCE = "scripts/maps/build_radars.py"
_VALVE_OVERVIEW_SOURCE = "CS2 game/csgo/pak01_dir.vpk: resource/overviews/{map_name}.txt (pos_x, pos_y, scale)"

_MAP_CONFIGS: dict[str, dict[str, Any]] = {
    "de_dust2": {
        "mapName": "de_dust2",
        "displayName": "Dust II",
        "radarImagePath": "/maps/de_dust2_radar.png",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": RADAR_SOURCE,
        "transform": {
            "type": "overview",
            "posX": -2476.0,
            "posY": 3239.0,
            "scale": 4.4,
            "imageSize": 1024.0,
        },
    },
    "de_mirage": {
        "mapName": "de_mirage",
        "displayName": "Mirage",
        "radarImagePath": "/maps/de_mirage_radar.png",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": RADAR_SOURCE,
        "transform": {
            "type": "bounds",
            "minX": -3400.0,
            "maxX": 1720.0,
            "minY": -3220.0,
            "maxY": 1880.0,
        },
    },
    "de_inferno": {
        "mapName": "de_inferno",
        "displayName": "Inferno",
        "radarImagePath": "/maps/de_inferno_radar.png",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": RADAR_SOURCE,
        "calibrationSource": _VALVE_OVERVIEW_SOURCE.format(map_name="de_inferno"),
        "transform": {
            "type": "overview",
            "posX": -2087.0,
            "posY": 3870.0,
            "scale": 4.9,
            "imageSize": 1024.0,
        },
    },
    "de_ancient": {
        "mapName": "de_ancient",
        "displayName": "Ancient",
        "radarImagePath": "/maps/de_ancient_radar.png",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": RADAR_SOURCE,
        "transform": {
            "type": "bounds",
            "minX": -2940.0,
            "maxX": 2170.0,
            "minY": -2890.0,
            "maxY": 2130.0,
        },
    },
    "de_nuke": {
        "mapName": "de_nuke",
        "displayName": "Nuke",
        "radarImagePath": "/maps/de_nuke_radar.png",
        "secondaryRadarImagePath": "/maps/de_nuke_lower_radar.png",
        "lowerLevelMaxZ": -495.0,
        "calibrationSource": "https://github.com/akiver/cs-demo-manager/blob/main/src/node/database/maps/default-maps.ts",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": RADAR_SOURCE,
        "transform": {
            "type": "overview",
            "posX": -3453.0,
            "posY": 2887.0,
            "scale": 7.0,
            "imageSize": 1024.0,
        },
    },
    "de_anubis": {
        "mapName": "de_anubis",
        "displayName": "Anubis",
        "radarImagePath": "/maps/de_anubis_radar.png",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": RADAR_SOURCE,
        "calibrationSource": _VALVE_OVERVIEW_SOURCE.format(map_name="de_anubis"),
        "transform": {
            "type": "overview",
            "posX": -2796.0,
            "posY": 3328.0,
            "scale": 5.22,
            "imageSize": 1024.0,
        },
    },
}


# Normalized frames carry radar percent (0-100), not world units, so a rule
# threshold expressed in percent means a different real distance on every map.
# Each config therefore publishes how many world units one percentage point is
# worth, and the analyzer converts before comparing against its thresholds.
#
# Dust II is the reference scale: the thresholds in RuleConfig were originally
# tuned against it.
REFERENCE_WORLD_UNITS_PER_PERCENT = 4.4 * 1024.0 / 100.0


def world_units_per_percent(config: dict[str, Any] | None) -> dict[str, float] | None:
    """World units spanned by one radar percentage point, per axis.

    Returns ``None`` when the transform carries no fixed scale (the dynamic
    bounds fallback), because that scale is only known per replay.
    """
    if not isinstance(config, dict):
        return None
    transform = config.get("transform")
    if not isinstance(transform, dict):
        return None
    if transform.get("type") == "overview":
        radar_size = float(transform["scale"]) * float(transform["imageSize"])
        return {"x": radar_size / 100.0, "y": radar_size / 100.0}
    if transform.get("type") == "bounds":
        return {
            "x": (float(transform["maxX"]) - float(transform["minX"])) / 100.0,
            "y": (float(transform["maxY"]) - float(transform["minY"])) / 100.0,
        }
    return None


def get_map_config(map_name: str | None) -> dict[str, Any] | None:
    if not map_name:
        return None
    config = _MAP_CONFIGS.get(map_name)
    if not config:
        return None
    resolved = deepcopy(config)
    scale = world_units_per_percent(resolved)
    if scale is not None:
        resolved["worldUnitsPerPercent"] = scale
    return resolved


def map_metadata_for(map_name: str | None) -> dict[str, Any]:
    config = get_map_config(map_name)
    if config:
        return config
    normalized_name = str(map_name or "unknown")
    return {
        "mapName": normalized_name,
        "displayName": normalized_name,
        "radarImagePath": None,
        "calibrated": False,
        "confidence": "fallback",
        "attribution": "Fallback grid generated by the local replay viewer.",
        "source": None,
        "transform": {"type": "dynamicBounds"},
    }


def world_to_radar_percent(map_name: str | None, x: float, y: float) -> dict[str, float | str] | None:
    config = get_map_config(map_name)
    if not config:
        return None
    if not _finite(x) or not _finite(y):
        return None

    point = transform_world_to_percent(config["transform"], float(x), float(y))
    if point is None:
        return None

    return {
        "x": round(_clamp(point[0]), 2),
        "y": round(_clamp(point[1]), 2),
        "confidence": str(config["confidence"]),
    }


def transform_world_to_percent(transform: Any, x: float, y: float) -> tuple[float, float] | None:
    """Unclamped, unrounded radar percent of a world XY under one transform.

    The single definition of the projection: the normalizer (through
    ``world_to_radar_percent``), legacy re-projection and the radar image
    generator in ``scripts/maps/build_radars.py`` all go through it.
    """
    if not _usable_transform(transform):
        return None
    if transform["type"] == "overview":
        radar_size = float(transform["scale"]) * float(transform["imageSize"])
        return (
            ((x - float(transform["posX"])) / radar_size) * 100,
            ((float(transform["posY"]) - y) / radar_size) * 100,
        )
    return (
        _scale_to_percent(x, float(transform["minX"]), float(transform["maxX"])),
        100 - _scale_to_percent(y, float(transform["minY"]), float(transform["maxY"])),
    )


def transform_percent_to_world(transform: Any, x: float, y: float) -> tuple[float, float] | None:
    """Inverse of ``transform_world_to_percent``."""
    if not _usable_transform(transform):
        return None
    if transform["type"] == "overview":
        radar_size = float(transform["scale"]) * float(transform["imageSize"])
        return (
            float(transform["posX"]) + x / 100 * radar_size,
            float(transform["posY"]) - y / 100 * radar_size,
        )
    min_x, max_x = float(transform["minX"]), float(transform["maxX"])
    min_y, max_y = float(transform["minY"]), float(transform["maxY"])
    return (min_x + x / 100 * (max_x - min_x), max_y - y / 100 * (max_y - min_y))


def is_current_transform(map_name: str | None, stored_transform: Any) -> bool:
    """Whether radar percent stored under ``stored_transform`` already matches this map's radar."""
    config = get_map_config(map_name)
    if not config:
        return False
    return _usable_transform(stored_transform) and _same_transform(stored_transform, config["transform"])


def legacy_radar_reprojection(
    map_name: str | None, stored_transform: Any
) -> Callable[[float, float], tuple[float, float] | None] | None:
    """Converter for radar percent stored under an older transform of this map.

    Replays keep the transform they were normalized with in
    ``mapMetadata.transform``. When the map's current transform differs (Inferno
    and Anubis moved from approximate bounds to overview transforms when their
    radar images were re-rendered), this returns a function taking stored percent
    to current percent, clamped and rounded like the normalizer. Returns ``None``
    when nothing needs converting or the stored transform is unusable.

    A stored coordinate of exactly 0 or 100 was clamped to the old image edge:
    the old bounds cut off real floor (Inferno T spawn and north B, Anubis CT
    spawn and north-east A), and the world position beyond the edge was never
    stored. The converter returns ``None`` for such a point instead of placing it
    on the old edge line, which now runs through the middle of the new radar.
    """
    config = get_map_config(map_name)
    if not config or not _usable_transform(stored_transform):
        return None
    current = config["transform"]
    if _same_transform(stored_transform, current):
        return None

    def convert(x: float, y: float) -> tuple[float, float] | None:
        if min(x, y) <= 0.0 or max(x, y) >= 100.0:
            return None
        world = transform_percent_to_world(stored_transform, x, y)
        point = transform_world_to_percent(current, *world) if world else None
        if point is None:
            return x, y
        return round(_clamp(point[0]), 2), round(_clamp(point[1]), 2)

    return convert


_TRANSFORM_FIELDS = {
    "overview": ("posX", "posY", "scale", "imageSize"),
    "bounds": ("minX", "maxX", "minY", "maxY"),
}


def _usable_transform(transform: Any) -> bool:
    if not isinstance(transform, dict):
        return False
    fields = _TRANSFORM_FIELDS.get(str(transform.get("type")))
    if fields is None or not all(_finite(transform.get(field)) for field in fields):
        return False
    if transform["type"] == "overview":
        return float(transform["scale"]) > 0 and float(transform["imageSize"]) > 0
    return float(transform["maxX"]) > float(transform["minX"]) and float(transform["maxY"]) > float(transform["minY"])


def _same_transform(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("type") != right.get("type"):
        return False
    return all(
        math.isclose(float(left[field]), float(right[field]), abs_tol=1e-9)
        for field in _TRANSFORM_FIELDS[str(left["type"])]
    )


def _scale_to_percent(value: float, low: float, high: float) -> float:
    if high == low:
        return 50.0
    return ((value - low) / (high - low)) * 100


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
