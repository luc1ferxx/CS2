"""Per-player key inputs (replay contract v3 ``inputs``).

A demo's user commands carry each player's held buttons on every tick in
``usercmd_buttonstate_1`` (the Source 2 InputBitMask). Only the nine keys the
key panel shows are kept, as change points: ``[tick, mask]`` pairs sorted by
tick, where a mask holds until the next pair. Short presses (a counter-strafe
tap) are kept as they are. A track starts at the player's first tick in the
first round and ends at the last round's end. From a death until the next round
start the mask is 0 -- a dead player's commands drive the spectator camera -- so
a death while a key is held shows up as ``[deathTick, 0]``.

The raw mask is decoded here, not demoparser2's derived button names: its 0.42
map calls 0x10000 (walk) SCOREBOARD and has no duck or jump.

Best effort: demos without usercmd data (matchmaking, POV and some third-party
recordings) yield ``{}`` and never fail the parse.
"""

from __future__ import annotations

import bisect
import math
from typing import Any

INPUT_PROP = "usercmd_buttonstate_1"
INPUT_SOURCE = "usercmd"

BUTTON_ATTACK = 1
BUTTON_JUMP = 2
BUTTON_DUCK = 4
BUTTON_FORWARD = 8
BUTTON_BACK = 16
BUTTON_LEFT = 512
BUTTON_RIGHT = 1024
BUTTON_ATTACK2 = 2048
BUTTON_WALK = 0x10000
DISPLAY_MASK = (
    BUTTON_ATTACK | BUTTON_JUMP | BUTTON_DUCK | BUTTON_FORWARD | BUTTON_BACK
    | BUTTON_LEFT | BUTTON_RIGHT | BUTTON_ATTACK2 | BUTTON_WALK
)
# Every displayed bit sits below this, so a float mask's low bits can be taken
# with fmod before the integer cast (float64 holds the mask exactly below 2**53).
_DISPLAY_SPAN = 1 << 17

# A whole Mirage match is ~8.4k change points per player; a track at the cap is
# flagged in the replay diagnostics as ``inputsCapped``.
MAX_INPUT_ENTRIES_PER_PLAYER = 60000
MAX_INPUT_PLAYERS = 64
MAX_PLAYER_ID_LENGTH = 64

type InputTrack = list[list[int]]


def parse_player_inputs(
    parser: Any,
    rounds: list[dict[str, Any]],
    deaths: list[dict[str, Any]],
) -> dict[str, InputTrack]:
    """Key change points per player id (the frames' id); ``{}`` when the demo has no usercmd data.

    One ``parse_ticks`` call over every tick with the one prop. The DataFrame is
    reduced to numpy columns inside ``_input_columns`` and freed when it returns,
    before any track is built.
    """
    try:
        columns = _input_columns(parser.parse_ticks([INPUT_PROP]))
    except Exception:
        return {}
    if columns is None:
        return {}
    try:
        return _tracks_from_columns(columns, rounds, deaths)
    except Exception:
        return {}


def build_player_inputs(
    rows: Any,
    rounds: list[dict[str, Any]],
    deaths: list[dict[str, Any]],
) -> dict[str, InputTrack]:
    """Tracks from ``parse_ticks`` rows (a DataFrame or a list of dicts)."""
    columns = _input_columns(rows)
    return {} if columns is None else _tracks_from_columns(columns, rounds, deaths)


def normalize_player_inputs(value: Any) -> dict[str, InputTrack]:
    """Sanitize a stored or parsed ``inputs`` map; junk yields ``{}``.

    Keeps ``[tick, mask]`` pairs of plain non-negative ints, masks them to the
    displayed bits, sorts by tick (the first of two pairs at one tick wins),
    collapses consecutive equal masks and caps players and pairs per player.
    Idempotent, and a well-formed track passes through in one loop.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, InputTrack] = {}
    for raw_id, raw_track in value.items():
        if len(result) >= MAX_INPUT_PLAYERS:
            break
        player_id = str(raw_id).strip()[:MAX_PLAYER_ID_LENGTH]
        if not player_id or player_id in result or not isinstance(raw_track, list):
            continue
        track = _normalize_track(raw_track)
        if track:
            result[player_id] = track
    return result


def input_track_capped(track: Any) -> bool:
    return isinstance(track, list) and len(track) >= MAX_INPUT_ENTRIES_PER_PLAYER


def _normalize_track(raw_track: list[Any]) -> InputTrack:
    # Fast path: a stored replay is loaded on every replay request.
    if _well_formed(raw_track):
        return raw_track[:MAX_INPUT_ENTRIES_PER_PLAYER]
    pairs: list[tuple[int, int]] = []
    ordered = True
    previous_tick = -1
    for entry in raw_track:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        tick, mask = entry
        # type() rather than isinstance(): bools are ints, floats and strings are junk.
        if type(tick) is not int or type(mask) is not int or tick < 0 or mask < 0:
            continue
        if tick < previous_tick:
            ordered = False
        previous_tick = tick
        pairs.append((tick, mask & DISPLAY_MASK))
    if not ordered:
        pairs.sort(key=lambda pair: pair[0])  # stable: the first pair at a tick stays first
    track: InputTrack = []
    last_tick = -1
    last_mask = -1
    for tick, mask in pairs:
        if tick == last_tick:
            continue
        last_tick = tick
        if mask == last_mask:
            continue
        last_mask = mask
        track.append([tick, mask])
        if len(track) >= MAX_INPUT_ENTRIES_PER_PLAYER:
            break
    return track


def _well_formed(raw_track: list[Any]) -> bool:
    """Already normalized: ``[int, int]`` lists, ticks strictly rising, masks displayed bits only and never repeated."""
    last_tick = -1
    last_mask = -1
    hidden_bits = ~DISPLAY_MASK
    for entry in raw_track:
        if type(entry) is not list or len(entry) != 2:
            return False
        tick, mask = entry
        if type(tick) is not int or type(mask) is not int:
            return False
        # mask & hidden_bits also catches negative masks.
        if tick <= last_tick or mask == last_mask or mask & hidden_bits:
            return False
        last_tick = tick
        last_mask = mask
    return True


def _input_columns(frame: Any) -> dict[str, Any] | None:
    """Usable rows as numpy columns: tick, mask (displayed bits), group code; plus group ids.

    The player id mirrors the frames' rule: the SteamID64 when it is usable,
    else the player name.
    """
    import numpy as np
    import pandas as pd

    if isinstance(frame, list):
        frame = pd.DataFrame([item for item in frame if isinstance(item, dict)])
    elif not isinstance(frame, pd.DataFrame) and hasattr(frame, "to_pandas"):
        frame = frame.to_pandas()
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if INPUT_PROP not in frame.columns or "tick" not in frame.columns:
        return None
    masks = _display_masks(frame[INPUT_PROP])
    if masks is None:
        return None
    ticks = pd.to_numeric(frame["tick"], errors="coerce").to_numpy(dtype="float64", na_value=np.nan)

    count = len(frame)
    groups = np.full(count, -1, dtype=np.int64)
    ids: list[str] = []
    if "steamid" in frame.columns:
        codes, uniques = pd.factorize(frame["steamid"], use_na_sentinel=True)
        usable = np.array([_player_id(value) is not None for value in uniques], dtype=bool)
        ids = [player_id for value in uniques if (player_id := _player_id(value)) is not None]
        remap = np.cumsum(usable) - 1
        known = codes >= 0
        known[known] = usable[codes[known]]
        groups[known] = remap[codes[known]]
    if "name" in frame.columns:
        nameless = np.flatnonzero(groups < 0)
        if nameless.size:
            names = [_name(value) for value in frame["name"].to_numpy()[nameless]]
            by_name: dict[str, int] = {}
            for row, name in zip(nameless.tolist(), names, strict=True):
                if name is None:
                    continue
                code = by_name.get(name)
                if code is None:
                    code = by_name[name] = len(ids)
                    ids.append(name)
                groups[row] = code

    valid = (groups >= 0) & (masks >= 0) & (np.where(np.isfinite(ticks), ticks, -1.0) >= 0)
    if not valid.any():
        return None
    return {
        "tick": ticks[valid].astype(np.int64),
        "mask": masks[valid],
        "group": groups[valid],
        "ids": ids,
    }


def _display_masks(series: Any) -> Any:
    """The displayed bits of each row as int64, -1 where the value is unusable; None for a junk column."""
    import numpy as np
    import pandas as pd

    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return None
    if pd.api.types.is_integer_dtype(dtype) and not isinstance(dtype, pd.api.extensions.ExtensionDtype):
        values = series.to_numpy()
        if pd.api.types.is_unsigned_integer_dtype(dtype):
            # A typed operand: uint64 mixed with a signed int would go through float64.
            return (values.astype(np.uint64) & np.uint64(DISPLAY_MASK)).astype(np.int64)
        values = values.astype(np.int64)
        return np.where(values >= 0, values & np.int64(DISPLAY_MASK), -1)
    numeric = pd.to_numeric(series, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    masks = np.full(len(numeric), -1, dtype=np.int64)
    usable = np.isfinite(numeric)
    usable[usable] = numeric[usable] >= 0
    masks[usable] = np.fmod(numeric[usable], float(_DISPLAY_SPAN)).astype(np.int64) & DISPLAY_MASK
    return masks


def _tracks_from_columns(
    columns: dict[str, Any],
    rounds: list[dict[str, Any]],
    deaths: list[dict[str, Any]],
) -> dict[str, InputTrack]:
    import numpy as np

    starts = sorted({tick for item in rounds if isinstance(item, dict) and (tick := _int(item.get("startTick"))) is not None})
    ends = [tick for item in rounds if isinstance(item, dict) and (tick := _int(item.get("endTick"))) is not None]
    first_tick = starts[0] if starts else None
    last_tick = max(ends) if ends else None
    deaths_by_player = _death_ticks(deaths)

    ticks, masks, groups, ids = columns["tick"], columns["mask"], columns["group"], columns["ids"]
    order = np.lexsort((ticks, groups))
    ticks, masks, groups = ticks[order], masks[order], groups[order]
    boundaries = np.flatnonzero(groups[1:] != groups[:-1]) + 1
    result: dict[str, InputTrack] = {}
    for start, stop in zip(
        [0, *boundaries.tolist()], [*boundaries.tolist(), len(groups)], strict=True,
    ):
        if len(result) >= MAX_INPUT_PLAYERS:
            break
        player_id = ids[int(groups[start])][:MAX_PLAYER_ID_LENGTH]
        if player_id in result:
            continue
        track = _player_track(
            ticks[start:stop], masks[start:stop], deaths_by_player.get(player_id, []),
            starts, first_tick, last_tick,
        )
        if track:
            result[player_id] = track
    return result


def _player_track(
    ticks: Any,
    masks: Any,
    deaths: list[int],
    starts: list[int],
    first_tick: int | None,
    last_tick: int | None,
) -> InputTrack:
    import numpy as np

    # Ticks arrive sorted; of two rows at one tick the first one wins.
    keep = np.ones(len(ticks), dtype=bool)
    keep[1:] = ticks[1:] != ticks[:-1]
    ticks, masks = ticks[keep], masks[keep]
    low = int(np.searchsorted(ticks, first_tick, "left")) if first_tick is not None else 0
    high = int(np.searchsorted(ticks, last_tick, "right")) if last_tick is not None else len(ticks)
    ticks, masks = ticks[low:high], masks[low:high].copy()
    if not len(ticks):
        return []

    in_range = [
        tick for tick in deaths
        if (first_tick is None or tick >= first_tick) and (last_tick is None or tick <= last_tick)
    ]
    # A death tick without a row of its own still gets its [tick, 0] point.
    missing = [
        tick for tick in in_range
        if (index := int(np.searchsorted(ticks, tick, "left"))) == len(ticks) or int(ticks[index]) != tick
    ]
    if missing:
        positions = np.searchsorted(ticks, missing, "left")
        ticks = np.insert(ticks, positions, missing)
        masks = np.insert(masks, positions, 0)
    for tick in in_range:
        respawn_index = bisect.bisect_right(starts, tick)
        begin = int(np.searchsorted(ticks, tick, "left"))
        end = (
            int(np.searchsorted(ticks, starts[respawn_index], "left"))
            if respawn_index < len(starts) else len(ticks)
        )
        masks[begin:end] = 0

    changes = np.ones(len(masks), dtype=bool)
    changes[1:] = masks[1:] != masks[:-1]
    kept = np.flatnonzero(changes)[:MAX_INPUT_ENTRIES_PER_PLAYER]
    track: InputTrack = np.column_stack((ticks[kept], masks[kept])).astype(np.int64).tolist()
    return track


def _death_ticks(deaths: list[dict[str, Any]]) -> dict[str, list[int]]:
    by_player: dict[str, set[int]] = {}
    for record in deaths:
        if not isinstance(record, dict):
            continue
        tick = _int(record.get("tick"))
        player_id = _player_id(record.get("user_steamid")) or _name(record.get("user_name"))
        if tick is None or tick < 0 or player_id is None:
            continue
        by_player.setdefault(player_id[:MAX_PLAYER_ID_LENGTH], set()).add(tick)
    return {player_id: sorted(ticks) for player_id, ticks in by_player.items()}


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


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) else None
