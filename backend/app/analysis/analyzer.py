from __future__ import annotations

from typing import Any

from app.analysis.rules import (
    CoachingEventCandidate,
    dedupe_events,
    find_isolated_entries,
    find_poor_spacing,
    find_untraded_deaths,
)

MAX_ANALYZER_EVENTS = 24


def analyze_replay(replay: dict[str, Any]) -> list[CoachingEventCandidate]:
    if not isinstance(replay, dict) or not replay.get("frames") or not replay.get("kills"):
        return []

    events = [
        *find_untraded_deaths(replay),
        *find_isolated_entries(replay),
        *find_poor_spacing(replay),
    ]
    return dedupe_events(events)[:MAX_ANALYZER_EVENTS]
