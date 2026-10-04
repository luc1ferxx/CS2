from __future__ import annotations

import math
import uuid
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple

from app.analysis.round_economy import buy_kinds_by_side
from app.parser.map_config import REFERENCE_WORLD_UNITS_PER_PERCENT, get_map_config

CoachingEventCandidate = dict[str, Any]

# Horizontal distances are world units. Normalized frames store radar percent,
# so ReplayContext resolves the map's world-units-per-percent scale and the
# distance helpers convert before any threshold comparison. Without that step a
# single threshold silently means a different real distance on every map --
# Dust II spans 4506 world units across the radar, Nuke spans 7168.
AxisScale = tuple[float, float]


@dataclass(frozen=True)
class RuleConfig:
    trade_window_seconds: float = 5.0
    # All distances below are CS2 world units (a player is ~72 units tall,
    # ~32 wide, and runs ~250 units/second). They were derived from the earlier
    # radar-percent values at the Dust II scale of 45.056 units per percent, so
    # Dust II behaviour is unchanged and every other map is now measured on the
    # same physical scale instead of its own.
    same_area_distance: float = 540.0
    isolated_teammate_distance: float = 990.0
    poor_spacing_min_distance: float = 112.0
    poor_spacing_max_distance: float = 1260.0
    max_stacked_vertical_distance: float = 128.0
    # Spacing is judged on stretches of consecutive position samples, not on one
    # sample: nothing is evaluated before this long after freeze end (spawn
    # walks), a stacked pair needs a stretch this long -- or both of them killed
    # by the same enemy within the multikill window -- and a too_far stretch
    # this long becomes a reason on the death card that follows it.
    poor_spacing_eval_delay_seconds: float = 15.0
    poor_spacing_min_duration_seconds: float = 3.0
    stacked_multikill_window_seconds: float = 3.0
    max_events_per_round_per_rule: int = 1
    dedupe_tick_window_seconds: float = 3.0
    max_events_total: int = 480
    max_events_per_player: int = 48
    max_position_age_seconds: float = 1.0
    post_plant_cluster_distance: float = 270.0
    post_plant_min_duration_seconds: float = 4.0
    post_plant_min_players: int = 3
    retake_site_distance: float = 540.0
    retake_desync_seconds: float = 4.0
    execute_utility_window_seconds: float = 12.0
    min_execute_utility_events: int = 2
    # Shooting rules (recorded `shots`). The CS2 max player speed (units/second)
    # of each judged weapon; a shot is accurate while the speed is at most
    # round(shot_accurate_speed_ratio * max). SMGs, shotguns, the other pistols
    # and LMGs are run-and-gun weapons and are never judged.
    shot_weapon_max_speeds: tuple[tuple[str, int], ...] = (
        ("ak47", 215), ("m4a1", 225), ("m4a1_silencer", 225), ("galilar", 215), ("famas", 220),
        ("aug", 220), ("sg556", 210), ("awp", 200), ("ssg08", 230), ("scar20", 215), ("g3sg1", 215),
        ("deagle", 230), ("revolver", 220),
    )
    shot_accurate_speed_ratio: float = 0.34
    # Consecutive shots of one judged weapon at most this far apart are one burst.
    shot_burst_gap_seconds: float = 0.5
    # A burst opens a fight when the player fired no gun in this long before it.
    shot_opener_quiet_seconds: float = 1.0
    # no_counter_strafe: the first shot is this many units/second above the accurate speed.
    counter_strafe_margin: float = 40.0
    moving_shots_min: int = 3
    shot_death_window_seconds: float = 2.0
    # A damage event this many ticks after a shot (or on its tick) is that shot's hit.
    shot_hit_window_ticks: int = 2
    # An opposite movement key pressed this long before the first shot is a counter-strafe.
    counter_strafe_window_seconds: float = 0.15
    shot_card_lead_seconds: float = 0.5
    shot_card_tail_seconds: float = 0.25
    # At most this many no_counter_strafe cards per player per match: the ones
    # where the player died first, then the fastest first shot, then the earliest.
    no_counter_strafe_max_per_match: int = 3


DEFAULT_RULE_CONFIG = RuleConfig()
TRADE_WINDOW_SECONDS = DEFAULT_RULE_CONFIG.trade_window_seconds
SAME_AREA_DISTANCE = DEFAULT_RULE_CONFIG.same_area_distance
ISOLATED_ENTRY_DISTANCE = DEFAULT_RULE_CONFIG.isolated_teammate_distance
POOR_SPACING_NEAREST_DISTANCE = DEFAULT_RULE_CONFIG.poor_spacing_max_distance
POOR_SPACING_STACKED_DISTANCE = DEFAULT_RULE_CONFIG.poor_spacing_min_distance
MAX_EVENTS_PER_RULE = 8
# Two evaluated position samples further apart than this break a spacing stretch.
# Samples are ~0.25 s apart, with extra ones near deaths and events, so stretch
# lengths are measured in ticks, never in sample counts.
SPACING_STRETCH_MAX_GAP_SECONDS = 1.0
# A stacked card keeps the 3 s tick_end the single-sample rule had, so a card
# whose stretch starts where that sample was keeps its id.
STACKED_CARD_SECONDS = 3
# A too_far stretch becomes a reason on a death card when it overlaps this
# window before the death.
TOO_FAR_BEFORE_DEATH_SECONDS = 5.0
DEATH_CARD_RULES = ("untraded_death", "isolated_entry")
BOMB_PLANTED_EVENT_TYPE = "bomb_planted"
UTILITY_EVENT_TYPES = {"smoke", "flash", "molotov", "he"}
UTILITY_LABELS = {
    "flash": "Flash",
    "he": "HE",
    "molotov": "Molotov",
    "smoke": "Smoke",
}
SHOOTING_RULES = ("moving_shots", "no_counter_strafe")
SHOT_WEAPON_LABELS = {
    "ak47": "AK-47",
    "m4a1": "M4A4",
    "m4a1_silencer": "M4A1-S",
    "galilar": "Galil AR",
    "famas": "FAMAS",
    "aug": "AUG",
    "sg556": "SG 553",
    "awp": "AWP",
    "ssg08": "SSG 08",
    "scar20": "SCAR-20",
    "g3sg1": "G3SG1",
    "deagle": "Desert Eagle",
    "revolver": "R8 Revolver",
}
SHOT_FLAG_AIRBORNE = 1
# The movement bits of the replay's `inputs` masks, in the order cards list them.
MOVEMENT_KEY_BITS = (("W", 8), ("A", 512), ("S", 16), ("D", 1024))
OPPOSITE_MOVEMENT_BIT = {8: 16, 16: 8, 512: 1024, 1024: 512}
# Damage from these is never a gun shot's hit; damage without a weapon still counts.
NON_GUN_DAMAGE_WEAPONS = {"hegrenade", "inferno", "molotov", "incgrenade", "flashbang", "smokegrenade", "decoy", "world"}

_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

_SHOT_LIMITATION = (
    "Speed comes from the velocity recorded with each shot; spread recovery, crouching, scope state and the "
    "target's movement are not modelled, and a miss can have other causes."
)

_REVIEW_GUIDANCE = {
    "untraded_death": (
        "Before repeating this peek, agree who can trade and wait for their ready call; review whether the fight was necessary.",
        "A missing trade does not prove this death was avoidable; visibility, intent and voice communication are unknown.",
    ),
    "isolated_entry": (
        "Review the opening contact with your nearest teammate; if taking a duel, arrange a route and timing for a follow-up trade.",
        "Sampled straight-line distance cannot establish visibility, a viable route, entry intent or teammate responsibility.",
    ),
    "poor_spacing": (
        "Check the next contact: leave enough room to avoid a shared spray while keeping a teammate able to follow your fight.",
        "Sampled positions are a review prompt. Lurks, crossfires, vertical separation and planned stacks can be intentional.",
    ),
    "post_plant_spread_issue": (
        "Review whether your post-plant position covers a distinct angle while retaining a trade with another defender.",
        "Clustering in sampled positions does not prove a bad crossfire or continuous lack of coverage.",
    ),
    "post_plant_spacing_with_bomb_event": (
        "Review whether your post-plant position covers a distinct angle while retaining a trade with another defender.",
        "The plant is recorded, but sampled distances alone cannot prove poor angle coverage.",
    ),
    "retake_desync": (
        "Compare your approach with the other CT arrivals; agree on a contact or flash cue when a coordinated retake is possible.",
        "Different arrival times can be intentional; distance to the bomb does not prove when a retake or fight began.",
    ),
    "weak_utility_before_execute": (
        "Review the team's utility around your plant and decide whether an exposed approach needed a flash or smoke.",
        "This is team context, not a planter mistake. A plant is not an execute timestamp; older or unrecorded utility may still matter.",
    ),
    "moving_shots": (
        "Stop before you shoot: with rifles, snipers and the Deagle, tap the opposite movement key and fire once your speed has dropped.",
        _SHOT_LIMITATION,
    ),
    "no_counter_strafe": (
        "Practise counter-strafing: release the movement key and tap the opposite one, then take the first shot.",
        _SHOT_LIMITATION,
    ),
}

_EVIDENCE_SOURCES = {
    "untraded_death": "recorded_kills",
    "moving_shots": "recorded_shots",
    "no_counter_strafe": "recorded_shots",
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

        round_number = context.live_round_at(tick)
        if round_number is None:
            continue
        victim_side = death.get("victimSide") or context.side_for_at(victim_id, victim_name, tick)
        if victim_side is None:
            continue

        if _round_rule_count(events, round_number, "untraded_death", victim_id) >= config.max_events_per_round_per_rule:
            continue

        death_position = context.player_position_at(victim_id, victim_name, tick)
        attacker_id = _optional_str(death.get("attackerId"))
        attacker_name = _optional_str(death.get("attackerName"))
        attacker_side = death.get("attackerSide") or context.side_for_at(attacker_id, attacker_name, tick)
        if not attacker_id or attacker_id == victim_id or attacker_side not in {"T", "CT"} or attacker_side == victim_side:
            continue
        # A complete observation window is required; a round ending is not a missed trade.
        # An unknown round has no window at all, so it cannot supply one either.
        round_end = _int_or_none(context.round_by_number.get(round_number, {}).get("endTick"))
        if round_end is None or tick + trade_window_ticks > round_end:
            continue
        alive_before = context.alive_counts_before(tick, victim_id, victim_name, victim_side)
        # The last player alive on his side cannot be traded. Unknown counts are not skipped.
        if alive_before is not None and alive_before[0] <= 1:
            continue
        if _has_trade(context, death, victim_side, death_position, trade_window_ticks, config):
            continue

        attacker_position = context.player_position_at(attacker_id, attacker_name, tick)
        distance = _distance_or_none(death_position, attacker_position, context.world_units_per_percent)
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
                title="Review an untraded death",
                message=(
                    f"{victim_name} died; the recorded killer was not killed by a teammate within "
                    f"{_format_seconds(config.trade_window_seconds)} seconds."
                ),
                involved_player_ids=[victim_id, attacker_id],
                evidence_ticks=[tick],
                metadata={
                    "attackerId": attacker_id,
                    "attackerName": attacker_name,
                    "windowSeconds": _format_number(config.trade_window_seconds),
                    "tradeDefinition": "same_killer",
                    "observationEndTick": tick + trade_window_ticks,
                    "distance": _round_or_none(distance),
                    "relatedEventIds": context.kill_event_ids(death),
                    **_death_card_facts(context, death, round_number, victim_side, alive_before),
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
        round_number = _int_or_none(round_info.get("roundNumber"))
        if round_number is None:
            continue
        first_death = context.first_death_by_round.get(round_number)
        if first_death is None:
            continue

        tick = _int_or_none(first_death.get("tick"))
        victim_id = _optional_str(first_death.get("victimId"))
        victim_name = _optional_str(first_death.get("victimName")) or victim_id or "Unknown player"
        if tick is None or victim_id is None:
            continue

        victim_side = first_death.get("victimSide") or context.side_for_at(victim_id, victim_name, tick)
        if victim_side != "T":
            continue
        attacker_id = _optional_str(first_death.get("attackerId"))
        attacker_name = _optional_str(first_death.get("attackerName"))
        attacker_side = first_death.get("attackerSide") or context.side_for_at(attacker_id, attacker_name, tick)
        if not attacker_id or attacker_id == victim_id or attacker_side != "CT":
            continue

        if _round_rule_count(events, round_number, "isolated_entry") >= config.max_events_per_round_per_rule:
            continue

        frame = context.position_frame_at(tick, config.max_position_age_seconds)
        if frame is None:
            continue
        victim = _find_frame_player(frame, victim_id, victim_name)
        if victim is None or not _has_xy(victim):
            continue

        teammates = [
            player
            for player in _frame_players(frame)
            if player.get("id") != victim_id
            and player.get("name") != victim_name
            and player.get("side") == "T"
            and _alive(player)
            and _has_xy(player)
        ]
        if not teammates:
            continue
        if not context.geometry_valid([victim, *teammates]):
            continue

        scale = context.world_units_per_percent
        nearest_teammate = min(teammates, key=lambda teammate: _distance(victim, teammate, scale))
        nearest_distance = _distance(victim, nearest_teammate, scale)
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
                tick_end=min(_round_int(round_info, "endTick", tick), tick + int(context.tick_rate * 3)),
                category="positioning",
                severity="medium",
                title="Review opening-death support distance",
                message=(
                    f"{victim_name} was the opening death on T; in the preceding position sample, "
                    f"the nearest living teammate was {nearest_distance:.0f} world units away."
                ),
                involved_player_ids=[victim_id, teammate_id],
                evidence_ticks=[int(frame["tick"]), tick],
                metadata={
                    "nearestTeammateId": teammate_id,
                    "distance": round(nearest_distance, 2),
                    "isolatedTeammateDistance": config.isolated_teammate_distance,
                    "positionSampleTick": int(frame["tick"]),
                    "sampleAgeSeconds": round((tick - int(frame["tick"])) / context.tick_rate, 3),
                    "relatedEventIds": context.kill_event_ids(first_death),
                    **({"attackerName": attacker_name} if attacker_name else {}),
                    **_death_card_facts(
                        context, first_death, round_number, "T",
                        context.alive_counts_before(tick, victim_id, victim_name, "T"),
                    ),
                },
                confidence=0.68,
            )
        )

    return events


@dataclass
class SpacingStretch:
    """Consecutive evaluated position samples on which one spacing finding holds.

    A stretch starts on a side's sample where the single-sample classification
    picks it (too_far before stacked, as the original rule) and continues while
    the same subject -- the isolated player, or the same stacked pair -- still
    meets its own condition on the next evaluated samples of the same round.
    The fields describe the starting sample; `last_tick` is the last sample on
    which the condition still held.
    """

    spacing_type: str
    side: str
    round_number: int
    start_tick: int
    last_tick: int
    focus_id: str
    focus_name: str
    partner_id: str | None
    involved_player_ids: list[str]
    distance: float
    vertical_distance: float | None
    nearby_count: int
    max_nearest_distance: float
    min_pair_distance: float

    @property
    def key(self) -> tuple[str, str, str, str | None]:
        if self.spacing_type == "stacked" and self.partner_id is not None:
            first, second = sorted((self.focus_id, self.partner_id))
            return (self.side, self.spacing_type, first, second)
        return (self.side, self.spacing_type, self.focus_id, None)

    def duration_seconds(self, tick_rate: int) -> float:
        return (self.last_tick - self.start_tick) / tick_rate


def scan_spacing_stretches(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[SpacingStretch]:
    """Every spacing stretch of the live rounds, ordered by start tick."""
    context = ReplayContext(replay)
    scale = context.world_units_per_percent
    gap_ticks = context.tick_rate * SPACING_STRETCH_MAX_GAP_SECONDS
    delay_ticks = context.tick_rate * config.poor_spacing_eval_delay_seconds
    active: dict[tuple[str, str, str, str | None], SpacingStretch] = {}
    closed: list[SpacingStretch] = []

    def close(keys: Iterable[tuple[str, str, str, str | None]]) -> None:
        for key in list(keys):
            closed.append(active.pop(key))

    for frame in context.frames:
        tick = int(frame["tick"])
        if context.live_round_at(tick) is None:
            close(active)
            continue
        round_number = context.round_for_tick(tick, frame.get("roundNumber"))
        round_info = context.round_by_number.get(round_number, {})
        round_start_tick = _round_int(round_info, "freezeEndTick", _round_int(round_info, "startTick", 0))
        if tick < round_start_tick + delay_ticks:
            close(active)
            continue

        for side in ("T", "CT"):
            side_keys = [key for key in active if key[0] == side]
            alive_players = [
                player for player in _frame_players(frame)
                if player.get("side") == side and _alive(player) and _has_xy(player)
            ]
            if len(alive_players) < 3 or not context.geometry_valid(alive_players):
                close(side_keys)
                continue

            by_id = {_player_id(player): player for player in alive_players}
            for key in side_keys:
                stretch = active[key]
                if (
                    stretch.round_number == round_number
                    and tick - stretch.last_tick <= gap_ticks
                    and _stretch_still_holds(stretch, by_id, alive_players, scale, config)
                ):
                    stretch.last_tick = tick
                else:
                    close([key])

            started = _spacing_stretch_start(alive_players, side, round_number, tick, scale, config)
            if started is not None and started.key not in active:
                active[started.key] = started

    close(active)
    return sorted(
        closed,
        key=lambda item: (item.start_tick, item.side, item.spacing_type, item.focus_id, item.partner_id or ""),
    )


def find_poor_spacing(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
    *,
    stretches: list[SpacingStretch] | None = None,
) -> list[CoachingEventCandidate]:
    """Stacked pairs that stayed together, or were both killed by one enemy.

    too_far is not a card of its own: it only ever becomes a reason on the death
    card that follows it (see `merge_death_cards`).
    """
    context = ReplayContext(replay)
    if stretches is None:
        stretches = scan_spacing_stretches(replay, config)
    events: list[CoachingEventCandidate] = []
    min_duration_ticks = context.tick_rate * config.poor_spacing_min_duration_seconds

    for stretch in stretches:
        if stretch.spacing_type != "stacked" or stretch.partner_id is None:
            continue
        if (
            _round_rule_count(events, stretch.round_number, "poor_spacing", stretch.focus_id)
            >= config.max_events_per_round_per_rule
        ):
            continue
        multikill: dict[str, Any] | None = None
        if stretch.last_tick - stretch.start_tick < min_duration_ticks:
            # Too short on its own: only a same-enemy double kill makes it a card,
            # and only then does the card carry the multikill fields.
            multikill = _stacked_multikill(context, stretch, config)
            if multikill is None:
                continue

        duration = stretch.duration_seconds(context.tick_rate)
        message = (
            f"The closest {stretch.side} teammates were {stretch.min_pair_distance:.0f} world units apart "
            f"and stayed that close for {_format_seconds(duration)} seconds."
        )
        if multikill is not None:
            message += " Both were then killed by the same enemy within a few seconds of each other."
        events.append(
            _event(
                replay,
                rule_id="poor_spacing",
                round_number=stretch.round_number,
                player_id=stretch.focus_id,
                player_name=stretch.focus_name,
                tick_start=stretch.start_tick,
                tick_end=stretch.start_tick + int(context.tick_rate * STACKED_CARD_SECONDS),
                category="positioning",
                severity="low",
                title="Review close teammate spacing",
                message=message,
                involved_player_ids=stretch.involved_player_ids,
                evidence_ticks=[stretch.start_tick],
                metadata={
                    "side": stretch.side,
                    "spacingType": "stacked",
                    "distance": _round_or_none(stretch.distance),
                    "nearbyCount": stretch.nearby_count,
                    "poorSpacingMinDistance": config.poor_spacing_min_distance,
                    "poorSpacingMaxDistance": config.poor_spacing_max_distance,
                    "maxNearestDistance": round(stretch.max_nearest_distance, 2),
                    "minPairDistance": round(stretch.min_pair_distance, 2),
                    **({
                        "verticalDistanceWorldUnits": round(stretch.vertical_distance, 2),
                        "maxStackedVerticalDistanceWorldUnits": config.max_stacked_vertical_distance,
                    } if stretch.vertical_distance is not None else {}),
                    "durationSeconds": _format_number(duration),
                    **(multikill or {}),
                },
                confidence=0.55,
            )
        )

    return events


def too_far_stretches(
    stretches: Iterable[SpacingStretch],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
    tick_rate: int = 64,
) -> list[SpacingStretch]:
    """The too_far stretches long enough to count as a reason on a death card."""
    min_duration_ticks = tick_rate * config.poor_spacing_min_duration_seconds
    return [
        stretch
        for stretch in stretches
        if stretch.spacing_type == "too_far" and stretch.last_tick - stretch.start_tick >= min_duration_ticks
    ]


def merge_death_cards(
    events: list[CoachingEventCandidate],
    far_stretches: Iterable[SpacingStretch] = (),
    tick_rate: int = 64,
) -> list[CoachingEventCandidate]:
    """One card per death.

    The untraded_death card is the card; without one, the isolated_entry card
    is. Both point at the same kill through `relatedEventIds`, which is the
    merge key; (round, victim, death tick) only stands in when a kill event id
    is missing. An isolated_entry of a death that has an untraded_death card
    becomes an extra reason on that card, and a too_far stretch of the victim
    overlapping the 5 s before the death becomes one on whichever card the
    death has. Cards keep their own ids: `extraReasons` never enters an id.
    """
    untraded_by_death: dict[tuple[Any, ...], CoachingEventCandidate] = {}
    for event in events:
        if _context_of(event).get("ruleId") == "untraded_death":
            untraded_by_death.setdefault(_death_key(event), event)

    extra_reasons: dict[str, list[dict[str, Any]]] = {}
    kept: list[CoachingEventCandidate] = []
    for event in events:
        if _context_of(event).get("ruleId") == "isolated_entry":
            card = untraded_by_death.get(_death_key(event))
            if card is not None:
                extra_reasons.setdefault(str(card["id"]), []).append(_isolated_entry_reason(event))
                continue
        kept.append(event)

    far_by_player: dict[tuple[int, str], list[SpacingStretch]] = {}
    for stretch in far_stretches:
        far_by_player.setdefault((stretch.round_number, stretch.focus_id), []).append(stretch)
    before_death_ticks = tick_rate * TOO_FAR_BEFORE_DEATH_SECONDS

    merged: list[CoachingEventCandidate] = []
    for event in kept:
        context = _context_of(event)
        if context.get("ruleId") not in DEATH_CARD_RULES:
            merged.append(event)
            continue
        reasons = list(extra_reasons.get(str(event["id"]), []))
        death_tick = int(event["tick_start"])
        overlapping = [
            stretch
            for stretch in far_by_player.get((int(event["round_number"]), str(event["player_id"])), [])
            if stretch.start_tick <= death_tick and stretch.last_tick >= death_tick - before_death_ticks
        ]
        if overlapping:
            # The stretch closest to the death; of equal ones, the longest.
            stretch = max(overlapping, key=lambda item: (item.last_tick, -item.start_tick))
            reasons.append({
                "ruleId": "poor_spacing",
                "spacingType": "too_far",
                "distance": round(stretch.distance),
                "durationSeconds": _format_number(stretch.duration_seconds(tick_rate)),
                "tick": stretch.start_tick,
            })
        if reasons:
            reasons.sort(key=lambda reason: (_tick_or(reason.get("tick"), death_tick), str(reason.get("ruleId"))))
            event = {**event, "structured_context_json": {**context, "extraReasons": reasons}}
        merged.append(event)
    return merged


def find_post_plant_spread_issues(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    min_duration_ticks = int(context.tick_rate * config.post_plant_min_duration_seconds)
    # A round with a recorded plant is covered by post_plant_spacing_with_bomb_event.
    rounds_with_plant_event = {
        context.round_for_tick(int(event["tick"]), event.get("roundNumber"))
        for event in context.bomb_plant_events()
    }

    for round_number, frames in context.frames_by_round().items():
        if round_number in rounds_with_plant_event:
            continue
        segment: list[dict[str, Any]] = []
        for frame in frames:
            if not _planted_bomb_position(frame):
                segment = []
                continue

            alive_t = [
                player
                for player in _frame_players(frame)
                if player.get("side") == "T" and _alive(player) and _has_xy(player)
            ]
            if len(alive_t) < config.post_plant_min_players:
                segment = []
                continue
            if not context.geometry_valid(alive_t):
                segment = []
                continue

            max_pair_distance = _max_pair_distance(alive_t, context.world_units_per_percent)
            if max_pair_distance is None or max_pair_distance > config.post_plant_cluster_distance:
                segment = []
                continue

            segment.append(frame)
            start_tick = int(segment[0]["tick"])
            end_tick = int(segment[-1]["tick"])
            if end_tick - start_tick < min_duration_ticks:
                continue
            if _round_rule_count(events, round_number, "post_plant_spread_issue") >= config.max_events_per_round_per_rule:
                break

            focus_player = alive_t[0]
            evidence_ticks = [int(item["tick"]) for item in segment]
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
                    title="Review sampled post-plant clustering",
                    message=(
                        f"{len(alive_t)} Ts are clustered in position samples spanning "
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


def find_weak_utility_before_execute(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    window_ticks = int(context.tick_rate * config.execute_utility_window_seconds)
    # A replay without any utility event has the family missing, not a team that threw nothing.
    utility_recorded = any(_event_type(event) in UTILITY_EVENT_TYPES for event in context.events)
    buy_kinds: dict[int, dict[str, str | None]] | None = None

    for plant_event in context.bomb_plant_events():
        plant_tick = _event_tick(plant_event)
        if plant_tick is None or not utility_recorded:
            continue
        round_number = context.round_for_tick(plant_tick, plant_event.get("roundNumber"))
        if (
            _round_rule_count(events, round_number, "weak_utility_before_execute")
            >= config.max_events_per_round_per_rule
        ):
            continue

        if buy_kinds is None:
            buy_kinds = buy_kinds_by_side(replay)
        t_buy_kind = buy_kinds.get(round_number, {}).get("T")
        round_utility_events = [
            event for event in context.utility_events_for_round(round_number) if context.event_side(event) == "T"
        ]
        # No T utility in the whole round still counts, unless the Ts were on an eco.
        if not round_utility_events and t_buy_kind == "eco":
            continue

        window_start_tick = plant_tick - window_ticks
        execute_utility_events = [
            event
            for event in round_utility_events
            if (event_tick := _event_tick(event)) is not None and window_start_tick <= event_tick <= plant_tick
        ]
        if len(execute_utility_events) >= config.min_execute_utility_events:
            continue

        related_events = [plant_event, *execute_utility_events]
        evidence_ticks = sorted(
            tick
            for tick in [_event_tick(plant_event), *[_event_tick(event) for event in execute_utility_events]]
            if tick is not None
        )
        utility_types = _unique_values(_event_type(event) for event in execute_utility_events)
        player_id = _event_player_id(plant_event) or "unknown"
        player_name = _event_player_name(plant_event) or context.player_name(player_id)
        events.append(
            _event(
                replay,
                rule_id="weak_utility_before_execute",
                round_number=round_number,
                player_id=player_id,
                player_name=player_name,
                tick_start=plant_tick,
                tick_end=plant_tick,
                category="utility",
                severity="low",
                title="Review team utility before the plant",
                message=(
                    f"{player_name} planted; {len(execute_utility_events)} T utility event"
                    f"{'' if len(execute_utility_events) == 1 else 's'} in the prior "
                    f"{_format_seconds(config.execute_utility_window_seconds)} seconds are recorded."
                ),
                involved_player_ids=[
                    player_id,
                    *[_event_player_id(event) for event in execute_utility_events],
                ],
                evidence_ticks=evidence_ticks,
                metadata={
                    "relatedEventIds": _related_event_ids(related_events),
                    "bombTick": plant_tick,
                    "bombEventType": _event_type(plant_event),
                    "bombEventLabel": _event_label(plant_event),
                    "utilityCount": len(execute_utility_events),
                    "requiredUtilityCount": int(config.min_execute_utility_events),
                    "utilityTypes": utility_types,
                    "windowSeconds": _format_number(config.execute_utility_window_seconds),
                    **({"tBuyKind": t_buy_kind} if t_buy_kind else {}),
                },
                confidence=0.64,
            )
        )

    return events


def find_post_plant_spacing_with_bomb_event(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    context = ReplayContext(replay)
    events: list[CoachingEventCandidate] = []
    min_duration_ticks = int(context.tick_rate * config.post_plant_min_duration_seconds)
    frames_by_round = context.frames_by_round()

    for plant_event in context.bomb_plant_events():
        plant_tick = _event_tick(plant_event)
        if plant_tick is None:
            continue
        round_number = context.round_for_tick(plant_tick, plant_event.get("roundNumber"))
        if (
            _round_rule_count(events, round_number, "post_plant_spacing_with_bomb_event")
            >= config.max_events_per_round_per_rule
        ):
            continue

        segment: list[dict[str, Any]] = []
        for frame in frames_by_round.get(round_number, []):
            frame_tick = _int_or_none(frame.get("tick")) or 0
            if frame_tick < plant_tick:
                continue
            if not _planted_bomb_position(frame):
                segment = []
                continue

            alive_t = [
                player
                for player in _frame_players(frame)
                if player.get("side") == "T" and _alive(player) and _has_xy(player)
            ]
            if len(alive_t) < config.post_plant_min_players:
                segment = []
                continue
            if not context.geometry_valid(alive_t):
                segment = []
                continue

            max_pair_distance = _max_pair_distance(alive_t, context.world_units_per_percent)
            if max_pair_distance is None or max_pair_distance > config.post_plant_cluster_distance:
                segment = []
                continue

            segment.append(frame)
            start_tick = _int_or_none(segment[0].get("tick")) or frame_tick
            end_tick = _int_or_none(segment[-1].get("tick")) or frame_tick
            if end_tick - start_tick < min_duration_ticks:
                continue

            focus_player = alive_t[0]
            evidence_ticks = [_int_or_none(item.get("tick")) or 0 for item in segment]
            site = _event_site(plant_event) or _bomb_site(segment[-1])
            events.append(
                _event(
                    replay,
                    rule_id="post_plant_spacing_with_bomb_event",
                    round_number=round_number,
                    player_id=_player_id(focus_player),
                    player_name=_player_name(focus_player),
                    tick_start=start_tick,
                    tick_end=end_tick,
                    category="objective",
                    severity="medium",
                    title="Review clustering after the recorded plant",
                    message=(
                        f"{len(alive_t)} Ts are clustered in position samples spanning "
                        f"{(end_tick - start_tick) / context.tick_rate:.1f} seconds after the recorded plant."
                    ),
                    involved_player_ids=[_player_id(player) for player in alive_t],
                    evidence_ticks=evidence_ticks,
                    metadata={
                        "relatedEventIds": _related_event_ids([plant_event]),
                        "bombTick": plant_tick,
                        "bombEventType": _event_type(plant_event),
                        "bombEventLabel": _event_label(plant_event),
                        "site": site,
                        "nearbyCount": len(alive_t),
                        "distance": round(max_pair_distance, 2),
                        "clusterDistance": config.post_plant_cluster_distance,
                        "windowSeconds": _format_number((end_tick - start_tick) / context.tick_rate),
                    },
                    confidence=0.69,
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
        # CTs already near the bomb in the first planted sample did not arrive: they never count.
        already_there: set[str] = set()
        first_entry_by_ct: dict[str, tuple[int, dict[str, Any], dict[str, Any]]] = {}
        planted_seen = False
        for frame in frames:
            bomb_position = _planted_bomb_position(frame)
            if bomb_position is None:
                continue
            first_planted_frame = not planted_seen
            planted_seen = True

            for player in _frame_players(frame):
                player_id = _player_id(player)
                if (
                    player.get("side") != "CT"
                    or player_id in first_entry_by_ct
                    or player_id in already_there
                    or not _alive(player)
                    or not _has_xy(player)
                ):
                    continue
                if not context.geometry_valid([player, frame.get("bombState", {})]):
                    continue
                if _distance_to_xy(player, bomb_position, context.world_units_per_percent) <= config.retake_site_distance:
                    if first_planted_frame:
                        already_there.add(player_id)
                    else:
                        first_entry_by_ct[player_id] = (int(frame["tick"]), player, frame)

        if len(first_entry_by_ct) < 2:
            continue

        ordered_entries = sorted(first_entry_by_ct.values(), key=lambda item: item[0])
        first_tick = ordered_entries[0][0]
        last_tick = ordered_entries[-1][0]
        if last_tick - first_tick < desync_ticks:
            continue
        # Once no T is alive the retake is over; a late arrival then is not a desync.
        last_frame = ordered_entries[-1][2]
        if not any(player.get("side") == "T" and _alive(player) for player in _frame_players(last_frame)):
            continue
        if _round_rule_count(events, round_number, "retake_desync") >= config.max_events_per_round_per_rule:
            continue

        involved_players = [player for _, player, _ in ordered_entries]
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
                title="Review staggered CT arrivals near the bomb",
                message=(
                    "CTs reached the planted bomb area "
                    f"{(last_tick - first_tick) / context.tick_rate:.1f} seconds apart."
                ),
                involved_player_ids=[_player_id(player) for player in involved_players],
                evidence_ticks=[tick for tick, _, _ in ordered_entries],
                metadata={
                    "windowSeconds": _format_number((last_tick - first_tick) / context.tick_rate),
                    "retakeSiteDistance": config.retake_site_distance,
                    "nearbyCount": len(involved_players),
                },
                confidence=0.6,
            )
        )

    return events


class RecordedShot(NamedTuple):
    tick: int
    speed: int
    airborne: bool
    weapon: str


@dataclass
class ShotBurst:
    """Consecutive shots of one judged weapon in one live round, at most the burst gap apart."""

    round_number: int
    weapon: str
    # The player fired no gun in the opener-quiet window before the first shot.
    opener: bool
    shots: list[RecordedShot]


def find_shooting_issues(
    replay: dict[str, Any],
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> list[CoachingEventCandidate]:
    """moving_shots and no_counter_strafe, from the recorded gun shots (replay contract v5 `shots`).

    Only rifles, snipers, the Deagle and the R8 are judged (`shot_weapon_max_speeds`).
    A shot is moving above its weapon's accurate speed or in the air, and hit when
    a gun damage event of the shooter lands on its tick or up to
    `shot_hit_window_ticks` after it. A weapon change, a shot of another gun or a
    gap longer than the burst gap ends a burst.

    - moving_shots: a burst with at least `moving_shots_min` moving shots, none of which hit.
    - no_counter_strafe: an opener burst whose first shot was clearly too fast
      (accurate speed + `counter_strafe_margin`) or airborne, that is no
      moving_shots burst, and in which no shot hit or after which the player died
      within `shot_death_window_seconds`.

    One card per player per round: the first moving_shots burst, else the first
    no_counter_strafe one; `occurrencesInRound` counts the round's bursts that
    qualified for either. A player keeps at most `no_counter_strafe_max_per_match`
    no_counter_strafe cards (died first, then the fastest first shot, then the
    earliest); moving_shots is not capped. Best effort: no `shots`, or a replay
    without any damage event (the family is missing, so no miss can be told),
    gives no cards.
    """
    shots_by_player = _recorded_shots(replay.get("shots"))
    if not shots_by_player:
        return []
    context = ReplayContext(replay)
    if not any(_event_type(event) == "damage" for event in context.events):
        return []

    tick_rate = context.tick_rate
    max_speeds = dict(config.shot_weapon_max_speeds)
    hits = _gun_hits_by_attacker(context.events)
    round_of = _live_round_lookup(context)
    raw_inputs = replay.get("inputs")
    inputs: dict[str, Any] = raw_inputs if isinstance(raw_inputs, dict) else {}
    death_window_ticks = round(tick_rate * config.shot_death_window_seconds)
    events: list[CoachingEventCandidate] = []

    for player_id in sorted(shots_by_player):
        bursts = _shot_bursts(
            shots_by_player[player_id],
            max_speeds,
            round_of,
            gap_ticks=round(tick_rate * config.shot_burst_gap_seconds),
            quiet_ticks=round(tick_rate * config.shot_opener_quiet_seconds),
        )
        player_hits = hits.get(player_id, ([], []))
        by_round: dict[int, list[tuple[str, ShotBurst, dict[str, Any]]]] = {}
        for burst in bursts:
            accurate = _accurate_speed(max_speeds[burst.weapon], config)
            moving_flags = [shot.speed > accurate or shot.airborne for shot in burst.shots]
            victims_by_shot = [_shot_victims(player_hits, shot.tick, config.shot_hit_window_ticks) for shot in burst.shots]
            moving = [shot for shot, is_moving in zip(burst.shots, moving_flags, strict=True) if is_moving]
            moving_hit = any(victims for victims, is_moving in zip(victims_by_shot, moving_flags, strict=True) if is_moving)
            burst_hit = any(victims_by_shot)
            death = context.death_by_round_and_victim.get((burst.round_number, player_id))
            death_tick = _int_or_none(death.get("tick")) if death else None
            died = death_tick is not None and 0 <= death_tick - burst.shots[-1].tick <= death_window_ticks
            first = burst.shots[0]
            if len(moving) >= config.moving_shots_min and not moving_hit:
                rule_id = "moving_shots"
            elif (
                burst.opener
                and (first.speed > accurate + config.counter_strafe_margin or first.airborne)
                and (not burst_hit or died)
            ):
                rule_id = "no_counter_strafe"
            else:
                continue
            by_round.setdefault(burst.round_number, []).append((rule_id, burst, {
                "accurate": accurate,
                "moving": moving,
                "hit": burst_hit,
                "victims": [victim for victims in victims_by_shot for victim in victims],
                "death": death if died else None,
            }))

        picks = []
        for round_number in sorted(by_round):
            qualified = by_round[round_number]
            rule_id, burst, facts = next(
                (item for item in qualified if item[0] == "moving_shots"),
                qualified[0],
            )
            picks.append((rule_id, burst, facts, len(qualified)))
        kept = _kept_no_counter_strafe(picks, config.no_counter_strafe_max_per_match)

        input_track: tuple[list[int], list[int]] | None = None
        for index, (rule_id, burst, facts, occurrences) in enumerate(picks):
            if rule_id == "no_counter_strafe" and index not in kept:
                continue
            if input_track is None:
                input_track = _input_track(inputs.get(player_id))
            events.append(_shooting_card(
                replay, context, config, player_id, rule_id, burst, facts,
                occurrences=occurrences, input_track=input_track,
            ))

    return events


def _kept_no_counter_strafe(picks: list[tuple[str, ShotBurst, dict[str, Any], int]], limit: int) -> set[int]:
    """Indexes of the no_counter_strafe picks kept: died first, then the fastest first shot, then the earliest."""
    candidates = [index for index, pick in enumerate(picks) if pick[0] == "no_counter_strafe"]
    candidates.sort(key=lambda index: (
        picks[index][2]["death"] is None,
        -picks[index][1].shots[0].speed,
        picks[index][1].shots[0].tick,
    ))
    return set(candidates[:max(0, limit)])


def _shooting_card(
    replay: dict[str, Any],
    context: ReplayContext,
    config: RuleConfig,
    player_id: str,
    rule_id: str,
    burst: ShotBurst,
    facts: dict[str, Any],
    *,
    occurrences: int,
    input_track: tuple[list[int], list[int]],
) -> CoachingEventCandidate:
    tick_rate = context.tick_rate
    first, last = burst.shots[0], burst.shots[-1]
    moving: list[RecordedShot] = facts["moving"]
    accurate: int = facts["accurate"]
    death: dict[str, Any] | None = facts["death"]
    label = SHOT_WEAPON_LABELS.get(burst.weapon, burst.weapon)
    player_name = context.player_name(player_id)
    round_info = context.round_by_number.get(burst.round_number, {})
    live_start = _round_int(round_info, "freezeEndTick", _round_int(round_info, "startTick", 0))
    tick_start = max(live_start, first.tick - round(tick_rate * config.shot_card_lead_seconds))
    tick_end = last.tick + round(tick_rate * config.shot_card_tail_seconds)

    killer_id = _optional_str(death.get("attackerId")) if death else None
    killer_name = _optional_str(death.get("attackerName")) if death else None
    if killer_id == player_id:
        killer_id = killer_name = None
    if rule_id == "moving_shots":
        speed = max(shot.speed for shot in moving)
        airborne = any(shot.airborne for shot in moving)
        title = "Review shots fired while moving"
        message = (
            f"{player_name} fired {len(moving)} {label} shots while moving (up to {speed} units/s; "
            f"accurate at or below {accurate}) and none of them hit."
        )
        confidence = 0.6
    else:
        speed = first.speed
        airborne = first.airborne
        title = "Review the first shot of a fight"
        outcomes = [
            *([] if facts["hit"] else ["no shot of the burst hit"]),
            *([f"{player_name} died within {_format_seconds(config.shot_death_window_seconds)} seconds"] if death else []),
        ]
        message = (
            f"{player_name} took the first {label} shot at {speed} units/s{' in the air' if airborne else ''} "
            f"(accurate at or below {accurate}); {' and '.join(outcomes)}."
        )
        confidence = 0.55

    keys = _movement_keys(input_track, first.tick, round(tick_rate * config.counter_strafe_window_seconds))
    side = context.side_for_at(player_id, None, first.tick)
    return _event(
        replay,
        rule_id=rule_id,
        round_number=burst.round_number,
        player_id=player_id,
        player_name=player_name,
        tick_start=tick_start,
        tick_end=tick_end,
        category="mechanics",
        severity="low",
        title=title,
        message=message,
        involved_player_ids=[player_id, *facts["victims"], killer_id],
        evidence_ticks=[shot.tick for shot in moving],
        metadata={
            "weapon": burst.weapon,
            "weaponLabel": label,
            "accurateSpeed": accurate,
            "speed": speed,
            "shotCount": len(burst.shots),
            "movingShotCount": len(moving),
            "airborne": airborne,
            "hit": bool(facts["hit"]),
            "died": death is not None,
            **({"attackerName": killer_name} if killer_name else {}),
            **({"side": side} if side in {"T", "CT"} else {}),
            **({"keysAtShot": keys[0], "counterStrafe": keys[1]} if keys is not None else {}),
            "occurrencesInRound": occurrences,
        },
        confidence=confidence,
    )


def _accurate_speed(max_speed: int, config: RuleConfig) -> int:
    return round(config.shot_accurate_speed_ratio * max_speed)


def _recorded_shots(value: Any) -> dict[str, list[RecordedShot]]:
    """Well-formed `[tick, speed, flags, weapon]` rows per player id, sorted by tick; junk rows are skipped."""
    if not isinstance(value, dict):
        return {}
    tracks: dict[str, list[RecordedShot]] = {}
    for raw_id, rows in value.items():
        player_id = _optional_str(raw_id)
        if not player_id or not isinstance(rows, list):
            continue
        shots = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 4:
                continue
            tick, speed, flags, weapon = row[0], row[1], row[2], row[3]
            if not _plain_int(tick) or not _plain_int(flags) or not isinstance(weapon, str) or not weapon:
                continue
            if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed < 0:
                continue
            shots.append(RecordedShot(int(tick), round(speed), bool(int(flags) & SHOT_FLAG_AIRBORNE), weapon))
        if shots:
            tracks[player_id] = sorted(shots, key=lambda shot: shot.tick)
    return tracks


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _live_round_lookup(context: ReplayContext) -> Callable[[int], int | None]:
    """`live_round_at` by bisection: shots run to tens of thousands per demo."""
    intervals = sorted(
        (_round_int(item, "freezeEndTick", _round_int(item, "startTick", 0)), _round_int(item, "endTick", 0), number)
        for item in context.rounds
        if (number := _int_or_none(item.get("roundNumber"))) is not None
    )
    starts = [start for start, _, _ in intervals]

    def round_of(tick: int) -> int | None:
        index = bisect_right(starts, tick) - 1
        if index < 0 or tick > intervals[index][1]:
            return None
        return intervals[index][2]

    return round_of


def _shot_bursts(
    shots: list[RecordedShot],
    max_speeds: dict[str, int],
    round_of: Callable[[int], int | None],
    *,
    gap_ticks: int,
    quiet_ticks: int,
) -> list[ShotBurst]:
    bursts: list[ShotBurst] = []
    current: ShotBurst | None = None
    previous_tick: int | None = None
    for shot in shots:
        round_number = round_of(shot.tick)
        if round_number is None or shot.weapon not in max_speeds:
            current = None
        elif (
            current is not None
            and current.weapon == shot.weapon
            and current.round_number == round_number
            and shot.tick - current.shots[-1].tick <= gap_ticks
        ):
            current.shots.append(shot)
        else:
            opener = previous_tick is None or shot.tick - previous_tick > quiet_ticks
            current = ShotBurst(round_number=round_number, weapon=shot.weapon, opener=opener, shots=[shot])
            bursts.append(current)
        previous_tick = shot.tick
    return bursts


def _gun_hits_by_attacker(events: list[dict[str, Any]]) -> dict[str, tuple[list[int], list[str | None]]]:
    """Attacker id -> (sorted damage ticks, victim ids) of the damage events a gun can have caused."""
    rows: dict[str, list[tuple[int, str | None]]] = {}
    for event in events:
        if _event_type(event) != "damage":
            continue
        tick = _event_tick(event)
        raw_metadata = event.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        attacker_id = _optional_str(metadata.get("attackerId")) or _optional_str(event.get("playerId"))
        if tick is None or not attacker_id:
            continue
        victim_id = _optional_str(metadata.get("victimId"))
        if victim_id == attacker_id:
            continue
        weapon = metadata.get("weapon")
        if isinstance(weapon, str) and weapon.strip().lower().removeprefix("weapon_") in NON_GUN_DAMAGE_WEAPONS:
            continue
        rows.setdefault(attacker_id, []).append((tick, victim_id))
    hits: dict[str, tuple[list[int], list[str | None]]] = {}
    for attacker_id, items in rows.items():
        items.sort(key=lambda item: item[0])
        hits[attacker_id] = ([tick for tick, _ in items], [victim for _, victim in items])
    return hits


def _shot_victims(hits: tuple[list[int], list[str | None]], tick: int, window_ticks: int) -> list[str | None]:
    """One entry per damage of the shooter on [tick, tick + window]: the victim id, None when unrecorded."""
    ticks, victims = hits
    index = bisect_left(ticks, tick)
    found = []
    while index < len(ticks) and ticks[index] <= tick + window_ticks:
        found.append(victims[index])
        index += 1
    return found


def _input_track(value: Any) -> tuple[list[int], list[int]]:
    """A player's v3 `inputs` change points as sorted (ticks, masks) columns; empty without a usable track."""
    if not isinstance(value, list):
        return [], []
    pairs = sorted(
        (
            (int(item[0]), int(item[1]))
            for item in value
            if isinstance(item, (list, tuple)) and len(item) >= 2 and _plain_int(item[0]) and _plain_int(item[1])
        ),
        key=lambda item: item[0],
    )
    return [tick for tick, _ in pairs], [mask for _, mask in pairs]


def _movement_keys(track: tuple[list[int], list[int]], tick: int, window_ticks: int) -> tuple[list[str], bool] | None:
    """(movement keys held at `tick` in W A S D order, counter-strafed); None when the inputs do not cover `tick`.

    The mask at a tick is the last change point at or before it. Counter-strafed:
    a movement key was pressed in (tick - window, tick] while its opposite key
    had been held earlier in that window.
    """
    ticks, masks = track
    index = bisect_right(ticks, tick) - 1
    if index < 0:
        return None
    held = [label for label, bit in MOVEMENT_KEY_BITS if masks[index] & bit]
    start_index = bisect_right(ticks, tick - window_ticks) - 1
    seen = previous = masks[start_index] if start_index >= 0 else 0
    counter_strafed = False
    for mask in masks[start_index + 1:index + 1]:
        pressed = mask & ~previous
        if any(pressed & bit and seen & OPPOSITE_MOVEMENT_BIT[bit] for _, bit in MOVEMENT_KEY_BITS):
            counter_strafed = True
        seen |= mask
        previous = mask
    return held, counter_strafed


class ReplayContext:
    def __init__(self, replay: dict[str, Any]):
        self.replay = replay
        self.tick_rate = max(1, int(replay.get("tickRate") or 64))
        self.players = _dict_items(replay.get("players"))
        # A frame without a usable tick cannot be placed in time, so no rule reads it.
        self.frames = sorted(
            [item for item in _dict_items(replay.get("frames")) if _int_or_none(item.get("tick")) is not None],
            key=lambda item: int(item["tick"]),
        )
        self.frame_ticks = [int(frame["tick"]) for frame in self.frames]
        self.rounds = _dict_items(replay.get("rounds"))
        self.kills = sorted(
            _dict_items(replay.get("kills")),
            key=lambda item: _int_or_none(item.get("tick")) or 0,
        )
        self.deaths = sorted(
            _dict_items(replay["deaths"]) if isinstance(replay.get("deaths"), list) else self.kills,
            key=lambda item: _int_or_none(item.get("tick")) or 0,
        )
        self.events = sorted(
            _dict_items(replay.get("events")),
            key=lambda item: _int_or_none(item.get("tick")) or 0,
        )
        self.round_by_number = {
            number: item
            for item in self.rounds
            if (number := _int_or_none(item.get("roundNumber"))) is not None
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
        map_metadata = replay.get("mapMetadata")
        map_config = {
            **(get_map_config(str(replay.get("mapName") or "")) or {}),
            **(map_metadata if isinstance(map_metadata, dict) else {}),
        }
        self.lower_level_max_z = map_config.get("lowerLevelMaxZ")
        self.world_units_per_percent = _axis_scale(map_config.get("worldUnitsPerPercent"))
        # Round -> its first death (by tick; of equal ticks, the first listed) among the live
        # round's deaths. The opening-death rule and every death card's firstDeath read this.
        self.first_death_by_round: dict[int, dict[str, Any]] = {}
        # (round, victim id) -> that victim's first death in the live round.
        self.death_by_round_and_victim: dict[tuple[int, str], dict[str, Any]] = {}
        for death in self.deaths:
            round_number = self.live_round_at(_int_or_none(death.get("tick")) or 0)
            if round_number is None:
                continue
            self.first_death_by_round.setdefault(round_number, death)
            victim_id = _optional_str(death.get("victimId"))
            if victim_id is not None:
                self.death_by_round_and_victim.setdefault((round_number, victim_id), death)

    def geometry_valid(self, players: list[dict[str, Any]]) -> bool:
        if self.lower_level_max_z is None:
            return True
        levels = set()
        for player in players:
            z = player.get("z")
            if not isinstance(z, (float, int)) or not math.isfinite(z):
                return False
            levels.add(z <= float(self.lower_level_max_z))
        return len(levels) <= 1

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
        for item in self.rounds:
            # A round whose number is unusable never made it into
            # round_by_number, so returning it here would hand callers a key
            # that cannot be looked up.
            number = _int_or_none(item.get("roundNumber"))
            if number is None:
                continue
            if _round_int(item, "startTick", 0) <= tick <= _round_int(item, "endTick", 0):
                return number
        if fallback is not None:
            parsed = _int_or_none(fallback)
            if parsed is not None and parsed > 0:
                return parsed
        for item in self.rounds:
            number = _int_or_none(item.get("roundNumber"))
            if number is not None:
                return number
        return 1

    def live_round_at(self, tick: int) -> int | None:
        for item in self.rounds:
            number = _int_or_none(item.get("roundNumber"))
            if number is None:
                continue
            start = _round_int(item, "freezeEndTick", _round_int(item, "startTick", 0))
            if start <= tick <= _round_int(item, "endTick", 0):
                return number
        return None

    def frame_at(self, tick: int) -> dict[str, Any] | None:
        index = bisect_right(self.frame_ticks, tick) - 1
        if index < 0:
            return None
        frame = self.frames[index]
        if self.round_for_tick(int(frame.get("tick", 0))) != self.round_for_tick(tick):
            return None
        return frame

    def position_frame_at(self, tick: int, max_age_seconds: float = 1.0) -> dict[str, Any] | None:
        frame = self.frame_at(tick)
        if frame is None or tick - int(frame["tick"]) > self.tick_rate * max_age_seconds:
            return None
        return frame

    def frame_before(self, tick: int) -> dict[str, Any] | None:
        """The last frame strictly before `tick`, if it belongs to the same round."""
        index = bisect_left(self.frame_ticks, tick) - 1
        if index < 0:
            return None
        frame = self.frames[index]
        if self.round_for_tick(int(frame["tick"])) != self.round_for_tick(tick):
            return None
        return frame

    def alive_counts_before(
        self,
        tick: int,
        victim_id: str | None,
        victim_name: str | None,
        victim_side: str | None,
    ) -> tuple[int, int] | None:
        """(own, enemy) players alive in the last frame strictly before a death.

        None when that frame is missing, or does not show the victim alive on
        his side: the counts would be a guess.
        """
        if victim_side not in {"T", "CT"}:
            return None
        frame = self.frame_before(tick)
        if frame is None:
            return None
        victim = _find_frame_player(frame, victim_id, victim_name)
        if victim is None or victim.get("side") != victim_side or not _alive(victim):
            return None
        enemy_side = "CT" if victim_side == "T" else "T"
        players = _frame_players(frame)
        own = sum(1 for player in players if player.get("side") == victim_side and _alive(player))
        enemy = sum(1 for player in players if player.get("side") == enemy_side and _alive(player))
        return own, enemy

    def player_name(self, player_id: str) -> str:
        for player in self.players:
            if str(player.get("id")) == player_id:
                return _player_name(player)
        return player_id

    def event_side(self, event: dict[str, Any]) -> str | None:
        side = _event_side(event)
        if side:
            return side
        player_id = _event_player_id(event)
        tick = _event_tick(event)
        frame = self.frame_at(tick) if tick is not None else None
        player = _find_frame_player(frame, player_id, _event_player_name(event)) if frame else None
        # Never infer a side from the roster across halftime when frame data is absent.
        return str(player["side"]) if player and player.get("side") in {"T", "CT"} else None

    def kill_event_ids(self, death: dict[str, Any]) -> list[str]:
        return _related_event_ids(
            event for event in self.events
            if _event_type(event) == "kill" and _event_tick(event) == _int_or_none(death.get("tick"))
            and isinstance(event.get("metadata"), dict)
            and event["metadata"].get("victimId") == death.get("victimId")
        )

    def player_position_at(
        self,
        player_id: str | None,
        player_name: str | None,
        tick: int,
    ) -> dict[str, Any] | None:
        frame = self.position_frame_at(tick)
        if frame is None:
            return None
        return _find_frame_player(frame, player_id, player_name)

    def frames_by_round(self) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for frame in self.frames:
            tick = _int_or_none(frame.get("tick")) or 0
            if self.live_round_at(tick) is None:
                continue
            round_number = self.round_for_tick(tick, frame.get("roundNumber"))
            grouped.setdefault(round_number, []).append(frame)
        return grouped

    def bomb_plant_events(self) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if _event_type(event) == BOMB_PLANTED_EVENT_TYPE and _event_tick(event) is not None
            and self.live_round_at(int(event["tick"])) is not None
        ]

    def utility_events_for_round(self, round_number: int) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if _event_type(event) in UTILITY_EVENT_TYPES
            and (event_tick := _event_tick(event)) is not None
            and self.live_round_at(event_tick) == round_number
        ]


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
        if (kill.get("attackerSide") or context.side_for_at(trade_attacker_id, trade_attacker_name, tick)) != victim_side:
            continue

        trade_victim_id = _optional_str(kill.get("victimId"))
        if attacker_id and trade_victim_id == attacker_id:
            return True

    return False


def _death_card_facts(
    context: ReplayContext,
    death: dict[str, Any],
    round_number: int,
    victim_side: str | None,
    alive_before: tuple[int, int] | None,
) -> dict[str, Any]:
    """A death card's `impact` and `weapon`; what the replay does not record is left out."""
    round_info = context.round_by_number.get(round_number, {})
    winner = round_info.get("winnerSide")
    # A round the parser saw end always has a winnerReason. Without one (a demo cut
    # off before round_end) the normalizer still fills winnerSide with "CT", so the
    # winner is unknown rather than CT.
    known_winner = bool(round_info.get("winnerReason")) and winner in {"T", "CT"}
    known_sides = known_winner and victim_side in {"T", "CT"}
    impact: dict[str, Any] = {
        "roundLost": winner != victim_side if known_sides else None,
        "firstDeath": context.first_death_by_round.get(round_number) is death,
    }
    if alive_before is not None:
        own, enemy = alive_before
        impact["aliveBefore"] = {"own": own, "enemy": enemy}
        impact["aliveAfter"] = {"own": own - 1, "enemy": enemy}
        impact["manDisadvantage"] = own >= enemy and own - 1 < enemy
    facts: dict[str, Any] = {"impact": impact}
    weapon = death.get("weapon")
    if isinstance(weapon, str) and weapon.strip():
        facts["weapon"] = weapon
    return facts


def _spacing_stretch_start(
    alive_players: list[dict[str, Any]],
    side: str,
    round_number: int,
    tick: int,
    scale: AxisScale,
    config: RuleConfig,
) -> SpacingStretch | None:
    """The stretch this side's sample starts, classified as the single-sample rule did."""
    spacing = _spacing_snapshot(alive_players, scale)
    if spacing is None:
        return None
    max_nearest = float(spacing["maxNearestDistance"])
    min_pair = float(spacing["minPairDistance"])
    if max_nearest >= config.poor_spacing_max_distance:
        focus = spacing["focusPlayer"]
        return SpacingStretch(
            spacing_type="too_far", side=side, round_number=round_number, start_tick=tick, last_tick=tick,
            focus_id=_player_id(focus), focus_name=_player_name(focus), partner_id=None,
            involved_player_ids=[_player_id(player) for player in alive_players],
            distance=max_nearest, vertical_distance=None, nearby_count=len(alive_players),
            max_nearest_distance=max_nearest, min_pair_distance=min_pair,
        )
    if min_pair <= config.poor_spacing_min_distance:
        first, second = spacing["closestPair"]
        vertical = _vertical_distance_or_none(first, second)
        if vertical is not None and vertical > config.max_stacked_vertical_distance:
            return None
        return SpacingStretch(
            spacing_type="stacked", side=side, round_number=round_number, start_tick=tick, last_tick=tick,
            focus_id=_player_id(first), focus_name=_player_name(first), partner_id=_player_id(second),
            involved_player_ids=[_player_id(first), _player_id(second)],
            distance=min_pair, vertical_distance=vertical, nearby_count=len(alive_players),
            max_nearest_distance=max_nearest, min_pair_distance=min_pair,
        )
    return None


def _stretch_still_holds(
    stretch: SpacingStretch,
    by_id: dict[str, dict[str, Any]],
    alive_players: list[dict[str, Any]],
    scale: AxisScale,
    config: RuleConfig,
) -> bool:
    """Whether the stretch's own subject still meets its condition in this sample."""
    focus = by_id.get(stretch.focus_id)
    if focus is None:
        return False
    if stretch.spacing_type == "too_far":
        nearest = min((_distance(focus, other, scale) for other in alive_players if other is not focus), default=None)
        return nearest is not None and nearest >= config.poor_spacing_max_distance
    partner = by_id.get(stretch.partner_id) if stretch.partner_id is not None else None
    if partner is None:
        return False
    vertical = _vertical_distance_or_none(focus, partner)
    return _distance(focus, partner, scale) <= config.poor_spacing_min_distance and (
        vertical is None or vertical <= config.max_stacked_vertical_distance
    )


def _stacked_multikill(
    context: ReplayContext,
    stretch: SpacingStretch,
    config: RuleConfig,
) -> dict[str, Any] | None:
    """Both players of the pair killed by the same enemy within the multikill window.

    The first of the two deaths has to fall inside [stretch start, stretch end
    + window]. Returns the metadata the stacked card carries, or None.
    """
    if stretch.partner_id is None:
        return None
    deaths: list[dict[str, Any]] = []
    for player_id in (stretch.focus_id, stretch.partner_id):
        death = context.death_by_round_and_victim.get((stretch.round_number, player_id))
        if death is None or _int_or_none(death.get("tick")) is None:
            return None
        deaths.append(death)
    attacker_ids = {_optional_str(death.get("attackerId")) for death in deaths}
    attacker_id = attacker_ids.pop() if len(attacker_ids) == 1 else None
    if not attacker_id or attacker_id in {stretch.focus_id, stretch.partner_id}:
        return None
    first, second = sorted(deaths, key=lambda death: int(death["tick"]))
    first_tick, second_tick = int(first["tick"]), int(second["tick"])
    attacker_name = _optional_str(first.get("attackerName"))
    attacker_side = first.get("attackerSide") or context.side_for_at(attacker_id, attacker_name, first_tick)
    if attacker_side not in {"T", "CT"} or attacker_side == stretch.side:
        return None
    window_ticks = context.tick_rate * config.stacked_multikill_window_seconds
    if second_tick - first_tick > window_ticks:
        return None
    if not stretch.start_tick <= first_tick <= stretch.last_tick + window_ticks:
        return None
    return {
        "stackedMultikill": True,
        "multikillAttackerId": attacker_id,
        **({"multikillAttackerName": attacker_name} if attacker_name else {}),
    }


def _context_of(event: CoachingEventCandidate) -> dict[str, Any]:
    context = event.get("structured_context_json")
    return context if isinstance(context, dict) else {}


def _death_key(event: CoachingEventCandidate) -> tuple[Any, ...]:
    """The death a card is about: its kill event, else (round, victim, death tick)."""
    related = _context_of(event).get("relatedEventIds")
    for value in related if isinstance(related, list) else []:
        if isinstance(value, str) and value:
            return ("kill", value, str(event.get("player_id")))
    return ("death", event.get("round_number"), str(event.get("player_id")), event.get("tick_start"))


def _isolated_entry_reason(event: CoachingEventCandidate) -> dict[str, Any]:
    context = _context_of(event)
    reason: dict[str, Any] = {"ruleId": "isolated_entry"}
    distance = context.get("distance")
    if isinstance(distance, (int, float)) and not isinstance(distance, bool) and math.isfinite(distance):
        reason["distance"] = round(distance)
    reason["tick"] = _tick_or(context.get("positionSampleTick"), int(event["tick_start"]))
    return reason


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
    action, limitation = _REVIEW_GUIDANCE[rule_id]
    map_metadata = replay.get("mapMetadata")
    if rule_id not in {"untraded_death", "weak_utility_before_execute", *SHOOTING_RULES}:
        limitation += " Distances are straight-line world units, not travel distance."
        if isinstance(map_metadata, dict) and not map_metadata.get("calibrated"):
            limitation += " Map calibration is approximate."
    round_info: dict[str, Any] = next((item for item in replay.get("rounds", []) if item.get("roundNumber") == round_number), {})
    tick_end = min(tick_end, _round_int(round_info, "endTick", tick_end))
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{replay.get('demoId')}:{rule_id}:{player_id}:{round_number}:{tick_start}:{tick_end}")),
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
            "targetPlayerId": player_id,
            "assessment": "review_candidate",
            "action": action,
            "limitation": limitation,
            "evidenceSource": _EVIDENCE_SOURCES.get(rule_id, "parser_events_and_sampled_positions"),
            **metadata,
        },
        "confidence": max(0.0, min(1.0, confidence)),
    }


def _spacing_snapshot(players: list[dict[str, Any]], scale: AxisScale) -> dict[str, Any] | None:
    nearest: list[tuple[dict[str, Any], float]] = []
    min_pair_distance = math.inf
    closest_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
    for player in players:
        distances = []
        for other in players:
            if other is player or not _has_xy(other):
                continue
            distance = _distance(player, other, scale)
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
    for player in _frame_players(frame):
        if player_id is not None and str(player.get("id")) == player_id:
            return player
        if player_id is None and player_name is not None and str(player.get("name")) == player_name:
            return player
    return None


def _axis_scale(raw: Any) -> AxisScale:
    """Per-axis world units per radar percentage point, with a safe fallback.

    Replays produced before this field existed, and any map whose transform
    carries no fixed scale, fall back to the Dust II reference so thresholds
    keep the physical meaning they were tuned for instead of collapsing to
    raw percentages.
    """
    fallback = (REFERENCE_WORLD_UNITS_PER_PERCENT, REFERENCE_WORLD_UNITS_PER_PERCENT)
    if not isinstance(raw, dict):
        return fallback
    resolved: list[float] = []
    for axis, default in (("x", fallback[0]), ("y", fallback[1])):
        value = raw.get(axis)
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
            resolved.append(float(value))
        else:
            resolved.append(default)
    return (resolved[0], resolved[1])


def _distance(first: dict[str, Any], second: dict[str, Any], scale: AxisScale) -> float:
    """Horizontal distance in world units between two radar-percent positions."""
    return math.hypot(
        (float(first["x"]) - float(second["x"])) * scale[0],
        (float(first["y"]) - float(second["y"])) * scale[1],
    )


def _distance_or_none(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
    scale: AxisScale,
) -> float | None:
    if not first or not second or not _has_xy(first) or not _has_xy(second):
        return None
    return _distance(first, second, scale)


def _vertical_distance_or_none(first: dict[str, Any], second: dict[str, Any]) -> float | None:
    # list[Any]: the all() guard below rejects None (and every non-finite value) before the
    # float() calls, but mypy cannot narrow element types through a generator inside all().
    heights: list[Any] = [first.get("z"), second.get("z")]
    if not all(isinstance(z, (int, float)) and math.isfinite(z) for z in heights):
        return None
    return abs(float(heights[0]) - float(heights[1]))


def _distance_to_xy(player: dict[str, Any], point: tuple[float, float], scale: AxisScale) -> float:
    return math.hypot(
        (float(player["x"]) - point[0]) * scale[0],
        (float(player["y"]) - point[1]) * scale[1],
    )


def _max_pair_distance(players: list[dict[str, Any]], scale: AxisScale) -> float | None:
    if len(players) < 2:
        return None
    max_distance = 0.0
    for index, player in enumerate(players):
        for other in players[index + 1 :]:
            max_distance = max(max_distance, _distance(player, other, scale))
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


def _event_type(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    return event_type.strip().lower() or None


def _event_tick(event: dict[str, Any]) -> int | None:
    return _int_or_none(event.get("tick"))


def _event_id(event: dict[str, Any]) -> str | None:
    return _optional_str(event.get("id"))


def _event_label(event: dict[str, Any]) -> str | None:
    label = _optional_str(event.get("label"))
    if label:
        return label
    event_type = _event_type(event)
    if event_type == BOMB_PLANTED_EVENT_TYPE:
        return "Bomb planted"
    if event_type in UTILITY_LABELS:
        return UTILITY_LABELS[event_type]
    return None


def _event_player_id(event: dict[str, Any]) -> str | None:
    player_id = _optional_str(event.get("playerId"))
    if player_id:
        return player_id
    player_ids = event.get("playerIds")
    if isinstance(player_ids, list):
        for value in player_ids:
            parsed = _optional_str(value)
            if parsed:
                return parsed
    return None


def _event_player_name(event: dict[str, Any]) -> str | None:
    return _optional_str(event.get("playerName"))


def _event_side(event: dict[str, Any]) -> str | None:
    side = _optional_str(event.get("side"))
    return side if side in {"T", "CT"} else None


def _event_site(event: dict[str, Any]) -> str | None:
    site = _optional_str(event.get("site"))
    if site:
        return site
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        return _optional_str(metadata.get("site"))
    return None


def _t_side_or_unknown(event: dict[str, Any]) -> bool:
    return _event_side(event) in {None, "T"}


def _related_event_ids(events: Iterable[dict[str, Any]]) -> list[str]:
    return [
        event_id
        for event_id in _unique_values(_event_id(event) for event in events)
        if isinstance(event_id, str)
    ]


def _alive(player: dict[str, Any]) -> bool:
    return bool(player.get("alive", True)) and int(player.get("hp", 100) or 0) > 0


def _has_xy(player: dict[str, Any]) -> bool:
    return all(isinstance(player.get(axis), (int, float)) and math.isfinite(player[axis]) for axis in ("x", "y"))


def _player_id(player: dict[str, Any]) -> str:
    return str(player.get("id") or player.get("name") or "unknown")


def _player_name(player: dict[str, Any]) -> str:
    return str(player.get("name") or player.get("id") or "Unknown player")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _tick_or(value: Any, default: int) -> int:
    parsed = _int_or_none(value)
    return default if parsed is None else parsed


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _frame_players(frame: dict[str, Any]) -> list[dict[str, Any]]:
    return _dict_items(frame.get("players"))


def _round_int(item: dict[str, Any], key: str, default: int) -> int:
    """Read an int off a round record, treating a null value as an absent one.

    dict.get()'s default only covers a *missing* key, so int(item.get(key, 0))
    still raises on a key that is present and None -- which a parser is free to
    emit. Routing every round number and tick through here also keeps the three
    places that derive a round number agreeing on which records are usable.
    """
    value = _int_or_none(item.get(key))
    return default if value is None else value


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


def _round_rule_count(events: list[CoachingEventCandidate], round_number: int, rule_id: str, player_id: str | None = None) -> int:
    return sum(
        1
        for event in events
        if event.get("round_number") == round_number
        and event.get("structured_context_json", {}).get("ruleId") == rule_id
        and (player_id is None or event.get("player_id") == player_id)
    )


def _sort_key(event: CoachingEventCandidate) -> tuple[int, int, str, str]:
    return (
        _SEVERITY_ORDER.get(str(event.get("severity")), 99),
        int(event.get("tick_start", 0)),
        str(event.get("structured_context_json", {}).get("ruleId", "")),
        str(event.get("player_id", "")),
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
