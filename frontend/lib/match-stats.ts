import type { PlayerSide, ReplayData, ReplayEvent, ReplayFrame, ReplayRound } from "@/types/replay";

/*
 * Match statistics derived from a stored replay. Definitions (kept in one place):
 * - A player's side in a round comes from that round's frames (teams swap at half time and
 *   in overtime); kill metadata sides, then event sides, are the fallback. `players[].side`
 *   is not the starting side and is never used for it.
 * - Team A = the players on T in the first round with frames (shown left), team B = the CT
 *   players. A player first seen later joins the team playing their side in that round.
 * - Team score = rounds whose `winnerSide` equals that team's side in that round. Halves:
 *   rounds 1-12, 13-24, overtime 25+ (a half's side is null when it changes within it).
 * - Kills and damage belong to the round their tick falls in. The parser files what happens
 *   between one round's endTick and the next startTick (exit kills, a bomb going off after the
 *   round) under the NEXT round; those count for the round that just ended, with its sides, as
 *   on HLTV. A self-inflicted kill or hit outside a round's live window (at its startTick or
 *   after its endTick: the team switch at half time, a pause) is an artefact and skipped.
 * - K/D/A come from kill events. A team kill or suicide is a death for the victim but no
 *   kill for anyone; an assist by the attacker or by the victim's own team is ignored.
 * - ADR = health removed from enemies / rounds played. Parser damage is not capped at the
 *   victim's remaining health, so each hit counts at most the health the victim had left in
 *   that round.
 * - KAST = share of rounds played with a Kill, Assist, Survival or Trade (the enemy who killed
 *   the player is killed, by anyone, within 5 s = 5 x tickRate ticks, in the same round).
 * - Opening duel = the first enemy kill while a round is live (team kills, suicides and
 *   post-round kills are skipped).
 * - Utility = smoke/flash/molotov/he events whose `playerId` is the thrower.
 * Every function is best effort: malformed or missing metadata is skipped, never thrown on.
 */

export type TeamKey = "A" | "B";
export type RoundEndReason = "bomb_exploded" | "bomb_defused" | "elimination" | "time" | "other";
export type HalfLabel = "上半场" | "下半场" | "加时";

export interface MatchHalf {
  label: HalfLabel;
  side: PlayerSide | null;
  score: number;
}

export interface MatchTeam {
  key: TeamKey;
  name: string | null;
  playerIds: string[];
  startSide: PlayerSide;
  score: number;
  halves: MatchHalf[];
}

export interface PlayerMatchStats {
  playerId: string;
  name: string;
  teamKey: TeamKey;
  kills: number;
  deaths: number;
  assists: number;
  diff: number;
  adr: number | null;
  hsPercent: number | null;
  kastPercent: number | null;
  openingKills: number;
  openingDeaths: number;
  roundsPlayed: number;
}

export interface PlayerRoundKills {
  roundNumber: number;
  kills: number;
  died: boolean;
  side: PlayerSide | null;
}

export interface PlayerDeath {
  roundNumber: number;
  tick: number;
  x: number;
  y: number;
  z?: number;
  killerId: string | null;
  killerName: string | null;
  weapon: string | null;
  headshot: boolean;
}

export interface OpeningDuel {
  roundNumber: number;
  tick: number;
  won: boolean;
  opponentId: string | null;
  opponentName: string | null;
}

export interface UtilityCounts {
  smoke: number;
  flash: number;
  molotov: number;
  he: number;
}

export const ROUND_END_REASON_LABELS: Record<RoundEndReason, string> = {
  bomb_exploded: "炸弹爆炸",
  bomb_defused: "拆除炸弹",
  elimination: "全歼",
  time: "时间耗尽",
  other: "其他"
};

const REGULATION_HALF_ROUNDS = 12;
const TRADE_WINDOW_SECONDS = 5;
const FULL_HEALTH = 100;
const UTILITY_TYPES = ["smoke", "flash", "molotov", "he"] as const;

interface KillRecord {
  tick: number;
  // The round the tick falls in; `frameRoundNumber` is the parser's, where its frames are.
  roundNumber: number;
  frameRoundNumber: number;
  // Between the round's startTick and endTick (not a post-round kill).
  live: boolean;
  attackerId: string | null;
  victimId: string;
  assisterId: string | null;
  attackerName: string | null;
  weapon: string | null;
  headshot: boolean;
  // True when the attacker shot an enemy (not a team kill, suicide or world death).
  enemyKill: boolean;
  x: number | null;
  y: number | null;
  z: number | null;
}

interface MatchIndex {
  rounds: ReplayRound[];
  framesByRound: Map<number, ReplayFrame[]>;
  sidesByRound: Map<number, Map<string, PlayerSide>>;
  playedByRound: Map<number, Set<string>>;
  names: Map<string, string>;
  playerOrder: string[];
  teamOf: Map<string, TeamKey>;
  teamSideByRound: Map<number, PlayerSide>;
  startSideA: PlayerSide | null;
  kills: KillRecord[];
  killsByRound: Map<number, KillRecord[]>;
  openingByRound: Map<number, KillRecord>;
  damageByPlayer: Map<string, number>;
  events: ReplayEvent[];
}

interface CacheEntry {
  events: unknown;
  rounds: unknown;
  players: unknown;
  tickRate: unknown;
  index: MatchIndex;
}

// Keyed by the frames array: the page rebuilds the replay wrapper on every video change
// but keeps the parsed arrays, so the index survives those renders.
const indexCache = new WeakMap<object, CacheEntry>();

export function roundEndReason(round: ReplayRound): RoundEndReason {
  const reason = typeof round?.winnerReason === "string" ? round.winnerReason.toLowerCase() : "";
  if (reason === "bomb_exploded" || reason === "target_bombed") return "bomb_exploded";
  if (reason === "bomb_defused") return "bomb_defused";
  if (reason === "t_killed" || reason === "ct_killed" || reason === "terrorists_killed" ||
    reason === "cts_killed" || reason === "elimination") return "elimination";
  if (reason === "time_ran_out" || reason === "target_saved" || reason === "time") return "time";
  return "other";
}

export function roundHalfLabel(roundNumber: number): HalfLabel {
  if (roundNumber <= REGULATION_HALF_ROUNDS) return "上半场";
  if (roundNumber <= REGULATION_HALF_ROUNDS * 2) return "下半场";
  return "加时";
}

export function matchTeams(
  replay: ReplayData,
  teamNames?: readonly { key: string; name?: string | null }[] | null
): MatchTeam[] {
  const index = matchIndex(replay);
  if (!index.startSideA) return [];
  const startSideA = index.startSideA;
  return (["A", "B"] as const).map((key) => {
    const startSide = key === "A" ? startSideA : opposite(startSideA);
    const sideIn = (roundNumber: number) => teamSide(index, key, roundNumber);
    const halves: MatchHalf[] = [];
    let score = 0;
    for (const label of ["上半场", "下半场", "加时"] as const) {
      const rounds = index.rounds.filter((round) => roundHalfLabel(round.roundNumber) === label);
      // A half nobody played (overtime, or a short match) is left out.
      if (rounds.length === 0) continue;
      const sides = new Set(rounds.map((round) => sideIn(round.roundNumber)).filter(isSide));
      const halfScore = rounds.filter((round) => sideIn(round.roundNumber) === round.winnerSide).length;
      score += halfScore;
      halves.push({ label, side: sides.size === 1 ? [...sides][0] : null, score: halfScore });
    }
    const name = teamNames?.find((team) => team?.key === key)?.name;
    return {
      key,
      name: typeof name === "string" && name.trim() ? name.trim() : null,
      playerIds: index.playerOrder.filter((playerId) => index.teamOf.get(playerId) === key),
      startSide,
      score,
      halves
    };
  });
}

export function sideOfPlayerInRound(replay: ReplayData, playerId: string, roundNumber: number): PlayerSide | null {
  return matchIndex(replay).sidesByRound.get(roundNumber)?.get(playerId) ?? null;
}

export function teamKeyOfPlayer(replay: ReplayData, playerId: string): TeamKey | null {
  return matchIndex(replay).teamOf.get(playerId) ?? null;
}

export function playerMatchStats(replay: ReplayData): PlayerMatchStats[] {
  const index = matchIndex(replay);
  const tradeWindow = TRADE_WINDOW_SECONDS * (replay.tickRate > 0 ? replay.tickRate : 64);
  const stats = new Map<string, PlayerMatchStats>();
  const kastRounds = new Map<string, Set<number>>();
  for (const playerId of index.playerOrder) {
    const teamKey = index.teamOf.get(playerId);
    if (!teamKey) continue;
    stats.set(playerId, {
      playerId, name: index.names.get(playerId) ?? playerId, teamKey,
      kills: 0, deaths: 0, assists: 0, diff: 0, adr: null, hsPercent: null, kastPercent: null,
      openingKills: 0, openingDeaths: 0, roundsPlayed: 0
    });
    kastRounds.set(playerId, new Set());
  }

  const headshots = new Map<string, number>();
  for (const kill of index.kills) {
    const victim = stats.get(kill.victimId);
    if (victim) victim.deaths += 1;
    if (kill.enemyKill && kill.attackerId) {
      const attacker = stats.get(kill.attackerId);
      if (attacker) {
        attacker.kills += 1;
        kastRounds.get(kill.attackerId)?.add(kill.roundNumber);
        if (kill.headshot) headshots.set(kill.attackerId, (headshots.get(kill.attackerId) ?? 0) + 1);
      }
    }
    if (kill.assisterId && validAssist(index, kill)) {
      const assister = stats.get(kill.assisterId);
      if (assister) {
        assister.assists += 1;
        kastRounds.get(kill.assisterId)?.add(kill.roundNumber);
      }
    }
  }

  for (const round of index.rounds) {
    const played = index.playedByRound.get(round.roundNumber);
    const roundKills = index.killsByRound.get(round.roundNumber) ?? [];
    for (const playerId of played ?? []) {
      const player = stats.get(playerId);
      if (!player) continue;
      player.roundsPlayed += 1;
      const death = roundKills.find((kill) => kill.victimId === playerId);
      // Only a death to an enemy can be traded.
      const traded = death?.enemyKill && death.attackerId
        ? roundKills.some((kill) => kill.victimId === death.attackerId &&
          kill.tick >= death.tick && kill.tick - death.tick <= tradeWindow)
        : false;
      if (!death || traded) kastRounds.get(playerId)?.add(round.roundNumber);
    }
    const opening = index.openingByRound.get(round.roundNumber);
    if (opening?.attackerId) {
      const attacker = stats.get(opening.attackerId);
      if (attacker) attacker.openingKills += 1;
      const victim = stats.get(opening.victimId);
      if (victim) victim.openingDeaths += 1;
    }
  }

  for (const player of stats.values()) {
    player.diff = player.kills - player.deaths;
    player.hsPercent = player.kills > 0 ? ((headshots.get(player.playerId) ?? 0) / player.kills) * 100 : null;
    if (player.roundsPlayed > 0) {
      player.adr = (index.damageByPlayer.get(player.playerId) ?? 0) / player.roundsPlayed;
      const kast = [...(kastRounds.get(player.playerId) ?? [])]
        .filter((roundNumber) => index.playedByRound.get(roundNumber)?.has(player.playerId)).length;
      player.kastPercent = (kast / player.roundsPlayed) * 100;
    }
  }

  return [...stats.values()].sort((left, right) =>
    right.diff - left.diff || right.kills - left.kills || left.name.localeCompare(right.name)
  );
}

export function playerRoundKills(replay: ReplayData, playerId: string): PlayerRoundKills[] {
  const index = matchIndex(replay);
  return index.rounds.map((round) => {
    const kills = index.killsByRound.get(round.roundNumber) ?? [];
    return {
      roundNumber: round.roundNumber,
      kills: kills.filter((kill) => kill.enemyKill && kill.attackerId === playerId).length,
      died: kills.some((kill) => kill.victimId === playerId),
      side: index.sidesByRound.get(round.roundNumber)?.get(playerId) ?? null
    };
  });
}

export function playerDeaths(replay: ReplayData, playerId: string): PlayerDeath[] {
  const index = matchIndex(replay);
  const deaths: PlayerDeath[] = [];
  for (const kill of index.kills) {
    if (kill.victimId !== playerId) continue;
    // A kill event's x/y is the victim's position; the victim's last frame is the fallback.
    let position: { x: number; y: number; z: number | null } | null =
      kill.x !== null && kill.y !== null ? { x: kill.x, y: kill.y, z: kill.z } : null;
    // A post-round kill's tick sits in the parser's next round, else after the end of its own.
    if (!position) position = framePositionAt(index.framesByRound.get(kill.frameRoundNumber) ?? [], playerId, kill.tick);
    if (!position && kill.frameRoundNumber !== kill.roundNumber) {
      position = framePositionAt(index.framesByRound.get(kill.roundNumber) ?? [], playerId, kill.tick);
    }
    if (!position) continue;
    const death: PlayerDeath = {
      roundNumber: kill.roundNumber,
      tick: kill.tick,
      x: position.x,
      y: position.y,
      killerId: kill.attackerId,
      killerName: kill.attackerName ?? (kill.attackerId ? index.names.get(kill.attackerId) ?? null : null),
      weapon: kill.weapon,
      headshot: kill.headshot
    };
    if (position.z !== null) death.z = position.z;
    deaths.push(death);
  }
  return deaths;
}

export function openingDuels(replay: ReplayData, playerId: string): OpeningDuel[] {
  const index = matchIndex(replay);
  const duels: OpeningDuel[] = [];
  for (const round of index.rounds) {
    const opening = index.openingByRound.get(round.roundNumber);
    if (!opening) continue;
    const won = opening.attackerId === playerId;
    if (!won && opening.victimId !== playerId) continue;
    const opponentId = won ? opening.victimId : opening.attackerId;
    duels.push({
      roundNumber: round.roundNumber,
      tick: opening.tick,
      won,
      opponentId,
      opponentName: won
        ? index.names.get(opening.victimId) ?? null
        : opening.attackerName ?? (opponentId ? index.names.get(opponentId) ?? null : null)
    });
  }
  return duels;
}

export function utilityCounts(replay: ReplayData, playerId: string): UtilityCounts {
  const counts: UtilityCounts = { smoke: 0, flash: 0, molotov: 0, he: 0 };
  for (const event of matchIndex(replay).events) {
    if (!event || !(UTILITY_TYPES as readonly string[]).includes(event.type)) continue;
    if (event.playerId === playerId) counts[event.type as keyof UtilityCounts] += 1;
  }
  return counts;
}

function matchIndex(replay: ReplayData): MatchIndex {
  const frames = Array.isArray(replay?.frames) ? replay.frames : [];
  const cached = indexCache.get(frames);
  if (cached && cached.events === replay?.events && cached.rounds === replay?.rounds &&
    cached.players === replay?.players && cached.tickRate === replay?.tickRate) {
    return cached.index;
  }
  const index = buildIndex(replay, frames);
  indexCache.set(frames, {
    events: replay?.events, rounds: replay?.rounds, players: replay?.players, tickRate: replay?.tickRate, index
  });
  return index;
}

function buildIndex(replay: ReplayData, frames: ReplayFrame[]): MatchIndex {
  const rounds = (Array.isArray(replay?.rounds) ? replay.rounds : [])
    .filter((round) => round && Number.isFinite(round.roundNumber))
    .sort((left, right) => left.roundNumber - right.roundNumber);
  const events = (Array.isArray(replay?.events) ? replay.events : []).filter((event) => event && typeof event === "object");
  const names = new Map<string, string>();
  const playerOrder: string[] = [];
  const addPlayer = (playerId: unknown, name?: unknown) => {
    if (typeof playerId !== "string" || !playerId) return;
    if (!names.has(playerId)) {
      playerOrder.push(playerId);
      names.set(playerId, typeof name === "string" && name ? name : playerId);
    } else if (names.get(playerId) === playerId && typeof name === "string" && name) {
      names.set(playerId, name);
    }
  };
  for (const player of Array.isArray(replay?.players) ? replay.players : []) addPlayer(player?.id, player?.name);

  // One pass over the frame samples: frames per round, and each player's side per round. Only
  // frames inside the round's [startTick, endTick] decide sides and who played: a round keeps
  // frames through the half-time break that already show the swapped sides. A round whose
  // frames all fall outside its bounds uses all of them (the backend summary does the same).
  const bounds = new Map<number, [number, number]>();
  for (const round of rounds) {
    if (Number.isFinite(round.startTick) && Number.isFinite(round.endTick) && round.endTick >= round.startTick) {
      if (!bounds.has(round.roundNumber)) bounds.set(round.roundNumber, [round.startTick, round.endTick]);
    }
  }
  const framesByRound = new Map<number, ReplayFrame[]>();
  const insideSides = new Map<number, Map<string, PlayerSide>>();
  const insidePlayed = new Map<number, Set<string>>();
  const outsideSides = new Map<number, Map<string, PlayerSide>>();
  const outsidePlayed = new Map<number, Set<string>>();
  for (const frame of frames) {
    if (!frame || !Number.isFinite(frame.roundNumber)) continue;
    let roundFrames = framesByRound.get(frame.roundNumber);
    if (!roundFrames) {
      roundFrames = [];
      framesByRound.set(frame.roundNumber, roundFrames);
    }
    roundFrames.push(frame);
    const limits = bounds.get(frame.roundNumber);
    const inside = !limits || (frame.tick >= limits[0] && frame.tick <= limits[1]);
    const sides = mapFor(inside ? insideSides : outsideSides, frame.roundNumber, () => new Map<string, PlayerSide>());
    const played = mapFor(inside ? insidePlayed : outsidePlayed, frame.roundNumber, () => new Set<string>());
    for (const player of Array.isArray(frame.players) ? frame.players : []) {
      if (!player || typeof player.id !== "string") continue;
      if (!sides.has(player.id) && isSide(player.side)) sides.set(player.id, player.side);
      if (!played.has(player.id)) {
        played.add(player.id);
        addPlayer(player.id, player.name);
      }
    }
  }
  const sidesByRound = new Map<number, Map<string, PlayerSide>>();
  const playedByRound = new Map<number, Set<string>>();
  for (const roundNumber of framesByRound.keys()) {
    const useInside = (insideSides.get(roundNumber)?.size ?? 0) > 0;
    sidesByRound.set(roundNumber, (useInside ? insideSides : outsideSides).get(roundNumber) ?? new Map());
    playedByRound.set(roundNumber, (useInside ? insidePlayed : outsidePlayed).get(roundNumber) ?? new Set());
  }
  for (const roundFrames of framesByRound.values()) {
    if (!roundFrames.every((frame, position) => position === 0 || roundFrames[position - 1].tick <= frame.tick)) {
      roundFrames.sort((left, right) => left.tick - right.tick);
    }
  }

  // Kill metadata sides, then event sides, fill rounds the frames do not cover.
  for (const event of events) {
    if (!Number.isFinite(event.roundNumber)) continue;
    const metadata = metadataOf(event);
    const sides = mapFor(sidesByRound, event.roundNumber, () => new Map<string, PlayerSide>());
    const fill = (playerId: unknown, side: unknown) => {
      if (typeof playerId === "string" && playerId && !sides.has(playerId) && isSide(side)) sides.set(playerId, side);
    };
    if (event.type === "kill") {
      fill(metadata.attackerId, metadata.attackerSide);
      fill(metadata.victimId, metadata.victimSide);
      addPlayer(metadata.attackerId, metadata.attackerName);
      addPlayer(metadata.victimId, metadata.victimName);
    }
    fill(event.playerId, event.side);
  }
  // Rounds without frames: whoever has a known side there played it.
  for (const round of rounds) {
    if (framesByRound.has(round.roundNumber)) continue;
    const sides = sidesByRound.get(round.roundNumber);
    if (sides && sides.size > 0) playedByRound.set(round.roundNumber, new Set(sides.keys()));
  }

  const { teamOf, teamSideByRound, startSideA } = assignTeams(rounds, framesByRound, sidesByRound);
  const sideIn = (playerId: string | null, roundNumber: number): PlayerSide | null => {
    if (!playerId) return null;
    const known = sidesByRound.get(roundNumber)?.get(playerId);
    if (known) return known;
    const team = teamOf.get(playerId);
    const sideA = teamSideByRound.get(roundNumber);
    if (!team || !sideA) return null;
    return team === "A" ? sideA : opposite(sideA);
  };

  const fileEvent = eventFiling(rounds);
  const kills: KillRecord[] = [];
  for (const event of events) {
    if (event.type !== "kill" || !Number.isFinite(event.tick) || !Number.isFinite(event.roundNumber)) continue;
    const metadata = metadataOf(event);
    const victimId = stringOrNull(metadata.victimId);
    if (!victimId) continue;
    const attackerId = stringOrNull(metadata.attackerId);
    const filed = fileEvent(event, attackerId, victimId, metadata.weapon);
    if (!filed) continue;
    const roundNumber = filed.roundNumber;
    const attackerSide = sideIn(attackerId, roundNumber) ?? (isSide(metadata.attackerSide) ? metadata.attackerSide : null);
    const victimSide = sideIn(victimId, roundNumber) ?? (isSide(metadata.victimSide) ? metadata.victimSide : null);
    const teamKill = attackerSide !== null && victimSide !== null && attackerSide === victimSide;
    kills.push({
      tick: event.tick,
      roundNumber,
      frameRoundNumber: event.roundNumber,
      live: filed.live,
      attackerId,
      victimId,
      assisterId: stringOrNull(metadata.assisterId),
      attackerName: stringOrNull(metadata.attackerName),
      weapon: stringOrNull(metadata.weapon),
      headshot: metadata.headshot === true,
      enemyKill: attackerId !== null && attackerId !== victimId && !teamKill,
      x: finiteOrNull(event.x),
      y: finiteOrNull(event.y),
      z: finiteOrNull(event.z)
    });
  }
  kills.sort((left, right) => left.tick - right.tick);
  const killsByRound = new Map<number, KillRecord[]>();
  const openingByRound = new Map<number, KillRecord>();
  for (const kill of kills) {
    mapFor(killsByRound, kill.roundNumber, () => [] as KillRecord[]).push(kill);
    if (kill.enemyKill && kill.live && !openingByRound.has(kill.roundNumber)) openingByRound.set(kill.roundNumber, kill);
  }

  const damageByPlayer = new Map<string, number>();
  const healthLeft = new Map<string, number>();
  const damageEvents = events
    .filter((event) => event.type === "damage" && Number.isFinite(event.tick) && Number.isFinite(event.roundNumber))
    .sort((left, right) => left.tick - right.tick);
  for (const event of damageEvents) {
    const metadata = metadataOf(event);
    const attackerId = stringOrNull(metadata.attackerId);
    const victimId = stringOrNull(metadata.victimId);
    const dealt = finiteOrNull(metadata.damageHealth);
    if (!victimId || dealt === null || dealt <= 0) continue;
    // Health is tracked per round by tick: a post-round hit is capped at what the victim had
    // left and never touches the next round's fresh 100.
    const filed = fileEvent(event, attackerId, victimId, metadata.weapon);
    if (!filed) continue;
    const roundNumber = filed.roundNumber;
    const healthKey = `${roundNumber}:${victimId}`;
    const before = healthLeft.get(healthKey) ?? FULL_HEALTH;
    const removed = Math.min(dealt, before);
    const after = finiteOrNull(metadata.health);
    healthLeft.set(healthKey, after !== null ? Math.max(0, after) : Math.max(0, before - removed));
    if (!attackerId || attackerId === victimId) continue;
    const attackerSide = sideIn(attackerId, roundNumber);
    const victimSide = sideIn(victimId, roundNumber);
    if (attackerSide !== null && attackerSide === victimSide) continue;
    damageByPlayer.set(attackerId, (damageByPlayer.get(attackerId) ?? 0) + removed);
  }

  return {
    rounds, framesByRound, sidesByRound, playedByRound, names, playerOrder, teamOf, teamSideByRound, startSideA,
    kills, killsByRound, openingByRound, damageByPlayer, events
  };
}

// Files a kill or damage event under the round its tick falls in (see the definitions at the
// top); null for a team-switch or pause artefact.
function eventFiling(rounds: ReplayRound[]) {
  const byNumber = new Map<number, ReplayRound>();
  for (const round of rounds) if (!byNumber.has(round.roundNumber)) byNumber.set(round.roundNumber, round);
  const byStart = rounds.filter((round) => Number.isFinite(round.startTick))
    .sort((left, right) => left.startTick - right.startTick);
  const lastStartedAt = (tick: number): ReplayRound | null => {
    let low = 0;
    let high = byStart.length - 1;
    let found: ReplayRound | null = null;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (byStart[middle].startTick <= tick) {
        found = byStart[middle];
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return found;
  };
  return (event: ReplayEvent, attackerId: string | null, victimId: string, weapon: unknown) => {
    const parserRound = byNumber.get(event.roundNumber);
    // Only an event ticked before its parser round starts moves back to the round that just ended.
    const round = parserRound && (!Number.isFinite(parserRound.startTick) || event.tick >= parserRound.startTick)
      ? parserRound
      : lastStartedAt(event.tick) ?? parserRound ?? null;
    if (!round) return { roundNumber: event.roundNumber, live: true };
    const live = (!Number.isFinite(round.startTick) || event.tick > round.startTick) &&
      (!Number.isFinite(round.endTick) || event.tick <= round.endTick);
    const selfInflicted = attackerId === victimId || (!attackerId && weapon === "world");
    if (!live && selfInflicted) return null;
    return { roundNumber: round.roundNumber, live };
  };
}

function assignTeams(
  rounds: ReplayRound[],
  framesByRound: Map<number, ReplayFrame[]>,
  sidesByRound: Map<number, Map<string, PlayerSide>>
) {
  const teamOf = new Map<string, TeamKey>();
  const teamSideByRound = new Map<number, PlayerSide>();
  const roundNumbers = [...new Set([...rounds.map((round) => round.roundNumber), ...sidesByRound.keys()])]
    .sort((left, right) => left - right);
  // Frames decide the teams; kill sides only when there are no frames at all.
  const firstRound = roundNumbers.find((roundNumber) => framesByRound.has(roundNumber) && (sidesByRound.get(roundNumber)?.size ?? 0) > 0)
    ?? roundNumbers.find((roundNumber) => (sidesByRound.get(roundNumber)?.size ?? 0) > 0);
  if (firstRound === undefined) return { teamOf, teamSideByRound, startSideA: null };

  for (const [playerId, side] of sidesByRound.get(firstRound) ?? []) teamOf.set(playerId, side === "T" ? "A" : "B");
  const startSideA: PlayerSide = [...teamOf.values()].includes("A") ? "T" : "CT";
  if (startSideA === "CT") {
    // Nobody was on T in the first round: the only team there is B (started CT).
    for (const playerId of teamOf.keys()) teamOf.set(playerId, "B");
  }

  for (const roundNumber of roundNumbers) {
    const sides = sidesByRound.get(roundNumber);
    let votes = 0;
    for (const [playerId, side] of sides ?? []) {
      const team = teamOf.get(playerId);
      if (!team) continue;
      const sideA = team === "A" ? side : opposite(side);
      votes += sideA === "T" ? 1 : -1;
    }
    if (votes !== 0) teamSideByRound.set(roundNumber, votes > 0 ? "T" : "CT");
    const sideA = teamSideByRound.get(roundNumber);
    if (!sideA) continue;
    // A player first seen now joins the team that plays their side this round.
    for (const [playerId, side] of sides ?? []) {
      if (!teamOf.has(playerId)) teamOf.set(playerId, side === sideA ? "A" : "B");
    }
  }
  // A round nobody on either team was seen in keeps the nearest earlier (else later) side.
  let previous: PlayerSide | null = null;
  const missing: number[] = [];
  for (const roundNumber of roundNumbers) {
    const known = teamSideByRound.get(roundNumber);
    if (known) {
      for (const gap of missing.splice(0)) teamSideByRound.set(gap, previous ?? known);
      previous = known;
    } else {
      missing.push(roundNumber);
    }
  }
  for (const gap of missing) if (previous) teamSideByRound.set(gap, previous);
  return { teamOf, teamSideByRound, startSideA };
}

function teamSide(index: MatchIndex, key: TeamKey, roundNumber: number): PlayerSide | null {
  const sideA = index.teamSideByRound.get(roundNumber);
  if (!sideA) return null;
  return key === "A" ? sideA : opposite(sideA);
}

function validAssist(index: MatchIndex, kill: KillRecord): boolean {
  if (!kill.assisterId || kill.assisterId === kill.attackerId || kill.assisterId === kill.victimId) return false;
  const assisterTeam = index.teamOf.get(kill.assisterId);
  const victimTeam = index.teamOf.get(kill.victimId);
  return !(assisterTeam && victimTeam && assisterTeam === victimTeam);
}

function framePositionAt(frames: ReplayFrame[], playerId: string, tick: number) {
  let low = 0;
  let high = frames.length - 1;
  let last = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (frames[middle].tick <= tick) {
      last = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  for (let position = last; position >= 0; position -= 1) {
    const player = frames[position].players?.find((item) => item?.id === playerId);
    const x = finiteOrNull(player?.x);
    const y = finiteOrNull(player?.y);
    if (x !== null && y !== null) return { x, y, z: finiteOrNull(player?.z) };
  }
  return null;
}

function mapFor<K, V>(map: Map<K, V>, key: K, create: () => V): V {
  let value = map.get(key);
  if (value === undefined) {
    value = create();
    map.set(key, value);
  }
  return value;
}

function metadataOf(event: ReplayEvent): Record<string, unknown> {
  return event.metadata && typeof event.metadata === "object" ? event.metadata : {};
}

function isSide(value: unknown): value is PlayerSide {
  return value === "T" || value === "CT";
}

function opposite(side: PlayerSide): PlayerSide {
  return side === "T" ? "CT" : "T";
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
