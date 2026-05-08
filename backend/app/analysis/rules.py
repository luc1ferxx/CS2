from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from typing import Any

TRADE_WINDOW_SECONDS = 5
SAME_AREA_DISTANCE = 12.0
ISOLATED_ENTRY_DISTANCE = 22.0
POOR_SPACING_NEAREST_DISTANCE = 28.0
POOR_SPACING_STACKED_DISTANCE = 2.5
MAX_EVENTS_PER_RULE = 8

CoachingEventCandidate = dict[str, Any]


def find_untraded_deaths(replay: dict[str, Any]) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    trade_window_ticks = int(context.tick_rate * TRADE_WINDOW_SECONDS)

    for death in context.deaths:
        tick = _int_or_none(death.get("tick"))
        victim_id = _optional_str(death.get("victimId"))
        victim_name = _optional_str(death.get("victimName")) or victim_id or "Unknown player"
        if tick is None or victim_id is None:
            continue

        victim_side = context.side_for_at(victim_id, victim_name, tick)
        if victim_side is None:
            continue

        death_position = context.player_position_at(victim_id, victim_name, tick)
        attacker_id = _optional_str(death.get("attackerId"))
        if _has_trade(context, death, victim_side, death_position, trade_window_ticks):
            continue

        events.append(
            _event(
                replay,
                rule="untraded_death",
                round_number=context.round_for_tick(tick, death.get("roundNumber")),
                player_id=victim_id,
                player_name=victim_name,
                tick_start=tick,
                tick_end=tick + trade_window_ticks,
                category="trading",
                severity="medium",
                title="Death went untraded",
                message=(
                    f"{victim_name} died and no teammate converted a trade within "
                    f"{TRADE_WINDOW_SECONDS} seconds in the same fight area."
                ),
                metadata={
                    "attackerId": attacker_id,
                    "attackerName": death.get("attackerName"),
                    "tradeWindowSeconds": TRADE_WINDOW_SECONDS,
                    "sameAreaDistance": SAME_AREA_DISTANCE,
                },
                confidence=0.72,
            )
        )
        if len(events) >= MAX_EVENTS_PER_RULE:
            break

    return events


def find_isolated_entries(replay: dict[str, Any]) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []

    for round_info in context.rounds:
        round_number = int(round_info.get("roundNumber", 1))
        deaths = [
            death
            for death in context.deaths
            if context.round_for_tick(_int_or_none(death.get("tick")) or 0, death.get("roundNumber")) == round_number
        ]
        if not deaths:
            continue

        first_death = min(deaths, key=lambda item: int(item.get("tick", 0)))
        tick = _int_or_none(first_death.get("tick"))
        victim_id = _optional_str(first_death.get("victimId"))
        victim_name = _optional_str(first_death.get("victimName")) or victim_id or "Unknown player"
        if tick is None or victim_id is None:
            continue

        victim_side = context.side_for_at(victim_id, victim_name, tick)
        if victim_side != "T":
            continue

        frame = context.frame_at(tick)
        if frame is None:
            continue
        victim = _find_frame_player(frame, victim_id, victim_name)
        if victim is None:
            continue

        teammates = [
            player
            for player in frame.get("players", [])
            if player.get("id") != victim_id
            and player.get("name") != victim_name
            and player.get("side") == "T"
            and _alive(player)
        ]
        if not teammates:
            continue
        nearest_distance = min(_distance(victim, teammate) for teammate in teammates)
        if nearest_distance <= ISOLATED_ENTRY_DISTANCE:
            continue

        events.append(
            _event(
                replay,
                rule="isolated_entry",
                round_number=round_number,
                player_id=victim_id,
                player_name=victim_name,
                tick_start=tick,
                tick_end=min(int(round_info.get("endTick", tick)), tick + int(context.tick_rate * 3)),
                category="positioning",
                severity="high",
                title="Entry died isolated from trade support",
                message=(
                    f"{victim_name} was the first T death and the nearest teammate was "
                    f"{nearest_distance:.1f} map units away."
                ),
                metadata={
                    "nearestTeammateDistance": round(nearest_distance, 2),
                    "distanceThreshold": ISOLATED_ENTRY_DISTANCE,
                },
                confidence=0.68,
            )
        )
        if len(events) >= MAX_EVENTS_PER_RULE:
            break

    return events


def find_poor_spacing(replay: dict[str, Any]) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    flagged_round_sides: set[tuple[int, str, str]] = set()

    for frame in context.frames:
        tick = int(frame.get("tick", 0))
        round_number = context.round_for_tick(tick, frame.get("roundNumber"))
        round_info = context.round_by_number.get(round_number, {})
        if tick < int(round_info.get("freezeEndTick", round_info.get("startTick", 0))):
            continue

        for side in ("T", "CT"):
            alive_players = [
                player
                for player in frame.get("players", [])
                if player.get("side") == side and _alive(player) and _has_xy(player)
            ]
            if len(alive_players) < 3:
                continue

            spacing = _spacing_snapshot(alive_players)
            if spacing is None:
                continue

            spacing_type = None
            if spacing["maxNearestDistance"] >= POOR_SPACING_NEAREST_DISTANCE:
                spacing_type = "too_far"
            elif spacing["minPairDistance"] <= POOR_SPACING_STACKED_DISTANCE:
                spacing_type = "stacked"
            if spacing_type is None or (round_number, side, spacing_type) in flagged_round_sides:
                continue

            flagged_round_sides.add((round_number, side, spacing_type))
            player = spacing["focusPlayer"]
            title = "Spacing stretched too far" if spacing_type == "too_far" else "Spacing too stacked"
            message = (
                f"{side} spacing is stretched; the most isolated player is "
                f"{spacing['maxNearestDistance']:.1f} map units from nearest support."
                if spacing_type == "too_far"
                else f"{side} spacing is stacked; closest teammates are only "
                f"{spacing['minPairDistance']:.1f} map units apart."
            )
            events.append(
                _event(
                    replay,
                    rule="poor_spacing",
                    round_number=round_number,
                    player_id=str(player.get("id") or player.get("name") or "unknown"),
                    player_name=str(player.get("name") or player.get("id") or "Unknown player"),
                    tick_start=tick,
                    tick_end=tick + int(context.tick_rate * 3),
                    category="positioning",
                    severity="low" if spacing_type == "stacked" else "medium",
                    title=title,
                    message=message,
                    metadata={
                        "side": side,
                        "spacingType": spacing_type,
                        "maxNearestDistance": round(spacing["maxNearestDistance"], 2),
                        "minPairDistance": round(spacing["minPairDistance"], 2),
                    },
                    confidence=0.55,
                )
            )
            if len(events) >= MAX_EVENTS_PER_RULE:
                return events

    return events


class ReplayContext:
    def __init__(self, replay: dict[str, Any]):
        self.replay = replay
        self.tick_rate = max(1, int(replay.get("tickRate") or 64))
        self.players = [item for item in replay.get("players", []) if isinstance(item, dict)]
        self.frames = sorted(
            [item for item in replay.get("frames", []) if isinstance(item, dict)],
            key=lambda item: int(item.get("tick", 0)),
        )
        self.rounds = [item for item in replay.get("rounds", []) if isinstance(item, dict)]
        self.kills = sorted(
            [item for item in replay.get("kills", []) if isinstance(item, dict)],
            key=lambda item: int(item.get("tick", 0)),
        )
        self.deaths = sorted(
            [item for item in replay.get("deaths", self.kills) if isinstance(item, dict)],
            key=lambda item: int(item.get("tick", 0)),
        )
        self.round_by_number = {
            int(item.get("roundNumber", 1)): item
            for item in self.rounds
            if item.get("roundNumber") is not None
        }
        self.side_by_id = {
            str(player["id"]): str(player["side"])
            for player in self.players
            if player.get("id") is not None and player.get("side") in {"T", "CT"}
        }
        self.side_by_name = {
            str(player["name"]): str(player["side"])
            for player in self.players
            if player.get("name") is not None and player.get("side") in {"T", "CT"}
        }

    def side_for(self, player_id: str | None, player_name: str | None = None) -> str | None:
        if player_id and player_id in self.side_by_id:
            return self.side_by_id[player_id]
        if player_name and player_name in self.side_by_name:
            return self.side_by_name[player_name]
        for frame in self.frames:
            player = _find_frame_player(frame, player_id, player_name)
            if player and player.get("side") in {"T", "CT"}:
                return str(player["side"])
        return None

    def side_for_at(self, player_id: str | None, player_name: str | None, tick: int) -> str | None:
        frame = self.frame_at(tick)
        if frame is not None:
            player = _find_frame_player(frame, player_id, player_name)
            if player and player.get("side") in {"T", "CT"}:
                return str(player["side"])
        return self.side_for(player_id, player_name)

    def round_for_tick(self, tick: int, fallback: Any = None) -> int:
        if fallback is not None:
            parsed = _int_or_none(fallback)
            if parsed is not None and parsed > 0:
                return parsed
        for item in self.rounds:
            if int(item.get("startTick", 0)) <= tick <= int(item.get("endTick", 0)):
                return int(item.get("roundNumber", 1))
        return int(self.rounds[0].get("roundNumber", 1)) if self.rounds else 1

    def frame_at(self, tick: int) -> dict[str, Any] | None:
        if not self.frames:
            return None
        return min(self.frames, key=lambda frame: abs(int(frame.get("tick", 0)) - tick))

    def player_position_at(
        self,
        player_id: str | None,
        player_name: str | None,
        tick: int,
    ) -> dict[str, Any] | None:
        frame = self.frame_at(tick)
        if frame is None:
            return None
        return _find_frame_player(frame, player_id, player_name)


def _has_trade(
    context: ReplayContext,
    death: dict[str, Any],
    victim_side: str,
    death_position: dict[str, Any] | None,
    trade_window_ticks: int,
) -> bool:
    death_tick = int(death.get("tick", 0))
    round_number = context.round_for_tick(death_tick, death.get("roundNumber"))
    attacker_id = _optional_str(death.get("attackerId"))

    for kill in context.kills:
        tick = _int_or_none(kill.get("tick"))
        if tick is None or tick <= death_tick or tick > death_tick + trade_window_ticks:
            continue
        if context.round_for_tick(tick, kill.get("roundNumber")) != round_number:
            continue

        trade_attacker_id = _optional_str(kill.get("attackerId"))
        trade_attacker_name = _optional_str(kill.get("attackerName"))
        if context.side_for_at(trade_attacker_id, trade_attacker_name, tick) != victim_side:
            continue

        trade_victim_id = _optional_str(kill.get("victimId"))
        trade_victim_name = _optional_str(kill.get("victimName"))
        if attacker_id and trade_victim_id == attacker_id:
            return True
        if context.side_for_at(trade_victim_id, trade_victim_name, tick) == victim_side:
            continue

        trade_victim_position = context.player_position_at(trade_victim_id, trade_victim_name, tick)
        if death_position and trade_victim_position and _distance(death_position, trade_victim_position) <= SAME_AREA_DISTANCE:
            return True

    return False


def _event(
    replay: dict[str, Any],
    *,
    rule: str,
    round_number: int,
    player_id: str,
    player_name: str,
    tick_start: int,
    tick_end: int,
    category: str,
    severity: str,
    title: str,
    message: str,
    metadata: dict[str, Any],
    confidence: float,
) -> CoachingEventCandidate:
    return {
        "id": str(uuid.uuid4()),
        "demo_id": str(replay.get("demoId") or "unknown"),
        "round_number": int(round_number),
        "player_id": player_id,
        "player_name": player_name,
        "tick_start": int(tick_start),
        "tick_end": int(max(tick_start, tick_end)),
        "category": category,
        "severity": severity,
        "title": title,
        "message": message,
        "structured_context_json": {"rule": rule, **metadata},
        "confidence": max(0.0, min(1.0, confidence)),
    }


def _spacing_snapshot(players: list[dict[str, Any]]) -> dict[str, Any] | None:
    nearest: list[tuple[dict[str, Any], float]] = []
    min_pair_distance = math.inf
    for player in players:
        distances = [
            _distance(player, other)
            for other in players
            if other is not player and _has_xy(other)
        ]
        if not distances:
            continue
        player_nearest = min(distances)
        nearest.append((player, player_nearest))
        min_pair_distance = min(min_pair_distance, player_nearest)
    if not nearest:
        return None

    focus_player, max_nearest_distance = max(nearest, key=lambda item: item[1])
    return {
        "focusPlayer": focus_player,
        "maxNearestDistance": max_nearest_distance,
        "minPairDistance": min_pair_distance,
    }


def _find_frame_player(
    frame: dict[str, Any],
    player_id: str | None,
    player_name: str | None,
) -> dict[str, Any] | None:
    for player in frame.get("players", []):
        if player_id is not None and str(player.get("id")) == player_id:
            return player
        if player_name is not None and str(player.get("name")) == player_name:
            return player
    return None


def _distance(first: dict[str, Any], second: dict[str, Any]) -> float:
    return math.hypot(float(first["x"]) - float(second["x"]), float(first["y"]) - float(second["y"]))


def _alive(player: dict[str, Any]) -> bool:
    return bool(player.get("alive", True)) and int(player.get("hp", 100) or 0) > 0


def _has_xy(player: dict[str, Any]) -> bool:
    return player.get("x") is not None and player.get("y") is not None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def dedupe_events(events: Iterable[CoachingEventCandidate]) -> list[CoachingEventCandidate]:
    seen: set[tuple[str, str, int]] = set()
    deduped = []
    for event in events:
        key = (
            str(event.get("structured_context_json", {}).get("rule")),
            str(event.get("player_id")),
            int(event.get("tick_start", 0)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return sorted(deduped, key=lambda item: (int(item["tick_start"]), str(item["category"])))
