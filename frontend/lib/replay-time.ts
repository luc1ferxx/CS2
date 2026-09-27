import type { ReplayRound, ReplayVideo } from "@/types/replay";

// Seconds of lead-in before a suggestion, so the player sees what led to it.
export const FINDING_LEAD_SECONDS = 3;

export interface VideoTimeRange {
  start: number;
  end: number;
}

export type VideoPlaybackState = "active" | "outside-clip" | "different-player" | "unavailable";

export function videoMediaIdentity(video: ReplayVideo): string {
  return JSON.stringify([
    video.url, video.renderJobId, video.povSteamId, video.tickStart,
    video.tickEnd, video.tickRate, video.timeOriginSeconds, video.durationSeconds
  ]);
}

export function videoPlaybackState(
  video: ReplayVideo,
  tick: number,
  selectedPlayerId: string | null,
  mediaUnavailable = false
): VideoPlaybackState {
  const range = videoTimeRange(video);
  if (
    mediaUnavailable || video.status !== "ready" || !video.url ||
    !Number.isFinite(tick) || !Number.isFinite(video.tickStart) ||
    !Number.isFinite(video.tickEnd) || video.tickEnd <= video.tickStart ||
    !Number.isFinite(video.tickRate) || video.tickRate <= 0 ||
    !Number.isFinite(range.start) || !Number.isFinite(range.end) || range.end <= range.start
  ) {
    return "unavailable";
  }
  // Older manual videos have no verified POV; their UI must say so explicitly.
  if (video.povSteamId && video.povSteamId !== selectedPlayerId) {
    return "different-player";
  }
  return tick >= video.tickStart && tick < video.tickEnd ? "active" : "outside-clip";
}

export function advanceReplayTick(
  tick: number,
  deltaTicks: number,
  roundEndTick: number,
  video: ReplayVideo,
  selectedPlayerId: string | null,
  mediaUnavailable = false
): number {
  const nextTick = Math.min(roundEndTick, tick + deltaTicks);
  if (
    tick < video.tickStart && nextTick >= video.tickStart &&
    videoPlaybackState(video, video.tickStart, selectedPlayerId, mediaUnavailable) === "active"
  ) {
    return video.tickStart;
  }
  return nextTick;
}

export function tickToVideoTime(tick: number, video: ReplayVideo): number {
  const boundedTick = Math.min(video.tickEnd, Math.max(video.tickStart, tick));
  const rawTime = videoTimeRange(video).start + (boundedTick - video.tickStart) / normalizedTickRate(video);
  const range = videoTimeRange(video);
  return Math.min(range.end, Math.max(range.start, rawTime));
}

export function videoTimeToTick(seconds: number, video: ReplayVideo): number {
  const range = videoTimeRange(video);
  const boundedSeconds = Math.min(range.end, Math.max(range.start, seconds));
  const rawTick = video.tickStart + (boundedSeconds - range.start) * normalizedTickRate(video);
  return Math.round(Math.min(video.tickEnd, Math.max(video.tickStart, rawTick)));
}

export function videoTimeRange(video: ReplayVideo): VideoTimeRange {
  const start = Math.max(0, video.timeOriginSeconds ?? 0);
  const tickDuration = Math.max(0, (video.tickEnd - video.tickStart) / normalizedTickRate(video));
  const tickRangeEnd = start + tickDuration;
  const mediaEnd = video.durationSeconds > 0 ? video.durationSeconds : tickRangeEnd;
  const end = Math.max(start, Math.min(mediaEnd, tickRangeEnd));
  return { start, end };
}

/** Round clock as "m:ss", the one time format used in player-facing copy. */
export function formatRoundTime(seconds: number): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, "0")}`;
}

export function roundTimeAt(tick: number, round: Pick<ReplayRound, "startTick"> | null | undefined, tickRate: number): string {
  const rate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  return formatRoundTime((tick - (round?.startTick ?? tick)) / rate);
}

/**
 * "When in the live round" for lists of moments (deaths, opening duels): counted from the end
 * of freeze time, so a half-time break or a long timeout before the round does not inflate it.
 */
export function liveRoundTimeAt(
  tick: number,
  round: Pick<ReplayRound, "startTick" | "freezeEndTick" | "endTick"> | null | undefined,
  tickRate: number
): string {
  const rate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  return formatRoundTime((tick - (round ? roundPlaybackStartTick(round) : tick)) / rate);
}

/** First tick worth watching: the end of freeze time when the parser recorded a usable one. */
export function roundPlaybackStartTick(round: Pick<ReplayRound, "startTick" | "freezeEndTick" | "endTick">): number {
  const freezeEnd = round.freezeEndTick;
  return Number.isFinite(freezeEnd) && freezeEnd >= round.startTick && freezeEnd < round.endTick
    ? freezeEnd
    : round.startTick;
}

/** Where "查看这一刻" lands: a short lead-in before the moment, never before the round's playable start. */
export function findingLeadInTick(
  tick: number,
  round: Pick<ReplayRound, "startTick" | "freezeEndTick" | "endTick"> | null | undefined,
  tickRate: number,
  leadSeconds = FINDING_LEAD_SECONDS
): number {
  const rate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  const leadIn = tick - leadSeconds * rate;
  if (!round) return Math.max(0, leadIn);
  const playableStart = roundPlaybackStartTick(round);
  const floor = tick >= playableStart ? playableStart : round.startTick;
  return Math.min(tick, Math.max(floor, leadIn));
}

function normalizedTickRate(video: ReplayVideo): number {
  return Math.max(1, video.tickRate || 0);
}
