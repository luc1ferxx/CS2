"use client";

import Link from "next/link";
import { ArrowLeft, ChevronLeft, ChevronRight, Film, Map as MapIcon, Pause, Play, Scissors } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { CoachingPanel } from "@/components/coaching/CoachingPanel";
import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { SessionControls } from "@/components/auth/SessionControls";
import {
  FirstPersonReplay,
  type FirstPersonReplayHandle
} from "@/components/replay/FirstPersonReplay";
import { RenderOperatorPanel } from "@/components/replay/RenderOperatorPanel";
import { ClipLibrary } from "@/components/replay/ClipLibrary";
import { ReplayViewer } from "@/components/replay/ReplayViewer";
import { PersonalReviewPanel } from "@/components/replay/PersonalReviewPanel";
import { RoundReviewPanel } from "@/components/replay/RoundReviewPanel";
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
import {
  detailSummaryItems,
  friendlyErrorMessage,
  isRenderActiveStatus,
  parseFailureReason,
  type DetailSummaryItem
} from "@/lib/demo-library";
import { buildReplayDiagnostics, type ReplayDetailDiagnostics } from "@/lib/replay-diagnostics";
import { resolvePrivateMediaSource } from "@/lib/media-url";
import { advanceReplayTick, videoMediaIdentity, videoPlaybackState } from "@/lib/replay-time";
import { roundClock, savedClipAtTick, usesVideoClock } from "@/lib/review-workspace";
import { buildEventClipRequest, buildTickClipRequest, clipIsActive, clipsForPlayer, matchingClipJob, playableClipVideo, retainSelectedClip, reviewVideo, type SelectedClip } from "@/lib/render-clips";
import {
  DEFAULT_PLAYER_IDENTITY,
  coachingForPlayer,
  matchPreferredPlayer,
  parserEventsForPlayer,
  personalReviewSummary,
  readPreferredPlayer,
  savePreferredPlayer
} from "@/lib/personal-review";
import type { CoachingEvent } from "@/types/coaching";
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
  const [renderRequesting, setRenderRequesting] = useState(false);
  const [clipRequestingEventId, setClipRequestingEventId] = useState<string | null>(null);
  const [tickClipRequesting, setTickClipRequesting] = useState(false);
  const [renderJobs, setRenderJobs] = useState<RenderJobStatus[]>([]);
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
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to load replay"));
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
  const parseFailureMessage = status ? parseFailureReason(status) : null;

  const loadRenderState = useCallback(async () => {
    try {
      const [nextJobs, video] = await Promise.all([getRenderJobs(demoId), getDemoVideo(demoId)]);
      setRenderJobs(nextJobs);
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
    if (existing && playableClipVideo(existing)) {
      playSavedClip(existing, tick);
      return;
    }
    if (clipIsActive(existing)) return;
    if (replay?.video.status === "ready" && replay.video.url) {
      const currentVideo = replay.video;
      setSelectedClip((current) => retainSelectedClip(current, renderJobs, currentVideo, demoId));
    }
    const response = await createRenderClipJob(demoId, request);
    setRenderJobs((currentJobs) => [response, ...currentJobs.filter((job) => job.job_id !== response.job_id)]);
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
              {status?.status === "failed"
                ? `比赛处理失败。${parseFailureMessage ?? "暂时无法加载回放，请返回比赛库重试。"}`
                : "正在准备回放，完成后会自动显示。"}
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
                <button className="secondary-button compact-button" type="button"
                  disabled={tickClipRequesting || clipIsActive(currentTickClipJob) || !selectedPlayerId}
                  onClick={() => void requestRenderClipAtCurrentTick()}>
                  <Scissors size={15} aria-hidden="true" />
                  {tickClipRequesting ? "正在提交" : clipIsActive(currentTickClipJob) ? "视频生成中" : playableClipVideo(currentTickClipJob) ? "观看此刻视频" : "生成此刻视频"}
                </button>
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
                onRequestMockRender={requestMockRender}
                onRequestRenderClip={requestRenderClipAtCurrentTick}
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
                <span>{videoUnavailable ? "视频暂时无法播放，已切换到战术回放。" : "当前时刻使用战术回放，可按需生成第一人称视频。"}</span>
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
                onGenerateClip={requestRenderClipForEvent}
              />
            </div>
            <details className="review-inspector">
              <summary><span>高级工具</span><small>视频校准、生成记录与技术详情</small></summary>
              <div className="review-inspector-content">
              <button className="secondary-button compact-button" type="button" disabled={renderRequesting}
                onClick={() => void requestMockRender()}>{renderRequesting ? "提交中" : "创建模拟视频任务（开发测试）"}</button>
            <section className="review-support-bay" aria-label="Render and calibration support">
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
                calibrationDisabled={Boolean(replay.video.renderJobId)}
                onSaveCalibration={saveManualVideoCalibration}
                onUploadVideo={uploadManualVideo}
              />
            </section>
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

function ReviewCommandBar({
  mapName,
  selectedRound,
  currentTick,
  roundTime,
  currentPovName,
  playing,
  speed,
  previousFinding,
  nextFinding,
  onTogglePlay,
  onSpeedChange,
  onPreviousFinding,
  onNextFinding
}: {
  mapName: string;
  selectedRound: number;
  currentTick: number;
  roundTime: string;
  currentPovName: string;
  playing: boolean;
  speed: number;
  previousFinding: CoachingEvent | null;
  nextFinding: CoachingEvent | null;
  onTogglePlay: () => void;
  onSpeedChange: (speed: number) => void;
  onPreviousFinding: () => void;
  onNextFinding: () => void;
}) {
  return (
    <section className="review-command-bar" aria-label="Review transport" title={`${mapName} · tick ${Math.round(currentTick)}`}>
      <div className="review-command-coordinate">
        <div className="review-transport-readouts">
          <TransportReadout label="回合" value={`第 ${selectedRound} 回合`} />
          <TransportReadout label="时间" value={roundTime} emphasis />
          <TransportReadout label="玩家" value={currentPovName} />
        </div>
      </div>
      <div className="review-command-actions">
        <button
          className="primary-button compact-button coordinate-play-button"
          type="button"
          onClick={onTogglePlay}
          aria-label={playing ? "Pause replay" : "Play replay"}
        >
          {playing ? <Pause size={17} /> : <Play size={17} />}
          <span>{playing ? "暂停" : "播放"}</span>
        </button>
        <label className="review-speed-control">
          <span>倍速</span>
          <select
            className="speed-select"
            value={speed}
            onChange={(event) => onSpeedChange(Number(event.target.value))}
            aria-label="Playback speed"
          >
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={4}>4x</option>
          </select>
        </label>
        <div className="review-finding-navigation" aria-label="Finding navigation">
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onPreviousFinding}
            disabled={!previousFinding}
            title={previousFinding ? "查看上一条建议" : "已经是第一条建议"}
          >
            <ChevronLeft size={15} aria-hidden="true" />
            <span>上一条</span>
          </button>
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onNextFinding}
            disabled={!nextFinding}
            title={nextFinding ? "查看下一条建议" : "没有下一条建议"}
          >
            <span>下一条</span>
            <ChevronRight size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
    </section>
  );
}

function TransportReadout({
  label,
  value,
  emphasis = false
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <span className={`review-transport-readout ${emphasis ? "emphasis" : ""}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function DetailSummary({ items }: { items: DetailSummaryItem[] }) {
  return (
    <section className="detail-summary-strip" aria-label="Demo status summary">
      {items.map((item) => (
        <div className={`detail-summary-item ${item.tone ?? "default"}`} key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          {item.detail ? <small>{item.detail}</small> : null}
        </div>
      ))}
    </section>
  );
}

function ReplayDiagnosticsPanel({ diagnostics }: { diagnostics: ReplayDetailDiagnostics }) {
  return (
    <section className="panel replay-diagnostics-panel" aria-label="Replay contract diagnostics">
      <div className="replay-diagnostics-header">
        <div>
          <h2>Replay Contract</h2>
          <p>
            {diagnostics.normalizedLegacy
              ? "Legacy or degraded contract normalized for review"
              : "Contract data loaded"}
          </p>
        </div>
        <span className={`mini-pill replay-contract-version ${diagnostics.normalizedLegacy ? "legacy" : "current"}`}>
          {diagnostics.contractVersion}
        </span>
      </div>
      <div className="replay-diagnostics-grid">
        <DiagnosticMetric label="Parser events" value={diagnostics.counts.parserEvents} />
        <DiagnosticMetric label="Coaching" value={diagnostics.counts.coachingEvents} />
        <DiagnosticMetric label="Rounds" value={diagnostics.counts.rounds} />
        <DiagnosticMetric label="Players" value={diagnostics.counts.players} />
        <DiagnosticMetric label="Frames" value={diagnostics.counts.frames} />
        <DiagnosticMetric label="Render" value={diagnostics.renderState.label} tone={diagnostics.renderState.tone} />
      </div>
      {diagnostics.warnings.length > 0 ? (
        <ul className="replay-diagnostics-warnings">
          {diagnostics.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function DiagnosticMetric({
  label,
  value,
  tone
}: {
  label: string;
  value: number | string;
  tone?: ReplayDetailDiagnostics["renderState"]["tone"];
}) {
  return (
    <div className={`diagnostic-metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function findRoundForTick(rounds: ReplayData["rounds"], tick: number) {
  return rounds.find((round) => tick >= round.startTick && tick <= round.endTick);
}
