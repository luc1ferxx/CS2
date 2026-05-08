import type { ReplayVideo } from "@/types/replay";

export interface VideoTimeRange {
  start: number;
  end: number;
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
