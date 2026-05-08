"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

import { CoachingPanel } from "@/components/coaching/CoachingPanel";
import { FirstPersonReplay } from "@/components/replay/FirstPersonReplay";
import { RenderOperatorPanel } from "@/components/replay/RenderOperatorPanel";
import { ReplayViewer } from "@/components/replay/ReplayViewer";
import { Timeline } from "@/components/replay/Timeline";
import { VideoSetupPanel } from "@/components/replay/VideoSetupPanel";
import {
  createMockRenderJob,
  createRenderClipJob,
  getCoaching,
  getDemoStatus,
  getDemoVideo,
  getRenderJobs,
  getReplay,
  saveVideoCalibration,
  uploadDemoVideo,
  type RenderClipRequest,
  type RenderJobStatus,
  type VideoCalibrationUpdate
} from "@/lib/api";
import type { CoachingEvent } from "@/types/coaching";
import type { DemoStatus } from "@/types/demo";
import type { ReplayData } from "@/types/replay";

export default function DemoDetailPage() {
  const params = useParams<{ demoId: string }>();
  const demoId = params.demoId;

  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [replay, setReplay] = useState<ReplayData | null>(null);
  const [events, setEvents] = useState<CoachingEvent[]>([]);
  const [currentTick, setCurrentTick] = useState(0);
  const [selectedRound, setSelectedRound] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [selectedPlayerId, setSelectedPlayerId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renderRequesting, setRenderRequesting] = useState(false);
  const [clipRequestingEventId, setClipRequestingEventId] = useState<string | null>(null);
  const [tickClipRequesting, setTickClipRequesting] = useState(false);
  const [renderJobs, setRenderJobs] = useState<RenderJobStatus[]>([]);
  const [renderJobsRefreshing, setRenderJobsRefreshing] = useState(false);
  const [currentVideoTime, setCurrentVideoTime] = useState(0);
  const [detectedVideoDuration, setDetectedVideoDuration] = useState<number | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const nextStatus = await getDemoStatus(demoId);
      setStatus(nextStatus);
      setError(null);
      return nextStatus;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load demo status");
      return null;
    }
  }, [demoId]);

  const loadRenderJobs = useCallback(async () => {
    try {
      const nextJobs = await getRenderJobs(demoId);
      setRenderJobs(nextJobs);
      return nextJobs;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load render jobs");
      return [];
    }
  }, [demoId]);

  const loadReplay = useCallback(async () => {
    try {
      const [nextReplay, nextEvents, nextRenderJobs] = await Promise.all([
        getReplay(demoId),
        getCoaching(demoId),
        getRenderJobs(demoId)
      ]);
      setReplay(nextReplay);
      setEvents(nextEvents);
      setRenderJobs(nextRenderJobs);
      const initialTick = nextReplay.video.url
        ? nextReplay.video.tickStart
        : nextReplay.rounds[0]?.startTick ?? 0;
      const initialRound = findRoundForTick(nextReplay.rounds, initialTick) ?? nextReplay.rounds[0];
      setSelectedRound(initialRound?.roundNumber ?? 1);
      setCurrentTick(initialTick);
      setCurrentVideoTime(nextReplay.video.timeOriginSeconds ?? 0);
      setDetectedVideoDuration(null);
      setSelectedPlayerId(nextReplay.players[0]?.id ?? null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load replay");
    }
  }, [demoId]);

  useEffect(() => {
    let cancelled = false;

    async function boot() {
      const nextStatus = await loadStatus();
      if (!cancelled && nextStatus?.status === "completed") {
        await loadReplay();
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, [loadReplay, loadStatus]);

  useEffect(() => {
    if (status?.status === "completed") {
      return;
    }

    const intervalId = window.setInterval(async () => {
      const nextStatus = await loadStatus();
      if (nextStatus?.status === "completed") {
        await loadReplay();
      }
    }, 1800);

    return () => window.clearInterval(intervalId);
  }, [loadReplay, loadStatus, status?.status]);

  const selectedRoundData = useMemo(
    () => replay?.rounds.find((round) => round.roundNumber === selectedRound),
    [replay?.rounds, selectedRound]
  );
  const renderJobByEventId = useMemo(() => {
    const jobsByEventId = new Map<string, RenderJobStatus>();
    for (const job of renderJobs) {
      const eventId = job.metadata.eventId;
      if (typeof eventId === "string" && !jobsByEventId.has(eventId)) {
        jobsByEventId.set(eventId, job);
      }
    }
    return jobsByEventId;
  }, [renderJobs]);
  const latestRenderClipJob = renderJobs[0] ?? null;
  const hasActiveRenderClipJob = renderJobs.some(
    (job) => job.status === "queued" || job.status === "rendering"
  );

  const videoStatus = replay?.video.status;

  const loadVideoStatus = useCallback(async () => {
    if (!replay) {
      return null;
    }
    try {
      const video = await getDemoVideo(demoId);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      return video;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load video status");
      return null;
    }
  }, [demoId, replay]);

  const refreshRenderOperatorState = useCallback(async () => {
    setRenderJobsRefreshing(true);
    try {
      const [nextJobs, video] = await Promise.all([
        getRenderJobs(demoId),
        getDemoVideo(demoId)
      ]);
      setRenderJobs(nextJobs);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refresh render state");
    } finally {
      setRenderJobsRefreshing(false);
    }
  }, [demoId]);

  useEffect(() => {
    if (videoStatus !== "queued" && videoStatus !== "rendering") {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadVideoStatus();
    }, 1200);

    return () => window.clearInterval(intervalId);
  }, [loadVideoStatus, videoStatus]);

  useEffect(() => {
    if (!hasActiveRenderClipJob) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadRenderJobs();
      void loadVideoStatus();
    }, 1200);

    return () => window.clearInterval(intervalId);
  }, [hasActiveRenderClipJob, loadRenderJobs, loadVideoStatus]);

  useEffect(() => {
    if (!playing || !replay || !selectedRoundData || replay.video.url) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setCurrentTick((tick) => {
        const nextTick = tick + replay.tickRate * speed * 0.1;
        if (nextTick >= selectedRoundData.endTick) {
          setPlaying(false);
          return selectedRoundData.endTick;
        }
        return nextTick;
      });
    }, 100);

    return () => window.clearInterval(intervalId);
  }, [playing, replay, selectedRoundData, speed]);

  const seek = useCallback((tick: number) => {
    setCurrentTick(tick);
    const nextRound = replay?.rounds.find(
      (round) => tick >= round.startTick && tick <= round.endTick
    );
    if (nextRound) {
      setSelectedRound(nextRound.roundNumber);
    }
  }, [replay?.rounds]);

  function changeRound(roundNumber: number) {
    const nextRound = replay?.rounds.find((round) => round.roundNumber === roundNumber);
    setSelectedRound(roundNumber);
    if (nextRound) {
      setCurrentTick(nextRound.startTick);
    }
  }

  async function requestMockRender() {
    if (!replay) {
      return;
    }

    setRenderRequesting(true);
    try {
      const response = await createMockRenderJob(demoId);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video: response.video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create mock render job");
    } finally {
      setRenderRequesting(false);
    }
  }

  async function requestRenderClipForEvent(event: CoachingEvent) {
    if (!replay) {
      return;
    }

    setClipRequestingEventId(event.id);
    try {
      const response = await createRenderClipJob(demoId, buildEventClipRequest(replay, event));
      const { video, ...jobStatus } = response;
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setRenderJobs((currentJobs) => [
        jobStatus,
        ...currentJobs.filter((job) => job.job_id !== jobStatus.job_id)
      ]);
      setError(null);
      void refreshRenderOperatorState();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create render clip job");
    } finally {
      setClipRequestingEventId(null);
    }
  }

  async function requestRenderClipAtCurrentTick() {
    if (!replay) {
      return;
    }

    setTickClipRequesting(true);
    try {
      const response = await createRenderClipJob(
        demoId,
        buildTickClipRequest(replay, currentTick, selectedRound, selectedPlayerId)
      );
      const { video, ...jobStatus } = response;
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setRenderJobs((currentJobs) => [
        jobStatus,
        ...currentJobs.filter((job) => job.job_id !== jobStatus.job_id)
      ]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create render clip job");
    } finally {
      setTickClipRequesting(false);
    }
  }

  async function uploadManualVideo(file: File) {
    const video = await uploadDemoVideo(demoId, file);
    setDetectedVideoDuration(null);
    setCurrentVideoTime(video.timeOriginSeconds ?? 0);
    setReplay((currentReplay) =>
      currentReplay ? { ...currentReplay, video } : currentReplay
    );
    setError(null);
  }

  async function saveManualVideoCalibration(calibration: VideoCalibrationUpdate) {
    const video = await saveVideoCalibration(demoId, calibration);
    setReplay((currentReplay) =>
      currentReplay ? { ...currentReplay, video } : currentReplay
    );
    setError(null);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <span>CS2 Demo Coach</span>
        </div>
        <div className="topbar-actions">
          <Link className="secondary-button" href="/dashboard">
            <ArrowLeft size={16} />
            Dashboard
          </Link>
        </div>
      </header>

      <section className="page">
        <div className="detail-top">
          <div className="detail-title">
            <h1>{status ? `Mock Demo ${status.id.slice(0, 8)}` : "Loading demo"}</h1>
            <div className="detail-meta">
              <span>{status?.map_name ?? "map pending"}</span>
              <span>{status?.round_count ?? 0} rounds</span>
              <span>{status?.coaching_event_count ?? 0} coaching events</span>
            </div>
          </div>
          {status ? <span className={`status-badge ${status.status}`}>{status.status}</span> : null}
        </div>

        {error ? <div className="error-panel">{error}</div> : null}

        {!replay ? (
          <div className="panel loading-panel">
            Demo status: {status?.status ?? "loading"}. Replay will load when the worker completes.
          </div>
        ) : (
          <>
            <div className="detail-grid first-person-detail-grid">
              <div className="analysis-main-column">
                <FirstPersonReplay
                  replay={replay}
                  currentTick={currentTick}
                  playing={playing}
                  speed={speed}
                  renderRequesting={renderRequesting}
                  renderClipRequesting={tickClipRequesting}
                  latestRenderClipJob={latestRenderClipJob}
                  onRequestMockRender={requestMockRender}
                  onRequestRenderClip={requestRenderClipAtCurrentTick}
                  onSeekTick={seek}
                  onVideoDurationChange={setDetectedVideoDuration}
                  onVideoTimeChange={setCurrentVideoTime}
                />
                <RenderOperatorPanel
                  video={replay.video}
                  latestJob={latestRenderClipJob}
                  jobCount={renderJobs.length}
                  refreshing={renderJobsRefreshing}
                  onRefresh={() => void refreshRenderOperatorState()}
                />
                <VideoSetupPanel
                  currentVideoTime={currentVideoTime}
                  detectedDurationSeconds={detectedVideoDuration}
                  video={replay.video}
                  onSaveCalibration={saveManualVideoCalibration}
                  onUploadVideo={uploadManualVideo}
                />
                <ReplayViewer
                  replay={replay}
                  currentTick={currentTick}
                  selectedPlayerId={selectedPlayerId}
                  onSelectPlayer={setSelectedPlayerId}
                  variant="companion"
                />
              </div>
              <CoachingPanel
                events={events}
                players={replay.players}
                currentTick={currentTick}
                selectedRound={selectedRound}
                renderJobByEventId={renderJobByEventId}
                requestingEventId={clipRequestingEventId}
                onSeek={seek}
                onGenerateClip={requestRenderClipForEvent}
              />
            </div>

            <Timeline
              currentTick={currentTick}
              selectedRound={selectedRound}
              rounds={replay.rounds}
              speed={speed}
              playing={playing}
              events={events}
              onSeek={seek}
              onTogglePlay={() => setPlaying((value) => !value)}
              onSpeedChange={setSpeed}
              onRoundChange={changeRound}
            />
          </>
        )}
      </section>
    </main>
  );
}

function findRoundForTick(rounds: ReplayData["rounds"], tick: number) {
  return rounds.find((round) => tick >= round.startTick && tick <= round.endTick);
}

function buildEventClipRequest(replay: ReplayData, event: CoachingEvent): RenderClipRequest {
  const tickRate = replay.tickRate || replay.video.tickRate || 64;
  const range = clipRangeForTick(replay, event.tick_start, event.round_number, tickRate);
  return {
    eventId: event.id,
    playerId: eventPlayerId(event),
    tickStart: range.tickStart,
    tickEnd: range.tickEnd,
    tickRate,
    roundNumber: event.round_number,
    renderPreset: "event_clip_v1"
  };
}

function buildTickClipRequest(
  replay: ReplayData,
  currentTick: number,
  selectedRound: number,
  selectedPlayerId: string | null
): RenderClipRequest {
  const tickRate = replay.tickRate || replay.video.tickRate || 64;
  const range = clipRangeForTick(replay, currentTick, selectedRound, tickRate);
  return {
    playerId: selectedPlayerId ?? undefined,
    tickStart: range.tickStart,
    tickEnd: range.tickEnd,
    tickRate,
    roundNumber: selectedRound,
    renderPreset: "selected_tick_v1"
  };
}

function clipRangeForTick(
  replay: ReplayData,
  tick: number,
  roundNumber: number,
  tickRate: number
) {
  const paddingTicks = tickRate * 20;
  const round = replay.rounds.find((item) => item.roundNumber === roundNumber);
  const firstRound = replay.rounds[0];
  const lastRound = replay.rounds[replay.rounds.length - 1];
  const minTick = round?.startTick ?? firstRound?.startTick ?? 0;
  const maxTick = round?.endTick ?? lastRound?.endTick ?? tick + paddingTicks;
  const tickStart = Math.max(minTick, Math.round(tick - paddingTicks));
  const tickEnd = Math.min(maxTick, Math.round(tick + paddingTicks));

  if (tickEnd > tickStart) {
    return { tickStart, tickEnd };
  }

  return {
    tickStart: Math.max(minTick, Math.round(tick)),
    tickEnd: Math.min(maxTick, Math.round(tick + tickRate))
  };
}

function eventPlayerId(event: CoachingEvent): string | undefined {
  const involvedPlayerIds = event.structured_context_json.involvedPlayerIds;
  if (
    Array.isArray(involvedPlayerIds) &&
    involvedPlayerIds.length > 0 &&
    typeof involvedPlayerIds[0] === "string"
  ) {
    return involvedPlayerIds[0];
  }
  return event.player_id || undefined;
}
