from __future__ import annotations

import math
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

ASSET_ATTRIBUTION = (
    "CS2 radar asset from rabume/cs2-dma-radar; source README attributes map assets "
    "to CS2 React HUD by Lexogrine and Boltobserv by boltgolt."
)

_MAP_CONFIGS: dict[str, dict[str, Any]] = {
    "de_dust2": {
        "mapName": "de_dust2",
        "displayName": "Dust II",
        "radarImagePath": "/maps/de_dust2_radar.png",
        "calibrated": True,
        "confidence": "calibrated",
        "attribution": ASSET_ATTRIBUTION,
        "source": "https://github.com/rabume/cs2-dma-radar",
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
        "calibrated": False,
        "confidence": "approximate",
        "attribution": ASSET_ATTRIBUTION,
        "source": "https://github.com/rabume/cs2-dma-radar",
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
        "calibrated": False,
        "confidence": "approximate",
        "attribution": ASSET_ATTRIBUTION,
        "source": "https://github.com/rabume/cs2-dma-radar",
        "transform": {
            "type": "bounds",
            "minX": -1120.0,
            "maxX": 3800.0,
            "minY": -2060.0,
            "maxY": 2920.0,
        },
    },
    "de_ancient": {
        "mapName": "de_ancient",
        "displayName": "Ancient",
        "radarImagePath": "/maps/de_ancient_radar.png",
        "calibrated": False,
        "confidence": "approximate",
        "attribution": ASSET_ATTRIBUTION,
        "source": "https://github.com/rabume/cs2-dma-radar",
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
        "source": "https://github.com/rabume/cs2-dma-radar",
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
        "calibrated": False,
        "confidence": "approximate",
        "attribution": ASSET_ATTRIBUTION,
        "source": "https://github.com/rabume/cs2-dma-radar",
        "transform": {
            "type": "bounds",
            "minX": -3300.0,
            "maxX": 1560.0,
            "minY": -3150.0,
            "maxY": 1850.0,
        },
    },
}


# Normalized frames carry radar percent (0-100), not world units, so a rule
# threshold expressed in percent means a different real distance on every map.
# Each config therefore publishes how many world units one percentage point is
# worth, and the analyzer converts before comparing against its thresholds.
#
# Dust II is the reference scale: it is one of the two truly calibrated maps and
# the thresholds in RuleConfig were originally tuned against it.
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

    transform = config["transform"]
    if transform["type"] == "overview":
        radar_size = float(transform["scale"]) * float(transform["imageSize"])
        radar_x = ((x - float(transform["posX"])) / radar_size) * 100
        radar_y = ((float(transform["posY"]) - y) / radar_size) * 100
    elif transform["type"] == "bounds":
        radar_x = _scale_to_percent(x, float(transform["minX"]), float(transform["maxX"]))
        radar_y = 100 - _scale_to_percent(y, float(transform["minY"]), float(transform["maxY"]))
    else:
        return None

    return {
        "x": round(_clamp(radar_x), 2),
        "y": round(_clamp(radar_y), 2),
        "confidence": str(config["confidence"]),
    }


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
