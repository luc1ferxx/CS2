import { matchKills, teamKeyOfPlayer, type MatchKill, type TeamKey } from "@/lib/match-stats";
import { getFrameForTick } from "@/lib/replay-frames";
import type { PlayerSide, ReplayData, ReplayFrame, ReplayPlayerState } from "@/types/replay";

/*
 * Live player state for the roster, read from the replay contract v2 `playerStates` change points
 * (state at tick t = the last entry with tick <= t; a field missing from an entry is unknown, never
 * zero). K/D and deaths come from lib/match-stats.ts's kill records, so the live counters use the
 * same rules as the scoreboard. v1 replays have no playerStates: every state lookup is null.
 * Best effort: malformed entries are skipped, never thrown on.
 */

export interface KillsDeaths {
  kills: number;
  deaths: number;
}

export interface DeathInfo {
  tick: number;
  killerId: string | null;
  killerName: string | null;
  weapon: string | null;
  headshot: boolean;
}

// Sanitised, tick-sorted tracks, keyed by the track array the API delivered.
const trackCache = new WeakMap<object, ReplayPlayerState[]>();
const EMPTY: ReplayPlayerState[] = [];

export function hasPlayerStates(replay: ReplayData): boolean {
  const states = replay?.playerStates;
  if (!states || typeof states !== "object") return false;
  return Object.values(states).some((track) => Array.isArray(track) && track.length > 0);
}

export function playerStateTrack(replay: ReplayData, playerId: string): ReplayPlayerState[] {
  const raw = replay?.playerStates?.[playerId];
  if (!Array.isArray(raw)) return EMPTY;
  const cached = trackCache.get(raw);
  if (cached) return cached;
  const track = raw.filter((entry): entry is ReplayPlayerState =>
    Boolean(entry) && typeof entry === "object" && Number.isFinite(entry.tick));
  if (!track.every((entry, index) => index === 0 || track[index - 1].tick <= entry.tick)) {
    track.sort((left, right) => left.tick - right.tick);
  }
  trackCache.set(raw, track);
  return track;
}

/** The player's state at `tick`: the last change point at or before it; null before the first one. */
export function stateAt(replay: ReplayData, playerId: string, tick: number): ReplayPlayerState | null {
  const track = playerStateTrack(replay, playerId);
  if (track.length === 0 || !Number.isFinite(tick) || tick < track[0].tick) return null;
  let low = 0;
  let high = track.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (track[middle].tick <= tick) low = middle + 1;
    else high = middle;
  }
  return track[low - 1] ?? null;
}

// The last K/D table per kill list: playback asks every frame, and it only changes on a kill.
const killsDeathsCache = new WeakMap<object, { count: number; counts: Map<string, KillsDeaths> }>();

/**
 * Match-cumulative kills and deaths of every player up to and including `tick`. Between two kills
 * the same Map (with the same entries) comes back, so the roster can skip unchanged rows.
 */
export function killsDeathsAt(replay: ReplayData, tick: number): Map<string, KillsDeaths> {
  const kills = matchKills(replay);
  const count = killsAtOrBefore(kills, tick);
  const cached = killsDeathsCache.get(kills);
  if (cached && cached.count === count) return cached.counts;
  const counts = new Map<string, KillsDeaths>();
  const entry = (playerId: string) => {
    let value = counts.get(playerId);
    if (!value) {
      value = { kills: 0, deaths: 0 };
      counts.set(playerId, value);
    }
    return value;
  };
  for (let index = 0; index < count; index += 1) {
    const kill = kills[index];
    entry(kill.victimId).deaths += 1;
    if (kill.enemyKill && kill.attackerId) entry(kill.attackerId).kills += 1;
  }
  killsDeathsCache.set(kills, { count, counts });
  return counts;
}

// Index of the first of the tick-sorted kills at or after `tick` (kills.length when there is none).
function firstKillAtOrAfter(kills: readonly MatchKill[], tick: number): number {
  let low = 0;
  let high = kills.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (kills[middle].tick < tick) low = middle + 1;
    else high = middle;
  }
  return low;
}

// How many of the tick-sorted kills happened at or before `tick`.
function killsAtOrBefore(kills: readonly MatchKill[], tick: number): number {
  if (Number.isNaN(tick)) return 0;
  let low = 0;
  let high = kills.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (kills[middle].tick <= tick) low = middle + 1;
    else high = middle;
  }
  return low;
}

/**
 * How the player died in the round `tick` falls in (the last round started at or before it), if
 * they have died there by `tick`; null otherwise. A kill filed after the round's end still shows
 * until the next round starts.
 */
export function deathInfoAt(replay: ReplayData, playerId: string, tick: number): DeathInfo | null {
  if (!Number.isFinite(tick)) return null;
  const roundStart = currentRoundStart(replay, tick);
  const kills = matchKills(replay);
  let death: MatchKill | null = null;
  // The roster asks for every dead player on every playback frame: read only this round's kills
  // (they are tick-sorted), not the match's from the first one.
  const end = killsAtOrBefore(kills, tick);
  for (let index = roundStart === null ? 0 : firstKillAtOrAfter(kills, roundStart); index < end; index += 1) {
    if (kills[index].victimId === playerId) death = kills[index];
  }
  if (!death) return null;
  return {
    tick: death.tick,
    killerId: death.attackerId,
    killerName: death.attackerName ?? (death.attackerId ? playerName(replay, death.attackerId, tick) : null),
    weapon: death.weapon,
    headshot: death.headshot
  };
}

/**
 * What a team ("A"/"B", the match-stats team keys) or side ("T"/"CT") still carries at `tick`: the
 * summed `equipValue` of its living players in the frame at that tick. A dead player's state keeps
 * the value he died with, so he is left out (an all-dead team carries $0). Null when none of the
 * members has an equipment value at all.
 */
export function teamEquipmentAt(
  replay: ReplayData, team: TeamKey | PlayerSide, tick: number, frameAtTick?: ReplayFrame | null
): number | null {
  // The roster passes the frame it is drawing, so playback does not interpolate it a second time
  // (only membership, side and alive are read here, which interpolation never changes).
  const frame = frameAtTick !== undefined
    ? frameAtTick
    : getFrameForTick(Array.isArray(replay?.frames) ? replay.frames : [], tick, replay?.tickRate > 0 ? replay.tickRate : 64);
  const members = (frame?.players ?? []).filter((player) => player && (
    team === "T" || team === "CT" ? player.side === team : teamKeyOfPlayer(replay, player.id) === team
  ));
  let total: number | null = null;
  for (const player of members) {
    const value = stateAt(replay, player.id, tick)?.equipValue;
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    total = (total ?? 0) + (player.alive === false ? 0 : value);
  }
  return total;
}

// Grenades read as the finder's Chinese names; knives, the bomb and the taser get short names.
const WEAPON_LABELS: Record<string, string> = {
  "smoke grenade": "烟雾弹",
  flashbang: "闪光弹",
  "high explosive grenade": "手雷",
  "he grenade": "手雷",
  molotov: "燃烧弹",
  "incendiary grenade": "燃烧弹",
  "decoy grenade": "诱饵弹",
  "c4 explosive": "C4",
  "zeus x27": "电击枪"
};
const KNIFE = /\b(knife|bayonet|karambit|daggers)\b/i;

/** The roster's text for the active weapon's display name; null for nothing in hand. */
export function activeWeaponLabel(weapon: unknown): string | null {
  if (typeof weapon !== "string" || !weapon.trim()) return null;
  const name = weapon.trim();
  return WEAPON_LABELS[name.toLowerCase()] ?? (KNIFE.test(name) ? "刀" : name);
}

function currentRoundStart(replay: ReplayData, tick: number): number | null {
  let start: number | null = null;
  for (const round of Array.isArray(replay?.rounds) ? replay.rounds : []) {
    if (!round || !Number.isFinite(round.startTick) || round.startTick > tick) continue;
    if (start === null || round.startTick > start) start = round.startTick;
  }
  return start;
}

function playerName(replay: ReplayData, playerId: string, tick: number): string | null {
  const listed = (Array.isArray(replay?.players) ? replay.players : []).find((player) => player?.id === playerId);
  if (listed?.name) return listed.name;
  const frame = getFrameForTick(Array.isArray(replay?.frames) ? replay.frames : [], tick);
  return frame?.players.find((player) => player?.id === playerId)?.name ?? null;
}
