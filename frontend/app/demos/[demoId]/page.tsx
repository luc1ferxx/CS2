"use client";

import Link from "next/link";
import { ArrowLeft, Film, Map as MapIcon, RefreshCcw, Scissors } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { CoachingPanel } from "@/components/coaching/CoachingPanel";
import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { useAuth } from "@/components/auth/AuthProvider";
import { SessionControls } from "@/components/auth/SessionControls";
import {
  FirstPersonReplay,
  type FirstPersonReplayHandle
} from "@/components/replay/FirstPersonReplay";
import { RenderOperatorPanel } from "@/components/replay/RenderOperatorPanel";
import { ClipLibrary } from "@/components/replay/ClipLibrary";
import { DetailSummary } from "@/components/replay/DetailSummary";
import { ReplayDiagnosticsPanel } from "@/components/replay/ReplayDiagnosticsPanel";
import { ReviewCommandBar } from "@/components/replay/ReviewCommandBar";
import { ReplayViewer } from "@/components/replay/ReplayViewer";
import { PersonalReviewPanel } from "@/components/replay/PersonalReviewPanel";
import { RoundReviewPanel } from "@/components/replay/RoundReviewPanel";
import { Timeline } from "@/components/replay/Timeline";
import { VideoSetupPanel } from "@/components/replay/VideoSetupPanel";
import {
  clearCoachingFeedback,
  createMockRenderJob,
  createRenderClipJob,
  getCoaching,
  getDemoStatus,
  getDemoVideo,
  getRenderJobs,
  getRenderWorkerStatus,
  getReplay,
  isApiError,
  retryDemoParse,
  retryRenderClipJob,
  saveCoachingFeedback,
  saveVideoCalibration,
  uploadDemoVideo,
  type RenderClipRequest,
  type RenderJobStatus,
  type RenderWorkerStatus,
  type VideoCalibrationUpdate
} from "@/lib/api";
import { NO_CAPABILITIES } from "@/lib/auth";
import {
  detailSummaryItems,
  friendlyErrorMessage,
  isRenderActiveStatus,
  replayUnavailableNotice
} from "@/lib/demo-library";
import { buildReplayDiagnostics } from "@/lib/replay-diagnostics";
import { resolvePrivateMediaSource } from "@/lib/media-url";
import { advanceReplayTick, videoMediaIdentity, videoPlaybackState } from "@/lib/replay-time";
import { roundClock, savedClipAtTick, usesVideoClock } from "@/lib/review-workspace";
import { findRoundForTick } from "@/lib/round-review";
import { buildEventClipRequest, buildTickClipRequest, clipIsActive, clipRequestAction, clipsForPlayer, matchingClipJob, playableClipVideo, retainSelectedClip, reviewVideo, type SelectedClip } from "@/lib/render-clips";
import { renderWorkerNotice } from "@/lib/render-worker";
import { uploadLimitMessage } from "@/lib/upload-limits";
import { withFeedback } from "@/lib/coaching-review";
import {
  DEFAULT_PLAYER_IDENTITY,
  coachingForPlayer,
  matchPreferredPlayer,
  parserEventsForPlayer,
  personalReviewSummary,
  readPreferredPlayer,
  savePreferredPlayer
} from "@/lib/personal-review";
import type { CoachingEvent, CoachingFeedback, CoachingVerdict } from "@/types/coaching";
import type { DemoStatus } from "@/types/demo";
import type { ReplayData } from "@/types/replay";

export default function DemoDetailPage() {
  return (
    <AuthBoundary>
      <DemoDetailContent />
    </AuthBoundary>
  );
}

function DemoDetailContent() {
  const params = useParams<{ demoId: string }>();
  const demoId = params.demoId;
  const { state: authState } = useAuth();
  const { devTools, renderClips } = authState.capabilities ?? NO_CAPABILITIES;

  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [loadedReplay, setReplay] = useState<ReplayData | null>(null);
  const [selectedClip, setSelectedClip] = useState<SelectedClip | null>(null);
  const [events, setEvents] = useState<CoachingEvent[]>([]);
  const [currentTick, setCurrentTick] = useState(0);
  const [selectedRound, setSelectedRound] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [viewMode, setViewMode] = useState<"auto" | "map">("auto");
  const [preferredIdentity, setPreferredIdentity] = useState(DEFAULT_PLAYER_IDENTITY);
  const [preferenceSaved, setPreferenceSaved] = useState(true);
  const [playerOverride, setPlayerOverride] = useState<{ demoId: string; playerId: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [replayLoadFailed, setReplayLoadFailed] = useState(false);
  const [renderRequesting, setRenderRequesting] = useState(false);
  const [parseRetrying, setParseRetrying] = useState(false);
  const [clipRequestingEventId, setClipRequestingEventId] = useState<string | null>(null);
  const [tickClipRequesting, setTickClipRequesting] = useState(false);
  const [renderJobs, setRenderJobs] = useState<RenderJobStatus[]>([]);
  const [renderWorker, setRenderWorker] = useState<RenderWorkerStatus | null>(null);
  // A background render may update the demo's default video without changing the clip being reviewed.
  const replay = useMemo(() => loadedReplay
    ? { ...loadedReplay, video: reviewVideo(loadedReplay.video, renderJobs, selectedClip, demoId) }
    : null, [demoId, loadedReplay, renderJobs, selectedClip]);
  const [renderJobsRefreshing, setRenderJobsRefreshing] = useState(false);
  const [currentVideoTime, setCurrentVideoTime] = useState(0);
  const [detectedVideoDuration, setDetectedVideoDuration] = useState<number | null>(null);
  const [unavailableVideoIdentity, setUnavailableVideoIdentity] = useState<string | null>(null);
  const firstPersonReplayRef = useRef<FirstPersonReplayHandle | null>(null);
  const stageRef = useRef<HTMLElement | null>(null);

  const revealStage = useCallback(() => {
    window.requestAnimationFrame(() => {
      const stage = stageRef.current;
      if (!stage) return;
      stage.focus({ preventScroll: true });
      const bounds = stage.getBoundingClientRect();
      if (bounds.top < 56 || bounds.bottom > window.innerHeight) {
        stage.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      }
    });
  }, []);

  useEffect(() => {
    setPreferredIdentity(readPreferredPlayer(() => window.localStorage));
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const nextStatus = await getDemoStatus(demoId);
      setStatus(nextStatus);
      setError(null);
      return nextStatus;
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to load demo status"));
      return null;
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
      const initialSelection = retainSelectedClip(null, nextRenderJobs, nextReplay.video, demoId);
      const initialVideo = reviewVideo(nextReplay.video, nextRenderJobs, initialSelection, demoId);
      setSelectedClip(initialSelection);
      setEvents(nextEvents);
      setRenderJobs(nextRenderJobs);
      const initialTick = initialVideo.url
        ? initialVideo.tickStart
        : nextReplay.rounds[0]?.startTick ?? 0;
      const initialRound = findRoundForTick(nextReplay.rounds, initialTick) ?? nextReplay.rounds[0];
      setSelectedRound(initialRound?.roundNumber ?? 1);
      setCurrentTick(initialTick);
      setCurrentVideoTime(initialVideo.timeOriginSeconds ?? 0);
      setDetectedVideoDuration(null);
      setError(null);
      setReplayLoadFailed(false);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to load replay"));
      setReplayLoadFailed(true);
    }
  }, [demoId]);

  const retryParse = useCallback(async () => {
    setParseRetrying(true);
    try {
      await retryDemoParse(demoId);
      // The previous fetch's verdict is void the moment a re-parse is queued;
      // clearing it here keeps the stale failure out of the window where status
      // flips back to "completed" while the new replay is still in flight.
      setReplayLoadFailed(false);
      // Refetch instead of folding the summary the retry returns into the
      // status shape: it also puts the demo back in a non-completed state,
      // which re-arms the poll below and loads the replay on its own.
      await loadStatus();
    } catch (err) {
      const limitMessage = isApiError(err)
        ? uploadLimitMessage(err.status, err.detailCode, err.retryAfterSeconds)
        : null;
      setError(limitMessage ?? friendlyErrorMessage(err instanceof Error ? err.message : "Failed to retry demo parse"));
    } finally {
      setParseRetrying(false);
    }
  }, [demoId, loadStatus]);

  const submitCoachingFeedback = useCallback(async (event: CoachingEvent, verdict: CoachingVerdict | null) => {
    // The verdict is the player's own statement, so show it at once and only
    // roll back if the server refuses it.
    const previous = event.feedback ?? null;
    setEvents((current) => withFeedback(
      current, event.id, verdict ? { verdict, note: null, updated_at: new Date().toISOString() } : null
    ));
    // A later click may already have moved the card on; a response (or a
    // rollback) only lands if the card still shows the verdict it was for.
    const applyIfStillCurrent = (feedback: CoachingFeedback | null) =>
      setEvents((current) => current.map((item) =>
        item.id === event.id && (item.feedback?.verdict ?? null) === verdict ? { ...item, feedback } : item
      ));
    try {
      if (verdict) {
        applyIfStillCurrent(await saveCoachingFeedback(demoId, event.id, { verdict }));
      } else {
        await clearCoachingFeedback(demoId, event.id);
      }
    } catch (err) {
      applyIfStillCurrent(previous);
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to save coaching feedback"));
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
  const preferredPlayerMatch = useMemo(
    () => matchPreferredPlayer(replay?.players ?? [], preferredIdentity),
    [preferredIdentity, replay?.players]
  );
  const selectedPlayerId = playerOverride?.demoId === demoId
    ? playerOverride.playerId
    : preferredPlayerMatch.player?.id ?? null;
  const selectPlayer = useCallback((playerId: string | null) => {
    setPlayerOverride({ demoId, playerId });
  }, [demoId]);
  const selectedPlayer = useMemo(
    () => replay?.players.find((player) => player.id === selectedPlayerId) ?? null,
    [replay?.players, selectedPlayerId]
  );
  const videoUnavailable = Boolean(replay && (
    unavailableVideoIdentity === videoMediaIdentity(replay.video) ||
    (replay.video.url && !resolvePrivateMediaSource(replay.video.url))
  ));
  const videoPlayback = replay
    ? videoPlaybackState(replay.video, currentTick, selectedPlayerId, videoUnavailable)
    : "unavailable";
  const videoDrivesClock = usesVideoClock(viewMode, videoPlayback);
  const personalEvents = useMemo(
    () => coachingForPlayer(events, selectedPlayer?.id ?? null),
    [events, selectedPlayer?.id]
  );
  const personalSummary = useMemo(
    () => personalReviewSummary(events, selectedPlayer?.id ?? null),
    [events, selectedPlayer?.id]
  );
  const scopedReplay = useMemo(
    () => replay ? { ...replay, events: parserEventsForPlayer(replay.events ?? [], selectedPlayer?.id ?? null) } : null,
    [replay, selectedPlayer?.id]
  );
  const orderedFindings = useMemo(
    () => [...personalEvents].sort((left, right) => left.tick_start - right.tick_start),
    [personalEvents]
  );
  const previousFinding = useMemo(() => {
    for (let index = orderedFindings.length - 1; index >= 0; index -= 1) {
      if (orderedFindings[index].tick_start < currentTick - 0.5) {
        return orderedFindings[index];
      }
    }
    return null;
  }, [currentTick, orderedFindings]);
  const nextFinding = useMemo(
    () => orderedFindings.find((event) => event.tick_start > currentTick + 0.5) ?? null,
    [currentTick, orderedFindings]
  );
  const renderJobByEventId = useMemo(() => {
    const jobsByEventId = new Map<string, RenderJobStatus>();
    if (!replay) return jobsByEventId;
    for (const event of personalEvents) {
      const job = savedClipAtTick(renderJobs, event.tick_start, event.player_id, replay.video.renderJobId)
        ?? matchingClipJob(renderJobs, buildEventClipRequest(replay, event));
      if (job) jobsByEventId.set(event.id, job);
    }
    return jobsByEventId;
  }, [personalEvents, renderJobs, replay]);
  const personalClips = useMemo(() => clipsForPlayer(renderJobs, selectedPlayerId), [renderJobs, selectedPlayerId]);
  const currentTickClipJob = useMemo(() => replay
    ? savedClipAtTick(renderJobs, currentTick, selectedPlayerId, replay.video.renderJobId)
      ?? matchingClipJob(renderJobs, buildTickClipRequest(replay, currentTick, selectedRound, selectedPlayerId))
    : null, [currentTick, renderJobs, replay, selectedPlayerId, selectedRound]);
  const latestRenderClipJob = renderJobs[0] ?? null;
  const tickClipWorkerNotice = useMemo(
    () => renderWorkerNotice(renderWorker, currentTickClipJob),
    [currentTickClipJob, renderWorker]
  );
  const hasActiveRenderClipJob = renderJobs.some((job) => isRenderActiveStatus(job.status));
  const summaryItems = useMemo(
    () => detailSummaryItems({ status, replay, latestRenderJob: latestRenderClipJob }),
    [latestRenderClipJob, replay, status]
  );
  const detailDiagnostics = useMemo(
    () => (replay ? buildReplayDiagnostics(replay, events, renderJobs) : null),
    [events, renderJobs, replay]
  );

  const videoStatus = loadedReplay?.video.status;
  const replayNotice = replayUnavailableNotice(status, replayLoadFailed);

  const loadRenderState = useCallback(async () => {
    try {
      const [nextJobs, video, worker] = await Promise.all([
        getRenderJobs(demoId),
        getDemoVideo(demoId),
        // A queued job goes nowhere without a renderer, so liveness rides along
        // with the same poll that already drives the job list.
        getRenderWorkerStatus().catch(() => null)
      ]);
      setRenderJobs(nextJobs);
      setRenderWorker(worker);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setSelectedClip((current) => retainSelectedClip(current, nextJobs, video, demoId));
      return video;
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to load render state"));
      return null;
    }
  }, [demoId]);

  const refreshRenderOperatorState = useCallback(async () => {
    setRenderJobsRefreshing(true);
    try {
      if (await loadRenderState()) setError(null);
    } finally {
      setRenderJobsRefreshing(false);
    }
  }, [loadRenderState]);

  useEffect(() => {
    if (!isRenderActiveStatus(videoStatus) && !hasActiveRenderClipJob) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadRenderState();
    }, 1200);

    return () => window.clearInterval(intervalId);
  }, [hasActiveRenderClipJob, loadRenderState, videoStatus]);

  useEffect(() => {
    if (!playing || !replay || !selectedRoundData || videoDrivesClock) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setCurrentTick((tick) => {
        const nextTick = advanceReplayTick(
          tick, replay.tickRate * speed * 0.1, selectedRoundData.endTick,
          replay.video, selectedPlayerId, videoUnavailable || viewMode === "map"
        );
        if (nextTick >= selectedRoundData.endTick) {
          setPlaying(false);
          return selectedRoundData.endTick;
        }
        return nextTick;
      });
    }, 100);

    return () => window.clearInterval(intervalId);
  }, [playing, replay, selectedPlayerId, selectedRoundData, speed, videoDrivesClock, videoUnavailable, viewMode]);

  const updateCoordinateFromTick = useCallback((tick: number) => {
    setCurrentTick(tick);
    const nextRound = replay?.rounds.find(
      (round) => tick >= round.startTick && tick <= round.endTick
    );
    if (nextRound) {
      setSelectedRound(nextRound.roundNumber);
    }
  }, [replay?.rounds]);

  const seek = useCallback((tick: number) => {
    firstPersonReplayRef.current?.seekToTick(tick);
    updateCoordinateFromTick(tick);
  }, [updateCoordinateFromTick]);

  const seekToFinding = useCallback((tick: number) => {
    const saved = savedClipAtTick(renderJobs, tick, selectedPlayerId, replay?.video.renderJobId);
    const video = playableClipVideo(saved);
    if (video) setSelectedClip({ demoId, video });
    setViewMode("auto");
    setPlaying(false);
    seek(tick);
    revealStage();
  }, [demoId, renderJobs, replay?.video.renderJobId, revealStage, seek, selectedPlayerId]);

  const viewVideoClip = useCallback(() => {
    if (!replay) return;
    if (replay.video.povSteamId) {
      const player = replay.players.find((item) => item.id === replay.video.povSteamId);
      if (!player) return;
      selectPlayer(player.id);
    }
    setUnavailableVideoIdentity(null);
    setViewMode("auto");
    seek(replay.video.tickStart);
    setPlaying(true);
    revealStage();
  }, [replay, revealStage, seek, selectPlayer]);

  const playSavedClip = useCallback((job: RenderJobStatus, tick?: number) => {
    const video = playableClipVideo(job);
    if (!video || !replay?.players.some((player) => player.id === video.povSteamId)) return;
    setSelectedClip({ demoId, video });
    setViewMode("auto");
    selectPlayer(video.povSteamId ?? null);
    setUnavailableVideoIdentity(null);
    setDetectedVideoDuration(null);
    const nextTick = tick !== undefined && tick >= video.tickStart && tick < video.tickEnd ? tick : video.tickStart;
    // Reused media needs an explicit seek before its clock resumes. A different clip seeks on mount.
    if (videoMediaIdentity(video) === videoMediaIdentity(replay.video)) {
      seek(nextTick);
    } else {
      updateCoordinateFromTick(nextTick);
    }
    setPlaying(true);
    revealStage();
  }, [demoId, replay?.players, replay?.video, revealStage, seek, selectPlayer, updateCoordinateFromTick]);

  const changeRound = useCallback((roundNumber: number) => {
    const nextRound = replay?.rounds.find((round) => round.roundNumber === roundNumber);
    if (nextRound) {
      seek(nextRound.startTick);
    } else {
      setSelectedRound(roundNumber);
    }
  }, [replay?.rounds, seek]);

  async function requestMockRender() {
    if (!replay) {
      return;
    }

    setRenderRequesting(true);
    try {
      const response = await createMockRenderJob(demoId);
      setSelectedClip(null);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video: response.video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to create mock render job"));
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
      await requestOrViewClip(buildEventClipRequest(replay, event), event.tick_start);
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to create render clip job"));
    } finally {
      setClipRequestingEventId(null);
    }
  }

  async function requestRenderClipAtCurrentTick() {
    if (!replay) {
      return;
    }

    if (currentTickClipJob && playableClipVideo(currentTickClipJob)) {
      playSavedClip(currentTickClipJob, currentTick);
      return;
    }

    setTickClipRequesting(true);
    try {
      await requestOrViewClip(buildTickClipRequest(replay, currentTick, selectedRound, selectedPlayerId), currentTick);
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to create render clip job"));
    } finally {
      setTickClipRequesting(false);
    }
  }

  async function requestOrViewClip(request: RenderClipRequest, tick: number) {
    const existing = savedClipAtTick(renderJobs, tick, request.povSteamId ?? request.playerId ?? null, replay?.video.renderJobId)
      ?? matchingClipJob(renderJobs, request);
    const action = clipRequestAction(existing);
    if (existing && action === "play") {
      playSavedClip(existing, tick);
      return;
    }
    if (action === "wait") return;
    if (replay?.video.status === "ready" && replay.video.url) {
      const currentVideo = replay.video;
      setSelectedClip((current) => retainSelectedClip(current, renderJobs, currentVideo, demoId));
    }
    const response = existing && action === "retry"
      // Requeue the row that failed instead of leaving it behind and starting a
      // second one for the same clip. Both calls return the same shape.
      ? await retryRenderClipJob(demoId, existing.job_id)
      : await createRenderClipJob(demoId, request);
    setRenderJobs((currentJobs) => [response, ...currentJobs.filter((job) => job.job_id !== response.job_id)]);
    // The job is created either way, so surface an offline renderer now rather
    // than leaving the caller on "queued" until the next poll.
    if (response.render_worker) setRenderWorker(response.render_worker);
    if (playableClipVideo(response)) {
      playSavedClip(response, tick);
    } else {
      setReplay((currentReplay) => currentReplay ? { ...currentReplay, video: response.video } : currentReplay);
    }
  }

  async function uploadManualVideo(file: File) {
    try {
      const video = await uploadDemoVideo(demoId, file);
      setSelectedClip(null);
      setDetectedVideoDuration(null);
      setCurrentVideoTime(video.timeOriginSeconds ?? 0);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to upload manual video"));
    }
  }

  async function saveManualVideoCalibration(calibration: VideoCalibrationUpdate) {
    try {
      const video = await saveVideoCalibration(demoId, calibration);
      setSelectedClip(null);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to save calibration"));
    }
  }

  return (
    <main className="app-shell review-detail-shell review-app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <span>CS2 Demo Coach</span>
        </div>
        <div className="topbar-actions">
          <Link className="secondary-button" href="/dashboard">
            <ArrowLeft size={16} />
            我的比赛
          </Link>
          <SessionControls />
        </div>
      </header>

      <section className="page">
        <div className="detail-top">
          <div className="detail-title">
            <span className="workspace-kicker">
              比赛复盘 / {status?.map_name?.replace(/^de_/, "") ?? "准备中"}
            </span>
            <h1>{status?.name ?? (status ? `比赛 ${status.id.slice(0, 8)}` : "正在打开比赛")}</h1>
            <div className="detail-meta">
              <span>{status?.round_count ?? 0} 回合</span>
              <span>{personalEvents.length} 条个人复盘线索</span>
              {status?.archived ? <span>已归档</span> : null}
            </div>
          </div>
          {status ? <span className={`status-badge ${status.status}`}>{status.status === "completed" ? "可以开始复盘" : status.status === "failed" ? "处理失败" : "正在准备比赛"}</span> : null}
        </div>

        {error ? <div className="error-panel" role="alert">{error}</div> : null}

        {!replay ? (
          <>
            <div className="panel loading-panel">
              <p>{replayNotice.message}</p>
              {replayNotice.retryable ? (
                <button
                  className="secondary-button compact-button"
                  type="button"
                  onClick={() => void retryParse()}
                  disabled={parseRetrying}
                >
                  <RefreshCcw size={14} />
                  {parseRetrying ? "正在重新处理…" : "重新处理"}
                </button>
              ) : null}
            </div>
            {status ? <DetailSummary items={summaryItems} /> : null}
          </>
        ) : (
          <>
            <PersonalReviewPanel
              key={preferredIdentity}
              preferredIdentity={preferredIdentity}
              match={preferredPlayerMatch}
              players={replay.players}
              selectedPlayer={selectedPlayer}
              summary={personalSummary}
              preferenceSaved={preferenceSaved}
              onSaveIdentity={(identity) => {
                setPreferredIdentity(identity.trim());
                setPlayerOverride(null);
                setPreferenceSaved(savePreferredPlayer(identity, () => window.localStorage));
              }}
              onSelectPlayer={selectPlayer}
              onSeek={seekToFinding}
            />
            <div className="review-layout">
            <div className="review-main-column">
            <section id="player" className="review-stage" aria-label="Replay player" tabIndex={-1} ref={stageRef}>
              <div className="review-stage-toolbar">
                <div className="review-view-switch" role="group" aria-label="回放视图">
                  <button type="button" aria-pressed={!videoDrivesClock} onClick={() => setViewMode("map")}>
                    <MapIcon size={16} aria-hidden="true" /> 战术回放
                  </button>
                  <button type="button" aria-pressed={videoDrivesClock} disabled={videoPlayback !== "active"}
                    title={videoPlayback === "active" ? "观看这一时刻的第一人称画面" : "这一时刻还没有可播放的视频"}
                    onClick={() => setViewMode("auto")}>
                    <Film size={16} aria-hidden="true" /> 第一人称
                  </button>
                </div>
                {renderClips ? <button className="secondary-button compact-button" type="button"
                  disabled={tickClipRequesting || clipIsActive(currentTickClipJob) || !selectedPlayerId}
                  title={tickClipWorkerNotice?.detail}
                  onClick={() => void requestRenderClipAtCurrentTick()}>
                  <Scissors size={15} aria-hidden="true" />
                  {tickClipRequesting ? "正在提交" : tickClipWorkerNotice ? tickClipWorkerNotice.label : clipIsActive(currentTickClipJob) ? "视频生成中" : playableClipVideo(currentTickClipJob) ? "观看此刻视频" : "生成此刻视频"}
                </button> : null}
              </div>
              <div className={`review-main-canvas ${videoDrivesClock ? "showing-video" : "showing-map"}`}>
              {videoDrivesClock ? (
              <FirstPersonReplay
                compact
                ref={firstPersonReplayRef}
                replay={replay}
                currentTick={currentTick}
                playing={playing}
                speed={speed}
                playbackState={videoPlayback}
                mediaUnavailable={videoUnavailable}
                renderRequesting={renderRequesting}
                renderClipRequesting={tickClipRequesting}
                latestRenderClipJob={latestRenderClipJob}
                currentTickClipJob={currentTickClipJob}
                renderClipPlayerSelected={Boolean(selectedPlayerId)}
                renderWorker={renderWorker}
                onRequestMockRender={requestMockRender}
                onRequestRenderClip={renderClips ? requestRenderClipAtCurrentTick : undefined}
                onVideoTickChange={updateCoordinateFromTick}
                onVideoUnavailable={setUnavailableVideoIdentity}
                onViewVideoClip={viewVideoClip}
                onVideoDurationChange={setDetectedVideoDuration}
                onVideoTimeChange={setCurrentVideoTime}
              />
              ) : (
                <ReplayViewer replay={scopedReplay ?? replay} currentTick={currentTick}
                  selectedPlayerId={selectedPlayerId} onSelectPlayer={selectPlayer} variant="featured" />
              )}
              </div>
              <ReviewCommandBar
                mapName={status?.map_name ?? replay.mapName}
                selectedRound={selectedRound}
                currentTick={currentTick}
                roundTime={roundClock(currentTick, selectedRoundData?.startTick ?? 0, replay.tickRate)}
                currentPovName={selectedPlayer?.name ?? "请选择玩家"}
                playing={playing}
                speed={speed}
                previousFinding={previousFinding}
                nextFinding={nextFinding}
                onTogglePlay={() => setPlaying((value) => !value)}
                onSpeedChange={setSpeed}
                onPreviousFinding={() => {
                  if (previousFinding) {
                    seekToFinding(previousFinding.tick_start);
                  }
                }}
                onNextFinding={() => {
                  if (nextFinding) {
                    seekToFinding(nextFinding.tick_start);
                  }
                }}
              />
              <Timeline currentTick={currentTick} selectedRound={selectedRound} rounds={replay.rounds}
                tickRate={replay.tickRate} events={personalEvents} parserEvents={scopedReplay?.events ?? []}
                selectedPlayerName={selectedPlayer?.name ?? null} onSeek={seek} />
            </section>
            {videoPlayback !== "active" ? (
              <div className="review-media-note" role="status">
                <span>{videoUnavailable ? "视频暂时无法播放，已切换到战术回放。" : renderClips ? "当前时刻使用战术回放，可按需生成第一人称视频。" : "当前时刻使用战术回放。"}</span>
                {replay.video.url && !videoUnavailable ? <button type="button" className="text-button" onClick={viewVideoClip}>打开已保存的视频</button> : null}
              </div>
            ) : null}
            <RoundReviewPanel
              replay={replay}
              coachingEvents={personalEvents}
              selectedPlayerName={selectedPlayer?.name ?? null}
              currentTick={currentTick}
              selectedRound={selectedRound}
              onSelectRound={changeRound}
              onSeek={seek}
            />
            <details className="review-saved-clips">
              <summary><Film size={16} aria-hidden="true" /><span>已保存的视频</span>
                <span className="saved-clips-count">{personalClips.filter((job) => playableClipVideo(job)).length} 段可播放</span>
                {hasActiveRenderClipJob ? <span role="status">有视频正在生成</span> : null}
              </summary>
              <ClipLibrary jobs={personalClips} playerName={selectedPlayer?.name ?? null} rounds={replay.rounds}
                selectedJobId={replay.video.renderJobId ?? null} onPlay={playSavedClip} />
            </details>
            </div>
              <CoachingPanel
                key={selectedPlayer?.id ?? "no-player"}
                events={personalEvents}
                selectedPlayerName={selectedPlayer?.name ?? null}
                players={replay.players}
                rounds={replay.rounds}
                tickRate={replay.tickRate}
                currentTick={currentTick}
                selectedRound={selectedRound}
                renderJobByEventId={renderJobByEventId}
                requestingEventId={clipRequestingEventId}
                onSeek={seekToFinding}
                onGenerateClip={renderClips ? requestRenderClipForEvent : undefined}
                onFeedback={submitCoachingFeedback}
              />
            </div>
            <details className="review-inspector">
              <summary><span>高级工具</span><small>{devTools || renderClips ? "视频校准、生成记录与技术详情" : "技术详情"}</small></summary>
              <div className="review-inspector-content">
              {devTools ? <button className="secondary-button compact-button" type="button" disabled={renderRequesting}
                onClick={() => void requestMockRender()}>{renderRequesting ? "提交中" : "创建模拟视频任务（开发测试）"}</button> : null}
            {renderClips || devTools ? <section className="review-support-bay" aria-label="Render and calibration support">
              {renderClips ? <RenderOperatorPanel
                video={replay.video}
                latestJob={latestRenderClipJob}
                renderWorker={renderWorker}
                jobCount={renderJobs.length}
                refreshing={renderJobsRefreshing}
                onRefresh={() => void refreshRenderOperatorState()}
              /> : null}
              {devTools ? <VideoSetupPanel
                currentVideoTime={currentVideoTime}
                detectedDurationSeconds={detectedVideoDuration}
                video={replay.video}
                calibrationDisabled={Boolean(replay.video.renderJobId)}
                onSaveCalibration={saveManualVideoCalibration}
                onUploadVideo={uploadManualVideo}
              /> : null}
            </section> : null}
                {status ? <DetailSummary items={summaryItems} /> : null}
                {detailDiagnostics ? <ReplayDiagnosticsPanel diagnostics={detailDiagnostics} /> : null}
              </div>
            </details>
          </>
        )}
      </section>
    </main>
  );
}
