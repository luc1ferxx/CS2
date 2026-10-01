import type { TacticalMapLevel, TacticalMapPresentation } from "@/lib/map-config";
import { getTacticalMapLevel } from "@/lib/map-config";
import { roundHalfLabel } from "@/lib/match-stats";
import { findingLeadInTick } from "@/lib/replay-time";
import type { ReplayData, ReplayRound, ReplayUtility, ReplayUtilityPoint, UtilityType } from "@/types/replay";

/*
 * Thrown grenades from replay contract v2 (`replay.utility`): playback state for the map layer
 * and the filters behind 道具反查. Points are in radar percent (0..100), like frame players.
 * Everything is best effort: a malformed throw or point is dropped, never thrown on, and a
 * replay without `utility` (contract v1) simply has no throws.
 */

export const UTILITY_TYPES: readonly UtilityType[] = ["smoke", "flash", "molotov", "he", "decoy"];
/** The four kinds the finder offers, in its segment order. */
export const FINDER_UTILITY_TYPES = ["smoke", "flash", "molotov", "he"] as const;
export type FinderUtilityType = (typeof FINDER_UTILITY_TYPES)[number];

export const UTILITY_LABELS: Record<UtilityType, string> = {
  smoke: "烟雾弹", flash: "闪光弹", molotov: "燃烧弹", he: "手雷", decoy: "诱饵弹"
};
export const UTILITY_SHORT_LABELS: Record<UtilityType, string> = {
  smoke: "烟雾", flash: "闪光", molotov: "燃烧", he: "手雷", decoy: "诱饵"
};

// Effect sizes in world units, converted with the map's scale.
export const SMOKE_RADIUS_WORLD_UNITS = 144;
export const FIRE_RADIUS_WORLD_UNITS = 120;
const FLASH_BURST_WORLD_UNITS = 110;
const HE_RING_WORLD_UNITS = 180;
// Used on a map without a world transform (the fallback grid).
const FALLBACK_RADIUS_PERCENT: Record<UtilityType, number> = { smoke: 3, molotov: 2.5, flash: 2.2, he: 3.5, decoy: 1.2 };

// How long the brief effects stay on the map, and how much of the flight the trail shows.
export const FLASH_BURST_SECONDS = 0.3;
export const HE_RING_SECONDS = 0.5;
export const TRAIL_SECONDS = 0.6;
// Effect windows when the parser could not match an expire event.
const SMOKE_FALLBACK_SECONDS = 18;
const FIRE_FALLBACK_SECONDS = 7;
/** "看这颗" lands this long before the throw. */
export const UTILITY_JUMP_LEAD_SECONDS = 2;

const MAX_TRAIL_POINTS = 12;
const CONTRACT_VERSION = /^replay_contract_v(\d+)$/;

export type UtilityPhase = "flight" | "effect";

export interface ActiveUtility {
  utility: ReplayUtility;
  phase: UtilityPhase;
  x: number;
  y: number;
  z?: number;
  /** Flight only: the recent path, oldest first, ending at the current position. */
  trail: { x: number; y: number }[];
  /** Effect only: radius in radar percent. */
  radius: number | null;
  /** Seconds since this phase started. */
  age: number;
  /** Effect only: 0 at detonation, 1 when the effect ends. */
  progress: number;
  /** Tick at which this phase ends on the map (detonation for a flight). */
  endsAt: number;
}

export interface UtilityRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface UtilityFilters {
  types?: readonly UtilityType[] | null;
  /** A team's players (a `MatchTeam` fits); null for everybody. */
  team?: { playerIds: readonly string[] } | null;
  playerId?: string | null;
  /** Round numbers to keep; null for every round. */
  rounds?: readonly number[] | null;
}

export type UtilityRoundScope = "all" | "current" | "first" | "second";

/** Whether the page offers 道具反查: throws to show, a background upgrade on its way, or nothing. */
export type UtilityAvailability = "available" | "pending" | "hidden";

interface UtilityIndex {
  source: unknown;
  rounds: unknown;
  throws: ReplayUtility[];
  byRound: Map<number, ReplayUtility[]>;
  // A throw's effects end when the next round starts, whatever its expire tick says.
  cutTick: Map<string, number>;
  roundOrder: ReplayRound[];
}

const indexCache = new WeakMap<object, UtilityIndex>();

/** Contract version number (1 for a replay that names none). */
export function replayContractVersion(replay: Pick<ReplayData, "contractVersion">): number {
  const match = CONTRACT_VERSION.exec(replay.contractVersion ?? "");
  const version = match ? Number(match[1]) : 1;
  return Number.isFinite(version) && version > 0 ? version : 1;
}

export function utilityAvailability(replay: ReplayData, upgradePending: boolean): UtilityAvailability {
  if (replayUtility(replay).length > 0) return "available";
  return replayContractVersion(replay) < 2 && upgradePending ? "pending" : "hidden";
}

export function replayUtility(replay: Pick<ReplayData, "utility" | "rounds">): ReplayUtility[] {
  return utilityIndex(replay).throws;
}

/** Throws per round number, each list by throw tick. */
export function utilityByRound(replay: Pick<ReplayData, "utility" | "rounds">): Map<number, ReplayUtility[]> {
  return utilityIndex(replay).byRound;
}

/** Where a map's scale comes from: the replay's stored scale first, else the map transform. */
export interface MapScaleSource {
  transform?: TacticalMapPresentation["transform"] | null;
  worldUnitsPerPercent?: { x?: number; y?: number } | null;
}

/** World units per radar percent, or null when neither the replay nor the map knows its scale. */
export function worldUnitsPerPercent(map: MapScaleSource | null | undefined): number | null {
  const stored = map?.worldUnitsPerPercent;
  if (stored && isPositive(stored.x) && isPositive(stored.y)) return (stored.x + stored.y) / 2;
  const transform = map?.transform;
  let value: number | null = null;
  if (transform?.type === "overview") {
    value = (transform.scale * transform.imageSize) / 100;
  } else if (transform?.type === "bounds") {
    value = (Math.abs(transform.maxX - transform.minX) + Math.abs(transform.maxY - transform.minY)) / 200;
  }
  return value !== null && isPositive(value) ? value : null;
}

/** The on-map radius of a type's effect, in radar percent. */
export function utilityRadiusPercent(type: UtilityType, map?: MapScaleSource | null): number {
  const worldUnits = type === "smoke" ? SMOKE_RADIUS_WORLD_UNITS
    : type === "molotov" ? FIRE_RADIUS_WORLD_UNITS
      : type === "flash" ? FLASH_BURST_WORLD_UNITS
        : type === "he" ? HE_RING_WORLD_UNITS : null;
  const scale = worldUnitsPerPercent(map);
  if (worldUnits === null || scale === null) return FALLBACK_RADIUS_PERCENT[type];
  return Math.round((worldUnits / scale) * 100) / 100;
}

/** Where the grenade came to rest: the last point of its path. */
export function landingPoint(utility: ReplayUtility): ReplayUtilityPoint | null {
  return utility.points[utility.points.length - 1] ?? null;
}

/** Where it left the thrower's hand. */
export function throwPoint(utility: ReplayUtility): ReplayUtilityPoint | null {
  return utility.points[0] ?? null;
}

/** The floor a throw landed on, on a two-floor map (null elsewhere or without a height). */
export function utilityLevel(
  map: Pick<TacticalMapPresentation, "secondaryRadarImagePath" | "lowerLevelMaxZ">,
  utility: ReplayUtility
): TacticalMapLevel | null {
  return getTacticalMapLevel(map, landingPoint(utility)?.z ?? throwPoint(utility)?.z);
}

/** Position along the path at a tick, interpolated between the stored points. */
export function utilityPositionAt(utility: ReplayUtility, tick: number): { x: number; y: number; z?: number } | null {
  const points = utility.points;
  if (points.length === 0) return null;
  if (tick <= points[0].tick) return pointOf(points[0]);
  const last = points[points.length - 1];
  if (tick >= last.tick) return pointOf(last);
  const index = lastIndexAtOrBefore(points, tick);
  const from = points[index];
  const to = points[index + 1];
  const span = to.tick - from.tick;
  const share = span > 0 ? (tick - from.tick) / span : 0;
  const position: { x: number; y: number; z?: number } = {
    x: round2(from.x + (to.x - from.x) * share),
    y: round2(from.y + (to.y - from.y) * share)
  };
  if (isFiniteNumber(from.z) && isFiniteNumber(to.z)) position.z = from.z + (to.z - from.z) * share;
  else if (isFiniteNumber(from.z)) position.z = from.z;
  return position;
}

/** Tick at which a throw's on-map effect ends (effect end, burst end, or the next round). */
export function utilityEffectEndTick(utility: ReplayUtility, tickRate: number, cutTick = Number.POSITIVE_INFINITY): number {
  const rate = safeRate(tickRate);
  let end: number;
  if (utility.type === "smoke") {
    end = utility.endTick > utility.detonateTick ? utility.endTick : utility.detonateTick + SMOKE_FALLBACK_SECONDS * rate;
  } else if (utility.type === "molotov") {
    end = utility.endTick > utility.detonateTick ? utility.endTick : utility.detonateTick + FIRE_FALLBACK_SECONDS * rate;
  } else if (utility.type === "flash") {
    end = utility.detonateTick + FLASH_BURST_SECONDS * rate;
  } else if (utility.type === "he") {
    end = utility.detonateTick + HE_RING_SECONDS * rate;
  } else {
    end = utility.endTick;
  }
  return Math.min(end, cutTick);
}

/**
 * What is on the map at a tick: grenades in flight (position and a short trail) and effects
 * still up (centre and radius). Only the round the tick falls in (and a throw the parser filed
 * under the next round) is looked at, so this stays cheap per playback frame.
 */
export function utilityActiveAt(
  replay: Pick<ReplayData, "utility" | "rounds" | "tickRate" | "mapMetadata">,
  tick: number,
  options: { map?: Pick<TacticalMapPresentation, "transform"> | null; roundNumber?: number | null } = {}
): ActiveUtility[] {
  if (!Number.isFinite(tick)) return [];
  const index = utilityIndex(replay);
  if (index.throws.length === 0) return [];
  const rate = safeRate(replay.tickRate);
  const scale: MapScaleSource = {
    transform: options.map?.transform ?? replay.mapMetadata?.transform ?? null,
    worldUnitsPerPercent: replay.mapMetadata?.worldUnitsPerPercent ?? null
  };
  const roundNumbers = candidateRounds(index, tick, options.roundNumber ?? null);
  const active: ActiveUtility[] = [];
  for (const roundNumber of roundNumbers) {
    for (const utility of index.byRound.get(roundNumber) ?? []) {
      if (utility.throwTick > tick) break;
      const cut = index.cutTick.get(utility.id) ?? Number.POSITIVE_INFINITY;
      if (tick >= cut) continue;
      if (tick < utility.detonateTick) {
        const position = utilityPositionAt(utility, tick);
        if (!position) continue;
        active.push({
          utility, phase: "flight", ...position, trail: trailAt(utility, tick, rate, position),
          radius: null, age: (tick - utility.throwTick) / rate, progress: 0,
          endsAt: Math.min(utility.detonateTick, cut)
        });
        continue;
      }
      const end = utilityEffectEndTick(utility, rate, cut);
      if (tick >= end || utility.type === "decoy") continue;
      const landing = landingPoint(utility);
      if (!landing) continue;
      const span = end - utility.detonateTick;
      active.push({
        utility, phase: "effect", ...pointOf(landing), trail: [],
        radius: utilityRadiusPercent(utility.type, scale),
        age: (tick - utility.detonateTick) / rate,
        progress: span > 0 ? Math.min(1, Math.max(0, (tick - utility.detonateTick) / span)) : 1,
        endsAt: end
      });
    }
  }
  return active;
}

export function normalizeRect(a: { x: number; y: number }, b: { x: number; y: number }): UtilityRect {
  const clamp = (value: number) => round2(Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0)));
  return {
    x0: clamp(Math.min(a.x, b.x)), y0: clamp(Math.min(a.y, b.y)),
    x1: clamp(Math.max(a.x, b.x)), y1: clamp(Math.max(a.y, b.y))
  };
}

/** Throws whose landing point is inside the rectangle (edges included). */
export function throwsInRect(throws: readonly ReplayUtility[], rect: UtilityRect | null): ReplayUtility[] {
  if (!rect) return [...throws];
  return throws.filter((utility) => {
    const landing = landingPoint(utility);
    return Boolean(landing && landing.x >= rect.x0 && landing.x <= rect.x1 && landing.y >= rect.y0 && landing.y <= rect.y1);
  });
}

export function filterThrows(throws: readonly ReplayUtility[], filters: UtilityFilters): ReplayUtility[] {
  const types = filters.types && filters.types.length > 0 ? new Set(filters.types) : null;
  const team = filters.team ? new Set(filters.team.playerIds) : null;
  const rounds = filters.rounds ? new Set(filters.rounds) : null;
  return throws.filter((utility) =>
    (!types || types.has(utility.type)) &&
    (!team || (utility.throwerId !== null && team.has(utility.throwerId))) &&
    (!filters.playerId || utility.throwerId === filters.playerId) &&
    (!rounds || rounds.has(utility.roundNumber))
  );
}

/** Round numbers a scope keeps: null for all, the current round, or a regulation half. */
export function roundsForScope(
  rounds: readonly Pick<ReplayRound, "roundNumber">[],
  scope: UtilityRoundScope,
  currentRound: number | null
): number[] | null {
  if (scope === "all") return null;
  if (scope === "current") return currentRound === null ? [] : [currentRound];
  const label = scope === "first" ? "上半场" : "下半场";
  return rounds.map((round) => round.roundNumber).filter((roundNumber) => roundHalfLabel(roundNumber) === label);
}

/** Where "看这颗" lands: two seconds before the throw, never before its round's playable start. */
export function utilityJumpTick(utility: ReplayUtility, rounds: readonly ReplayRound[], tickRate: number): number {
  const round = rounds.find((item) => item.roundNumber === utility.roundNumber)
    ?? rounds.find((item) => utility.throwTick >= item.startTick && utility.throwTick <= item.endTick);
  return Math.round(findingLeadInTick(utility.throwTick, round, tickRate, UTILITY_JUMP_LEAD_SECONDS));
}

function utilityIndex(replay: Pick<ReplayData, "utility" | "rounds">): UtilityIndex {
  const source = Array.isArray(replay.utility) ? replay.utility : EMPTY;
  const cached = indexCache.get(source);
  if (cached && cached.rounds === replay.rounds) return cached;
  const index = buildIndex(source, Array.isArray(replay.rounds) ? replay.rounds : []);
  indexCache.set(source, { ...index, source, rounds: replay.rounds });
  return indexCache.get(source)!;
}

const EMPTY: ReplayUtility[] = [];

function buildIndex(source: readonly unknown[], rounds: readonly ReplayRound[]): Omit<UtilityIndex, "source" | "rounds"> {
  const seen = new Set<string>();
  const throws: ReplayUtility[] = [];
  for (const item of source) {
    const utility = sanitizeUtility(item);
    if (!utility || seen.has(utility.id)) continue;
    seen.add(utility.id);
    throws.push(utility);
  }
  throws.sort((left, right) => left.throwTick - right.throwTick || compareIds(left.id, right.id));
  const byRound = new Map<number, ReplayUtility[]>();
  for (const utility of throws) {
    const list = byRound.get(utility.roundNumber);
    if (list) list.push(utility);
    else byRound.set(utility.roundNumber, [utility]);
  }
  const roundOrder = rounds
    .filter((round) => isFiniteNumber(round?.roundNumber) && isFiniteNumber(round?.startTick))
    .slice()
    .sort((left, right) => left.startTick - right.startTick);
  const cutTick = new Map<string, number>();
  for (const utility of throws) {
    const next = roundOrder.find((round) => round.startTick > utility.throwTick);
    if (next) cutTick.set(utility.id, next.startTick);
  }
  return { throws, byRound, cutTick, roundOrder };
}

function sanitizeUtility(value: unknown): ReplayUtility | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<ReplayUtility>;
  if (typeof item.id !== "string" || !item.id) return null;
  if (!UTILITY_TYPES.includes(item.type as UtilityType)) return null;
  if (!isFiniteNumber(item.throwTick) || !isFiniteNumber(item.roundNumber)) return null;
  const points = (Array.isArray(item.points) ? item.points : [])
    .map(sanitizePoint)
    .filter((point): point is ReplayUtilityPoint => point !== null)
    .sort((left, right) => left.tick - right.tick);
  if (points.length === 0) return null;
  const throwTick = item.throwTick;
  const detonateTick = isFiniteNumber(item.detonateTick) && item.detonateTick >= throwTick
    ? item.detonateTick : points[points.length - 1].tick;
  const endTick = isFiniteNumber(item.endTick) && item.endTick >= detonateTick ? item.endTick : detonateTick;
  const side = item.throwerSide === "T" || item.throwerSide === "CT" ? item.throwerSide : null;
  return {
    id: item.id,
    type: item.type as UtilityType,
    throwerId: typeof item.throwerId === "string" && item.throwerId ? item.throwerId : null,
    throwerName: typeof item.throwerName === "string" && item.throwerName.trim() ? item.throwerName : null,
    throwerSide: side,
    roundNumber: item.roundNumber,
    throwTick,
    detonateTick,
    endTick,
    points
  };
}

function sanitizePoint(value: unknown): ReplayUtilityPoint | null {
  if (!value || typeof value !== "object") return null;
  const point = value as Partial<ReplayUtilityPoint>;
  if (!isFiniteNumber(point.tick) || !isFiniteNumber(point.x) || !isFiniteNumber(point.y)) return null;
  const sanitized: ReplayUtilityPoint = {
    tick: point.tick,
    x: round2(Math.max(0, Math.min(100, point.x))),
    y: round2(Math.max(0, Math.min(100, point.y)))
  };
  if (isFiniteNumber(point.z)) sanitized.z = point.z;
  return sanitized;
}

function candidateRounds(index: UtilityIndex, tick: number, roundNumber: number | null): number[] {
  let current = roundNumber;
  if (current === null) {
    // The last round started at or before the tick (the gap after a round belongs to it).
    for (const round of index.roundOrder) {
      if (round.startTick > tick) break;
      current = round.roundNumber;
    }
  }
  if (current === null) return index.roundOrder.length === 0 ? [...index.byRound.keys()] : [];
  const position = index.roundOrder.findIndex((round) => round.roundNumber === current);
  const next = position >= 0 ? index.roundOrder[position + 1] : undefined;
  return next ? [current, next.roundNumber] : [current];
}

function trailAt(
  utility: ReplayUtility,
  tick: number,
  rate: number,
  position: { x: number; y: number }
): { x: number; y: number }[] {
  const since = tick - TRAIL_SECONDS * rate;
  const trail = utility.points
    .filter((point) => point.tick > since && point.tick < tick)
    .slice(-(MAX_TRAIL_POINTS - 1))
    .map((point) => ({ x: point.x, y: point.y }));
  trail.push({ x: position.x, y: position.y });
  return trail;
}

function lastIndexAtOrBefore(points: readonly ReplayUtilityPoint[], tick: number): number {
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (points[middle].tick <= tick) low = middle;
    else high = middle - 1;
  }
  return low;
}

function pointOf(point: ReplayUtilityPoint): { x: number; y: number; z?: number } {
  return isFiniteNumber(point.z) ? { x: point.x, y: point.y, z: point.z } : { x: point.x, y: point.y };
}

function safeRate(tickRate: number): number {
  return Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
}

function isPositive(value: unknown): value is number {
  return isFiniteNumber(value) && value > 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function compareIds(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
