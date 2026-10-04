"""Per-player gun shots (replay contract v5 ``shots``).

Every ``weapon_fire`` event carries the shooter's props at the moment of the
shot; one ``parse_event`` call reads ``is_airborne`` and ``velocity_X/Y`` with
it. (A handful of shots per match come back with NaN velocity; they are
dropped.) Only gun shots are kept: the weapon key is the event's ``weapon``
without the ``weapon_`` prefix, and knives, grenades, C4 and the taser are not
shots.

A track is a list of ``[tick, speed, flags, weapon]`` rows sorted by tick:
``speed`` is ``round(hypot(vx, vy))`` in units per second clamped to 0..1000,
``flags`` bit 1 is airborne, ``weapon`` a ``[a-z0-9_]{1,32}`` key such as
``ak47``, ``m4a1_silencer`` or ``deagle``. Tracks are keyed by the frames'
player id (the decimal SteamID64, else the player name). Every shot of the
demo is kept, warmup included; consumers bucket them by round.

Best effort: a demo without the event or its velocity props yields ``{}`` and
never fails the parse.
"""

from __future__ import annotations

import math
import re
from typing import Any

SHOT_EVENT = "weapon_fire"
SHOT_SOURCE = "weapon_fire"
SHOT_PROP_SETS = (["is_airborne", "velocity_X", "velocity_Y"], ["velocity_X", "velocity_Y"])

SHOT_FLAG_AIRBORNE = 1
SHOT_FLAG_MASK = SHOT_FLAG_AIRBORNE
MAX_SHOT_SPEED = 1000
# A whole pro match is a few hundred gun shots per player; a track at the cap is
# flagged in the replay diagnostics as ``shotsCapped``.
MAX_SHOTS_PER_PLAYER = 20000
MAX_SHOT_PLAYERS = 64
MAX_PLAYER_ID_LENGTH = 64

WEAPON_KEY_PATTERN = re.compile(r"[a-z0-9_]{1,32}")
NON_GUN_WEAPONS = frozenset({
    "bayonet",
    "smokegrenade",
    "flashbang",
    "hegrenade",
    "molotov",
    "incgrenade",
    "decoy",
    "c4",
    "taser",
})

type ShotTrack = list[list[int | str]]
type _ShotRow = tuple[int, int, int, str]


def parse_shots(parser: Any) -> dict[str, ShotTrack]:
    """Gun shots per player id; ``{}`` when the demo has no usable ``weapon_fire`` rows.

    Without ``is_airborne`` when the first call fails; without velocity there is
    no speed, so no further fallback.
    """
    for props in SHOT_PROP_SETS:
        try:
            rows = parser.parse_event(SHOT_EVENT, player=list(props))
        except Exception:
            continue
        try:
            return build_shots(rows)
        except Exception:
            return {}
    return {}


def build_shots(rows: Any) -> dict[str, ShotTrack]:
    """Tracks from ``weapon_fire`` rows (a DataFrame or a list of dicts)."""
    by_player: dict[str, list[_ShotRow]] = {}
    for record in _records(rows):
        shot = _shot_from_record(record)
        if shot is None:
            continue
        player_id = _player_id(record.get("user_steamid")) or _name(record.get("user_name"))
        if player_id is None:
            continue
        shots = by_player.get(player_id)
        if shots is None:
            if len(by_player) >= MAX_SHOT_PLAYERS:
                continue
            shots = by_player[player_id] = []
        shots.append(shot)
    result: dict[str, ShotTrack] = {}
    for player_id, shots in by_player.items():
        track = _sorted_track(shots)
        if track:
            result[player_id] = track
    return result


def normalize_shots(value: Any) -> dict[str, ShotTrack]:
    """Sanitize a stored or parsed ``shots`` map; junk yields ``{}``.

    Keeps ``[tick, speed, flags, weapon]`` rows of plain ints and a gun weapon
    key, clamps the speed to 0..1000, keeps the known flag bits, sorts, drops
    identical rows and caps players and rows per player. Idempotent, and a
    well-formed track passes through in one loop.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, ShotTrack] = {}
    for raw_id, raw_track in value.items():
        if len(result) >= MAX_SHOT_PLAYERS:
            break
        player_id = str(raw_id).strip()[:MAX_PLAYER_ID_LENGTH]
        if not player_id or player_id in result or not isinstance(raw_track, list):
            continue
        track = _normalize_track(raw_track)
        if track:
            result[player_id] = track
    return result


def shot_track_capped(track: Any) -> bool:
    return isinstance(track, list) and len(track) >= MAX_SHOTS_PER_PLAYER


def shot_count(shots: Any) -> int:
    if not isinstance(shots, dict):
        return 0
    return sum(len(track) for track in shots.values() if isinstance(track, list))


def weapon_key(value: Any) -> str | None:
    """The gun key of an event's ``weapon`` (``weapon_ak47`` -> ``ak47``); None for non-guns and junk."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower().removeprefix("weapon_")
    return key if _is_gun_key(key) else None


def _is_gun_key(key: str) -> bool:
    return (
        WEAPON_KEY_PATTERN.fullmatch(key) is not None
        and not key.startswith("knife")
        and key not in NON_GUN_WEAPONS
    )


def _shot_from_record(record: dict[str, Any]) -> _ShotRow | None:
    weapon = weapon_key(record.get("weapon"))
    tick = _int(record.get("tick"))
    if weapon is None or tick is None or tick < 0:
        return None
    vx = _float(_first_present(record, ("user_velocity_X", "velocity_X")))
    vy = _float(_first_present(record, ("user_velocity_Y", "velocity_Y")))
    if vx is None or vy is None:
        return None
    speed = min(MAX_SHOT_SPEED, round(math.hypot(vx, vy)))
    airborne = _bool(_first_present(record, ("user_is_airborne", "is_airborne")))
    return tick, speed, SHOT_FLAG_AIRBORNE if airborne else 0, weapon


def _sorted_track(rows: list[_ShotRow]) -> ShotTrack:
    rows.sort()
    track: ShotTrack = []
    previous: _ShotRow | None = None
    for row in rows:
        if row == previous:
            continue
        previous = row
        track.append(list(row))
        if len(track) >= MAX_SHOTS_PER_PLAYER:
            break
    return track


def _normalize_track(raw_track: list[Any]) -> ShotTrack:
    # Fast path: a stored replay is loaded on every replay request.
    if _well_formed(raw_track):
        return raw_track[:MAX_SHOTS_PER_PLAYER]
    rows: list[_ShotRow] = []
    for entry in raw_track:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            continue
        tick, speed, flags, weapon = entry
        # type() rather than isinstance(): bools are ints, floats and strings are junk.
        if type(tick) is not int or type(speed) is not int or type(flags) is not int:
            continue
        if tick < 0 or flags < 0 or not isinstance(weapon, str) or not _is_gun_key(weapon):
            continue
        rows.append((tick, max(0, min(MAX_SHOT_SPEED, speed)), flags & SHOT_FLAG_MASK, weapon))
    return _sorted_track(rows)


def _well_formed(raw_track: list[Any]) -> bool:
    """Already normalized: plain rows in strictly rising order, values in range, gun keys only."""
    previous: _ShotRow | None = None
    known_weapons: set[str] = set()
    for entry in raw_track:
        if type(entry) is not list or len(entry) != 4:
            return False
        tick, speed, flags, weapon = entry
        if type(tick) is not int or type(speed) is not int or type(flags) is not int or type(weapon) is not str:
            return False
        if tick < 0 or speed < 0 or speed > MAX_SHOT_SPEED or flags < 0 or flags & ~SHOT_FLAG_MASK:
            return False
        if weapon not in known_weapons:
            if not _is_gun_key(weapon):
                return False
            known_weapons.add(weapon)
        row = (tick, speed, flags, weapon)
        if previous is not None and row <= previous:
            return False
        previous = row
    return True


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if hasattr(value, "to_dicts"):
        return list(value.to_dicts())
    if hasattr(value, "to_dict"):
        return [dict(item) for item in value.to_dict("records")]
    return []


def _first_present(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _player_id(value: Any) -> str | None:
    """A player id in the frames' form (the decimal SteamID64); None when unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        # float64 cannot hold a SteamID64 exactly; a rounded id would match nobody.
        if not math.isfinite(value) or not value.is_integer() or abs(value) > 2**53:
            return None
        value = int(value)
    text = str(value).strip()
    return text[:MAX_PLAYER_ID_LENGTH] if text and text not in {"0", "nan", "None", "<NA>"} else None


def _name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:MAX_PLAYER_ID_LENGTH].strip()
    return name or None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    # numpy.bool_ from a DataFrame column.
    if getattr(getattr(value, "dtype", None), "kind", None) == "b":
        return bool(value)
    return False


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None
