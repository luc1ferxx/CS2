import type { RenderJobStatus } from "@/lib/api";
import { playableClipVideo } from "@/lib/render-clips";
import type { VideoPlaybackState } from "@/lib/replay-time";

export function usesVideoClock(mode: "auto" | "map", playback: VideoPlaybackState): boolean {
  return mode === "auto" && playback === "active";
}

export function savedClipAtTick(
  jobs: RenderJobStatus[], tick: number, playerId: string | null, preferredJobId?: string | null
): RenderJobStatus | null {
  if (!playerId || !Number.isFinite(tick)) return null;
  const covering = jobs.filter((job) => {
    const video = playableClipVideo(job);
    return video?.povSteamId === playerId && tick >= video.tickStart && tick < video.tickEnd;
  });
  return covering.find((job) => job.job_id === preferredJobId) ?? covering[0] ?? null;
}
