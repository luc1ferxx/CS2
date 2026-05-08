from __future__ import annotations

from typing import Any

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
    return dedupe_events(events, config, tick_rate)[: config.max_events_total]
