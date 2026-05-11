from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

PARSER_EVENT_TYPES = {
    "kill",
    "death",
    "damage",
    "bomb_planted",
    "bomb_defused",
    "bomb_exploded",
    "smoke",
    "flash",
    "molotov",
    "he",
    "round_start",
    "round_end",
}
EVENT_LABELS = {
    "bomb_defused": "Bomb defused",
    "bomb_exploded": "Bomb exploded",
    "bomb_planted": "Bomb planted",
    "damage": "Damage",
    "death": "Death",
    "flash": "Flash",
    "he": "HE",
    "kill": "Kill",
    "molotov": "Molotov",
    "round_end": "Round ended",
    "round_start": "Round started",
    "smoke": "Smoke",
}
MAX_METADATA_KEYS = 16
MAX_METADATA_STRING_LENGTH = 200
MAX_METADATA_LIST_LENGTH = 16

PositionNormalizer = Callable[[dict[str, Any]], dict[str, float] | None]


def normalize_replay_contract(replay: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(replay if isinstance(replay, dict) else {})
    tick_rate = _positive_int_or_default(normalized.get("tickRate"), 64)
    rounds = _normalize_rounds(normalized.get("rounds"))
    frames = _dict_list(normalized.get("frames"))
    tick_start, tick_end = _tick_bounds(rounds, frames)

    normalized["demoId"] = str(normalized.get("demoId") or "unknown")
    normalized["mapName"] = str(normalized.get("mapName") or "unknown")
    normalized["tickRate"] = tick_rate
    normalized["rounds"] = rounds
    kills = _dict_list(normalized.get("kills"))
    deaths = _dict_list(normalized.get("deaths")) if isinstance(normalized.get("deaths"), list) else kills
    normalized["players"] = _dict_list(normalized.get("players"))
    normalized["frames"] = frames
    normalized["kills"] = kills
    normalized["deaths"] = deaths
    normalized["events"] = normalize_replay_events(
        _dict_list(normalized.get("events")),
        [],
        rounds,
    )
    normalized["video"] = _normalize_video(normalized.get("video"), tick_rate, tick_start, tick_end)
    normalized["generatedAt"] = str(normalized.get("generatedAt") or datetime.now(timezone.utc).isoformat())
    return normalized


def normalize_replay_events(
    raw_events: list[dict[str, Any]],
    raw_kills: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    position_normalizer: PositionNormalizer | None = None,
) -> list[dict[str, Any]]:
    events = [
        *[normalize_kill_event(kill, rounds, position_normalizer) for kill in raw_kills],
        *[normalize_parser_event(event, rounds, position_normalizer) for event in raw_events],
    ]
    return sorted(
        [event for event in events if event is not None],
        key=lambda item: (int(item["tick"]), str(item["id"])),
    )


def normalize_kill_event(
    kill: dict[str, Any],
    rounds: list[dict[str, Any]],
    position_normalizer: PositionNormalizer | None = None,
) -> dict[str, Any] | None:
    tick = _optional_int(kill.get("tick"))
    if tick is None:
        return None
    attacker_id = _optional_str(kill.get("attackerId"))
    attacker_name = _optional_str(kill.get("attackerName")) or attacker_id
    victim_id = _optional_str(kill.get("victimId"))
    victim_name = _optional_str(kill.get("victimName")) or victim_id
    player_ids = _unique_strings([attacker_id, victim_id, _optional_str(kill.get("assisterId"))])
    metadata = _compact_metadata(
        {
            "attackerId": attacker_id,
            "attackerName": attacker_name,
            "attackerSide": _normalize_side(kill.get("attackerSide")),
            "victimId": victim_id,
            "victimName": victim_name,
            "victimSide": _normalize_side(kill.get("victimSide")),
            "assisterId": _optional_str(kill.get("assisterId")),
            "assisterName": _optional_str(kill.get("assisterName")),
            "weapon": _optional_str(kill.get("weapon")),
            "headshot": kill.get("headshot") if isinstance(kill.get("headshot"), bool) else None,
        }
    )
    event = {
        "id": _event_id("kill", tick, attacker_id or attacker_name, victim_id or victim_name),
        "type": "kill",
        "tick": tick,
        "roundNumber": _round_for_tick(tick, rounds, kill.get("roundNumber")),
        "source": _event_source(kill.get("source")),
        "playerIds": player_ids,
        "playerId": attacker_id,
        "playerName": attacker_name,
        "side": _normalize_side(kill.get("attackerSide")),
        "label": (
            f"{attacker_name or 'Unknown'} killed {victim_name or 'Unknown'}"
            if attacker_name or victim_name
            else EVENT_LABELS["kill"]
        ),
        "metadata": metadata,
    }
    _attach_position(event, kill, position_normalizer)
    return _without_none(event)


def normalize_parser_event(
    event: dict[str, Any],
    rounds: list[dict[str, Any]],
    position_normalizer: PositionNormalizer | None = None,
) -> dict[str, Any] | None:
    event_type = _normalize_event_type(event.get("type"))
    tick = _optional_int(event.get("tick"))
    if event_type is None or tick is None:
        return None

    raw_metadata = dict(event.get("metadata")) if isinstance(event.get("metadata"), dict) else {}
    site = _optional_str(event.get("site"))
    if site:
        raw_metadata["site"] = site
    metadata = _compact_metadata(raw_metadata)
    player_ids = _event_player_ids(event, metadata)
    player_id = _optional_str(event.get("playerId")) or (player_ids[0] if player_ids else None)
    normalized = {
        "id": _optional_str(event.get("id")) or _event_id(event_type, tick, *player_ids),
        "type": event_type,
        "tick": tick,
        "roundNumber": _round_for_tick(tick, rounds, event.get("roundNumber")),
        "source": _event_source(event.get("source")),
        "playerIds": player_ids,
        "playerId": player_id,
        "playerName": _optional_str(event.get("playerName")),
        "side": _normalize_side(event.get("side")),
        "label": _event_label(event_type, event, metadata),
        "metadata": metadata,
    }
    _attach_position(normalized, event, position_normalizer)
    return _without_none(normalized)


def _normalize_rounds(value: Any) -> list[dict[str, Any]]:
    rounds = []
    for index, item in enumerate(_dict_list(value), start=1):
        start_tick = _int_or_default(item.get("startTick"), 0)
        end_tick = _int_or_default(item.get("endTick"), start_tick)
        rounds.append(
            {
                "roundNumber": _positive_int_or_default(item.get("roundNumber"), index),
                "startTick": start_tick,
                "freezeEndTick": _int_or_default(item.get("freezeEndTick"), start_tick),
                "endTick": max(start_tick, end_tick),
                "winnerSide": _normalize_side(item.get("winnerSide")) or "CT",
                **(
                    {"winnerReason": str(item["winnerReason"])}
                    if item.get("winnerReason") is not None
                    else {}
                ),
            }
        )
    return sorted(rounds, key=lambda item: item["roundNumber"])


def _normalize_video(value: Any, tick_rate: int, tick_start: int, tick_end: int) -> dict[str, Any]:
    video = dict(value) if isinstance(value, dict) else {}
    video_tick_rate = _positive_int_or_default(video.get("tickRate"), tick_rate)
    video_tick_start = _int_or_default(video.get("tickStart"), tick_start)
    video_tick_end = max(video_tick_start, _int_or_default(video.get("tickEnd"), tick_end))
    duration = video.get("durationSeconds")
    duration_seconds = (
        float(duration)
        if _finite(duration)
        else round(max(0, video_tick_end - video_tick_start) / max(1, video_tick_rate), 2)
    )
    time_origin = float(video.get("timeOriginSeconds", 0) or 0) if _finite(video.get("timeOriginSeconds", 0)) else 0
    return {
        **video,
        "status": str(video.get("status") or "ready"),
        "url": video.get("url"),
        "durationSeconds": max(0.0, duration_seconds),
        "tickStart": video_tick_start,
        "tickEnd": video_tick_end,
        "tickRate": video_tick_rate,
        "source": str(video.get("source") or "mock"),
        "errorMessage": video.get("errorMessage"),
        "timeOriginSeconds": max(0.0, time_origin),
    }


def _tick_bounds(rounds: list[dict[str, Any]], frames: list[dict[str, Any]]) -> tuple[int, int]:
    if rounds:
        return int(rounds[0]["startTick"]), int(rounds[-1]["endTick"])
    ticks = [_optional_int(frame.get("tick")) for frame in frames]
    valid_ticks = [tick for tick in ticks if tick is not None]
    if not valid_ticks:
        return 0, 0
    return min(valid_ticks), max(valid_ticks)


def _attach_position(
    event: dict[str, Any],
    source: dict[str, Any],
    position_normalizer: PositionNormalizer | None,
) -> None:
    if position_normalizer is not None:
        position = position_normalizer(source)
    else:
        position = _existing_position(source)
    if position is None:
        return
    event["x"] = position["x"]
    event["y"] = position["y"]


def _existing_position(source: dict[str, Any]) -> dict[str, float] | None:
    if not _finite(source.get("x")) or not _finite(source.get("y")):
        return None
    return {"x": float(source["x"]), "y": float(source["y"])}


def _event_label(event_type: str, event: dict[str, Any], metadata: dict[str, Any]) -> str:
    label = _optional_str(event.get("label"))
    if label:
        return label
    if event_type == "bomb_planted" and metadata.get("site"):
        return f"Bomb planted {metadata['site']}"
    return EVENT_LABELS[event_type]


def _event_player_ids(event: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    raw_player_ids = event.get("playerIds")
    values = raw_player_ids if isinstance(raw_player_ids, list) else []
    return _unique_strings(
        [
            *values,
            event.get("playerId"),
            event.get("attackerId"),
            event.get("victimId"),
            event.get("assisterId"),
            metadata.get("attackerId"),
            metadata.get("victimId"),
            metadata.get("assisterId"),
        ]
    )


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None or key in compact:
            continue
        compact_value = _compact_value(value)
        if compact_value is None:
            continue
        compact[str(key)] = compact_value
        if len(compact) >= MAX_METADATA_KEYS:
            break
    return compact


def _compact_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_METADATA_STRING_LENGTH]
    if isinstance(value, list):
        compact_list = [_compact_value(item) for item in value[:MAX_METADATA_LIST_LENGTH]]
        return [item for item in compact_list if item is not None]
    return None


def _event_source(value: Any) -> str:
    source = _optional_str(value)
    return source.strip() if source and source.strip() else "parser"


def _normalize_event_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in PARSER_EVENT_TYPES else None


def _event_id(event_type: str, tick: int, *parts: Any) -> str:
    suffix = "-".join(str(part) for part in parts if part is not None and str(part))
    return f"{event_type}-{tick}-{suffix or 'event'}"


def _round_for_tick(tick: int, rounds: list[dict[str, Any]], fallback: Any = None) -> int:
    fallback_round = _optional_int(fallback)
    if fallback_round is not None and fallback_round > 0:
        return fallback_round
    for item in rounds:
        if int(item["startTick"]) <= tick <= int(item["endTick"]):
            return int(item["roundNumber"])
    return int(rounds[0]["roundNumber"]) if rounds else 1


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


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


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_default(value: Any, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _positive_int_or_default(value: Any, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None or parsed <= 0 else parsed


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
