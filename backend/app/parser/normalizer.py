from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, SupportsFloat, SupportsIndex, TypeGuard

from app.parser.map_config import map_metadata_for, world_to_radar_percent
from app.parser.player_states import normalize_player_states
from app.parser.replay_contract import REPLAY_CONTRACT_VERSION
from app.parser.replay_contract import normalize_replay_events as normalize_contract_events
from app.parser.utility_tracks import normalize_utility

SIDE_COLORS = {"T": "#f5b542", "CT": "#2ed3d0"}


def normalize_parser_output(demo_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    tick_rate = _positive_int_or_default(parsed.get("tickRate"), 64)
    map_name = str(parsed.get("mapName") or "unknown")
    raw_rounds = _dict_list(parsed.get("rounds"))
    raw_players = _dict_list(parsed.get("players"))
    raw_frames = _dict_list(parsed.get("frames"))
    raw_kills = _dict_list(parsed.get("kills"))
    raw_deaths = _dict_list(parsed.get("deaths"))
    raw_events = _dict_list(parsed.get("events"))
    rounds = _normalize_rounds(raw_rounds, raw_frames)
    players = _normalize_players(raw_players, raw_frames)
    bounds = _position_bounds(raw_frames)
    frames = _normalize_frames(raw_frames, rounds, map_name, bounds)
    if not frames:
        raise ValueError("Parser produced no player position frames")

    tick_start = int(rounds[0]["startTick"]) if rounds else int(frames[0]["tick"])
    tick_end = int(rounds[-1]["endTick"]) if rounds else int(frames[-1]["tick"])
    events = normalize_contract_events(
        raw_events,
        raw_kills,
        rounds,
        position_normalizer=_event_position_normalizer(map_name, bounds),
    )
    try:
        utility = _with_thrower_sides(
            normalize_utility(_utility_in_radar_percent(parsed.get("utility"), map_name, bounds), rounds, tick_rate),
            rounds,
            frames,
            events,
        )
    except Exception:
        utility = []  # a partial success: the replay just lacks trajectories

    return {
        "contractVersion": REPLAY_CONTRACT_VERSION,
        "demoId": demo_id,
        "mapName": map_name,
        "mapMetadata": _resolved_map_metadata(map_name, bounds),
        "tickRate": tick_rate,
        "video": {
            "status": "ready",
            "url": None,
            "durationSeconds": round(max(0, tick_end - tick_start) / tick_rate, 2),
            "tickStart": tick_start,
            "tickEnd": tick_end,
            "tickRate": tick_rate,
            "source": "mock",
            "errorMessage": None,
            "timeOriginSeconds": 0,
        },
        "rounds": rounds,
        "players": players,
        "frames": frames,
        "kills": raw_kills,
        "deaths": raw_deaths,
        "events": events,
        "playerStates": normalize_player_states(parsed.get("playerStates")),
        "utility": utility,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _utility_in_radar_percent(value: Any, map_name: str, bounds: dict[str, float]) -> list[dict[str, Any]]:
    """Parser utility tracks with their world-unit points moved into radar percent.

    The same projection as the frames (map transform, or this replay's dynamic
    bounds for an unknown map); ``normalize_utility`` then clamps and caps.
    """
    throws = []
    for item in _dict_list(value):
        points = [
            {
                "tick": point.get("tick"),
                **_normalize_position(float(point["x"]), float(point["y"]), bounds, map_name),
                **_world_z(point),
            }
            for point in _dict_list(item.get("points"))
            if _has_position(point)
        ]
        throws.append({**item, "points": points})
    return throws


def _with_thrower_sides(
    utility: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Each thrower's side in the throw's round, by the shared match side rule."""
    if not utility:
        return utility
    # Imported here: the rule lives with the match summary, which imports the parser.
    from app.services.demo_service.match_summary import match_side_rules

    try:
        sides_by_round = match_side_rules({"rounds": rounds, "frames": frames, "events": events}).sides_by_round
    except Exception:
        return utility
    for throw in utility:
        thrower = throw.get("throwerId")
        side = sides_by_round.get(throw["roundNumber"], {}).get(thrower) if isinstance(thrower, str) else None
        throw["throwerSide"] = side if side in {"T", "CT"} else None
    return utility


def _event_position_normalizer(map_name: str, bounds: dict[str, float]):
    def normalize(source: dict[str, Any]) -> dict[str, float] | None:
        x_value = source.get("x", source.get("X"))
        y_value = source.get("y", source.get("Y"))
        if not _finite(x_value) or not _finite(y_value):
            return None
        return {**_normalize_position(float(x_value), float(y_value), bounds, map_name),
                **_world_z(source)}

    return normalize


def _normalize_rounds(raw_rounds: list[dict[str, Any]], raw_frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if raw_rounds:
        rounds = []
        for index, item in enumerate(raw_rounds, start=1):
            start_tick = _int_or_default(item.get("startTick"), 0)
            end_tick = max(start_tick, _int_or_default(item.get("endTick"), start_tick))
            freeze_end_tick = _int_or_default(item.get("freezeEndTick"), start_tick)
            freeze_end_tick = max(start_tick, min(end_tick, freeze_end_tick))
            rounds.append(
                {
                    "roundNumber": _int_or_default(item.get("roundNumber"), index),
                    "startTick": start_tick,
                    "freezeEndTick": freeze_end_tick,
                    "endTick": end_tick,
                    "winnerSide": _normalize_side(item.get("winnerSide")) or "CT",
                    **(
                        {"winnerReason": str(item["winnerReason"])}
                        if item.get("winnerReason") is not None
                        else {}
                    ),
                }
            )
        return sorted(rounds, key=lambda item: (item["startTick"], item["roundNumber"]))

    ticks = [
        tick
        for frame in raw_frames
        if (tick := _optional_int(frame.get("tick"))) is not None
    ]
    if not ticks:
        return []
    return [
        {
            "roundNumber": 1,
            "startTick": min(ticks),
            "freezeEndTick": min(ticks),
            "endTick": max(ticks),
            "winnerSide": "CT",
        }
    ]


def _normalize_players(raw_players: list[dict[str, Any]], raw_frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for player in raw_players:
        player_id = str(player.get("id") or player.get("steamid") or player.get("name") or "unknown")
        by_id[player_id] = {
            "id": player_id,
            "name": str(player.get("name") or player_id),
            "side": _normalize_side(player.get("side") or player.get("team")) or "T",
        }

    for frame in raw_frames:
        for player in _dict_list(frame.get("players")):
            player_id = str(player.get("id") or player.get("steamid") or player.get("name") or "unknown")
            by_id.setdefault(
                player_id,
                {
                    "id": player_id,
                    "name": str(player.get("name") or player_id),
                    "side": _normalize_side(player.get("side") or player.get("team")) or "T",
                },
            )

    return [
        {
            "id": player["id"],
            "name": player["name"],
            "side": player["side"],
            "color": SIDE_COLORS[player["side"]],
        }
        for player in sorted(by_id.values(), key=lambda item: (item["side"], item["name"]))
    ]


def _normalize_frames(
    raw_frames: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    map_name: str,
    bounds: dict[str, float],
) -> list[dict[str, Any]]:
    normalized = []
    sortable_frames = [
        (tick, frame)
        for frame in raw_frames
        if (tick := _optional_int(frame.get("tick"))) is not None
    ]
    for tick, frame in sorted(sortable_frames, key=lambda item: item[0]):
        players = [
            normalized_player
            for player in _dict_list(frame.get("players"))
            if (normalized_player := _normalize_frame_player(player, bounds, map_name)) is not None
        ]
        normalized.append(
            {
                "tick": tick,
                "timeSeconds": _float_or_default(frame.get("timeSeconds"), 0.0),
                "roundNumber": _int_or_default(frame.get("roundNumber"), _round_for_tick(tick, rounds)),
                "players": players,
                "bombState": _normalize_bomb_state(frame.get("bombState"), bounds, map_name),
            }
        )
    return [frame for frame in normalized if frame["players"]]


def _normalize_frame_player(
    player: dict[str, Any],
    bounds: dict[str, float],
    map_name: str,
) -> dict[str, Any] | None:
    if not _has_position(player):
        return None
    player_id = str(player.get("id") or player.get("steamid") or player.get("name") or "unknown")
    side = _normalize_side(player.get("side") or player.get("team")) or "T"
    alive = bool(player.get("alive", True))
    hp = _int_or_default(player.get("hp"), 100 if alive else 0)
    if hp <= 0:
        alive = False
    return {
        "id": player_id,
        "name": str(player.get("name") or player_id),
        "side": side,
        **_normalize_position(float(player["x"]), float(player["y"]), bounds, map_name),
        **_world_z(player),
        "alive": alive,
        "hp": max(0, min(100, hp)),
        "hasBomb": bool(player.get("hasBomb", False)),
    }


def _position_bounds(raw_frames: list[dict[str, Any]]) -> dict[str, float]:
    xs: list[float] = []
    ys: list[float] = []
    for frame in raw_frames:
        for player in _dict_list(frame.get("players")):
            if _has_position(player):
                xs.append(float(player["x"]))
                ys.append(float(player["y"]))
    if not xs or not ys:
        raise ValueError("Parser produced frames without player coordinates")
    return {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)}


def _scale(value: float, low: float, high: float) -> float:
    if high == low:
        return 50.0
    return 5 + ((value - low) / (high - low)) * 90


def _resolved_map_metadata(map_name: str, bounds: dict[str, float]) -> dict[str, Any]:
    """Map metadata with a usable percent-to-world-unit scale attached.

    Calibrated and approximate maps carry a fixed scale from their transform.
    The dynamic bounds fallback does not: it stretches whatever this match's
    players touched across 90 percentage points, so the scale is only knowable
    here, per replay. Without it the analyzer would compare a percent distance
    against a world-unit threshold.
    """
    metadata = map_metadata_for(map_name)
    if metadata.get("worldUnitsPerPercent"):
        return metadata
    span_x = bounds["max_x"] - bounds["min_x"]
    span_y = bounds["max_y"] - bounds["min_y"]
    if span_x > 0 and span_y > 0:
        metadata["worldUnitsPerPercent"] = {"x": span_x / 90.0, "y": span_y / 90.0}
    return metadata


def _normalize_position(
    x: float,
    y: float,
    bounds: dict[str, float],
    map_name: str,
) -> dict[str, float]:
    radar_point = world_to_radar_percent(map_name, x, y)
    if radar_point is None:
        return {
            "x": round(_scale(x, bounds["min_x"], bounds["max_x"]), 2),
            "y": round(100 - _scale(y, bounds["min_y"], bounds["max_y"]), 2),
        }

    return {"x": float(radar_point["x"]), "y": float(radar_point["y"])}


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _has_position(player: dict[str, Any]) -> bool:
    return _finite(player.get("x")) and _finite(player.get("y"))


def _round_for_tick(tick: int, rounds: list[dict[str, Any]]) -> int:
    for item in rounds:
        if int(item["startTick"]) <= tick <= int(item["endTick"]):
            return int(item["roundNumber"])
    return int(rounds[0]["roundNumber"]) if rounds else 1


def _world_z(raw: dict[str, Any]) -> dict[str, float]:
    value = raw.get("z", raw.get("Z"))
    return {"z": float(value)} if _finite(value) else {}


def _normalize_bomb_state(raw: Any, bounds: dict[str, float], map_name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    status = raw.get("status")
    if not isinstance(status, str) or status not in {"unknown", "carried", "planted", "dropped", "defused", "exploded"}:
        return {"status": "unknown"}
    normalized: dict[str, Any] = {"status": status}
    if status == "carried" and isinstance(raw.get("carrierPlayerId"), (str, int)):
        normalized["carrierPlayerId"] = str(raw["carrierPlayerId"])
    if _has_position(raw):
        normalized.update(_normalize_position(float(raw["x"]), float(raw["y"]), bounds, map_name))
    normalized.update(_world_z(raw))
    return normalized


def _normalize_side(value: Any) -> str | None:
    if value in {"T", "CT"}:
        return str(value)
    if isinstance(value, str):
        upper = value.upper()
        if "CT" in upper or "COUNTER" in upper:
            return "CT"
        if upper in {"2", "TERRORIST", "TERRORISTS"} or "TERRORIST" in upper:
            return "T"
    if value == 3:
        return "CT"
    if value == 2:
        return "T"
    return None


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _int_or_default(value: Any, default: int) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if parsed > 0 else default


def _float_or_default(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


# TypeGuard, not plain bool: a True result means float(value) already succeeded,
# so callers may pass the value to float() without a further None check.
def _finite(value: Any) -> TypeGuard[SupportsFloat | SupportsIndex | str]:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
