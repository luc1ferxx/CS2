import type { CoachingEvent } from "@/types/coaching";
import type { ReplayEvent, ReplayPlayer } from "@/types/replay";

export const DEFAULT_PLAYER_IDENTITY = "xelex";
export const PLAYER_PREFERENCE_KEY = "cs2-coach.player-preference.v1";

interface PreferenceStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
}

export interface PlayerMatch {
  status: "matched" | "missing" | "ambiguous";
  player: ReplayPlayer | null;
  candidates: ReplayPlayer[];
}

export function matchPreferredPlayer(players: ReplayPlayer[], identity: string): PlayerMatch {
  const normalized = identity.trim().toLowerCase();
  const candidates = normalized
    ? players.filter((player) =>
        player.name.trim().toLowerCase() === normalized || player.id.toLowerCase() === normalized
      )
    : [];
  return {
    status: candidates.length === 1 ? "matched" : candidates.length > 1 ? "ambiguous" : "missing",
    player: candidates.length === 1 ? candidates[0] : null,
    candidates
  };
}

export function readPreferredPlayer(getStorage: () => PreferenceStorage | null): string {
  try {
    const value = getStorage()?.getItem(PLAYER_PREFERENCE_KEY);
    if (!value) return DEFAULT_PLAYER_IDENTITY;
    const saved: unknown = JSON.parse(value);
    if (typeof saved !== "object" || saved === null) return DEFAULT_PLAYER_IDENTITY;
    const preference = saved as Record<string, unknown>;
    return preference.version === 1 && validIdentity(preference.identity)
      ? preference.identity.trim()
      : DEFAULT_PLAYER_IDENTITY;
  } catch {
    return DEFAULT_PLAYER_IDENTITY;
  }
}

export function savePreferredPlayer(
  identity: string,
  getStorage: () => PreferenceStorage | null
): boolean {
  if (!validIdentity(identity)) return false;
  try {
    const storage = getStorage();
    if (!storage) return false;
    storage.setItem(PLAYER_PREFERENCE_KEY, JSON.stringify({ version: 1, identity: identity.trim() }));
    return true;
  } catch {
    return false;
  }
}

export function coachingForPlayer(events: CoachingEvent[], playerId: string | null): CoachingEvent[] {
  return playerId ? events.filter((event) => event.player_id === playerId) : [];
}

export function playerInReplayEvent(event: ReplayEvent, playerId: string): boolean {
  return event.playerId === playerId || event.playerIds.includes(playerId);
}

export function parserEventsForPlayer(events: ReplayEvent[], playerId: string | null): ReplayEvent[] {
  return events.filter((event) =>
    isMatchContext(event) || (playerId !== null && playerInReplayEvent(event, playerId))
  );
}

export function personalReviewSummary(events: CoachingEvent[], playerId: string | null) {
  const findings = coachingForPlayer(events, playerId);
  return {
    findingCount: findings.length,
    highPriorityCount: findings.filter((event) => event.severity === "high" || event.severity === "critical").length,
    roundCount: new Set(findings.map((event) => event.round_number)).size,
    firstFindingTick: findings.length ? Math.min(...findings.map((event) => event.tick_start)) : null
  };
}

function isMatchContext(event: ReplayEvent): boolean {
  return event.type.startsWith("bomb_") || event.type === "round_start" || event.type === "round_end";
}

function validIdentity(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.trim().length <= 128;
}
