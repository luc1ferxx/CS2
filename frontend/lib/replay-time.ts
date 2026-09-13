import type { ReplayVideo } from "@/types/replay";

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

function normalizedTickRate(video: ReplayVideo): number {
  return Math.max(1, video.tickRate || 0);
}
