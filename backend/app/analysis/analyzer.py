from __future__ import annotations

from typing import Any
import uuid

from app.analysis.rules import (
    CoachingEventCandidate,
    DEFAULT_RULE_CONFIG,
    RuleConfig,
    dedupe_events,
    find_isolated_entries,
    find_late_post_plant_utility,
    find_post_plant_spacing_with_bomb_event,
    find_post_plant_spread_issues,
    find_poor_spacing,
    find_retake_desyncs,
    find_untraded_deaths,
    find_weak_utility_before_execute,
)

MAX_ANALYZER_EVENTS = DEFAULT_RULE_CONFIG.max_events_total


def analyze_replay(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    if not isinstance(replay, dict) or not replay.get("frames"):
        return []

    events = [
        *find_untraded_deaths(replay, config),
        *find_isolated_entries(replay, config),
        *find_poor_spacing(replay, config),
        *find_post_plant_spread_issues(replay, config),
        *find_post_plant_spacing_with_bomb_event(replay, config),
        *find_retake_desyncs(replay, config),
        *find_weak_utility_before_execute(replay, config),
        *find_late_post_plant_utility(replay, config),
    ]
    tick_rate = max(1, int(replay.get("tickRate") or 64))
    personal_events = _personal_subjects(events, replay)
    return _balanced_limit(dedupe_events(personal_events, config, tick_rate), config)


def _personal_subjects(events: list[CoachingEventCandidate], replay: dict[str, Any]) -> list[CoachingEventCandidate]:
    names = {str(player["id"]): str(player.get("name") or player["id"]) for player in replay.get("players", [])}
    for frame in replay.get("frames", []):
        for player in frame.get("players", []):
            names.setdefault(str(player["id"]), str(player.get("name") or player["id"]))
    personal = []
    for event in events:
        context = event["structured_context_json"]
        rule_id = context["ruleId"]
        shared = rule_id in {"post_plant_spread_issue", "post_plant_spacing_with_bomb_event", "retake_desync"}
        shared = shared or (rule_id == "poor_spacing" and context.get("spacingType") == "stacked")
        subjects = context.get("involvedPlayerIds", []) if shared else [event["player_id"]]
        for player_id in subjects:
            if player_id not in names:
                continue
            personal.append({
                **event,
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event['id']}:{player_id}")),
                "player_id": player_id,
                "player_name": names[player_id],
                "structured_context_json": {**context, "targetPlayerId": player_id},
            })
    return personal


def _balanced_limit(events: list[CoachingEventCandidate], config: RuleConfig) -> list[CoachingEventCandidate]:
    # Fill every player's rounds before taking additional findings from any round.
    by_player: dict[str, dict[int, list[CoachingEventCandidate]]] = {}
    for event in events:
        by_player.setdefault(event["player_id"], {}).setdefault(event["round_number"], []).append(event)
    queues: dict[str, list[CoachingEventCandidate]] = {}
    for player_id, rounds in sorted(by_player.items()):
        queue = []
        while any(rounds.values()):
            for round_number in sorted(rounds):
                if rounds[round_number]:
                    queue.append(rounds[round_number].pop(0))
        queues[player_id] = queue[:max(0, config.max_events_per_player)]
    selected_ids = set()
    while any(queues.values()) and len(selected_ids) < max(0, config.max_events_total):
        for queue in queues.values():
            if queue and len(selected_ids) < config.max_events_total:
                selected_ids.add(queue.pop(0)["id"])
    return [event for event in events if event["id"] in selected_ids]
