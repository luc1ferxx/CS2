import type { CoachingEvent } from "@/types/coaching";
import type { PlayerSide, ReplayEvent, ReplayFrame, ReplayRound } from "@/types/replay";
import { normalizeBombSite } from "@/lib/bomb-site";

export type RoundJumpTargetId = "round_start" | "live_start" | "first_kill" | "bomb_plant" | "player_first_kill" | "player_death";

export interface RoundEventReference {
  tick: number | null;
  playerId?: string | null;
  playerName?: string | null;
  label?: string | null;
  site?: string | null;
}

export interface RoundReviewSummary {
  roundNumber: number;
  winnerSide: PlayerSide;
  startTick: number;
  freezeEndTick: number;
  endTick: number;
  durationSeconds: number;
  killCount: number;
  bombEventCount: number;
  utilityEventCount: number;
  coachingEventCount: number;
  firstKill: RoundEventReference;
  bombPlant: RoundEventReference;
  // Relative to the reviewed player; null sides/outcomes when that cannot be told.
  playerSide: PlayerSide | null;
  playerOutcome: "won" | "lost" | null;
  playerDeath: RoundEventReference;
  playerFirstKill: RoundEventReference;
  // True when the teams swapped sides since the round before (half time, overtime halves).
  startsNewHalf: boolean;
  isSelected: boolean;
  isCurrent: boolean;
}

export interface RoundReviewModel {
  rounds: RoundReviewSummary[];
  selectedRound: RoundReviewSummary | null;
  currentRoundNumber: number | null;
}

export interface RoundReviewInput {
  rounds: ReplayRound[];
  parserEvents?: ReplayEvent[];
  coachingEvents?: CoachingEvent[];
  currentTick: number;
  // Wins over currentTick when the caller already knows it, so the model need not change every tick.
  currentRoundNumber?: number | null;
  selectedRoundNumber: number;
  tickRate: number;
  selectedPlayerId?: string | null;
  frames?: ReplayFrame[];
}

export interface RoundJumpTarget {
  id: RoundJumpTargetId;
  label: string;
  tick: number | null;
  available: boolean;
}

const UTILITY_EVENT_TYPES = new Set<ReplayEvent["type"]>(["flash", "he", "molotov", "smoke"]);
const BOMB_EVENT_TYPES = new Set<ReplayEvent["type"]>([
  "bomb_pickup",
  "bomb_dropped",
  "bomb_defused",
  "bomb_exploded",
  "bomb_planted"
]);

export const ROUND_JUMP_LABELS: Record<RoundJumpTargetId, string> = {
  round_start: "回合开始",
  live_start: "冻结时间结束",
  first_kill: "全场首杀",
  bomb_plant: "安装炸弹",
  player_first_kill: "个人首杀",
  player_death: "阵亡"
};

export function buildRoundReviewModel({
  rounds,
  parserEvents = [],
  coachingEvents = [],
  currentTick,
  currentRoundNumber: knownCurrentRound,
  selectedRoundNumber,
  tickRate,
  selectedPlayerId = null,
  frames = []
}: RoundReviewInput): RoundReviewModel {
  const currentRoundNumber = knownCurrentRound !== undefined ? knownCurrentRound : findRoundNumberForTick(rounds, currentTick);
  const eventsByRound = groupBy(parserEvents, (event) => event.roundNumber);
  const coachingByRound = groupBy(coachingEvents, (event) => event.round_number);
  const halfStarts = halfStartRounds(rounds, eventsByRound, frames);
  const summaries = rounds.map((round) =>
    summarizeRound(
      round,
      eventsByRound.get(round.roundNumber) ?? [],
      coachingByRound.get(round.roundNumber)?.length ?? 0,
      selectedRoundNumber,
      currentRoundNumber,
      tickRate,
      selectedPlayerId,
      frames,
      halfStarts.has(round.roundNumber)
    )
  );

  return {
    rounds: summaries,
    selectedRound:
      summaries.find((summary) => summary.roundNumber === selectedRoundNumber) ?? summaries[0] ?? null,
    currentRoundNumber
  };
}

export function findRoundForTick(rounds: ReplayRound[], tick: number): ReplayRound | undefined {
  return rounds.find((round) => tick >= round.startTick && tick <= round.endTick);
}

export function findRoundNumberForTick(rounds: ReplayRound[], tick: number): number | null {
  if (rounds.length === 0) {
    return null;
  }

  const exactRound = findRoundForTick(rounds, tick);
  if (exactRound) {
    return exactRound.roundNumber;
  }

  const sortedRounds = [...rounds].sort((left, right) => left.startTick - right.startTick);
  if (tick < sortedRounds[0].startTick) {
    return sortedRounds[0].roundNumber;
  }

  let nearestPreviousRound = sortedRounds[0];
  for (const round of sortedRounds) {
    if (round.startTick > tick) {
      break;
    }
    nearestPreviousRound = round;
  }
  return nearestPreviousRound.roundNumber;
}

export function jumpTargetsForRound(round: RoundReviewSummary | null | undefined, includePlayer = false): RoundJumpTarget[] {
  if (!round) {
    return [];
  }

  const target = (id: RoundJumpTargetId, tick: number | null): RoundJumpTarget => ({
    id, label: ROUND_JUMP_LABELS[id], tick, available: tick !== null
  });
  return [
    target("round_start", round.startTick),
    target("live_start", round.freezeEndTick),
    target("first_kill", round.firstKill.tick),
    target("bomb_plant", round.bombPlant.tick),
    ...(includePlayer ? [
      target("player_first_kill", round.playerFirstKill.tick),
      target("player_death", round.playerDeath.tick)
    ] : [])
  ];
}

function summarizeRound(
  round: ReplayRound,
  roundEventsAll: ReplayEvent[],
  coachingEventCount: number,
  selectedRoundNumber: number,
  currentRoundNumber: number | null,
  tickRate: number,
  selectedPlayerId: string | null,
  frames: ReplayFrame[],
  startsNewHalf: boolean
): RoundReviewSummary {
  const roundEvents = roundEventsAll.filter((event) => event.tick >= round.startTick && event.tick <= round.endTick);
  const killEvents = roundEvents.filter((event) => event.type === "kill");
  const bombEvents = roundEvents.filter((event) => BOMB_EVENT_TYPES.has(event.type));
  const utilityEvents = roundEvents.filter((event) => UTILITY_EVENT_TYPES.has(event.type));
  const firstKill = earliestEvent(killEvents);
  const bombPlant = earliestEvent(roundEvents.filter((event) => event.type === "bomb_planted"));
  const startTick = finiteTick(round.startTick, 0);
  const freezeEndTick = finiteTick(round.freezeEndTick, startTick);
  const endTick = finiteTick(round.endTick, startTick);
  const playerDeath = selectedPlayerId
    ? earliestEvent(killEvents.filter((event) => metadataString(event, "victimId") === selectedPlayerId))
    : null;
  const playerFirstKill = selectedPlayerId
    ? earliestEvent(killEvents.filter((event) => metadataString(event, "attackerId") === selectedPlayerId))
    : null;
  const playerSide = selectedPlayerId
    ? sideFromFrames(frames, freezeEndTick, endTick, selectedPlayerId) ?? sideFromKills(killEvents, selectedPlayerId)
    : null;

  return {
    roundNumber: round.roundNumber,
    winnerSide: round.winnerSide,
    startTick,
    freezeEndTick,
    endTick,
    durationSeconds: formatSeconds((endTick - startTick) / Math.max(1, tickRate)),
    killCount: killEvents.length,
    bombEventCount: bombEvents.length,
    utilityEventCount: utilityEvents.length,
    coachingEventCount,
    firstKill: eventReference(firstKill),
    bombPlant: eventReference(bombPlant),
    playerSide,
    playerOutcome: playerSide ? (playerSide === round.winnerSide ? "won" : "lost") : null,
    playerDeath: eventReference(playerDeath),
    playerFirstKill: eventReference(playerFirstKill),
    startsNewHalf,
    isSelected: round.roundNumber === selectedRoundNumber,
    isCurrent: round.roundNumber === currentRoundNumber
  };
}

// The first recorded frame once the round is live says which side the player is on (sides swap at half).
function sideFromFrames(frames: ReplayFrame[], fromTick: number, toTick: number, playerId: string): PlayerSide | null {
  const start = firstFrameIndexAt(frames, fromTick);
  for (let index = start; index < frames.length && frames[index].tick <= toTick && index < start + 8; index += 1) {
    const player = frames[index].players.find((item) => item.id === playerId);
    if (player) return player.side;
  }
  return null;
}

function firstFrameIndexAt(frames: ReplayFrame[], tick: number): number {
  let low = 0;
  let high = frames.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (frames[middle].tick < tick) low = middle + 1;
    else high = middle;
  }
  return low;
}

// A round opens a new half when most players seen in it and the round before are on the other side.
// Only what the demo shows counts: without positions or kill sides no half is claimed.
function halfStartRounds(rounds: ReplayRound[], eventsByRound: Map<number, ReplayEvent[]>, frames: ReplayFrame[]): Set<number> {
  const starts = new Set<number>();
  let previous: Map<string, PlayerSide> | null = null;
  for (const round of [...rounds].sort((left, right) => left.roundNumber - right.roundNumber)) {
    const startTick = finiteTick(round.startTick, 0);
    const sides = liveSides(frames, finiteTick(round.freezeEndTick, startTick), finiteTick(round.endTick, startTick))
      ?? killSides(eventsByRound.get(round.roundNumber) ?? []);
    if (sides.size === 0) continue;
    if (previous) {
      let shared = 0;
      let swapped = 0;
      for (const [playerId, side] of sides) {
        const before = previous.get(playerId);
        if (!before) continue;
        shared += 1;
        if (before !== side) swapped += 1;
      }
      if (shared > 0 && swapped * 2 > shared) starts.add(round.roundNumber);
    }
    previous = sides;
  }
  return starts;
}

function liveSides(frames: ReplayFrame[], fromTick: number, toTick: number): Map<string, PlayerSide> | null {
  const start = firstFrameIndexAt(frames, fromTick);
  for (let index = start; index < frames.length && frames[index].tick <= toTick && index < start + 8; index += 1) {
    if (frames[index].players.length > 0) {
      return new Map(frames[index].players.map((player) => [player.id, player.side]));
    }
  }
  return null;
}

function killSides(events: ReplayEvent[]): Map<string, PlayerSide> {
  const sides = new Map<string, PlayerSide>();
  for (const event of events) {
    if (event.type !== "kill") continue;
    for (const [idKey, sideKey] of [["attackerId", "attackerSide"], ["victimId", "victimSide"]]) {
      const playerId = metadataString(event, idKey);
      const side = metadataString(event, sideKey);
      if (playerId && (side === "T" || side === "CT") && !sides.has(playerId)) sides.set(playerId, side);
    }
  }
  return sides;
}

function sideFromKills(kills: ReplayEvent[], playerId: string): PlayerSide | null {
  for (const kill of kills) {
    const side = metadataString(kill, "attackerId") === playerId ? metadataString(kill, "attackerSide")
      : metadataString(kill, "victimId") === playerId ? metadataString(kill, "victimSide") : null;
    if (side === "T" || side === "CT") return side;
  }
  return null;
}

function metadataString(event: ReplayEvent, key: string): string | null {
  const value = event.metadata?.[key];
  return typeof value === "string" && value ? value : null;
}

function groupBy<T>(items: T[], key: (item: T) => number): Map<number, T[]> {
  const groups = new Map<number, T[]>();
  for (const item of items) {
    const group = groups.get(key(item));
    if (group) group.push(item);
    else groups.set(key(item), [item]);
  }
  return groups;
}

function earliestEvent(events: ReplayEvent[]): ReplayEvent | null {
  if (events.length === 0) {
    return null;
  }
  return [...events].sort((left, right) => left.tick - right.tick)[0];
}

function eventReference(event: ReplayEvent | null): RoundEventReference {
  if (!event) {
    return { tick: null };
  }
  const site = normalizeBombSite(event.metadata?.site);
  return {
    tick: event.tick,
    playerId: event.playerId,
    playerName: event.playerName,
    label: event.type === "bomb_planted" ? (site ? `Bomb planted ${site}` : "Bomb planted") : event.label,
    site
  };
}

function finiteTick(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function formatSeconds(value: number): number {
  return Math.round(value * 100) / 100;
}
