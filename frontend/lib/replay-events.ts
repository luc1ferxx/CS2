import type { ReplayEvent, ReplayEventType } from "@/types/replay";

export type ParserEventTone = "combat" | "damage" | "objective" | "utility";

export interface ParserEventPresentation {
  label: string;
  tone: ParserEventTone;
  shortLabel: string;
}

export interface ParserEventTimelineMarker {
  event: ReplayEvent;
  leftPercent: number;
  seekTick: number;
  presentation: ParserEventPresentation;
}

const EVENT_PRESENTATION: Record<ReplayEventType, ParserEventPresentation> = {
  kill: { label: "Kill", tone: "combat", shortLabel: "K" },
  death: { label: "Death", tone: "combat", shortLabel: "D" },
  damage: { label: "Damage", tone: "damage", shortLabel: "D" },
  bomb_planted: { label: "Plant", tone: "objective", shortLabel: "P" },
  bomb_defused: { label: "Defuse", tone: "objective", shortLabel: "D" },
  bomb_exploded: { label: "Explode", tone: "objective", shortLabel: "X" },
  smoke: { label: "Smoke", tone: "utility", shortLabel: "S" },
  flash: { label: "Flash", tone: "utility", shortLabel: "F" },
  molotov: { label: "Molotov", tone: "damage", shortLabel: "M" },
  he: { label: "HE", tone: "damage", shortLabel: "H" },
  round_start: { label: "Round start", tone: "objective", shortLabel: "R" },
  round_end: { label: "Round end", tone: "objective", shortLabel: "R" }
};

const FALLBACK_PRESENTATION: ParserEventPresentation = {
  label: "Event",
  tone: "objective",
  shortLabel: "E"
};

export function parserEventPresentationForType(
  type: ReplayEventType | string
): ParserEventPresentation {
  return EVENT_PRESENTATION[type as ReplayEventType] ?? FALLBACK_PRESENTATION;
}

export function timelineParserEventMarkersForRound(
  events: ReplayEvent[],
  roundNumber: number,
  minTick: number,
  maxTick: number
): ParserEventTimelineMarker[] {
  const durationTicks = Math.max(1, maxTick - minTick);
  return events
    .filter((event) => event.roundNumber === roundNumber)
    .map((event) => ({
      event,
      leftPercent: roundPercent(((event.tick - minTick) / durationTicks) * 100),
      seekTick: event.tick,
      presentation: parserEventPresentationForType(event.type)
    }))
    .sort((left, right) => left.event.tick - right.event.tick);
}

export function recentMapParserEvents(
  events: ReplayEvent[],
  roundNumber: number,
  currentTick: number,
  tickRate: number
): ReplayEvent[] {
  const nearbyWindowTicks = Math.max(1, tickRate * 2);
  return events
    .filter((event) => event.roundNumber === roundNumber)
    .filter((event) => Math.abs(event.tick - currentTick) <= nearbyWindowTicks)
    .filter((event) => typeof event.x === "number" && typeof event.y === "number")
    .sort((left, right) => Math.abs(left.tick - currentTick) - Math.abs(right.tick - currentTick))
    .slice(0, 4);
}

function roundPercent(value: number) {
  return Math.round(value * 100) / 100;
}
