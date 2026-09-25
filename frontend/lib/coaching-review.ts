import type { CoachingEvent, CoachingFeedback, CoachingSeverity, CoachingVerdict } from "@/types/coaching";
import type { PlayerSide, ReplayFrame, ReplayPlayer, ReplayRound } from "@/types/replay";
import { bombPlantEvidenceLabel, normalizeBombSite } from "@/lib/bomb-site";

export type SeverityFilter = "all" | "high" | "medium" | "low";
export type RuleFilter =
  | "all"
  | "untraded_death"
  | "isolated_entry"
  | "poor_spacing"
  | "post_plant_spread"
  | "retake_desync"
  | "weak_utility_before_execute"
  | "late_post_plant_utility"
  | "post_plant_spacing_with_bomb_event";

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
  // The one Chinese line that tells this card apart from others of its rule.
  facts?: string;
  // Evidence a player can read: no event ids, ticks or raw enum values.
  playerEvidence?: EvidenceSummaryItem[];
  // The card read as a kill-feed row (see coachingFeed).
  feed?: CoachingFeed;
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

export interface SeverityFilterOption {
  value: Exclude<SeverityFilter, "all">;
  count: number;
}

// Mirrors the analyzer's own ordering (backend rules._sort_key).
const SEVERITY_RANK: Record<CoachingSeverity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4
};

// Short Chinese rule names, keyed by the analyzer's ruleId.
const RULE_LABELS: Record<string, string> = {
  isolated_entry: "首杀交火缺少支援",
  late_post_plant_utility: "下包后的道具时机",
  mock: "模拟建议",
  poor_spacing: "队友站位间距",
  post_plant_spacing_with_bomb_event: "下包后的站位分工",
  post_plant_spread: "下包后站位集中",
  post_plant_spread_issue: "下包后站位集中",
  retake_desync: "回防进场不同步",
  untraded_death: "无人补枪的阵亡",
  weak_utility_before_execute: "下包前的道具配合"
};

// Ids, ticks and parser enums stay in the card's 技术详情, not in player-facing evidence.
const TECHNICAL_EVIDENCE_KEYS = new Set(["relatedEventIds", "evidenceTicks", "bombTick", "bombEventType"]);

const UTILITY_NAMES: Record<string, string> = {
  decoy: "诱饵弹",
  flash: "闪光弹",
  flashbang: "闪光弹",
  he: "手雷",
  hegrenade: "手雷",
  incendiary: "燃烧弹",
  inferno: "燃烧弹",
  molotov: "燃烧弹",
  smoke: "烟雾弹",
  smokegrenade: "烟雾弹"
};

const SPACING_TYPE_NAMES: Record<string, string> = {
  stacked: "过近",
  too_far: "过远"
};

// A card counts as "at this moment" within about two seconds either side.
export const ACTIVE_EVENT_WINDOW_TICKS = 128;

const EVIDENCE_KEYS = [
  "relatedEventIds",
  "distance",
  "verticalDistanceWorldUnits",
  "maxStackedVerticalDistanceWorldUnits",
  "windowSeconds",
  "evidenceTicks",
  "utilityType",
  "utilityLabel",
  "utilityTypes",
  "utilityCount",
  "requiredUtilityCount",
  "bombTick",
  "bombEventType",
  "bombEventLabel",
  "graceWindowSeconds",
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
      events: groupEvents.sort((left, right) => compareFindingPriority(left.event, right.event))
    }));

  return {
    totalCount: events.length,
    filteredCount: filteredEvents.length,
    roundGroups,
    availableRules
  };
}

export function severityRank(severity: string): number {
  return SEVERITY_RANK[severity as CoachingSeverity] ?? Number.MAX_SAFE_INTEGER;
}

// Most severe first, then earliest: the order a player should work through.
export function compareFindingPriority(left: CoachingEvent, right: CoachingEvent): number {
  return severityRank(left.severity) - severityRank(right.severity) || left.tick_start - right.tick_start;
}

// Real rules emit only medium and low, so "worth reviewing first" starts at medium.
export function isPriorityFinding(event: CoachingEvent): boolean {
  return severityRank(event.severity) <= SEVERITY_RANK.medium;
}

export function severityFilterOptions(events: CoachingEvent[]): SeverityFilterOption[] {
  return (["high", "medium", "low"] as const)
    .map((value) => ({ value, count: events.filter((event) => matchesSeverity(event.severity, value)).length }))
    .filter((option) => option.count > 0);
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
    evidence: evidenceSummaryForEvent(event),
    facts: coachingFacts(event),
    playerEvidence: playerEvidenceForEvent(event),
    feed: coachingFeed(event)
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
    ruleId === "retake_desync" ||
    ruleId === "weak_utility_before_execute" ||
    ruleId === "late_post_plant_utility" ||
    ruleId === "post_plant_spacing_with_bomb_event"
  ) {
    return ruleId;
  }
  return "all";
}

export function ruleLabelForRuleId(ruleId: string): string {
  return Object.prototype.hasOwnProperty.call(RULE_LABELS, ruleId) ? RULE_LABELS[ruleId] : "其他建议";
}

export function evidenceSummaryForEvent(event: CoachingEvent): EvidenceSummaryItem[] {
  const context = event.structured_context_json;
  const summary: EvidenceSummaryItem[] = [];

  for (const key of EVIDENCE_KEYS) {
    const value = context[key];
    if (value === undefined || value === null || value === "") {
      continue;
    }
    if (Array.isArray(value) && value.length === 0) {
      continue;
    }
    summary.push({
      label: key,
      value: key === "site" ? normalizeBombSite(value) ?? "未知" :
        key === "bombEventLabel" && (context.bombEventType === "bomb_planted" ||
          (typeof value === "string" && /^Bomb planted/i.test(value))) ?
          bombPlantEvidenceLabel(value, context.site) : formatEvidenceValue(value)
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
    ...involvedPlayersForEvent(event, playerNameById),
    ...evidenceSummaryForEvent(event).flatMap((item) => [item.label, item.value])
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

/**
 * One deterministic Chinese line of the facts that make this card specific
 * (who killed you, how far the nearest teammate was). Hedged with 约/记录到
 * because each rule only flags a candidate. Empty for rules it does not know.
 */
export function coachingFacts(event: CoachingEvent): string {
  const context = event.structured_context_json;
  const seconds = factNumber(context.windowSeconds, 1);
  const count = factNumber(context.nearbyCount, 0);
  switch (ruleIdForEvent(event)) {
    case "untraded_death": {
      const killer = factText(context.attackerName);
      return joinFacts(killer ? `被 ${killer} 击杀` : "", seconds ? `${seconds} 秒内没有队友补枪` : "没有记录到队友补枪");
    }
    case "isolated_entry": {
      // Only rows with the support threshold carry world-unit distances.
      const distance = typeof context.isolatedTeammateDistance === "number" ? factNumber(context.distance, 0) : "";
      return joinFacts("T 方首个阵亡", distance ? `最近的队友约 ${distance} 单位外` : "");
    }
    case "poor_spacing": {
      if (context.spacingType === "too_far") {
        const distance = factNumber(context.maxNearestDistance, 0);
        return distance ? `最近的队友约 ${distance} 单位外` : "离最近的队友较远";
      }
      if (context.spacingType === "stacked") {
        const distance = factNumber(context.minPairDistance, 0);
        return distance ? `两名队友相距约 ${distance} 单位` : "队友站位较近";
      }
      return "";
    }
    case "post_plant_spread":
    case "post_plant_spread_issue":
    case "post_plant_spacing_with_bomb_event": {
      const site = normalizeBombSite(context.site);
      return joinFacts(`${site ? `${site} 点` : ""}下包后${count ? ` ${count} 名 T ` : ""}站位集中`, seconds ? `持续约 ${seconds} 秒` : "");
    }
    case "retake_desync":
      return joinFacts(count ? `${count} 名 CT 先后到达包点` : "CT 先后到达包点", seconds ? `前后相差约 ${seconds} 秒` : "");
    case "weak_utility_before_execute": {
      const used = factNumber(context.utilityCount, 0);
      const required = factNumber(context.requiredUtilityCount, 0);
      if (!used && !required) return "";
      return `下包前${seconds ? ` ${seconds} 秒内` : ""}记录到 ${used || 0}${required ? `/${required}` : ""} 个道具`;
    }
    case "late_post_plant_utility": {
      const utility = utilityName(context.utilityType) || utilityName(context.utilityLabel) || "道具";
      return seconds ? `下包约 ${seconds} 秒后投出第一个${utility}` : "";
    }
    default:
      return "";
  }
}

export function playerEvidenceForEvent(event: CoachingEvent): EvidenceSummaryItem[] {
  const context = event.structured_context_json;
  const summary: EvidenceSummaryItem[] = [];
  for (const key of EVIDENCE_KEYS) {
    if (TECHNICAL_EVIDENCE_KEYS.has(key)) continue;
    const value = context[key];
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0)) continue;
    let text: string;
    if (key === "site") text = normalizeBombSite(value) ?? "未知";
    else if (key === "bombEventLabel") text = bombPlantEvidenceLabel(value, context.site);
    else if (key === "spacingType") text = SPACING_TYPE_NAMES[String(value)] ?? formatEvidenceValue(value);
    else if (key === "utilityType" || key === "utilityLabel") text = utilityName(value) || formatEvidenceValue(value);
    else if (key === "utilityTypes" && Array.isArray(value)) text = value.slice(0, 6).map((item) => utilityName(item) || String(item)).join("、");
    else text = formatEvidenceValue(value);
    summary.push({ label: key, value: text });
    if (summary.length >= 5) break;
  }
  return summary;
}

// Where a card sits in its round, as the same m:ss round clock the player uses.
export function coachingMomentLabel(event: CoachingEvent, rounds: ReplayRound[] = [], tickRate?: number): string {
  const roundLabel = Number.isFinite(event.round_number) ? `第 ${event.round_number} 回合` : "回合未知";
  const clock = coachingRoundClock(event, rounds, tickRate);
  return clock ? `${roundLabel} ${clock}` : roundLabel;
}

// The m:ss round clock alone (the kill-feed time column); null when it cannot be told.
export function coachingRoundClock(event: CoachingEvent, rounds: ReplayRound[] = [], tickRate?: number): string | null {
  const round = rounds.find((candidate) => candidate.roundNumber === event.round_number);
  if (!round || !Number.isFinite(round.startTick) || !Number.isFinite(event.tick_start) ||
    typeof tickRate !== "number" || !Number.isFinite(tickRate) || tickRate <= 0 || event.tick_start < round.startTick) {
    return null;
  }
  const seconds = Math.floor((event.tick_start - round.startTick) / tickRate);
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, "0")}`;
}

/** A card read as a kill-feed row: killer ✕ victim for a death, then what the rule found. */
export interface CoachingFeed {
  // Set when the rule is about the reviewed player's death.
  died: boolean;
  // The recorded killer, when the rule names one; on the other side.
  killer: string | null;
  // What the players shown do not already say.
  finding: string;
}

export function coachingFeed(event: CoachingEvent): CoachingFeed {
  const context = event.structured_context_json;
  const rule = ruleIdForEvent(event);
  if (rule === "untraded_death") {
    const seconds = factNumber(context.windowSeconds, 1);
    return {
      died: true,
      killer: factText(context.attackerName) || null,
      finding: seconds ? `${seconds} 秒内没有队友补枪` : "没有记录到队友补枪"
    };
  }
  return { died: rule === "isolated_entry", killer: null, finding: coachingFacts(event) };
}

/**
 * The reviewed player's side in the event's round as far as the rule itself
 * tells it (sides swap at half, so the roster's side is not enough).
 */
export function coachingEventSide(event: CoachingEvent): PlayerSide | null {
  const side = event.structured_context_json.side;
  if (side === "T" || side === "CT") return side;
  switch (ruleIdForEvent(event)) {
    case "isolated_entry":
    case "post_plant_spread":
    case "post_plant_spread_issue":
    case "post_plant_spacing_with_bomb_event":
    case "weak_utility_before_execute":
    case "late_post_plant_utility":
      return "T";
    case "retake_desync":
      return "CT";
    default:
      return null;
  }
}

// Per round, the side any of the player's cards in that round tells.
export function coachingSidesByRound(events: CoachingEvent[]): Map<number, PlayerSide> {
  const sides = new Map<number, PlayerSide>();
  for (const event of events) {
    const side = coachingEventSide(event);
    if (side && !sides.has(event.round_number)) sides.set(event.round_number, side);
  }
  return sides;
}

// The player's side in each round from the first frames once the round is live.
export function playerSidesByRound(frames: ReplayFrame[], rounds: ReplayRound[], playerId: string | null): Map<number, PlayerSide> {
  const sides = new Map<number, PlayerSide>();
  if (!playerId) return sides;
  for (const round of rounds) {
    const fromTick = Number.isFinite(round.freezeEndTick) ? round.freezeEndTick : round.startTick;
    let low = 0;
    let high = frames.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (frames[middle].tick < fromTick) low = middle + 1;
      else high = middle;
    }
    for (let index = low; index < frames.length && index < low + 8 && frames[index].tick <= round.endTick; index += 1) {
      const player = frames[index].players.find((item) => item.id === playerId);
      if (player?.side === "T" || player?.side === "CT") {
        sides.set(round.roundNumber, player.side);
        break;
      }
    }
  }
  return sides;
}

export function activeCoachingEventIds(events: CoachingEvent[], tick: number): Set<string> {
  return new Set(events
    .filter((event) => tick >= event.tick_start - ACTIVE_EVENT_WINDOW_TICKS && tick <= event.tick_end + ACTIVE_EVENT_WINDOW_TICKS)
    .map((event) => event.id));
}

export function sameIdSet(left: ReadonlySet<string>, right: ReadonlySet<string>): boolean {
  if (left.size !== right.size) return false;
  for (const id of left) if (!right.has(id)) return false;
  return true;
}

function utilityName(value: unknown): string {
  if (typeof value !== "string") return "";
  const key = value.trim().toLowerCase().replace(/[\s_-]/g, "");
  return Object.prototype.hasOwnProperty.call(UTILITY_NAMES, key) ? UTILITY_NAMES[key] : "";
}

function factNumber(value: unknown, digits: number): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "";
  return digits === 0 ? String(Math.round(value)) : value.toFixed(digits).replace(/\.?0+$/, "");
}

function factText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

// Natural phrasing, not a dot-joined meta string.
function joinFacts(...parts: string[]): string {
  return parts.filter(Boolean).join("，");
}

function roundPercent(value: number): number {
  return Math.round(Math.max(0, Math.min(100, value)) * 100) / 100;
}

export interface FeedbackProgress {
  total: number;
  rated: number;
  helpful: number;
  irrelevant: number;
  unsure: number;
}

// A verdict save the card is waiting on, or one that failed and can be re-sent.
export interface FeedbackSaveState {
  status: "saving" | "failed";
  verdict: CoachingVerdict | null;
}

// How far a player is through rating the suggestions in front of them. Counts
// only the events passed in, so callers scope it to the reviewed player.
export function feedbackProgress(events: CoachingEvent[]): FeedbackProgress {
  const progress: FeedbackProgress = { total: events.length, rated: 0, helpful: 0, irrelevant: 0, unsure: 0 };
  for (const event of events) {
    const verdict = event.feedback?.verdict;
    if (verdict === "helpful" || verdict === "irrelevant" || verdict === "unsure") {
      progress.rated += 1;
      progress[verdict] += 1;
    }
  }
  return progress;
}

export function withFeedback(
  events: CoachingEvent[],
  eventId: string,
  feedback: CoachingFeedback | null
): CoachingEvent[] {
  return events.map((event) => (event.id === eventId ? { ...event, feedback } : event));
}
