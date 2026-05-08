from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CoachingEventCandidate = dict[str, Any]


@dataclass(frozen=True)
class RuleConfig:
    trade_window_seconds: float = 5.0
    same_area_distance: float = 12.0
    isolated_teammate_distance: float = 22.0
    poor_spacing_min_distance: float = 2.5
    poor_spacing_max_distance: float = 28.0
    max_events_per_round_per_rule: int = 1
    dedupe_tick_window_seconds: float = 3.0
    max_events_total: int = 24
    post_plant_cluster_distance: float = 6.0
    post_plant_min_duration_seconds: float = 4.0
    post_plant_min_players: int = 3
    retake_site_distance: float = 12.0
    retake_desync_seconds: float = 4.0


DEFAULT_RULE_CONFIG = RuleConfig()
TRADE_WINDOW_SECONDS = DEFAULT_RULE_CONFIG.trade_window_seconds
SAME_AREA_DISTANCE = DEFAULT_RULE_CONFIG.same_area_distance
ISOLATED_ENTRY_DISTANCE = DEFAULT_RULE_CONFIG.isolated_teammate_distance
POOR_SPACING_NEAREST_DISTANCE = DEFAULT_RULE_CONFIG.poor_spacing_max_distance
POOR_SPACING_STACKED_DISTANCE = DEFAULT_RULE_CONFIG.poor_spacing_min_distance
MAX_EVENTS_PER_RULE = 8

_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def find_untraded_deaths(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    trade_window_ticks = int(context.tick_rate * config.trade_window_seconds)

    for death in context.deaths:
        tick = _int_or_none(death.get("tick"))
        victim_id = _optional_str(death.get("victimId"))
        victim_name = _optional_str(death.get("victimName")) or victim_id or "Unknown player"
        if tick is None or victim_id is None:
            continue

        victim_side = context.side_for_at(victim_id, victim_name, tick)
        if victim_side is None:
            continue

        round_number = context.round_for_tick(tick, death.get("roundNumber"))
        if _round_rule_count(events, round_number, "untraded_death") >= config.max_events_per_round_per_rule:
            continue

        death_position = context.player_position_at(victim_id, victim_name, tick)
        attacker_id = _optional_str(death.get("attackerId"))
        attacker_name = _optional_str(death.get("attackerName"))
        if _has_trade(context, death, victim_side, death_position, trade_window_ticks, config):
            continue

        attacker_position = context.player_position_at(attacker_id, attacker_name, tick)
        distance = _distance_or_none(death_position, attacker_position)
        events.append(
            _event(
                replay,
                rule_id="untraded_death",
                round_number=round_number,
                player_id=victim_id,
                player_name=victim_name,
                tick_start=tick,
                tick_end=tick + trade_window_ticks,
                category="trading",
                severity="medium",
                title="Death went untraded",
                message=(
                    f"{victim_name} died and no teammate converted a trade within "
                    f"{_format_seconds(config.trade_window_seconds)} seconds in the same fight area."
                ),
                involved_player_ids=[victim_id, attacker_id],
                evidence_ticks=[tick],
                metadata={
                    "attackerId": attacker_id,
                    "attackerName": attacker_name,
                    "windowSeconds": _format_number(config.trade_window_seconds),
                    "sameAreaDistance": config.same_area_distance,
                    "distance": _round_or_none(distance),
                },
                confidence=0.72,
            )
        )

    return events


def find_isolated_entries(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
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

        if _round_rule_count(events, round_number, "isolated_entry") >= config.max_events_per_round_per_rule:
            continue

        frame = context.frame_at(tick)
        if frame is None:
            continue
        victim = _find_frame_player(frame, victim_id, victim_name)
        if victim is None or not _has_xy(victim):
            continue

        teammates = [
            player
            for player in frame.get("players", [])
            if player.get("id") != victim_id
            and player.get("name") != victim_name
            and player.get("side") == "T"
            and _alive(player)
            and _has_xy(player)
        ]
        if not teammates:
            continue

        nearest_teammate = min(teammates, key=lambda teammate: _distance(victim, teammate))
        nearest_distance = _distance(victim, nearest_teammate)
        if nearest_distance <= config.isolated_teammate_distance:
            continue

        teammate_id = _optional_str(nearest_teammate.get("id"))
        events.append(
            _event(
                replay,
                rule_id="isolated_entry",
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
                involved_player_ids=[victim_id, teammate_id],
                evidence_ticks=[tick],
                metadata={
                    "nearestTeammateId": teammate_id,
                    "distance": round(nearest_distance, 2),
                    "isolatedTeammateDistance": config.isolated_teammate_distance,
                },
                confidence=0.68,
            )
        )

    return events


def find_poor_spacing(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []

    for frame in context.frames:
        tick = int(frame.get("tick", 0))
        round_number = context.round_for_tick(tick, frame.get("roundNumber"))
        round_info = context.round_by_number.get(round_number, {})
        if tick < int(round_info.get("freezeEndTick", round_info.get("startTick", 0))):
            continue
        if _round_rule_count(events, round_number, "poor_spacing") >= config.max_events_per_round_per_rule:
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
            distance = None
            focus_player = spacing["focusPlayer"]
            involved_player_ids = [_player_id(player) for player in alive_players]
            if spacing["maxNearestDistance"] >= config.poor_spacing_max_distance:
                spacing_type = "too_far"
                distance = spacing["maxNearestDistance"]
            elif spacing["minPairDistance"] <= config.poor_spacing_min_distance:
                spacing_type = "stacked"
                distance = spacing["minPairDistance"]
                closest_pair = spacing["closestPair"]
                focus_player = closest_pair[0]
                involved_player_ids = [_player_id(player) for player in closest_pair]
            if spacing_type is None:
                continue

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
                    rule_id="poor_spacing",
                    round_number=round_number,
                    player_id=_player_id(focus_player),
                    player_name=_player_name(focus_player),
                    tick_start=tick,
                    tick_end=tick + int(context.tick_rate * 3),
                    category="positioning",
                    severity="low" if spacing_type == "stacked" else "medium",
                    title=title,
                    message=message,
                    involved_player_ids=involved_player_ids,
                    evidence_ticks=[tick],
                    metadata={
                        "side": side,
                        "spacingType": spacing_type,
                        "distance": _round_or_none(distance),
                        "nearbyCount": len(alive_players),
                        "poorSpacingMinDistance": config.poor_spacing_min_distance,
                        "poorSpacingMaxDistance": config.poor_spacing_max_distance,
                        "maxNearestDistance": round(spacing["maxNearestDistance"], 2),
                        "minPairDistance": round(spacing["minPairDistance"], 2),
                    },
                    confidence=0.55,
                )
            )
            break

    return events


def find_post_plant_spread_issues(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    min_duration_ticks = int(context.tick_rate * config.post_plant_min_duration_seconds)

    for round_number, frames in context.frames_by_round().items():
        segment: list[dict[str, Any]] = []
        for frame in frames:
            if not _planted_bomb_position(frame):
                segment = []
                continue

            alive_t = [
                player
                for player in frame.get("players", [])
                if player.get("side") == "T" and _alive(player) and _has_xy(player)
            ]
            if len(alive_t) < config.post_plant_min_players:
                segment = []
                continue

            max_pair_distance = _max_pair_distance(alive_t)
            if max_pair_distance is None or max_pair_distance > config.post_plant_cluster_distance:
                segment = []
                continue

            segment.append(frame)
            start_tick = int(segment[0].get("tick", 0))
            end_tick = int(segment[-1].get("tick", 0))
            if end_tick - start_tick < min_duration_ticks:
                continue
            if _round_rule_count(events, round_number, "post_plant_spread_issue") >= config.max_events_per_round_per_rule:
                break

            focus_player = alive_t[0]
            evidence_ticks = [int(item.get("tick", 0)) for item in segment]
            events.append(
                _event(
                    replay,
                    rule_id="post_plant_spread_issue",
                    round_number=round_number,
                    player_id=_player_id(focus_player),
                    player_name=_player_name(focus_player),
                    tick_start=start_tick,
                    tick_end=end_tick,
                    category="objective",
                    severity="medium",
                    title="Post-plant spacing stayed too clustered",
                    message=(
                        f"{len(alive_t)} Ts stayed clustered for "
                        f"{(end_tick - start_tick) / context.tick_rate:.1f} seconds after the plant."
                    ),
                    involved_player_ids=[_player_id(player) for player in alive_t],
                    evidence_ticks=evidence_ticks,
                    metadata={
                        "site": _bomb_site(segment[-1]),
                        "nearbyCount": len(alive_t),
                        "distance": round(max_pair_distance, 2),
                        "clusterDistance": config.post_plant_cluster_distance,
                        "windowSeconds": _format_number((end_tick - start_tick) / context.tick_rate),
                    },
                    confidence=0.62,
                )
            )
            break

    return events


def find_retake_desyncs(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    desync_ticks = int(context.tick_rate * config.retake_desync_seconds)

    for round_number, frames in context.frames_by_round().items():
        first_entry_by_ct: dict[str, tuple[int, dict[str, Any]]] = {}
        for frame in frames:
            bomb_position = _planted_bomb_position(frame)
            if bomb_position is None:
                continue

            for player in frame.get("players", []):
                player_id = _player_id(player)
                if (
                    player.get("side") != "CT"
                    or player_id in first_entry_by_ct
                    or not _alive(player)
                    or not _has_xy(player)
                ):
                    continue
                if _distance_to_xy(player, bomb_position) <= config.retake_site_distance:
                    first_entry_by_ct[player_id] = (int(frame.get("tick", 0)), player)

        if len(first_entry_by_ct) < 2:
            continue

        ordered_entries = sorted(first_entry_by_ct.values(), key=lambda item: item[0])
        first_tick = ordered_entries[0][0]
        last_tick = ordered_entries[-1][0]
        if last_tick - first_tick < desync_ticks:
            continue
        if _round_rule_count(events, round_number, "retake_desync") >= config.max_events_per_round_per_rule:
            continue

        involved_players = [player for _, player in ordered_entries]
        focus_player = involved_players[0]
        events.append(
            _event(
                replay,
                rule_id="retake_desync",
                round_number=round_number,
                player_id=_player_id(focus_player),
                player_name=_player_name(focus_player),
                tick_start=first_tick,
                tick_end=last_tick,
                category="timing",
                severity="medium",
                title="Retake timing was desynced",
                message=(
                    "CTs reached the planted bomb area "
                    f"{(last_tick - first_tick) / context.tick_rate:.1f} seconds apart."
                ),
                involved_player_ids=[_player_id(player) for player in involved_players],
                evidence_ticks=[tick for tick, _ in ordered_entries],
                metadata={
                    "windowSeconds": _format_number((last_tick - first_tick) / context.tick_rate),
                    "retakeSiteDistance": config.retake_site_distance,
                    "nearbyCount": len(involved_players),
                },
                confidence=0.6,
            )
        )

    return events


class ReplayContext:
    def __init__(self, replay: dict[str, Any]):
        self.replay = replay
        self.tick_rate = max(1, int(replay.get("tickRate") or 64))
        self.players = [item for item in replay.get("players", []) if isinstance(item, dict)]
        self.frames = sorted(
            [item for item in replay.get("frames", []) if isinstance(item, dict)],
            key=lambda item: _int_or_none(item.get("tick")) or 0,
        )
        self.rounds = [item for item in replay.get("rounds", []) if isinstance(item, dict)]
        self.kills = sorted(
            [item for item in replay.get("kills", []) if isinstance(item, dict)],
            key=lambda item: _int_or_none(item.get("tick")) or 0,
        )
        self.deaths = sorted(
            [item for item in replay.get("deaths", self.kills) if isinstance(item, dict)],
            key=lambda item: _int_or_none(item.get("tick")) or 0,
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

    def frames_by_round(self) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for frame in self.frames:
            tick = _int_or_none(frame.get("tick")) or 0
            round_number = self.round_for_tick(tick, frame.get("roundNumber"))
            grouped.setdefault(round_number, []).append(frame)
        return grouped


def _has_trade(
    context: ReplayContext,
    death: dict[str, Any],
    victim_side: str,
    death_position: dict[str, Any] | None,
    trade_window_ticks: int,
    config: RuleConfig,
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
        if (
            death_position
            and trade_victim_position
            and _distance(death_position, trade_victim_position) <= config.same_area_distance
        ):
            return True

    return False


def _event(
    replay: dict[str, Any],
    *,
    rule_id: str,
    round_number: int,
    player_id: str,
    player_name: str,
    tick_start: int,
    tick_end: int,
    category: str,
    severity: str,
    title: str,
    message: str,
    involved_player_ids: Iterable[str | None],
    evidence_ticks: Iterable[int],
    metadata: dict[str, Any],
    confidence: float,
) -> CoachingEventCandidate:
    normalized_player_ids = _unique_values(involved_player_ids)
    normalized_ticks = [int(tick) for tick in _unique_values(evidence_ticks)]
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
        "structured_context_json": {
            "rule": rule_id,
            "ruleId": rule_id,
            "involvedPlayerIds": normalized_player_ids,
            "evidenceTicks": normalized_ticks,
            **metadata,
        },
        "confidence": max(0.0, min(1.0, confidence)),
    }


def _spacing_snapshot(players: list[dict[str, Any]]) -> dict[str, Any] | None:
    nearest: list[tuple[dict[str, Any], float]] = []
    min_pair_distance = math.inf
    closest_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
    for player in players:
        distances = []
        for other in players:
            if other is player or not _has_xy(other):
                continue
            distance = _distance(player, other)
            distances.append(distance)
            if distance < min_pair_distance:
                min_pair_distance = distance
                closest_pair = (player, other)
        if not distances:
            continue
        nearest.append((player, min(distances)))
    if not nearest or closest_pair is None:
        return None

    focus_player, max_nearest_distance = max(nearest, key=lambda item: item[1])
    return {
        "focusPlayer": focus_player,
        "closestPair": closest_pair,
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


def _distance_or_none(first: dict[str, Any] | None, second: dict[str, Any] | None) -> float | None:
    if not first or not second or not _has_xy(first) or not _has_xy(second):
        return None
    return _distance(first, second)


def _distance_to_xy(player: dict[str, Any], point: tuple[float, float]) -> float:
    return math.hypot(float(player["x"]) - point[0], float(player["y"]) - point[1])


def _max_pair_distance(players: list[dict[str, Any]]) -> float | None:
    if len(players) < 2:
        return None
    max_distance = 0.0
    for index, player in enumerate(players):
        for other in players[index + 1 :]:
            max_distance = max(max_distance, _distance(player, other))
    return max_distance


def _planted_bomb_position(frame: dict[str, Any]) -> tuple[float, float] | None:
    bomb_state = frame.get("bombState")
    if not isinstance(bomb_state, dict) or bomb_state.get("status") != "planted":
        return None
    if bomb_state.get("x") is None or bomb_state.get("y") is None:
        return None
    return float(bomb_state["x"]), float(bomb_state["y"])


def _bomb_site(frame: dict[str, Any]) -> str | None:
    bomb_state = frame.get("bombState")
    if isinstance(bomb_state, dict) and bomb_state.get("site"):
        return str(bomb_state["site"])
    return None


def _alive(player: dict[str, Any]) -> bool:
    return bool(player.get("alive", True)) and int(player.get("hp", 100) or 0) > 0


def _has_xy(player: dict[str, Any]) -> bool:
    return player.get("x") is not None and player.get("y") is not None


def _player_id(player: dict[str, Any]) -> str:
    return str(player.get("id") or player.get("name") or "unknown")


def _player_name(player: dict[str, Any]) -> str:
    return str(player.get("name") or player.get("id") or "Unknown player")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _format_number(value: float) -> int | float:
    rounded = round(float(value), 3)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _format_seconds(value: float) -> str:
    formatted = _format_number(value)
    return str(formatted)


def _unique_values(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    unique = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _round_rule_count(events: list[CoachingEventCandidate], round_number: int, rule_id: str) -> int:
    return sum(
        1
        for event in events
        if event.get("round_number") == round_number
        and event.get("structured_context_json", {}).get("ruleId") == rule_id
    )


def _sort_key(event: CoachingEventCandidate) -> tuple[int, int, str]:
    return (
        _SEVERITY_ORDER.get(str(event.get("severity")), 99),
        int(event.get("tick_start", 0)),
        str(event.get("id")),
    )


def dedupe_events(
    events: Iterable[CoachingEventCandidate],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
    tick_rate: int = 64,
) -> list[CoachingEventCandidate]:
    dedupe_window_ticks = int(tick_rate * config.dedupe_tick_window_seconds)
    deduped: list[CoachingEventCandidate] = []
    for event in sorted(events, key=_sort_key):
        event_tick = int(event.get("tick_start", 0))
        is_duplicate = any(
            event.get("round_number") == kept.get("round_number")
            and event.get("player_id") == kept.get("player_id")
            and event.get("category") == kept.get("category")
            and abs(event_tick - int(kept.get("tick_start", 0))) <= dedupe_window_ticks
            for kept in deduped
        )
        if is_duplicate:
            continue
        deduped.append(event)
    return sorted(deduped, key=_sort_key)
