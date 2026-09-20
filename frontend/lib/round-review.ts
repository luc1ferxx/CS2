import type { CoachingEvent } from "@/types/coaching";
import type { PlayerSide, ReplayEvent, ReplayRound } from "@/types/replay";
import { normalizeBombSite } from "@/lib/bomb-site";

export type RoundJumpTargetId = "round_start" | "live_start" | "first_kill" | "bomb_plant";

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
  selectedRoundNumber: number;
  tickRate: number;
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

export function buildRoundReviewModel({
  rounds,
  parserEvents = [],
  coachingEvents = [],
  currentTick,
  selectedRoundNumber,
  tickRate
}: RoundReviewInput): RoundReviewModel {
  const currentRoundNumber = findRoundNumberForTick(rounds, currentTick);
  const summaries = rounds.map((round) =>
    summarizeRound(
      round,
      parserEvents,
      coachingEvents,
      selectedRoundNumber,
      currentRoundNumber,
      tickRate
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

export function jumpTargetsForRound(round: RoundReviewSummary | null | undefined): RoundJumpTarget[] {
  if (!round) {
    return [];
  }

  return [
    {
      id: "round_start",
      label: "Round start",
      tick: round.startTick,
      available: true
    },
    {
      id: "live_start",
      label: "Live start",
      tick: round.freezeEndTick,
      available: true
    },
    {
      id: "first_kill",
      label: "First kill",
      tick: round.firstKill.tick,
      available: round.firstKill.tick !== null
    },
    {
      id: "bomb_plant",
      label: "Bomb plant",
      tick: round.bombPlant.tick,
      available: round.bombPlant.tick !== null
    }
  ];
}

function summarizeRound(
  round: ReplayRound,
  parserEvents: ReplayEvent[],
  coachingEvents: CoachingEvent[],
  selectedRoundNumber: number,
  currentRoundNumber: number | null,
  tickRate: number
): RoundReviewSummary {
  const roundEvents = parserEvents.filter((event) =>
    event.roundNumber === round.roundNumber && event.tick >= round.startTick && event.tick <= round.endTick
  );
  const killEvents = roundEvents.filter((event) => event.type === "kill");
  const bombEvents = roundEvents.filter((event) => BOMB_EVENT_TYPES.has(event.type));
  const utilityEvents = roundEvents.filter((event) => UTILITY_EVENT_TYPES.has(event.type));
  const firstKill = earliestEvent(killEvents);
  const bombPlant = earliestEvent(roundEvents.filter((event) => event.type === "bomb_planted"));
  const startTick = finiteTick(round.startTick, 0);
  const freezeEndTick = finiteTick(round.freezeEndTick, startTick);
  const endTick = finiteTick(round.endTick, startTick);

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
    coachingEventCount: coachingEvents.filter((event) => event.round_number === round.roundNumber).length,
    firstKill: eventReference(firstKill),
    bombPlant: eventReference(bombPlant),
    isSelected: round.roundNumber === selectedRoundNumber,
    isCurrent: round.roundNumber === currentRoundNumber
  };
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
