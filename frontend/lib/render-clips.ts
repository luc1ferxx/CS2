import type { RenderClipRequest, RenderJobStatus } from "@/lib/api";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayData, ReplayVideo } from "@/types/replay";

export interface SelectedClip {
  demoId: string;
  video: ReplayVideo;
}

export function retainSelectedClip(
  current: SelectedClip | null,
  jobs: RenderJobStatus[],
  defaultVideo: ReplayVideo,
  demoId: string
): SelectedClip | null {
  if (current?.demoId === demoId) {
    if (isStableClipVideo(current.video, demoId)) return current;
    const canonicalCurrent = clipForDefaultVideo(current.video, jobs);
    if (canonicalCurrent) return { demoId, video: canonicalCurrent };
  }
  const canonicalDefault = clipForDefaultVideo(defaultVideo, jobs);
  if (canonicalDefault) return { demoId, video: canonicalDefault };
  return null;
}

export function reviewVideo(
  defaultVideo: ReplayVideo,
  jobs: RenderJobStatus[],
  selected: SelectedClip | null,
  demoId: string
): ReplayVideo {
  const retained = retainSelectedClip(selected, jobs, defaultVideo, demoId);
  if (retained) return retained.video;
  // The mutable demo route may already serve a newer job. Wait for its bound job video.
  return defaultVideo.source === "rendered" && defaultVideo.url ? { ...defaultVideo, url: null } : defaultVideo;
}

function isStableClipVideo(video: ReplayVideo, demoId: string): boolean {
  return Boolean(video.status === "ready" && video.povSteamId && video.renderJobId &&
    video.url === `/demos/${demoId}/render/jobs/${video.renderJobId}/media/video`);
}

function clipForDefaultVideo(video: ReplayVideo, jobs: RenderJobStatus[]): ReplayVideo | null {
  if (video.source !== "rendered" || video.status !== "ready" || !video.renderJobId) return null;
  const job = jobs.find((candidate) => candidate.job_id === video.renderJobId);
  const canonical = playableClipVideo(job);
  return canonical && canonical.povSteamId === video.povSteamId && canonical.tickStart === video.tickStart &&
    canonical.tickEnd === video.tickEnd && canonical.tickRate === video.tickRate ? canonical : null;
}

export function clipPlayerId(job: RenderJobStatus): string | null {
  return job.pov_steam_id || job.metadata.povSteamId || job.player_id || job.metadata.playerId || null;
}

export function playableClipVideo(job: RenderJobStatus | null | undefined): ReplayVideo | null {
  const video = job?.video;
  if (!job || job.job_type !== "render_clip" || job.status !== "completed" ||
      !video || video.status !== "ready" || video.source !== "rendered" || !video.url ||
      !video.povSteamId || video.povSteamId !== clipPlayerId(job) || video.renderJobId !== job.job_id ||
      !isStableClipVideo(video, job.demo_id) ||
      !Number.isFinite(video.tickRate) || video.tickRate <= 0 ||
      !Number.isFinite(video.tickStart) || !Number.isFinite(video.tickEnd) || video.tickEnd <= video.tickStart) {
    return null;
  }
  return video;
}

export function clipIsActive(job: RenderJobStatus | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "rendering" || job?.status === "processing";
}

export type ClipRequestAction = "play" | "wait" | "retry" | "create";

export function clipRequestAction(existing: RenderJobStatus | null | undefined): ClipRequestAction {
  if (playableClipVideo(existing)) return "play";
  if (clipIsActive(existing)) return "wait";
  // A failed row is requeued rather than left behind next to a second job for
  // the same clip. Anything else -- no job at all, or a completed one whose
  // output went missing -- has nothing to put back on the queue.
  return existing?.status === "failed" ? "retry" : "create";
}

export function matchingClipJob(jobs: RenderJobStatus[], request: RenderClipRequest): RenderJobStatus | null {
  const playerId = request.povSteamId || request.playerId;
  if (!playerId) return null;
  const matches = jobs.filter((job) => job.job_type === "render_clip" &&
    clipPlayerId(job) === playerId &&
    (job.tick_start ?? job.metadata.tickStart) === request.tickStart &&
    (job.tick_end ?? job.metadata.tickEnd) === request.tickEnd &&
    (job.tick_rate ?? job.metadata.tickRate) === request.tickRate &&
    (job.render_preset ?? job.metadata.renderPreset) === request.renderPreset);
  return matches.find((job) => playableClipVideo(job)) ?? matches.find(clipIsActive) ?? matches[0] ?? null;
}

export function clipsForPlayer(jobs: RenderJobStatus[], playerId: string | null): RenderJobStatus[] {
  return playerId ? jobs.filter((job) => job.job_type === "render_clip" && clipPlayerId(job) === playerId) : [];
}

export function buildEventClipRequest(replay: ReplayData, event: CoachingEvent): RenderClipRequest {
  const tickRate = replay.tickRate || replay.video.tickRate || 64;
  return {
    eventId: event.id,
    playerId: event.player_id || undefined,
    ...clipRangeForTick(replay, event.tick_start, event.round_number, tickRate),
    tickRate,
    roundNumber: event.round_number,
    renderPreset: "event_clip_v1"
  };
}

export function buildTickClipRequest(
  replay: ReplayData,
  currentTick: number,
  selectedRound: number,
  selectedPlayerId: string | null
): RenderClipRequest {
  const tickRate = replay.tickRate || replay.video.tickRate || 64;
  return {
    playerId: selectedPlayerId ?? undefined,
    ...clipRangeForTick(replay, currentTick, selectedRound, tickRate),
    tickRate,
    roundNumber: selectedRound,
    renderPreset: "selected_tick_v1"
  };
}

function clipRangeForTick(replay: ReplayData, tick: number, roundNumber: number, tickRate: number) {
  const paddingTicks = tickRate * 20;
  const round = replay.rounds.find((item) => item.roundNumber === roundNumber);
  const minTick = round?.startTick ?? replay.rounds[0]?.startTick ?? 0;
  const maxTick = round?.endTick ?? replay.rounds[replay.rounds.length - 1]?.endTick ?? tick + paddingTicks;
  const tickStart = Math.max(minTick, Math.round(tick - paddingTicks));
  const tickEnd = Math.min(maxTick, Math.round(tick + paddingTicks));
  return tickEnd > tickStart ? { tickStart, tickEnd } : {
    tickStart: Math.max(minTick, Math.round(tick)),
    tickEnd: Math.min(maxTick, Math.round(tick + tickRate))
  };
}
