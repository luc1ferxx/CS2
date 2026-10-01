import { matchSideRules, teamKeyOfPlayer, type TeamKey } from "@/lib/match-stats";
import { stateAt } from "@/lib/player-state";
import type { PlayerSide, ReplayData, ReplayFrame, ReplayRound } from "@/types/replay";

/*
 * Each team's buy type per round, read from the replay contract v2 `playerStates` change points
 * (through lib/player-state.ts). Definitions (kept in one place):
 * - Teams and each round's sides come from the side rule in lib/match-stats.ts: team A started T,
 *   team B started CT. A side is never inferred from the round number.
 * - Buy tick: round.freezeEndTick when finite and within [startTick, endTick] (a missing bound
 *   does not limit it); otherwise the first frame tick of the round at or after startTick + 20 s
 *   (20 x tickRate), or that tick itself when no frame reaches it, capped at endTick. A round
 *   without startTick and without a usable freezeEndTick has no buy tick and is left out.
 * - A team's players in a round: its members with a frame filed under that round and a known
 *   equipValue at the buy tick; n = their count. equip / money = their summed equipValue / money.
 *   A player without a known state is unknown, not $0, so he is left out of n and of both sums.
 *   n = 0 leaves the kind unknown (null).
 * - Kind, per-player thresholds times n, first match wins:
 *   pistol: round 1, and the first round in which team A's side differs from its round-1 side
 *     (the regulation half-time switch). A demo recorded from a later round compares against
 *     its first round with a side, and its first switch counts only up to round 16 (MR15's
 *     half time; MR12's is 13). Later switches (overtime) are never pistol rounds.
 *   full: equip >= 4000n.
 *   eco: equip < 1000n, or equip < 2000n with money >= 1000n.
 *   force: money < 1000n (spent nearly everything).
 *   half: the rest (equip >= 2000n with money kept).
 * - won: the team's side in the round equals the round's winnerSide.
 * No playerStates (v1 replays), no equipValue anywhere, no teams or no known kind at all: no
 * economy ([]), so the UI hides it instead of showing zeros. Best effort: malformed rounds,
 * frames and states are skipped, never thrown on.
 */

export type EconomyKind = "pistol" | "full" | "force" | "half" | "eco";

export const ECONOMY_LABELS: Record<EconomyKind, { short: string; name: string }> = {
  pistol: { short: "枪", name: "手枪局" },
  full: { short: "全", name: "全起" },
  force: { short: "强", name: "强起" },
  half: { short: "半", name: "半起" },
  eco: { short: "经", name: "ECO" }
};

export interface TeamRoundEconomy {
  teamKey: "A" | "B";
  side: PlayerSide | null;
  equipValue: number;
  money: number;
  players: number;
  kind: EconomyKind | null;
  won: boolean | null;
}

export interface RoundEconomy {
  roundNumber: number;
  freezeEndTick: number;
  /** Team A, then team B. */
  teams: [TeamRoundEconomy, TeamRoundEconomy];
}

export type EconomySummary = Record<"A" | "B", Record<EconomyKind, { rounds: number; wins: number }>>;

const ECONOMY_KINDS: readonly EconomyKind[] = ["pistol", "full", "force", "half", "eco"];
const TEAM_KEYS = ["A", "B"] as const;
const BUY_FALLBACK_SECONDS = 20;
// The latest regulation half-time switch (MR15); a first switch after it is overtime.
const LAST_REGULATION_SWITCH_ROUND = 16;
const FULL_PER_PLAYER = 4000;
const HALF_PER_PLAYER = 2000;
const ECO_PER_PLAYER = 1000;
const MONEY_KEPT_PER_PLAYER = 1000;

interface CacheEntry {
  rounds: unknown;
  players: unknown;
  events: unknown;
  playerStates: unknown;
  tickRate: unknown;
  result: RoundEconomy[];
}

// Keyed by the frames array, as lib/match-stats.ts does: the page rebuilds the replay wrapper on
// every video change but keeps the parsed arrays, so the same result comes back.
const cache = new WeakMap<object, CacheEntry>();

export function roundEconomies(replay: ReplayData): RoundEconomy[] {
  const frames = Array.isArray(replay?.frames) ? replay.frames : [];
  const cached = cache.get(frames);
  if (cached && cached.rounds === replay?.rounds && cached.players === replay?.players &&
    cached.events === replay?.events && cached.playerStates === replay?.playerStates &&
    cached.tickRate === replay?.tickRate) {
    return cached.result;
  }
  const result = buildEconomies(replay, frames);
  cache.set(frames, {
    rounds: replay?.rounds, players: replay?.players, events: replay?.events,
    playerStates: replay?.playerStates, tickRate: replay?.tickRate, result
  });
  return result;
}

export function hasEconomy(replay: ReplayData): boolean {
  return roundEconomies(replay).length > 0;
}

/** Rounds played and won per team and buy type; rounds with an unknown kind are left out. */
export function economySummary(rounds: readonly RoundEconomy[]): EconomySummary {
  const empty = () => Object.fromEntries(ECONOMY_KINDS.map((kind) => [kind, { rounds: 0, wins: 0 }])) as
    Record<EconomyKind, { rounds: number; wins: number }>;
  const summary: EconomySummary = { A: empty(), B: empty() };
  for (const round of Array.isArray(rounds) ? rounds : []) {
    const teams: readonly (TeamRoundEconomy | null | undefined)[] = Array.isArray(round?.teams) ? round.teams : [];
    for (const team of teams) {
      const kinds = team && (team.teamKey === "A" || team.teamKey === "B") ? summary[team.teamKey] : null;
      if (!team || !kinds || !team.kind || !ECONOMY_KINDS.includes(team.kind)) continue;
      const entry = kinds[team.kind];
      entry.rounds += 1;
      if (team.won === true) entry.wins += 1;
    }
  }
  return summary;
}

function buildEconomies(replay: ReplayData, frames: ReplayFrame[]): RoundEconomy[] {
  if (!hasEquipValues(replay)) return [];
  const rules = matchSideRules(replay);
  if (rules.teams.length === 0) return [];
  const sideOf = (key: TeamKey, roundNumber: number): PlayerSide | null =>
    rules.teamSidesByRound[String(roundNumber)]?.[key] ?? null;

  // The side rule's rounds: one per roundNumber, the first one wins, in roundNumber order.
  const rounds: ReplayRound[] = [];
  const seen = new Set<number>();
  for (const round of (Array.isArray(replay.rounds) ? replay.rounds : [])
    .filter((item) => item && isRuleNumber(item.roundNumber))
    .sort((left, right) => left.roundNumber - right.roundNumber)) {
    if (seen.has(round.roundNumber)) continue;
    seen.add(round.roundNumber);
    rounds.push(round);
  }

  const frameTicks = new Map<number, number[]>();
  const present = new Map<number, Set<string>>();
  for (const frame of frames) {
    if (!frame || !Number.isFinite(frame.roundNumber)) continue;
    if (Number.isFinite(frame.tick)) mapFor(frameTicks, frame.roundNumber, () => [] as number[]).push(frame.tick);
    const players = mapFor(present, frame.roundNumber, () => new Set<string>());
    for (const player of Array.isArray(frame.players) ? frame.players : []) {
      if (player && typeof player.id === "string" && player.id) players.add(player.id);
    }
  }
  for (const ticks of frameTicks.values()) ticks.sort((left, right) => left - right);

  const pistolRounds = pistolRoundNumbers(rounds, (roundNumber) => sideOf("A", roundNumber));
  const tickRate = Number.isFinite(replay.tickRate) && replay.tickRate > 0 ? replay.tickRate : 64;
  const result: RoundEconomy[] = [];
  let anyKind = false;
  for (const round of rounds) {
    const tick = buyTick(round, frameTicks.get(round.roundNumber) ?? [], tickRate);
    if (tick === null) continue;
    const roundPlayers = [...(present.get(round.roundNumber) ?? [])];
    const teams = TEAM_KEYS.map((key): TeamRoundEconomy => {
      let equipValue = 0;
      let money = 0;
      let players = 0;
      for (const playerId of roundPlayers) {
        if (teamKeyOfPlayer(replay, playerId) !== key) continue;
        const state = stateAt(replay, playerId, tick);
        if (!state || !isFiniteNumber(state.equipValue)) continue;
        players += 1;
        equipValue += state.equipValue;
        if (isFiniteNumber(state.money)) money += state.money;
      }
      const side = sideOf(key, round.roundNumber);
      const kind = players === 0 ? null : pistolRounds.has(round.roundNumber) ? "pistol" : buyKind(equipValue, money, players);
      if (kind) anyKind = true;
      const won = side && isSide(round.winnerSide) ? side === round.winnerSide : null;
      return { teamKey: key, side, equipValue, money, players, kind, won };
    }) as [TeamRoundEconomy, TeamRoundEconomy];
    result.push({ roundNumber: round.roundNumber, freezeEndTick: tick, teams });
  }
  return anyKind ? result : [];
}

function buyKind(equip: number, money: number, players: number): EconomyKind {
  if (equip >= FULL_PER_PLAYER * players) return "full";
  if (equip < ECO_PER_PLAYER * players) return "eco";
  if (equip < HALF_PER_PLAYER * players && money >= MONEY_KEPT_PER_PLAYER * players) return "eco";
  if (money < MONEY_KEPT_PER_PLAYER * players) return "force";
  return "half";
}

// Round 1 and the first round where team A's side differs from its round-1 side.
function pistolRoundNumbers(rounds: ReplayRound[], sideOfA: (roundNumber: number) => PlayerSide | null): Set<number> {
  const pistols = new Set<number>();
  if (rounds.some((round) => round.roundNumber === 1)) pistols.add(1);
  // A demo recorded from a later round compares against the first round with a side; a first
  // switch after round 16 is then an overtime one (a demo from round 13 of an MR12 match).
  const reference = rounds.find((round) => sideOfA(round.roundNumber) !== null);
  if (!reference) return pistols;
  const startSide = sideOfA(reference.roundNumber);
  const switched = rounds.find((round) => round.roundNumber > reference.roundNumber &&
    sideOfA(round.roundNumber) !== null && sideOfA(round.roundNumber) !== startSide);
  if (switched && switched.roundNumber <= LAST_REGULATION_SWITCH_ROUND) pistols.add(switched.roundNumber);
  return pistols;
}

// The tick the buy is read at (see the definitions above); null when the round has no usable tick.
function buyTick(round: ReplayRound, frameTicks: number[], tickRate: number): number | null {
  const start = isFiniteNumber(round.startTick) ? round.startTick : null;
  const end = isFiniteNumber(round.endTick) && (start === null || round.endTick >= start) ? round.endTick : null;
  const freezeEnd = isFiniteNumber(round.freezeEndTick) ? round.freezeEndTick : null;
  if (freezeEnd !== null && (start === null || freezeEnd >= start) && (end === null || freezeEnd <= end)) return freezeEnd;
  if (start === null) return null;
  const target = start + BUY_FALLBACK_SECONDS * tickRate;
  const tick = frameTicks.find((frameTick) => frameTick >= target) ?? target;
  return end === null ? tick : Math.min(tick, end);
}

function hasEquipValues(replay: ReplayData): boolean {
  const states = replay?.playerStates;
  if (!states || typeof states !== "object") return false;
  return Object.values(states).some((track) => Array.isArray(track) &&
    track.some((entry) => entry && typeof entry === "object" && isFiniteNumber(entry.equipValue)));
}

function mapFor<K, V>(map: Map<K, V>, key: K, create: () => V): V {
  let value = map.get(key);
  if (value === undefined) {
    value = create();
    map.set(key, value);
  }
  return value;
}

function isSide(value: unknown): value is PlayerSide {
  return value === "T" || value === "CT";
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

// The side rule's round numbers: finite and exact after JSON.parse (see lib/match-stats.ts).
function isRuleNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER;
}
