import type { PlayerSide, ReplayData, ReplayEvent, ReplayFrame, ReplayRound } from "@/types/replay";

/*
 * Side rule, shared with the backend match summary
 * (backend/app/services/demo_service/match_summary.py implements it identically). The cases in
 * fixtures/match-rules/ pin it: lib/match-side-rules.test.mjs and
 * backend/tests/test_match_side_rules.py both run every one. Change the rule in both files and
 * in the fixtures together. Input: the replay contract as GET /demos/{id}/replay serves it.
 * A roundNumber or tick counts only as a finite number of magnitude at most 2^53 - 1: JSON.parse
 * rounds larger integers, so the backend reads those as missing too. Player ids sort by code point.
 * 1. Rounds are `rounds` without repeated roundNumbers (the first one wins). Frames and events
 *    filed under any other round number never decide a side.
 * 2. A player's side in round R is the majority of that player's T/CT entries in R's in-bounds
 *    frames: a frame is in bounds when its tick is a finite number within R's
 *    [startTick, endTick]; when R has no valid bounds, every frame with a tick counts. The
 *    frames a round keeps through the half-time break already show the swapped sides and fall
 *    outside it. When nobody has an in-bounds entry in R, all of R's frames vote instead. A tie
 *    goes to the side of the player's earliest entry (by tick, frames without a tick last,
 *    then array order).
 * 3. A player without a frame vote in R takes the side from R's kill events (metadata
 *    attackerSide / victimSide; by tick, then array order, attacker before victim). No other
 *    event assigns a side. `players[].side` is not the starting side and is never used.
 * 4. The start round is the first round in which anybody has a side. Its T players are team A
 *    (started T, shown left), its CT players team B (started CT). Round by round, team members
 *    vote for A's side: A members with their side, B members with the opposite one. The
 *    majority places the round; a tie leaves it unplaced. After a placed round, players without
 *    a team (a substitute) join the team playing their side there. An unplaced round takes the
 *    side of the nearest earlier placed round, else the nearest later one.
 * 5. Team score = rounds whose `winnerSide` equals that team's side in that round.
 *
 * Match statistics derived from a stored replay. Definitions (kept in one place):
 * - Sides, teams and scores follow the side rule above. Halves: rounds 1-12, 13-24, overtime
 *   25+ (a half's side is null when it changes within it).
 * - A round was played by the players in the frames that decided its sides (all of them in a
 *   round without frames: those a kill gave a side).
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

export interface MatchSideRules {
  sidesByRound: Record<string, Record<string, PlayerSide>>;
  teams: { key: TeamKey; playerIds: string[]; startSide: PlayerSide; score: number }[];
  teamSidesByRound: Record<string, Record<TeamKey, PlayerSide | null>>;
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

const TEAM_KEYS = ["A", "B"] as const;
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
  // Team A's side in every round; empty when nobody has a side anywhere (then no teams).
  teamSideByRound: Map<number, PlayerSide>;
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
  if (index.teamSideByRound.size === 0) return [];
  return TEAM_KEYS.map((key) => {
    const startSide: PlayerSide = key === "A" ? "T" : "CT";
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

/** The side rule's result in the shape of a fixtures/match-rules case's `expected`. */
export function matchSideRules(replay: ReplayData): MatchSideRules {
  const index = matchIndex(replay);
  const sidesByRound: MatchSideRules["sidesByRound"] = {};
  const teamSidesByRound: MatchSideRules["teamSidesByRound"] = {};
  for (const round of index.rounds) {
    const sides = [...(index.sidesByRound.get(round.roundNumber) ?? [])]
      .sort(([left], [right]) => byCodePoint(left, right));
    sidesByRound[String(round.roundNumber)] = Object.fromEntries(sides);
    teamSidesByRound[String(round.roundNumber)] = {
      A: teamSide(index, "A", round.roundNumber),
      B: teamSide(index, "B", round.roundNumber)
    };
  }
  const teams = matchTeams(replay).map((team) => ({
    key: team.key,
    playerIds: [...team.playerIds].sort(byCodePoint),
    startSide: team.startSide,
    score: team.score
  }));
  return { sidesByRound, teams, teamSidesByRound };
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
  // Rule step 1: one round per roundNumber, the first one wins (sort is stable).
  const rounds: ReplayRound[] = [];
  const seenRounds = new Set<number>();
  for (const round of (Array.isArray(replay?.rounds) ? replay.rounds : [])
    .filter((item) => item && isRuleNumber(item.roundNumber))
    .sort((left, right) => left.roundNumber - right.roundNumber)) {
    if (seenRounds.has(round.roundNumber)) continue;
    seenRounds.add(round.roundNumber);
    rounds.push(round);
  }
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

  // Rule step 2, in one pass over the frame samples: frames per round (all of them, for
  // positions) and each player's side votes per round, in bounds and everywhere.
  const bounds = new Map<number, [number, number] | null>();
  for (const round of rounds) {
    const valid = isRuleNumber(round.startTick) && isRuleNumber(round.endTick) && round.endTick >= round.startTick;
    bounds.set(round.roundNumber, valid ? [round.startTick, round.endTick] : null);
  }
  const framesByRound = new Map<number, ReplayFrame[]>();
  const insideVotes = new Map<number, Map<string, SideTally>>();
  const insidePlayed = new Map<number, Set<string>>();
  const allVotes = new Map<number, Map<string, SideTally>>();
  const allPlayed = new Map<number, Set<string>>();
  frames.forEach((frame, frameIndex) => {
    if (!frame || !Number.isFinite(frame.roundNumber)) return;
    mapFor(framesByRound, frame.roundNumber, () => [] as ReplayFrame[]).push(frame);
    const players = Array.isArray(frame.players) ? frame.players : [];
    for (const player of players) if (player) addPlayer(player.id, player.name);
    if (!bounds.has(frame.roundNumber)) return;
    const limits = bounds.get(frame.roundNumber);
    const ticked = isRuleNumber(frame.tick);
    const inside = ticked && (!limits || (frame.tick >= limits[0] && frame.tick <= limits[1]));
    const allRound = mapFor(allVotes, frame.roundNumber, () => new Map<string, SideTally>());
    const allSeen = mapFor(allPlayed, frame.roundNumber, () => new Set<string>());
    const insideRound = inside ? mapFor(insideVotes, frame.roundNumber, () => new Map<string, SideTally>()) : null;
    const insideSeen = inside ? mapFor(insidePlayed, frame.roundNumber, () => new Set<string>()) : null;
    players.forEach((player, position) => {
      if (!player || typeof player.id !== "string" || !player.id) return;
      allSeen.add(player.id);
      insideSeen?.add(player.id);
      if (!isSide(player.side)) return;
      const order: VoteOrder = [ticked ? 0 : 1, ticked ? frame.tick : 0, frameIndex, position];
      vote(allRound, player.id, player.side, order);
      if (insideRound) vote(insideRound, player.id, player.side, order);
    });
  });
  const sidesByRound = new Map<number, Map<string, PlayerSide>>();
  const playedByRound = new Map<number, Set<string>>();
  for (const round of rounds) {
    const roundNumber = round.roundNumber;
    // Nobody voted in bounds: all of the round's frames vote instead.
    const useInside = (insideVotes.get(roundNumber)?.size ?? 0) > 0;
    const tallies = (useInside ? insideVotes : allVotes).get(roundNumber) ?? new Map<string, SideTally>();
    sidesByRound.set(roundNumber, new Map([...tallies].map(([playerId, tally]) => [playerId, tallySide(tally)])));
    if (framesByRound.has(roundNumber)) {
      playedByRound.set(roundNumber, (useInside ? insidePlayed : allPlayed).get(roundNumber) ?? new Set());
    }
  }
  for (const roundFrames of framesByRound.values()) {
    if (!roundFrames.every((frame, position) => position === 0 || roundFrames[position - 1].tick <= frame.tick)) {
      roundFrames.sort((left, right) => left.tick - right.tick);
    }
  }

  // Rule step 3: kill events, by tick then array order, give a side to players without a
  // frame vote. No other event assigns one.
  const killEvents = events
    .map((event, position) => ({ event, position }))
    .filter(({ event }) => event.type === "kill" && isRuleNumber(event.tick) && isRuleNumber(event.roundNumber))
    .sort((left, right) => left.event.tick - right.event.tick || left.position - right.position);
  for (const { event } of killEvents) {
    const sides = sidesByRound.get(event.roundNumber);
    if (!sides || !event.metadata || typeof event.metadata !== "object") continue;
    const metadata = event.metadata;
    for (const [idKey, sideKey] of [["attackerId", "attackerSide"], ["victimId", "victimSide"]] as const) {
      const playerId = metadata[idKey];
      const side = metadata[sideKey];
      if (typeof playerId === "string" && playerId && !sides.has(playerId) && isSide(side)) sides.set(playerId, side);
    }
  }
  for (const event of events) {
    if (event.type !== "kill" || !Number.isFinite(event.roundNumber)) continue;
    const metadata = metadataOf(event);
    addPlayer(metadata.attackerId, metadata.attackerName);
    addPlayer(metadata.victimId, metadata.victimName);
  }
  // Rounds without frames: whoever has a known side there played it.
  for (const round of rounds) {
    if (framesByRound.has(round.roundNumber)) continue;
    const sides = sidesByRound.get(round.roundNumber);
    if (sides && sides.size > 0) playedByRound.set(round.roundNumber, new Set(sides.keys()));
  }

  const { teamOf, teamSideByRound } = assignTeams(rounds.map((round) => round.roundNumber), sidesByRound);
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
    rounds, framesByRound, sidesByRound, playedByRound, names, playerOrder, teamOf, teamSideByRound,
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

// Rule step 4: team membership and team A's side in every round.
function assignTeams(roundNumbers: number[], sidesByRound: Map<number, Map<string, PlayerSide>>) {
  const teamOf = new Map<string, TeamKey>();
  const teamSideByRound = new Map<number, PlayerSide>();
  const start = roundNumbers.find((roundNumber) => (sidesByRound.get(roundNumber)?.size ?? 0) > 0);
  if (start === undefined) return { teamOf, teamSideByRound };
  for (const [playerId, side] of sidesByRound.get(start) ?? []) teamOf.set(playerId, side === "T" ? "A" : "B");

  const placed = new Map<number, PlayerSide>();
  for (const roundNumber of roundNumbers) {
    const sides = sidesByRound.get(roundNumber) ?? new Map<string, PlayerSide>();
    let votes = 0;
    for (const [playerId, side] of sides) {
      const team = teamOf.get(playerId);
      if (!team) continue;
      const sideA = team === "A" ? side : opposite(side);
      votes += sideA === "T" ? 1 : -1;
    }
    if (votes === 0) continue;
    const sideA: PlayerSide = votes > 0 ? "T" : "CT";
    placed.set(roundNumber, sideA);
    // A player first seen now (a substitute) joins the team that plays their side this round.
    for (const [playerId, side] of sides) {
      if (!teamOf.has(playerId)) teamOf.set(playerId, side === sideA ? "A" : "B");
    }
  }
  // An unplaced round keeps the nearest earlier placed round's side, else the nearest later one.
  let previous: PlayerSide | null = null;
  const unplaced: number[] = [];
  for (const roundNumber of roundNumbers) {
    const sideA = placed.get(roundNumber);
    if (!sideA) {
      unplaced.push(roundNumber);
      continue;
    }
    for (const gap of unplaced.splice(0)) teamSideByRound.set(gap, previous ?? sideA);
    teamSideByRound.set(roundNumber, sideA);
    previous = sideA;
  }
  for (const gap of unplaced) if (previous) teamSideByRound.set(gap, previous);
  return { teamOf, teamSideByRound };
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

// [frame without a tick, tick, frame index, entry index]: the order of a frame vote.
type VoteOrder = [number, number, number, number];

interface SideTally {
  T: number;
  CT: number;
  firstSide: PlayerSide;
  firstOrder: VoteOrder;
}

function vote(tallies: Map<string, SideTally>, playerId: string, side: PlayerSide, order: VoteOrder) {
  const tally = tallies.get(playerId);
  if (!tally) {
    tallies.set(playerId, { T: side === "T" ? 1 : 0, CT: side === "CT" ? 1 : 0, firstSide: side, firstOrder: order });
    return;
  }
  tally[side] += 1;
  if (compareOrder(order, tally.firstOrder) < 0) {
    tally.firstSide = side;
    tally.firstOrder = order;
  }
}

function compareOrder(left: VoteOrder, right: VoteOrder): number {
  for (let position = 0; position < left.length; position += 1) {
    if (left[position] !== right[position]) return left[position] < right[position] ? -1 : 1;
  }
  return 0;
}

// Majority; a tie goes to the side of the earliest vote.
function tallySide(tally: SideTally): PlayerSide {
  if (tally.T !== tally.CT) return tally.T > tally.CT ? "T" : "CT";
  return tally.firstSide;
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

// A number the side rule counts: finite, and exact after JSON.parse (see the rule above).
function isRuleNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER;
}

// Code point order, as the backend's sorted() has it; `<` compares UTF-16 code units.
function byCodePoint(left: string, right: string): number {
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    const a = left.codePointAt(i) ?? 0;
    const b = right.codePointAt(j) ?? 0;
    if (a !== b) return a - b;
    i += a > 0xffff ? 2 : 1;
    j += b > 0xffff ? 2 : 1;
  }
  return left.length - i - (right.length - j);
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
