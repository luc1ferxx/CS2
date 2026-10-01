"""Per-player equipment and economy change points (replay contract v2 ``playerStates``).

The parser samples money, armor, helmet, defuser, the active weapon, carried
grenades and equipment value at the replay's frame ticks. Only change points are
kept: a player gets a new entry when at least one field differs from their
previous entry, so the state at tick t is the last entry with ``tick <= t``.
Every entry is a full snapshot of the fields the demo carries; a field missing
from an entry is unknown for that demo, not zero.
"""

from __future__ import annotations

import math
from typing import Any

GRENADE_KEYS = ("smoke", "flash", "he", "molotov", "decoy")
# Display names demoparser2 reports in `inventory`, plus the weapon class names
# some builds use. Molotov and incendiary share one key: same effect on the map.
_GRENADE_NAMES = {
    "smoke grenade": "smoke",
    "weapon_smokegrenade": "smoke",
    "flashbang": "flash",
    "weapon_flashbang": "flash",
    "high explosive grenade": "he",
    "he grenade": "he",
    "weapon_hegrenade": "he",
    "molotov": "molotov",
    "weapon_molotov": "molotov",
    "incendiary grenade": "molotov",
    "weapon_incgrenade": "molotov",
    "decoy grenade": "decoy",
    "weapon_decoy": "decoy",
}
# The state props read alongside the frame props; the parser falls back to the
# frame props alone when a demoparser2 build rejects any of them.
PLAYER_STATE_PROPS = (
    "balance",
    "armor_value",
    "has_helmet",
    "has_defuser",
    "active_weapon_name",
    "current_equip_value",
)
MAX_WEAPON_NAME_LENGTH = 32
MAX_GRENADES_PER_ENTRY = 8
MAX_STATE_ENTRIES_PER_PLAYER = 20000
MAX_STATE_PLAYERS = 64
MAX_PLAYER_ID_LENGTH = 64
MAX_MONEY = 65535
MAX_ARMOR = 255
MAX_EQUIP_VALUE = 100000
_STATE_FIELDS = ("money", "armor", "helmet", "defuser", "weapon", "grenades", "equipValue")


def grenade_keys(inventory: Any) -> list[str] | None:
    """Carried grenades as contract keys, one entry per grenade, in a fixed order.

    ``None`` when the record carries no usable inventory (unknown), ``[]`` when it
    carries one without grenades.
    """
    if hasattr(inventory, "tolist"):
        inventory = inventory.tolist()
    if not isinstance(inventory, (list, tuple)):
        return None
    keys = [
        key
        for item in inventory
        if isinstance(item, str) and (key := _GRENADE_NAMES.get(item.strip().lower())) is not None
    ]
    return sorted(keys, key=GRENADE_KEYS.index)[:MAX_GRENADES_PER_ENTRY]


def build_player_states(tick_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Change points per player id (the same id the frames use) from sampled tick records."""
    samples: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for record in tick_records:
        tick = _optional_int(record.get("tick"))
        player = record.get("steamid") or record.get("player_steamid") or record.get("name")
        if tick is None or tick < 0 or player is None:
            continue
        state = _state_from_record(record)
        if state:
            samples.setdefault(str(player), []).append((tick, state))
    return {
        player_id: _change_points(entries)
        for player_id, entries in samples.items()
    }


def normalize_player_states(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Sanitize a stored or parsed ``playerStates`` map; junk yields ``{}``.

    Keeps finite, in-range numbers, bools, capped weapon names and known grenade
    keys; drops malformed entries; sorts by tick and re-collapses entries that no
    longer differ from their predecessor. Caps players and entries per player.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for raw_id, raw_entries in value.items():
        if len(result) >= MAX_STATE_PLAYERS:
            break
        player_id = str(raw_id).strip()[:MAX_PLAYER_ID_LENGTH]
        if not player_id or not isinstance(raw_entries, list):
            continue
        entries: list[tuple[int, dict[str, Any]]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            tick = _optional_int(raw.get("tick"))
            if tick is None or tick < 0:
                continue
            state = _sanitize_state(raw)
            if state:
                entries.append((tick, state))
        points = _change_points(entries)
        if points:
            result[player_id] = points
    return result


def _state_from_record(record: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if "balance" in record:
        state["money"] = record.get("balance")
    if "armor_value" in record:
        state["armor"] = record.get("armor_value")
    if "has_helmet" in record:
        state["helmet"] = record.get("has_helmet")
    if "has_defuser" in record:
        state["defuser"] = record.get("has_defuser")
    if "active_weapon_name" in record:
        state["weapon"] = record.get("active_weapon_name")
    if "inventory" in record:
        grenades = grenade_keys(record.get("inventory"))
        if grenades is not None:
            state["grenades"] = grenades
    if "current_equip_value" in record:
        state["equipValue"] = record.get("current_equip_value")
    return _sanitize_state(state)


def _sanitize_state(raw: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for key, upper in (("money", MAX_MONEY), ("armor", MAX_ARMOR), ("equipValue", MAX_EQUIP_VALUE)):
        number = _bounded_int(raw.get(key), upper)
        if number is not None:
            state[key] = number
    for key in ("helmet", "defuser"):
        flag = _safe_bool(raw.get(key))
        if flag is not None:
            state[key] = flag
    if "weapon" in raw:
        weapon = raw.get("weapon")
        if weapon is None:
            state["weapon"] = None
        elif isinstance(weapon, str):
            name = " ".join(weapon.split())[:MAX_WEAPON_NAME_LENGTH].strip()
            state["weapon"] = name or None
    grenades = raw.get("grenades")
    if isinstance(grenades, list):
        state["grenades"] = sorted(
            (item for item in grenades if isinstance(item, str) and item in GRENADE_KEYS),
            key=GRENADE_KEYS.index,
        )[:MAX_GRENADES_PER_ENTRY]
    return state


def _change_points(entries: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    seen_tick: int | None = None
    # Stable sort: of two samples at one tick, the first one wins.
    for tick, state in sorted(entries, key=lambda item: item[0]):
        if tick == seen_tick:
            continue
        seen_tick = tick
        if state != previous:
            points.append({"tick": tick, **{key: state[key] for key in _STATE_FIELDS if key in state}})
            previous = state
            if len(points) >= MAX_STATE_ENTRIES_PER_PLAYER:
                break
    return points


def _bounded_int(value: Any, upper: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return max(0, min(upper, int(number)))


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, bool):
            return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None
