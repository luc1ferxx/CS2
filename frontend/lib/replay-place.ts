import type { ReplayPlayer, ReplayRound } from "@/types/replay";

// The review's place lives in the query string (?r=round&t=tick&p=player) so a refresh,
// the back button or signing in again after the session lapsed lands where the player was.
export interface ReviewPlace {
  roundNumber: number;
  tick: number;
  playerId: string | null;
}

export interface ReviewPlaceHint {
  roundNumber: number | null;
  tick: number | null;
  playerId: string | null;
}

/** Reads ?r=&t=&p= and keeps only values that fit this replay; anything else is ignored. */
export function parseReviewPlace(
  search: string,
  replay: { rounds: ReplayRound[]; players: ReplayPlayer[] }
): ReviewPlaceHint {
  const params = new URLSearchParams(search);
  const roundParam = integerParam(params.get("r"));
  const tickParam = numberParam(params.get("t"));
  const playerParam = params.get("p");
  const round = roundParam === null ? undefined : replay.rounds.find((item) => item.roundNumber === roundParam);
  const tickRound = tickParam === null
    ? undefined
    : round && tickParam >= round.startTick && tickParam <= round.endTick
      ? round
      : round ? undefined : replay.rounds.find((item) => tickParam >= item.startTick && tickParam <= item.endTick);
  return {
    roundNumber: tickRound?.roundNumber ?? round?.roundNumber ?? null,
    tick: tickRound && tickParam !== null ? tickParam : null,
    playerId: playerParam && replay.players.some((player) => player.id === playerParam) ? playerParam : null
  };
}

/** The query string for a place, keeping any unrelated parameters. */
export function reviewPlaceSearch(currentSearch: string, place: ReviewPlace): string {
  const params = new URLSearchParams(currentSearch);
  params.set("r", String(place.roundNumber));
  params.set("t", String(Math.round(place.tick)));
  if (place.playerId) params.set("p", place.playerId);
  else params.delete("p");
  const search = params.toString();
  return search ? `?${search}` : "";
}

function integerParam(value: string | null): number | null {
  if (value === null || !/^\d{1,4}$/.test(value)) return null;
  return Number(value);
}

function numberParam(value: string | null): number | null {
  if (value === null || !/^\d{1,9}(\.\d+)?$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
