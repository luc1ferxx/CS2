"""Each team's buy type per round: a Python port of `frontend/lib/round-economy.ts`.

The review page's file is the reference; this port reads the same replay
contract fields with the same definitions (see that file's header), and the
shared cases in `fixtures/round-economy/` pin both: `backend/tests/test_round_economy.py`
and `frontend/lib/round-economy-fixtures.test.mjs` run every one of them. Change
the two files and the fixtures together.

- Teams and each round's sides come from the side rule (`match_side_rules` in
  `services/demo_service/match_summary.py`); a side is never inferred from the
  round number.
- Buy tick: `freezeEndTick` when it lies within [startTick, endTick]; otherwise
  the first frame tick of the round at or after startTick + 20 s, or that tick
  itself, capped at endTick. No startTick and no usable freezeEndTick: the
  round is left out.
- A team's players in a round: members with a frame filed under the round and
  a known equipValue at the buy tick (state at t = last change point with
  tick <= t). An unknown state is left out of n and both sums; n = 0 leaves the
  kind unknown (None).
- Kind, per-player thresholds times n, first match wins: pistol (round 1 and
  the first side switch up to round 16), full (equip >= 4000n), eco
  (equip < 1000n, or < 2000n with money >= 1000n), force (money < 1000n),
  half (the rest).
- No playerStates (v1 replays), no equipValue anywhere, no teams or no known
  kind at all: [] . Best effort: malformed data is skipped, never raised on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import isfinite
from typing import Any

from app.parser.replay_contract import normalize_replay_contract

BUY_FALLBACK_SECONDS = 20
# The latest regulation half-time switch (MR15); a first switch after it is overtime.
LAST_REGULATION_SWITCH_ROUND = 16
FULL_PER_PLAYER = 4000
HALF_PER_PLAYER = 2000
ECO_PER_PLAYER = 1000
MONEY_KEPT_PER_PLAYER = 1000
TEAM_KEYS = ("A", "B")
SIDES = ("T", "CT")

RoundEconomy = dict[str, Any]


def round_economies(replay: Mapping[str, Any]) -> list[RoundEconomy]:
    """Per round: {roundNumber, freezeEndTick (the buy tick), teams: [A, B]}.

    Each team is {teamKey, side, equipValue, money, players, kind, won}, the
    shape `roundEconomies` in round-economy.ts returns.
    """
    source = replay if isinstance(replay, Mapping) else {}
    # What the review page reads: the served rounds (a missing winner reads CT),
    # tick rate and player states. Frames are read as stored; serving only
    # touches their optional fields.
    served = normalize_replay_contract(
        {key: source[key] for key in ("rounds", "tickRate", "playerStates") if key in source}
    )
    if not _has_equip_values(served.get("playerStates")):
        return []
    tracks = _state_tracks(served.get("playerStates"))

    # Imported here: the demo_service package imports the analyzer (coaching
    # recompute), so a module-level import would be circular.
    from app.services.demo_service.match_summary import match_side_rules

    rules = match_side_rules(source)
    if not rules.has_teams:
        return []

    def side_of(key: str, round_number: float) -> str | None:
        return rules.team_side(key, round_number)

    rounds: list[Mapping[str, Any]] = []
    seen: set[float] = set()
    ordered = sorted(
        (item for item in served.get("rounds", []) if isinstance(item, Mapping) and _is_rule_number(item.get("roundNumber"))),
        key=lambda item: item["roundNumber"],
    )
    for item in ordered:
        number = item["roundNumber"]
        if number in seen:
            continue
        seen.add(number)
        rounds.append(item)

    frame_ticks: dict[float, list[float]] = {}
    present: dict[float, list[str]] = {}
    present_sets: dict[float, set[str]] = {}
    frames = source.get("frames")
    for frame in frames if isinstance(frames, list) else []:
        if not isinstance(frame, Mapping) or not _is_finite_number(frame.get("roundNumber")):
            continue
        number = frame["roundNumber"]
        if _is_finite_number(frame.get("tick")):
            frame_ticks.setdefault(number, []).append(frame["tick"])
        round_players = present.setdefault(number, [])
        known = present_sets.setdefault(number, set())
        frame_players = frame.get("players")
        for player in frame_players if isinstance(frame_players, list) else []:
            player_id = player.get("id") if isinstance(player, Mapping) else None
            if isinstance(player_id, str) and player_id and player_id not in known:
                known.add(player_id)
                round_players.append(player_id)
    for ticks in frame_ticks.values():
        ticks.sort()

    pistol_rounds = _pistol_round_numbers(rounds, lambda number: side_of("A", number))
    tick_rate: Any = served.get("tickRate")
    rate: float = tick_rate if _is_finite_number(tick_rate) and tick_rate > 0 else 64
    result: list[RoundEconomy] = []
    any_kind = False
    for item in rounds:
        number = item["roundNumber"]
        tick = _buy_tick(item, frame_ticks.get(number, []), rate)
        if tick is None:
            continue
        teams = []
        for key in TEAM_KEYS:
            equip_value: float = 0
            money: float = 0
            players = 0
            for player_id in present.get(number, []):
                if rules.team_of.get(player_id) != key:
                    continue
                state = _state_at(tracks.get(player_id, []), tick)
                if state is None or not _is_finite_number(state.get("equipValue")):
                    continue
                players += 1
                equip_value += state["equipValue"]
                if _is_finite_number(state.get("money")):
                    money += state["money"]
            side = side_of(key, number)
            if players == 0:
                kind = None
            elif number in pistol_rounds:
                kind = "pistol"
            else:
                kind = buy_kind(equip_value, money, players)
            if kind:
                any_kind = True
            winner = item.get("winnerSide")
            won = side == winner if side and winner in SIDES else None
            teams.append({
                "teamKey": key,
                "side": side,
                "equipValue": equip_value,
                "money": money,
                "players": players,
                "kind": kind,
                "won": won,
            })
        result.append({"roundNumber": number, "freezeEndTick": tick, "teams": teams})
    return result if any_kind else []


def buy_kinds_by_side(replay: Mapping[str, Any]) -> dict[int, dict[str, str | None]]:
    """Round number -> side ("T"/"CT") -> that side's buy kind (None when unknown)."""
    kinds: dict[int, dict[str, str | None]] = {}
    for round_economy in round_economies(replay):
        number = round_economy["roundNumber"]
        if not isinstance(number, int):
            continue
        for team in round_economy["teams"]:
            if team["side"] in SIDES:
                kinds.setdefault(number, {})[team["side"]] = team["kind"]
    return kinds


def buy_kind(equip: float, money: float, players: int) -> str:
    if equip >= FULL_PER_PLAYER * players:
        return "full"
    if equip < ECO_PER_PLAYER * players:
        return "eco"
    if equip < HALF_PER_PLAYER * players and money >= MONEY_KEPT_PER_PLAYER * players:
        return "eco"
    if money < MONEY_KEPT_PER_PLAYER * players:
        return "force"
    return "half"


def _pistol_round_numbers(
    rounds: list[Mapping[str, Any]],
    side_of_a: Callable[[float], str | None],
) -> set[float]:
    # Round 1 and the first round where team A's side differs from its first known side.
    pistols: set[float] = set()
    if any(item["roundNumber"] == 1 for item in rounds):
        pistols.add(1)
    reference = next((item for item in rounds if side_of_a(item["roundNumber"]) is not None), None)
    if reference is None:
        return pistols
    start_side = side_of_a(reference["roundNumber"])
    switched = next(
        (
            item
            for item in rounds
            if item["roundNumber"] > reference["roundNumber"]
            and side_of_a(item["roundNumber"]) is not None
            and side_of_a(item["roundNumber"]) != start_side
        ),
        None,
    )
    if switched is not None and switched["roundNumber"] <= LAST_REGULATION_SWITCH_ROUND:
        pistols.add(switched["roundNumber"])
    return pistols


def _buy_tick(item: Mapping[str, Any], frame_ticks: list[float], tick_rate: float) -> float | None:
    start = item.get("startTick") if _is_finite_number(item.get("startTick")) else None
    raw_end = item.get("endTick")
    end = raw_end if _is_finite_number(raw_end) and (start is None or raw_end >= start) else None
    freeze_end = item.get("freezeEndTick") if _is_finite_number(item.get("freezeEndTick")) else None
    if freeze_end is not None and (start is None or freeze_end >= start) and (end is None or freeze_end <= end):
        return freeze_end
    if start is None:
        return None
    target = start + BUY_FALLBACK_SECONDS * tick_rate
    tick = next((frame_tick for frame_tick in frame_ticks if frame_tick >= target), target)
    return tick if end is None else min(tick, end)


def _state_tracks(states: Any) -> dict[str, list[Mapping[str, Any]]]:
    tracks: dict[str, list[Mapping[str, Any]]] = {}
    if not isinstance(states, Mapping):
        return tracks
    for player_id, raw in states.items():
        if not isinstance(raw, list):
            continue
        track = [entry for entry in raw if isinstance(entry, Mapping) and _is_finite_number(entry.get("tick"))]
        # A stable sort, as the review page's; a sorted track keeps its order.
        track.sort(key=lambda entry: entry["tick"])
        tracks[str(player_id)] = track
    return tracks


def _state_at(track: list[Mapping[str, Any]], tick: float) -> Mapping[str, Any] | None:
    # The last change point at or before `tick`; None before the first one.
    if not track or not _is_finite_number(tick) or tick < track[0]["tick"]:
        return None
    low, high = 0, len(track)
    while low < high:
        middle = (low + high) // 2
        if track[middle]["tick"] <= tick:
            low = middle + 1
        else:
            high = middle
    return track[low - 1] if low > 0 else None


def _has_equip_values(states: Any) -> bool:
    if not isinstance(states, Mapping):
        return False
    return any(
        isinstance(track, list)
        and any(isinstance(entry, Mapping) and _is_finite_number(entry.get("equipValue")) for entry in track)
        for track in states.values()
    )


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


# The side rule's round numbers: finite and exact after JSON.parse (see match_summary.py).
def _is_rule_number(value: Any) -> bool:
    return _is_finite_number(value) and abs(value) <= 2**53 - 1
