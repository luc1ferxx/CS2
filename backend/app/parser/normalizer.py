from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.parser.map_config import map_metadata_for, world_to_radar_percent
from app.parser.replay_contract import normalize_replay_events as normalize_contract_events

SIDE_COLORS = {"T": "#f5b542", "CT": "#2ed3d0"}


def normalize_parser_output(demo_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    tick_rate = int(parsed.get("tickRate") or 64)
    map_name = str(parsed.get("mapName") or "unknown")
    rounds = _normalize_rounds(parsed.get("rounds") or [], parsed.get("frames") or [])
    players = _normalize_players(parsed.get("players") or [], parsed.get("frames") or [])
    bounds = _position_bounds(parsed.get("frames") or [])
    frames = _normalize_frames(parsed.get("frames") or [], rounds, map_name, bounds)
    if not frames:
        raise ValueError("Parser produced no player position frames")

    tick_start = int(rounds[0]["startTick"]) if rounds else int(frames[0]["tick"])
    tick_end = int(rounds[-1]["endTick"]) if rounds else int(frames[-1]["tick"])

    return {
        "contractVersion": "replay_contract_v1",
        "demoId": demo_id,
        "mapName": map_name,
        "mapMetadata": map_metadata_for(map_name),
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
        "kills": list(parsed.get("kills") or []),
        "deaths": list(parsed.get("deaths") or []),
        "events": normalize_contract_events(
            parsed.get("events") or [],
            parsed.get("kills") or [],
            rounds,
            position_normalizer=_event_position_normalizer(map_name, bounds),
        ),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _event_position_normalizer(map_name: str, bounds: dict[str, float]):
    def normalize(source: dict[str, Any]) -> dict[str, float] | None:
        x_value = source.get("x", source.get("X"))
        y_value = source.get("y", source.get("Y"))
        if x_value is None or y_value is None:
            return None
        return _normalize_position(float(x_value), float(y_value), bounds, map_name)

    return normalize


def _normalize_rounds(raw_rounds: list[dict[str, Any]], raw_frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if raw_rounds:
        rounds = []
        for index, item in enumerate(raw_rounds, start=1):
            start_tick = int(item.get("startTick", 0))
            end_tick = int(item.get("endTick", start_tick))
            rounds.append(
                {
                    "roundNumber": int(item.get("roundNumber", index)),
                    "startTick": start_tick,
                    "freezeEndTick": int(item.get("freezeEndTick", start_tick)),
                    "endTick": end_tick,
                    "winnerSide": _normalize_side(item.get("winnerSide")) or "CT",
                    **(
                        {"winnerReason": str(item["winnerReason"])}
                        if item.get("winnerReason") is not None
                        else {}
                    ),
                }
            )
        return sorted(rounds, key=lambda item: item["roundNumber"])

    ticks = [int(frame["tick"]) for frame in raw_frames if "tick" in frame]
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
        for player in frame.get("players", []):
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
    for frame in sorted(raw_frames, key=lambda item: int(item.get("tick", 0))):
        tick = int(frame.get("tick", 0))
        normalized.append(
            {
                "tick": tick,
                "timeSeconds": float(frame.get("timeSeconds", 0)),
                "roundNumber": int(frame.get("roundNumber") or _round_for_tick(tick, rounds)),
                "players": [
                    _normalize_frame_player(player, bounds, map_name)
                    for player in frame.get("players", [])
                    if _has_position(player)
                ],
                "bombState": _normalize_bomb_state(frame.get("bombState")),
            }
        )
    return [frame for frame in normalized if frame["players"]]


def _normalize_frame_player(
    player: dict[str, Any],
    bounds: dict[str, float],
    map_name: str,
) -> dict[str, Any]:
    player_id = str(player.get("id") or player.get("steamid") or player.get("name") or "unknown")
    side = _normalize_side(player.get("side") or player.get("team")) or "T"
    alive = bool(player.get("alive", True))
    hp = int(player.get("hp", 100 if alive else 0) or 0)
    if hp <= 0:
        alive = False
    return {
        "id": player_id,
        "name": str(player.get("name") or player_id),
        "side": side,
        **_normalize_position(float(player["x"]), float(player["y"]), bounds, map_name),
        "alive": alive,
        "hp": max(0, min(100, hp)),
        "hasBomb": bool(player.get("hasBomb", False)),
    }


def _position_bounds(raw_frames: list[dict[str, Any]]) -> dict[str, float]:
    xs: list[float] = []
    ys: list[float] = []
    for frame in raw_frames:
        for player in frame.get("players", []):
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
    return player.get("x") is not None and player.get("y") is not None


def _round_for_tick(tick: int, rounds: list[dict[str, Any]]) -> int:
    for item in rounds:
        if int(item["startTick"]) <= tick <= int(item["endTick"]):
            return int(item["roundNumber"])
    return int(rounds[0]["roundNumber"]) if rounds else 1


def _normalize_bomb_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"status": "carried"}
    status = raw.get("status")
    if status not in {"carried", "planted", "dropped"}:
        return {"status": "carried"}
    return raw


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
