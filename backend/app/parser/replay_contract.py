from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, SupportsFloat, SupportsIndex, TypeGuard

from app.parser.map_config import is_current_transform, legacy_radar_reprojection, map_metadata_for
from app.parser.player_inputs import INPUT_SOURCE, input_track_capped, normalize_player_inputs
from app.parser.player_states import normalize_player_states
from app.parser.utility_tracks import normalize_utility

# v2 adds `playerStates` (equipment change points) and `utility` (grenade
# trajectories); v3 adds `inputs` (per-player key change points). Older replays
# load with them empty; the worker's re-parse backstop upgrades completed demos
# whose stored version is older than this one.
REPLAY_CONTRACT_VERSION = "replay_contract_v3"
_CONTRACT_VERSION_PATTERN = re.compile(r"replay_contract_v(\d{1,4})")

PARSER_EVENT_TYPES = {
    "kill",
    "death",
    "damage",
    "bomb_planted",
    "bomb_defused",
    "bomb_exploded",
    "bomb_pickup",
    "bomb_dropped",
    "smoke",
    "flash",
    "molotov",
    "he",
    "round_start",
    "round_end",
}
EVENT_FAMILY_TYPES = {
    "combat": {"kill", "death"},
    "damage": {"damage", "he", "molotov"},
    "objective": {"bomb_planted", "bomb_defused", "bomb_exploded", "bomb_pickup", "bomb_dropped", "round_start", "round_end"},
    "utility": {"smoke", "flash"},
}
EVENT_LABELS = {
    "bomb_defused": "Bomb defused",
    "bomb_exploded": "Bomb exploded",
    "bomb_planted": "Bomb planted",
    "bomb_pickup": "Bomb picked up",
    "bomb_dropped": "Bomb dropped",
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


def replay_contract_number(version: object) -> int | None:
    """The number of a ``replay_contract_vN`` version string; None for anything else."""
    if not isinstance(version, str):
        return None
    match = _CONTRACT_VERSION_PATTERN.fullmatch(version.strip())
    return int(match.group(1)) if match is not None else None


_CURRENT_CONTRACT_NUMBER = replay_contract_number(REPLAY_CONTRACT_VERSION) or 0


def replay_contract_is_current(version: object) -> bool:
    """Whether a stored replay's ``contractVersion`` already carries every field of the current contract."""
    number = replay_contract_number(version)
    return number is not None and number >= _CURRENT_CONTRACT_NUMBER


def normalize_bomb_site(value: Any) -> str | None:
    """CS2 entity indices are not portable A/B site identifiers."""
    if not isinstance(value, str):
        return None
    site = value.strip().upper()
    return site if site in {"A", "B"} else None


def normalize_video_identity(video: dict[str, Any]) -> dict[str, str]:
    """Optional render identity must never survive replacement by another source."""
    if video.get("source") != "rendered" or video.get("status") != "ready":
        return {}
    identity: dict[str, str] = {}
    pov = video.get("povSteamId")
    if isinstance(pov, str) and re.fullmatch(r"7656[0-9]{13}", pov):
        identity["povSteamId"] = pov
    job_id = video.get("renderJobId")
    if isinstance(job_id, str):
        try:
            canonical = str(uuid.UUID(job_id))
            if canonical == job_id:
                identity["renderJobId"] = canonical
        except ValueError:
            pass
    return identity


def normalize_replay_contract(replay: dict[str, Any]) -> dict[str, Any]:
    raw = replay if isinstance(replay, dict) else {}
    normalized = _with_current_radar_transform(dict(raw))
    tick_rate = _positive_int_or_default(normalized.get("tickRate"), 64)
    rounds = _normalize_rounds(normalized.get("rounds"))
    frames = _normalize_frame_optional_fields(normalized.get("frames"))
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
    normalized["playerStates"] = normalize_player_states(normalized.get("playerStates"))
    normalized["utility"] = normalize_utility(normalized.get("utility"), rounds, tick_rate)
    normalized["inputs"] = normalize_player_inputs(normalized.get("inputs"))
    normalized["video"] = _normalize_video(normalized.get("video"), tick_rate, tick_start, tick_end)
    normalized["generatedAt"] = str(normalized.get("generatedAt") or datetime.now(UTC).isoformat())
    normalized["contractVersion"] = _contract_version(raw)
    normalized["diagnostics"] = _replay_diagnostics(raw, normalized)
    return normalized


def _with_current_radar_transform(replay: dict[str, Any]) -> dict[str, Any]:
    """Bring the stored radar positions and map metadata of a supported map up to date.

    Positions stored under the current transform are left alone, but the
    display fields stored beside them (calibration, credit, source) are
    refreshed from the map config, so a replay parsed before a map's radar was
    re-rendered stops showing the old calibration label and credit. Positions
    stored under an older transform are re-projected; those clamped to the old
    image edge cannot be recovered and lose their x/y (the viewer then hides the
    dot), counted in ``mapMetadata.legacyEdgePositionsHidden``. Unknown maps and
    replays without stored metadata or transform are untouched. After one pass
    the metadata carries the current transform and a second pass changes
    nothing. Frames and events are copied, never mutated in place.
    """
    metadata = replay.get("mapMetadata")
    map_name = replay.get("mapName")
    if not isinstance(metadata, dict) or not isinstance(map_name, str):
        return replay
    if is_current_transform(map_name, metadata.get("transform")):
        replay["mapMetadata"] = {**metadata, **map_metadata_for(map_name)}
        return replay
    convert = legacy_radar_reprojection(map_name, metadata.get("transform"))
    if convert is None:
        return replay
    hidden = 0

    def moved(point: Any) -> Any:
        nonlocal hidden
        if not isinstance(point, dict) or not _finite(point.get("x")) or not _finite(point.get("y")):
            return point
        converted = convert(float(point["x"]), float(point["y"]))
        if converted is None:
            hidden += 1
            return {key: value for key, value in point.items() if key not in ("x", "y")}
        return {**point, "x": converted[0], "y": converted[1]}

    frames = replay.get("frames")
    if isinstance(frames, list):
        replay["frames"] = [
            {
                **frame,
                **({"players": [moved(player) for player in frame["players"]]} if isinstance(frame.get("players"), list) else {}),
                **({"bombState": moved(frame["bombState"])} if isinstance(frame.get("bombState"), dict) else {}),
            } if isinstance(frame, dict) else frame
            for frame in frames
        ]
    events = replay.get("events")
    if isinstance(events, list):
        replay["events"] = [moved(event) for event in events]
    utility = replay.get("utility")
    if isinstance(utility, list):
        replay["utility"] = [
            {**item, "points": [moved(point) for point in item["points"]]}
            if isinstance(item, dict) and isinstance(item.get("points"), list) else item
            for item in utility
        ]
    replay["mapMetadata"] = {**metadata, **map_metadata_for(map_name)}
    if hidden:
        replay["mapMetadata"]["legacyEdgePositionsHidden"] = hidden
    return replay


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


def _finite_z_copy(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if "z" in result:
        if _finite(result["z"]):
            result["z"] = float(result["z"])
        else:
            result.pop("z")
    return result


def _normalize_frame_optional_fields(value: Any) -> list[dict[str, Any]]:
    frames = _dict_list(value)
    for frame in frames:
        frame["players"] = [_finite_z_copy(player) for player in _dict_list(frame.get("players"))]
        raw_bomb = frame.get("bombState")
        bomb = _finite_z_copy(raw_bomb) if isinstance(raw_bomb, dict) else {"status": "unknown"}
        status = bomb.get("status")
        if not isinstance(status, str) or status not in {"unknown", "carried", "dropped", "planted", "defused", "exploded"}:
            bomb = {"status": "unknown"}
        if "x" in bomb or "y" in bomb:
            if not _finite(bomb.get("x")) or not _finite(bomb.get("y")):
                bomb.pop("x", None)
                bomb.pop("y", None)
        site = normalize_bomb_site(bomb.pop("site", None))
        if site:
            bomb["site"] = site
        frame["bombState"] = bomb
    return frames


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

    metadata_value = event.get("metadata")
    raw_metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
    site = normalize_bomb_site(event.get("site")) or normalize_bomb_site(raw_metadata.pop("site", None))
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
    identity = normalize_video_identity(video)
    video.pop("povSteamId", None)
    video.pop("renderJobId", None)
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
        **identity,
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


def _contract_version(raw: dict[str, Any]) -> str:
    version = _optional_str(raw.get("contractVersion")) or _optional_str(raw.get("version"))
    return version.strip() if version and version.strip() else "legacy"


def _replay_diagnostics(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    missing_fields = [
        field
        for field in ("events", "rounds", "players", "frames", "video")
        if field not in raw
    ]
    degraded_fields: list[str] = [
        field
        for field in ("rounds", "players", "frames", "events")
        if field in raw and not isinstance(raw.get(field), list)
    ]
    if "video" in raw and not isinstance(raw.get("video"), dict):
        degraded_fields.append("video")
    if "playerStates" in raw and not isinstance(raw.get("playerStates"), dict):
        degraded_fields.append("playerStates")
    if "utility" in raw and not isinstance(raw.get("utility"), list):
        degraded_fields.append("utility")
    if "inputs" in raw and not isinstance(raw.get("inputs"), dict):
        degraded_fields.append("inputs")
    metadata = normalized.get("mapMetadata")
    if isinstance(metadata, dict) and (_optional_int(metadata.get("legacyEdgePositionsHidden")) or 0) > 0:
        degraded_fields.append("legacyRadarEdgePositions")

    events = _dict_list(normalized.get("events"))
    family_counts = _event_family_counts(events)
    normalized_legacy = bool(normalized["contractVersion"] == "legacy" or missing_fields or degraded_fields)
    utility_count = len(normalized.get("utility") or [])
    player_state_count = len(normalized.get("playerStates") or {})
    inputs = normalized.get("inputs") or {}
    if any(input_track_capped(track) for track in inputs.values()):
        degraded_fields.append("inputsCapped")
    # No flag for a v3 replay without inputs: many demo sources carry no usercmd
    # data, and `inputSource: null` already says so.
    if (replay_contract_number(normalized["contractVersion"]) or 0) >= 2:
        # A v2 parse whose grenade or equipment extraction came back empty is a
        # partial success: flag it here, never as a failed parse.
        if utility_count == 0 and "utility" not in degraded_fields:
            degraded_fields.append("utility")
        if player_state_count == 0 and "playerStates" not in degraded_fields:
            degraded_fields.append("playerStates")
    return {
        "contractVersion": normalized["contractVersion"],
        "normalizedLegacy": normalized_legacy,
        "parserEventCount": len(events),
        "roundCount": len(_dict_list(normalized.get("rounds"))),
        "playerCount": len(_dict_list(normalized.get("players"))),
        "frameCount": len(_dict_list(normalized.get("frames"))),
        "missingFields": missing_fields,
        "degradedFields": degraded_fields,
        "eventFamilyCounts": family_counts,
        "missingEventFamilies": [
            family for family, count in family_counts.items() if count == 0
        ],
        "utilityCount": utility_count,
        "playerStateCount": player_state_count,
        "inputSource": INPUT_SOURCE if inputs else None,
        "inputPlayerCount": len(inputs),
    }


def _event_family_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(EVENT_FAMILY_TYPES, 0)
    for event in events:
        event_type = _optional_str(event.get("type"))
        for family, event_types in EVENT_FAMILY_TYPES.items():
            if event_type in event_types:
                counts[family] += 1
                break
    return counts


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
    if _finite(position.get("z")):
        event["z"] = float(position["z"])


def _existing_position(source: dict[str, Any]) -> dict[str, float] | None:
    if not _finite(source.get("x")) or not _finite(source.get("y")):
        return None
    return {"x": float(source["x"]), "y": float(source["y"]),
            **({"z": float(source["z"])} if _finite(source.get("z")) else {})}


def _event_label(event_type: str, event: dict[str, Any], metadata: dict[str, Any]) -> str:
    if event_type == "bomb_planted":
        site = normalize_bomb_site(metadata.get("site"))
        return f"Bomb planted {site}" if site else "Bomb planted"
    label = _optional_str(event.get("label"))
    if label:
        return label
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
    except (TypeError, ValueError, OverflowError):
        return None


def _int_or_default(value: Any, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _positive_int_or_default(value: Any, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None or parsed <= 0 else parsed


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


# TypeGuard, not plain bool: a True result means float(value) already succeeded,
# so callers may pass the value to float() without a further None check.
def _finite(value: Any) -> TypeGuard[SupportsFloat | SupportsIndex | str]:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
