import type { AuthAccount } from "@/lib/auth";
import { compareFindingPriority, isPriorityFinding } from "@/lib/coaching-review";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayEvent, ReplayPlayer } from "@/types/replay";

// Local QA reviews the development corpus as this player. Real accounts never
// default to it: they start from their own SteamID64 or display name.
export const DEVELOPMENT_PLAYER_IDENTITY = "xelex";
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

export type IdentitySource = "saved" | "steamId" | "displayName" | "development";

export interface IdentityCandidate {
  identity: string;
  source: IdentitySource;
}

export interface ReviewIdentityMatch extends PlayerMatch {
  identity: string;
  source: IdentitySource | null;
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

// One saved identity per signed-in account, so a shared browser never carries
// one player's choice into another account's review.
export function playerPreferenceKey(account: AuthAccount | null | undefined): string | null {
  if (!account) return null;
  if (account.provider === "development") return PLAYER_PREFERENCE_KEY;
  const subject = steamIdOf(account) ?? account.displayName.trim();
  return subject ? `${PLAYER_PREFERENCE_KEY}:${account.provider}:${subject}` : null;
}

export function accountIdentityCandidates(account: AuthAccount | null | undefined): IdentityCandidate[] {
  if (!account) return [];
  if (account.provider === "development") {
    return [{ identity: DEVELOPMENT_PLAYER_IDENTITY, source: "development" }];
  }
  const steamId = steamIdOf(account);
  if (steamId) return [{ identity: steamId, source: "steamId" }];
  const displayName = account.displayName.trim();
  return validIdentity(displayName) ? [{ identity: displayName, source: "displayName" }] : [];
}

export function reviewIdentityCandidates(
  savedIdentity: string,
  account: AuthAccount | null | undefined
): IdentityCandidate[] {
  const candidates: IdentityCandidate[] = validIdentity(savedIdentity)
    ? [{ identity: savedIdentity.trim(), source: "saved" }]
    : [];
  for (const candidate of accountIdentityCandidates(account)) {
    if (!candidates.some((item) => item.identity.toLowerCase() === candidate.identity.toLowerCase())) {
      candidates.push(candidate);
    }
  }
  return candidates;
}

// The first candidate that names exactly one player wins. A saved identity that
// is not in this match falls through to the account's own, so a choice made on
// someone else's demo never hides the viewer in their own.
export function resolveReviewIdentity(
  players: ReplayPlayer[],
  candidates: IdentityCandidate[]
): ReviewIdentityMatch {
  let fallback: ReviewIdentityMatch | null = null;
  for (const candidate of candidates) {
    const result = { ...matchPreferredPlayer(players, candidate.identity), ...candidate };
    if (result.status === "matched") return result;
    if (!fallback || (result.status === "ambiguous" && fallback.status !== "ambiguous")) {
      fallback = result;
    }
  }
  return fallback ?? { status: "missing", player: null, candidates: [], identity: "", source: null };
}

export function readPreferredPlayer(
  getStorage: () => PreferenceStorage | null,
  key: string | null
): string {
  if (!key) return "";
  try {
    const value = getStorage()?.getItem(key);
    if (!value) return "";
    const saved: unknown = JSON.parse(value);
    if (typeof saved !== "object" || saved === null) return "";
    const preference = saved as Record<string, unknown>;
    return preference.version === 1 && validIdentity(preference.identity)
      ? preference.identity.trim()
      : "";
  } catch {
    return "";
  }
}

export function savePreferredPlayer(
  identity: string,
  getStorage: () => PreferenceStorage | null,
  key: string | null
): boolean {
  if (!key || !validIdentity(identity)) return false;
  try {
    const storage = getStorage();
    if (!storage) return false;
    storage.setItem(key, JSON.stringify({ version: 1, identity: identity.trim() }));
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
    priorityCount: findings.filter(isPriorityFinding).length,
    roundCount: new Set(findings.map((event) => event.round_number)).size,
    topFinding: findings.reduce<CoachingEvent | null>(
      (best, event) => (best === null || compareFindingPriority(event, best) < 0 ? event : best),
      null
    )
  };
}

function isMatchContext(event: ReplayEvent): boolean {
  return event.type.startsWith("bomb_") || event.type === "round_start" || event.type === "round_end";
}

function steamIdOf(account: AuthAccount): string | null {
  const steamId = account.steamId?.trim();
  return steamId && /^\d{17}$/.test(steamId) ? steamId : null;
}

function validIdentity(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.trim().length <= 128;
}
