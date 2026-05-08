import type { CoachingEvent, CoachingSeverity } from "@/types/coaching";
import type { ReplayPlayer } from "@/types/replay";

export type SeverityFilter = "all" | "high" | "medium" | "low";
export type RuleFilter =
  | "all"
  | "untraded_death"
  | "isolated_entry"
  | "poor_spacing"
  | "post_plant_spread"
  | "retake_desync";

export interface CoachingReviewFilters {
  severity: SeverityFilter;
  rule: RuleFilter;
  search: string;
}

export interface EvidenceSummaryItem {
  label: string;
  value: string;
}

export interface ReviewEvent {
  event: CoachingEvent;
  ruleId: string;
  ruleFilterId: RuleFilter;
  ruleLabel: string;
  involvedPlayers: string[];
  evidence: EvidenceSummaryItem[];
}

export interface RoundGroup {
  roundNumber: number;
  events: ReviewEvent[];
}

export interface AvailableRule {
  id: RuleFilter;
  label: string;
  count: number;
}

export interface CoachingReviewModel {
  totalCount: number;
  filteredCount: number;
  roundGroups: RoundGroup[];
  availableRules: AvailableRule[];
}

export interface TimelineMarker {
  event: CoachingEvent;
  leftPercent: number;
}

const RULE_LABELS: Record<string, string> = {
  isolated_entry: "Isolated entry",
  poor_spacing: "Poor spacing",
  post_plant_spread: "Post-plant spread",
  post_plant_spread_issue: "Post-plant spread",
  retake_desync: "Retake desync",
  untraded_death: "Untraded death"
};

const EVIDENCE_KEYS = [
  "distance",
  "windowSeconds",
  "evidenceTicks",
  "nearbyCount",
  "spacingType",
  "side",
  "site",
  "sameAreaDistance",
  "isolatedTeammateDistance",
  "poorSpacingMinDistance",
  "poorSpacingMaxDistance",
  "maxNearestDistance",
  "minPairDistance",
  "clusterDistance",
  "retakeSiteDistance"
];

export function buildCoachingReviewModel(
  events: CoachingEvent[],
  players: ReplayPlayer[],
  filters: CoachingReviewFilters
): CoachingReviewModel {
  const playerNameById = new Map(players.map((player) => [player.id, player.name]));
  const availableRules = availableRulesForEvents(events);
  const filteredEvents = events
    .filter((event) => matchesSeverity(event.severity, filters.severity))
    .filter((event) => matchesRule(event, filters.rule))
    .filter((event) => matchesSearch(event, playerNameById, filters.search))
    .map((event) => reviewEventForEvent(event, playerNameById));

  const groupsByRound = new Map<number, ReviewEvent[]>();
  for (const event of filteredEvents) {
    const roundEvents = groupsByRound.get(event.event.round_number) ?? [];
    roundEvents.push(event);
    groupsByRound.set(event.event.round_number, roundEvents);
  }

  const roundGroups = [...groupsByRound.entries()]
    .sort(([left], [right]) => left - right)
    .map(([roundNumber, groupEvents]) => ({
      roundNumber,
      events: groupEvents.sort((left, right) => left.event.tick_start - right.event.tick_start)
    }));

  return {
    totalCount: events.length,
    filteredCount: filteredEvents.length,
    roundGroups,
    availableRules
  };
}

export function reviewEventForEvent(
  event: CoachingEvent,
  playerNameById: Map<string, string>
): ReviewEvent {
  const ruleId = ruleIdForEvent(event);
  const ruleFilterId = ruleFilterForRuleId(ruleId);
  return {
    event,
    ruleId,
    ruleFilterId,
    ruleLabel: ruleLabelForRuleId(ruleId),
    involvedPlayers: involvedPlayersForEvent(event, playerNameById),
    evidence: evidenceSummaryForEvent(event)
  };
}

export function ruleIdForEvent(event: CoachingEvent): string {
  const context = event.structured_context_json;
  const ruleId = context.ruleId ?? context.rule;
  return typeof ruleId === "string" && ruleId.trim() ? ruleId : "mock";
}

export function ruleFilterForRuleId(ruleId: string): RuleFilter {
  if (ruleId === "post_plant_spread_issue") {
    return "post_plant_spread";
  }
  if (
    ruleId === "untraded_death" ||
    ruleId === "isolated_entry" ||
    ruleId === "poor_spacing" ||
    ruleId === "post_plant_spread" ||
    ruleId === "retake_desync"
  ) {
    return ruleId;
  }
  return "all";
}

export function ruleLabelForRuleId(ruleId: string): string {
  return RULE_LABELS[ruleId] ?? humanizeIdentifier(ruleId);
}

export function evidenceSummaryForEvent(event: CoachingEvent): EvidenceSummaryItem[] {
  const context = event.structured_context_json;
  const summary: EvidenceSummaryItem[] = [];

  for (const key of EVIDENCE_KEYS) {
    const value = context[key];
    if (value === undefined || value === null || value === "") {
      continue;
    }
    summary.push({
      label: key,
      value: formatEvidenceValue(value)
    });
    if (summary.length >= 5) {
      break;
    }
  }

  return summary;
}

export function timelineMarkersForRound(
  events: CoachingEvent[],
  roundNumber: number,
  minTick: number,
  maxTick: number
): TimelineMarker[] {
  const durationTicks = Math.max(1, maxTick - minTick);
  return events
    .filter((event) => event.round_number === roundNumber)
    .map((event) => ({
      event,
      leftPercent: roundPercent(((event.tick_start - minTick) / durationTicks) * 100)
    }))
    .sort((left, right) => left.event.tick_start - right.event.tick_start);
}

function availableRulesForEvents(events: CoachingEvent[]): AvailableRule[] {
  const counts = new Map<RuleFilter, number>();
  for (const event of events) {
    const rule = ruleFilterForRuleId(ruleIdForEvent(event));
    if (rule === "all") {
      continue;
    }
    counts.set(rule, (counts.get(rule) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([id, count]) => ({
      id,
      label: ruleLabelForRuleId(id),
      count
    }));
}

function matchesSeverity(severity: CoachingSeverity, filter: SeverityFilter): boolean {
  if (filter === "all") {
    return true;
  }
  if (filter === "high") {
    return severity === "high" || severity === "critical";
  }
  if (filter === "low") {
    return severity === "low" || severity === "info";
  }
  return severity === filter;
}

function matchesRule(event: CoachingEvent, filter: RuleFilter): boolean {
  return filter === "all" || ruleFilterForRuleId(ruleIdForEvent(event)) === filter;
}

function matchesSearch(
  event: CoachingEvent,
  playerNameById: Map<string, string>,
  rawSearch: string
): boolean {
  const search = rawSearch.trim().toLowerCase();
  if (!search) {
    return true;
  }

  const haystack = [
    event.title,
    event.message,
    event.player_id,
    event.player_name,
    ruleIdForEvent(event),
    ruleLabelForRuleId(ruleIdForEvent(event)),
    ...involvedPlayersForEvent(event, playerNameById)
  ]
    .join(" ")
    .toLowerCase();

  return haystack.includes(search);
}

function involvedPlayersForEvent(
  event: CoachingEvent,
  playerNameById: Map<string, string>
): string[] {
  const contextIds = event.structured_context_json.involvedPlayerIds;
  const ids = Array.isArray(contextIds)
    ? contextIds.filter((value): value is string => typeof value === "string" && value.length > 0)
    : [];
  const names = ids.map((id) => playerNameById.get(id) ?? id);
  if (event.player_name && !names.includes(event.player_name)) {
    names.unshift(event.player_name);
  }
  return [...new Set(names)].slice(0, 6);
}

function formatEvidenceValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.slice(0, 6).join(", ");
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, "");
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  return String(value);
}

function humanizeIdentifier(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function roundPercent(value: number): number {
  return Math.round(Math.max(0, Math.min(100, value)) * 100) / 100;
}
