import type { ReplayVideo } from "@/types/replay";

export function tickToVideoTime(tick: number, video: ReplayVideo): number {
  const boundedTick = Math.min(video.tickEnd, Math.max(video.tickStart, tick));
  return (boundedTick - video.tickStart) / video.tickRate;
}

export function videoTimeToTick(seconds: number, video: ReplayVideo): number {
  const boundedSeconds = Math.min(video.durationSeconds, Math.max(0, seconds));
  return Math.round(video.tickStart + boundedSeconds * video.tickRate);
}
