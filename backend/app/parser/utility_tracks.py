"""Thrown-grenade trajectories (replay contract v2 ``utility``).

``parse_grenades()`` reports every grenade entity at every tick. Held grenades
(``CSmokeGrenade``, ``CFlashbang`` ...) carry no coordinates and are ignored; the
projectile classes are the thrown grenades. One entity id is reused across the
match, so a track is a run of rows of one entity, one thrower and one class with
no tick gap wider than ``TRACK_GAP_TICKS``.

A track is cut at its detonation: the detonate event of the same entity (smoke,
flash, HE, decoy) or, for fire, the ``inferno_startburn`` of the same thrower in
the track's window (the inferno is its own entity). Without a matching event the
last tick the projectile moved stands in. The effect end comes from the expire
events (``smokegrenade_expired``, ``inferno_expire``) with fixed fallbacks.

Points stay in world units here; ``app.parser.normalizer`` converts them to
radar percent with the frames' transform. Everything is best effort: a demo
without grenade data yields ``[]`` and never fails the parse.
"""

from __future__ import annotations

import math
from typing import Any

UTILITY_TYPES = ("smoke", "flash", "he", "molotov", "decoy")
PROJECTILE_TYPES = {
    "CSmokeGrenadeProjectile": "smoke",
    "CFlashbangProjectile": "flash",
    "CHEGrenadeProjectile": "he",
    "CMolotovProjectile": "molotov",
    "CIncendiaryGrenadeProjectile": "molotov",
    "CDecoyProjectile": "decoy",
}
DETONATE_EVENTS = {
    "smoke": ("smokegrenade_detonate",),
    "flash": ("flashbang_detonate",),
    "he": ("hegrenade_detonate",),
    "molotov": ("inferno_startburn", "molotov_detonate"),
    "decoy": ("decoy_started", "decoy_detonate"),
}
EXPIRE_EVENTS = {"smoke": "smokegrenade_expired", "molotov": "inferno_expire"}
# Effect length when the expire event is missing.
FALLBACK_EFFECT_SECONDS = {"smoke": 18.0, "molotov": 7.0}
MAX_EFFECT_SECONDS = 30.0
TRACK_GAP_TICKS = 8
# A detonate event may land a tick or so after the projectile's last row.
DETONATE_SLACK_TICKS = 16
POINT_STEP_TICKS = 4
MAX_POINTS_PER_THROW = 120
MAX_THROWS = 4000
MAX_NAME_LENGTH = 64


def parse_utility_tracks(
    parser: Any,
    rounds: list[dict[str, Any]],
    tick_rate: int,
    detonations: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Tracks for one demo; ``[]`` when demoparser2 has no grenade data for it.

    ``detonations`` are detonate records the caller already parsed, by type; any
    type missing from it is parsed here.
    """
    try:
        rows = _projectile_rows(_parse_grenade_rows(parser))
    except Exception:
        return []
    if rows is None:
        return []
    # Events are only read for grenade types this demo actually threw.
    thrown = {PROJECTILE_TYPES[name] for name in set(rows["type"])}
    events = dict(detonations or {})
    for utility_type, names in DETONATE_EVENTS.items():
        if utility_type in thrown and not events.get(utility_type):
            events[utility_type] = _first_event_records(parser, names)
    expirations = {
        utility_type: _first_event_records(parser, (name,)) if utility_type in thrown else []
        for utility_type, name in EXPIRE_EVENTS.items()
    }
    try:
        return _tracks_from_rows(rows, events, expirations, rounds, tick_rate)
    except Exception:
        return []


def build_utility_tracks(
    grenades: Any,
    detonations: dict[str, list[dict[str, Any]]],
    expirations: dict[str, list[dict[str, Any]]],
    rounds: list[dict[str, Any]],
    tick_rate: int,
) -> list[dict[str, Any]]:
    """World-unit tracks from ``parse_grenades()`` rows (a DataFrame or a list of dicts)."""
    rows = _projectile_rows(grenades)
    return [] if rows is None else _tracks_from_rows(rows, detonations, expirations, rounds, tick_rate)


def _tracks_from_rows(
    rows: dict[str, list[Any]],
    detonations: dict[str, list[dict[str, Any]]],
    expirations: dict[str, list[dict[str, Any]]],
    rounds: list[dict[str, Any]],
    tick_rate: int,
) -> list[dict[str, Any]]:
    ordered_rounds = sorted(
        (item for item in rounds if _int(item.get("startTick")) is not None),
        key=lambda item: int(item["startTick"]),
    )
    if not ordered_rounds:
        return []
    detonate_index = {key: _event_list(detonations.get(key)) for key in UTILITY_TYPES}
    expire_index = {key: _event_list(expirations.get(key)) for key in EXPIRE_EVENTS}
    used: set[tuple[str, int]] = set()
    tracks: list[dict[str, Any]] = []
    for start, stop in _segments(rows):
        track = _track(
            rows, start, stop, ordered_rounds, max(1, int(tick_rate)),
            detonate_index, expire_index, used,
        )
        if track is not None:
            tracks.append(track)
    tracks.sort(key=lambda item: (item["throwTick"], item["id"]))
    return tracks[:MAX_THROWS]


def _projectile_rows(grenades: Any) -> dict[str, list[Any]] | None:
    """Projectile rows with finite coordinates, sorted by (entity, tick), as columns."""
    import numpy as np
    import pandas as pd

    frame = grenades
    del grenades
    if isinstance(frame, list):
        frame = pd.DataFrame([item for item in frame if isinstance(item, dict)])
    elif not isinstance(frame, pd.DataFrame) and hasattr(frame, "to_pandas"):
        frame = frame.to_pandas()
    if not isinstance(frame, pd.DataFrame):
        return None
    required = {"grenade_type", "grenade_entity_id", "x", "y", "tick"}
    if frame.empty or not required.issubset(frame.columns):
        return None
    frame = frame.loc[frame["grenade_type"].isin(list(PROJECTILE_TYPES))]
    numeric = {
        column: pd.to_numeric(frame[column], errors="coerce")
        for column in ("grenade_entity_id", "x", "y", "tick", *(("z",) if "z" in frame.columns else ()))
    }
    usable = (
        numeric["grenade_entity_id"].notna()
        & numeric["tick"].notna()
        & np.isfinite(numeric["x"].to_numpy(dtype="float64", na_value=np.nan))
        & np.isfinite(numeric["y"].to_numpy(dtype="float64", na_value=np.nan))
    )
    frame = frame.loc[usable]
    if frame.empty:
        return None
    frame = frame.assign(**{column: values.loc[frame.index] for column, values in numeric.items()})
    frame = frame.sort_values(["grenade_entity_id", "tick"], kind="stable")
    return {
        "type": frame["grenade_type"].tolist(),
        "entity": [int(value) for value in frame["grenade_entity_id"].tolist()],
        "tick": [int(value) for value in frame["tick"].tolist()],
        "x": [float(value) for value in frame["x"].tolist()],
        "y": [float(value) for value in frame["y"].tolist()],
        "z": [float(value) if _finite(value) else None for value in frame["z"].tolist()]
        if "z" in frame.columns else [None] * len(frame),
        "steamid": [_player_id(value) for value in frame["steamid"].tolist()]
        if "steamid" in frame.columns else [None] * len(frame),
        "name": [_name(value) for value in frame["name"].tolist()]
        if "name" in frame.columns else [None] * len(frame),
    }


def _parse_grenade_rows(parser: Any) -> Any:
    # grenades=False leaves out the held-grenade weapon rows (no coordinates,
    # ~90 % of the rows); that halves the parse's peak memory on a full match.
    try:
        return parser.parse_grenades(grenades=False)
    except TypeError:
        return parser.parse_grenades()


def _segments(rows: dict[str, list[Any]]) -> list[tuple[int, int]]:
    ticks, entities, types, throwers = rows["tick"], rows["entity"], rows["type"], rows["steamid"]
    segments: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(ticks) + 1):
        if (
            index == len(ticks)
            or entities[index] != entities[index - 1]
            or types[index] != types[index - 1]
            or throwers[index] != throwers[index - 1]
            or ticks[index] - ticks[index - 1] > TRACK_GAP_TICKS
        ):
            segments.append((start, index))
            start = index
    return segments


def _track(
    rows: dict[str, list[Any]],
    start: int,
    stop: int,
    rounds: list[dict[str, Any]],
    tick_rate: int,
    detonate_index: dict[str, list[dict[str, Any]]],
    expire_index: dict[str, list[dict[str, Any]]],
    used: set[tuple[str, int]],
) -> dict[str, Any] | None:
    utility_type = PROJECTILE_TYPES[rows["type"][start]]
    entity = rows["entity"][start]
    thrower = rows["steamid"][start]
    ticks = rows["tick"]
    throw_tick = ticks[start]
    round_info = next((item for item in reversed(rounds) if int(item["startTick"]) <= throw_tick), None)
    if round_info is None:
        return None
    detonation = _match_detonation(
        detonate_index[utility_type], utility_type, entity, thrower, throw_tick, ticks[stop - 1], used,
    )
    if detonation is not None:
        detonate_tick = max(throw_tick, int(detonation["tick"]))
    else:
        detonate_tick = _last_moving_tick(rows, start, stop)
    points = _points(rows, start, stop, detonate_tick, detonation)
    if not points:
        return None
    return {
        "id": f"utility-{utility_type}-{entity}-{throw_tick}",
        "type": utility_type,
        "throwerId": thrower,
        "throwerName": rows["name"][start],
        "roundNumber": int(round_info["roundNumber"]),
        "throwTick": throw_tick,
        "detonateTick": detonate_tick,
        "endTick": _end_tick(
            utility_type, detonation, detonate_tick, tick_rate,
            detonate_index[utility_type], expire_index,
        ),
        "points": points,
    }


def _match_detonation(
    events: list[dict[str, Any]],
    utility_type: str,
    entity: int,
    thrower: str | None,
    throw_tick: int,
    last_tick: int,
    used: set[tuple[str, int]],
) -> dict[str, Any] | None:
    window = [
        (index, event) for index, event in enumerate(events)
        if (utility_type, index) not in used and throw_tick <= event["tick"] <= last_tick + DETONATE_SLACK_TICKS
    ]
    match = next(((index, event) for index, event in window if event.get("entityid") == entity), None)
    if match is None and thrower is not None:
        # The inferno is its own entity: fire is matched by thrower and time,
        # nearest to the projectile's last row (it vanishes as the fire starts).
        match = min(
            ((index, event) for index, event in window if event.get("steamid") == thrower),
            key=lambda item: (abs(item[1]["tick"] - last_tick - 1), item[1]["tick"]),
            default=None,
        )
    if match is None:
        return None
    used.add((utility_type, match[0]))
    return match[1]


def _last_moving_tick(rows: dict[str, list[Any]], start: int, stop: int) -> int:
    xs, ys, zs, ticks = rows["x"], rows["y"], rows["z"], rows["tick"]
    for index in range(stop - 1, start, -1):
        if (xs[index], ys[index], zs[index]) != (xs[index - 1], ys[index - 1], zs[index - 1]):
            return int(ticks[index])
    return int(ticks[start])


def _points(
    rows: dict[str, list[Any]],
    start: int,
    stop: int,
    detonate_tick: int,
    detonation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    xs, ys, zs, ticks = rows["x"], rows["y"], rows["z"], rows["tick"]
    points: list[dict[str, Any]] = []
    last_index = start
    for index in range(start, stop):
        if ticks[index] > detonate_tick:
            break
        last_index = index
        if points and ticks[index] - points[-1]["tick"] < POINT_STEP_TICKS:
            continue
        if points and (xs[index], ys[index], zs[index]) == _xyz(points[-1]):
            continue  # at rest: the next change or the final point says where it went
        points.append(_point(ticks[index], xs[index], ys[index], zs[index]))
    if not points:
        return []
    if points[-1]["tick"] != ticks[last_index]:
        points.append(_point(ticks[last_index], xs[last_index], ys[last_index], zs[last_index]))
    # The landing: the detonate event's own position when it comes after the last row.
    if (
        detonation is not None
        and detonate_tick > points[-1]["tick"]
        and _finite(detonation.get("x"))
        and _finite(detonation.get("y"))
    ):
        points.append(_point(detonate_tick, detonation["x"], detonation["y"], detonation.get("z")))
    return _cap_points(points)


def _end_tick(
    utility_type: str,
    detonation: dict[str, Any] | None,
    detonate_tick: int,
    tick_rate: int,
    detonations: list[dict[str, Any]],
    expire_index: dict[str, list[dict[str, Any]]],
) -> int:
    if utility_type not in EXPIRE_EVENTS:
        return detonate_tick
    if utility_type == "molotov" and detonation is None and detonations:
        return detonate_tick  # fire events exist but none for this one: it burst in the air
    latest = detonate_tick + round(MAX_EFFECT_SECONDS * tick_rate)
    entity = detonation.get("entityid") if detonation is not None else None
    if entity is not None:
        expiry = next(
            (
                event for event in expire_index[utility_type]
                if event.get("entityid") == entity and detonate_tick <= event["tick"] <= latest
            ),
            None,
        )
        if expiry is not None:
            return int(expiry["tick"])
    return detonate_tick + round(FALLBACK_EFFECT_SECONDS[utility_type] * tick_rate)


def _cap_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(points) <= MAX_POINTS_PER_THROW:
        return points
    last = len(points) - 1
    picks = sorted({round(index * last / (MAX_POINTS_PER_THROW - 1)) for index in range(MAX_POINTS_PER_THROW)})
    return [points[index] for index in picks]


def _point(tick: Any, x: Any, y: Any, z: Any) -> dict[str, Any]:
    point: dict[str, Any] = {"tick": int(tick), "x": float(x), "y": float(y)}
    if _finite(z):
        point["z"] = float(z)
    return point


def _xyz(point: dict[str, Any]) -> tuple[Any, Any, Any]:
    return point["x"], point["y"], point.get("z")


def _event_list(records: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        tick = _int(record.get("tick"))
        if tick is None:
            continue
        events.append({
            "tick": tick,
            "entityid": _int(record.get("entityid")),
            "steamid": _player_id(record.get("user_steamid") or record.get("player_steamid")),
            "x": record.get("x"),
            "y": record.get("y"),
            "z": record.get("z"),
        })
    events.sort(key=lambda item: item["tick"])
    return events


def _first_event_records(parser: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    for name in names:
        try:
            value = parser.parse_event(name)
        except Exception:
            continue
        records = _records(value)
        if records:
            return records
    return []


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if hasattr(value, "to_dicts"):
        return list(value.to_dicts())
    if hasattr(value, "to_dict"):
        return [dict(item) for item in value.to_dict("records")]
    return []


def _player_id(value: Any) -> str | None:
    """A thrower id in the frames' form (the decimal SteamID64); None when unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        # float64 cannot hold a SteamID64 exactly; a rounded id would match nobody.
        if not math.isfinite(value) or not value.is_integer() or abs(value) > 2**53:
            return None
        value = int(value)
    text = str(value).strip()
    return text[:MAX_NAME_LENGTH] if text and text not in {"0", "nan", "None"} else None


def _name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:MAX_NAME_LENGTH].strip()
    return name or None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) else None


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


MAX_UTILITY_ID_LENGTH = 128
SIDES = ("T", "CT")
# A throw this long after its round ended is not part of any round (a pause or a
# restart between rounds); normal post-round throws land within a few seconds.
POST_ROUND_THROW_SECONDS = 10.0


def normalize_utility(
    value: Any,
    rounds: list[dict[str, Any]] | None = None,
    tick_rate: int = 64,
) -> list[dict[str, Any]]:
    """Sanitize a stored ``utility`` list (radar-percent points); junk yields ``[]``.

    Drops throws without a known type, a throw tick or a usable point; clamps
    points to 0..100, sorts them and caps them per throw; keeps detonate/end
    ticks ordered after the throw; caps the throw count and de-duplicates ids.
    With the rounds it also ends every effect by the next round's start (the
    round reset removes smokes and fires, whatever the stored end says) and
    drops throws made long after their round ended. Runs at parse time and
    on every load, so replays stored before these rules get them too.
    """
    if not isinstance(value, list):
        return []
    starts = _round_starts(rounds or [])
    ends = _round_ends(rounds or [])
    post_round = round(POST_ROUND_THROW_SECONDS * (tick_rate if tick_rate > 0 else 64))
    throws: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if len(throws) >= MAX_THROWS:
            break
        throw = _normalize_throw(item, rounds or [])
        if throw is None or throw["id"] in seen:
            continue
        if _between_rounds(throw, ends, post_round):
            continue
        next_start = next((start for start, _number in starts if start > throw["throwTick"]), None)
        if next_start is not None and throw["endTick"] > next_start:
            throw["endTick"] = max(throw["detonateTick"], next_start)
        seen.add(throw["id"])
        throws.append(throw)
    throws.sort(key=lambda item: (item["throwTick"], item["id"]))
    return throws


def _round_starts(rounds: list[dict[str, Any]]) -> list[tuple[int, int]]:
    return sorted(
        (start, number)
        for item in rounds
        if isinstance(item, dict)
        and (start := _int(item.get("startTick"))) is not None
        and (number := _int(item.get("roundNumber"))) is not None
    )


def _round_ends(rounds: list[dict[str, Any]]) -> dict[int, int]:
    """Round number -> end tick, for rounds whose end is known (after their start)."""
    ends: dict[int, int] = {}
    for item in rounds:
        if not isinstance(item, dict):
            continue
        number, start, end = _int(item.get("roundNumber")), _int(item.get("startTick")), _int(item.get("endTick"))
        if number is not None and start is not None and end is not None and end > start:
            ends[number] = end
    return ends


def _between_rounds(throw: dict[str, Any], ends: dict[int, int], post_round: int) -> bool:
    end = ends.get(throw["roundNumber"])
    return end is not None and throw["throwTick"] > end + post_round


def _normalize_throw(item: Any, rounds: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    utility_type = item.get("type")
    throw_tick = _int(item.get("throwTick"))
    if utility_type not in UTILITY_TYPES or throw_tick is None or throw_tick < 0:
        return None
    points = _normalize_points(item.get("points"))
    if not points:
        return None
    detonate_tick = _int(item.get("detonateTick"))
    if detonate_tick is None or detonate_tick < throw_tick:
        detonate_tick = max(throw_tick, points[-1]["tick"])
    end_tick = _int(item.get("endTick"))
    if end_tick is None or end_tick < detonate_tick:
        end_tick = detonate_tick
    raw_id = item.get("id")
    throw_id = (
        raw_id.strip()[:MAX_UTILITY_ID_LENGTH]
        if isinstance(raw_id, str) and raw_id.strip()
        else f"utility-{utility_type}-{throw_tick}-{len(points)}"
    )
    round_number = _int(item.get("roundNumber"))
    if round_number is None or round_number <= 0:
        round_number = _round_for_tick(throw_tick, rounds)
    side = item.get("throwerSide")
    thrower_id = item.get("throwerId")
    return {
        "id": throw_id,
        "type": utility_type,
        "throwerId": (thrower_id.strip()[:MAX_NAME_LENGTH] or None) if isinstance(thrower_id, str) else None,
        "throwerName": _name(item.get("throwerName")),
        "throwerSide": side if side in SIDES else None,
        "roundNumber": round_number,
        "throwTick": throw_tick,
        "detonateTick": detonate_tick,
        "endTick": end_tick,
        "points": points,
    }


def _normalize_points(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    points: dict[int, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        tick = _int(raw.get("tick"))
        if tick is None or tick < 0 or tick in points or not _finite(raw.get("x")) or not _finite(raw.get("y")):
            continue
        point: dict[str, Any] = {
            "tick": tick,
            "x": round(max(0.0, min(100.0, float(raw["x"]))), 2),
            "y": round(max(0.0, min(100.0, float(raw["y"]))), 2),
        }
        if _finite(raw.get("z")):
            point["z"] = round(float(raw["z"]), 1)
        points[tick] = point
    return _cap_points([points[tick] for tick in sorted(points)])


def _round_for_tick(tick: int, rounds: list[dict[str, Any]]) -> int:
    starts = _round_starts(rounds)
    current = starts[0][1] if starts else 1
    for start, number in starts:
        if start <= tick:
            current = number
    return current
