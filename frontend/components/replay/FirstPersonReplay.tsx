"use client";

import { Crosshair, RadioTower, Video } from "lucide-react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef
} from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import type { RenderJobStatus, RenderWorkerStatus } from "@/lib/api";
import { isRenderActiveStatus } from "@/lib/demo-library";
import { resolvePrivateMediaSource } from "@/lib/media-url";
import {
  RENDER_WORKER_OFFLINE_DETAIL as RENDER_OFFLINE_DETAIL,
  RENDER_WORKER_OFFLINE_LABEL as RENDER_OFFLINE_LABEL,
  renderWorkerOffline
} from "@/lib/render-worker";
import { formatRoundTime, tickToVideoTime, videoMediaIdentity, videoTimeRange, videoTimeToTick, type VideoPlaybackState } from "@/lib/replay-time";
import { renderFailureMessage } from "@/lib/user-errors";
import type { ReplayData, ReplayFrame } from "@/types/replay";

const VIDEO_TICK_PUBLISH_STEP = 2;

interface FirstPersonReplayProps {
  replay: ReplayData;
  currentTick: number;
  playing: boolean;
  speed: number;
  playbackState: VideoPlaybackState;
  mediaUnavailable: boolean;
  renderRequesting: boolean;
  renderClipRequesting: boolean;
  latestRenderClipJob: RenderJobStatus | null;
  currentTickClipJob?: RenderJobStatus | null;
  renderClipPlayerSelected?: boolean;
  renderWorker?: RenderWorkerStatus | null;
  compact?: boolean;
  showDevActions?: boolean;
  onRequestMockRender: () => void;
  // Omitted when render clips are off, which hides the clip button.
  onRequestRenderClip?: () => void;
  onVideoTickChange: (tick: number) => void;
  onVideoUnavailable: (identity: string) => void;
  onViewVideoClip: () => void;
  onVideoDurationChange?: (durationSeconds: number) => void;
  onVideoTimeChange?: (seconds: number) => void;
}

export interface FirstPersonReplayHandle {
  seekToTick: (tick: number) => void;
}

export const FirstPersonReplay = forwardRef<FirstPersonReplayHandle, FirstPersonReplayProps>(function FirstPersonReplay({
  replay,
  currentTick,
  playing,
  speed,
  playbackState,
  mediaUnavailable,
  renderRequesting,
  renderClipRequesting,
  latestRenderClipJob,
  currentTickClipJob,
  renderClipPlayerSelected = true,
  renderWorker = null,
  compact = false,
  showDevActions = false,
  onRequestMockRender,
  onRequestRenderClip,
  onVideoTickChange,
  onVideoUnavailable,
  onViewVideoClip,
  onVideoDurationChange,
  onVideoTimeChange
}: FirstPersonReplayProps, ref) {
  const { refreshSession } = useAuth();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastSyncedTickRef = useRef<number | null>(null);
  const pendingSeekTickRef = useRef<number | null>(null);
  const feedbackAllowedRef = useRef(false);
  const frame = useMemo(() => getFrameForTick(replay.frames, currentTick), [currentTick, replay.frames]);
  const mediaSource = resolvePrivateMediaSource(replay.video.url);
  const videoSource = mediaSource?.src ?? null;
  const activeVideoSource = playbackState === "active" && !mediaUnavailable ? videoSource : null;
  const mediaIdentity = videoMediaIdentity(replay.video);
  const recordedPlayer = replay.players.find((player) => player.id === replay.video.povSteamId);
  const povLabel = replay.video.povSteamId ? `${recordedPlayer?.name ?? replay.video.povSteamId} 的视角` : "玩家视角未确认";
  const invalidMediaReference = Boolean(replay.video.url && !mediaSource);
  const timeRange = videoTimeRange(replay.video);
  const videoTime = tickToVideoTime(currentTick, replay.video);
  const clipJobBusy =
    renderClipRequesting ||
    isRenderActiveStatus(currentTickClipJob?.status);
  const tickClipReady = currentTickClipJob?.status === "completed" && currentTickClipJob.video?.status === "ready" && Boolean(currentTickClipJob.video.url);
  const clipWorkerOffline = renderWorkerOffline(renderWorker) && currentTickClipJob?.status === "queued";
  const progress = Math.min(
    1,
    Math.max(0, (videoTime - timeRange.start) / Math.max(1, timeRange.end - timeRange.start))
  );

  const seekVideoToTick = useCallback((tick: number) => {
    if (!activeVideoSource || tick < replay.video.tickStart || tick >= replay.video.tickEnd) {
      feedbackAllowedRef.current = false;
      videoRef.current?.pause();
      return;
    }

    const nextVideoTime = tickToVideoTime(tick, replay.video);
    const nextVideoTick = videoTimeToTick(nextVideoTime, replay.video);
    pendingSeekTickRef.current = nextVideoTick;
    lastSyncedTickRef.current = nextVideoTick;
    feedbackAllowedRef.current = true;

    const element = videoRef.current;
    if (!element) {
      return;
    }

    try {
      element.currentTime = nextVideoTime;
      const appliedTick = videoTimeToTick(element.currentTime, replay.video);
      if (Math.abs(appliedTick - nextVideoTick) <= 1) {
        pendingSeekTickRef.current = null;
      }
      onVideoTimeChange?.(element.currentTime);
    } catch {
      // Metadata load will apply the pending seek before video feedback is accepted.
    }
  }, [activeVideoSource, onVideoTimeChange, replay.video]);

  useImperativeHandle(ref, () => ({ seekToTick: seekVideoToTick }), [seekVideoToTick]);

  // A paused video emits no timeupdate, so a listener that attaches later gets the current position once.
  useEffect(() => {
    const element = videoRef.current;
    if (!onVideoTimeChange || !element || !activeVideoSource) return;
    onVideoTimeChange(element.currentTime);
  }, [activeVideoSource, onVideoTimeChange]);

  const finishVideoClip = useCallback(() => {
    if (!feedbackAllowedRef.current || !activeVideoSource) return;
    feedbackAllowedRef.current = false;
    videoRef.current?.pause();
    onVideoTickChange(replay.video.tickEnd);
  }, [activeVideoSource, onVideoTickChange, replay.video.tickEnd]);

  const publishVideoTime = useCallback((nextVideoTime: number) => {
    if (!activeVideoSource || !feedbackAllowedRef.current) return;
    onVideoTimeChange?.(nextVideoTime);
    if (nextVideoTime >= videoTimeRange(replay.video).end) {
      finishVideoClip();
      return;
    }
    const nextTick = Math.min(replay.video.tickEnd - 1, videoTimeToTick(nextVideoTime, replay.video));
    const pendingSeekTick = pendingSeekTickRef.current;

    if (pendingSeekTick !== null) {
      if (Math.abs(nextTick - pendingSeekTick) > 1) {
        return;
      }
      pendingSeekTickRef.current = null;
    }

    // About 30 updates a second at 64 tick: enough for the map and clock, without a page commit every frame.
    const lastSyncedTick = lastSyncedTickRef.current;
    if (lastSyncedTick === null || Math.abs(nextTick - lastSyncedTick) >= VIDEO_TICK_PUBLISH_STEP) {
      lastSyncedTickRef.current = nextTick;
      onVideoTickChange(nextTick);
    }
  }, [activeVideoSource, finishVideoClip, onVideoTickChange, onVideoTimeChange, replay.video]);

  useEffect(() => {
    lastSyncedTickRef.current = null;
    pendingSeekTickRef.current = null;
    feedbackAllowedRef.current = false;
  }, [activeVideoSource, mediaIdentity]);

  // Read at play/pause time only, so this effect does not re-run on every published tick.
  const currentTickRef = useRef(currentTick);
  currentTickRef.current = currentTick;

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !activeVideoSource) {
      return;
    }

    if (lastSyncedTickRef.current === null) {
      seekVideoToTick(currentTickRef.current);
    }
    element.playbackRate = speed;
    if (playing && element.paused) {
      void element.play().catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        onVideoUnavailable(mediaIdentity);
      });
    } else if (!playing) {
      element.pause();
    }
  }, [activeVideoSource, mediaIdentity, onVideoUnavailable, playing, seekVideoToTick, speed]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !activeVideoSource || playing) {
      return;
    }

    if (Math.abs(element.currentTime - videoTime) > 0.35) {
      seekVideoToTick(currentTick);
    }
  }, [activeVideoSource, currentTick, playing, seekVideoToTick, videoTime]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !activeVideoSource || !playing) {
      return;
    }

    let animationFrameId = 0;
    const syncTickFromVideo = () => {
      publishVideoTime(element.currentTime);
      animationFrameId = window.requestAnimationFrame(syncTickFromVideo);
    };

    animationFrameId = window.requestAnimationFrame(syncTickFromVideo);
    return () => window.cancelAnimationFrame(animationFrameId);
  }, [activeVideoSource, playing, publishVideoTime]);

  return (
    <section className={`panel first-person-panel ${compact ? "compact" : ""}`} aria-label="第一人称视频">
      {!compact ? <div className="first-person-header">
        <div>
          <h2>第一人称视频</h2>
          <span>
            {activeVideoSource ? `${videoLabel(replay.video.source)}：${povLabel}` : "战术回放可用"}，{formatTime(videoTime)}
          </span>
        </div>
        <div className="render-actions">
          {videoSource && replay.video.status === "ready" ? (
            <button
              className="secondary-button compact-button"
              type="button"
              onClick={onViewVideoClip}
              disabled={Boolean(replay.video.povSteamId && !recordedPlayer)}
              title={`观看 ${povLabel}`}
            >
              <Video size={14} />
              观看{recordedPlayer?.name ? ` ${recordedPlayer.name} ` : ""}视频
            </button>
          ) : null}
          <span className={`mini-pill video-status-pill ${replay.video.status}`}>
            <Video size={13} />
            {isMockVideoPlaceholder(replay.video) ? "战术回放" : statusLabel(replay.video.status)}
          </span>
          {latestRenderClipJob && isRenderActiveStatus(latestRenderClipJob.status) ? (
            <span
              className={`mini-pill clip-job-pill ${latestRenderClipJob.status}`}
              title="正在生成新的视频，已有视频仍可观看"
            >
              {statusLabel(latestRenderClipJob.status)}
            </span>
          ) : null}
          {onRequestRenderClip ? <button
            className="secondary-button compact-button"
            type="button"
            onClick={onRequestRenderClip}
            disabled={clipJobBusy || !renderClipPlayerSelected}
            title={clipWorkerOffline ? RENDER_OFFLINE_DETAIL : tickClipReady ? "观看当前时刻已保存的视频" : "生成当前时刻的第一人称视频，完成后可重复观看"}
          >
            <Video size={14} aria-hidden="true" />
            {renderClipRequesting ? "正在提交…" : tickClipReady ? "观看这一刻的视频" : clipWorkerOffline ? RENDER_OFFLINE_LABEL : isRenderActiveStatus(currentTickClipJob?.status) ? statusLabel(currentTickClipJob?.status ?? "queued") : "生成这一刻的视频"}
          </button> : null}
          {showDevActions ? <button
            className="secondary-button compact-button"
            type="button"
            onClick={onRequestMockRender}
            disabled={renderRequesting || isRenderActiveStatus(replay.video.status)}
            title="创建用于测试的模拟任务"
          >
            模拟视频任务
          </button> : null}
        </div>
      </div> : null}

      <div className="first-person-viewport">
        {activeVideoSource ? (
          <video
            key={mediaIdentity}
            ref={videoRef}
            className="first-person-video"
            src={activeVideoSource}
            crossOrigin={mediaSource?.crossOrigin}
            muted
            playsInline
            preload="metadata"
            onError={() => {
              feedbackAllowedRef.current = false;
              onVideoUnavailable(mediaIdentity);
              void refreshSession();
            }}
            onLoadedMetadata={(event) => {
              const duration = event.currentTarget.duration;
              if (Number.isFinite(duration) && duration > 0) {
                onVideoDurationChange?.(duration);
              }
              seekVideoToTick(currentTick);
            }}
            onTimeUpdate={(event) => {
              publishVideoTime(event.currentTarget.currentTime);
            }}
            onEnded={finishVideoClip}
          />
        ) : (
          frame ? (
            <MockFirstPersonFrame frame={frame} progress={progress} />
          ) : (
            <EmptyFirstPersonFrame />
          )
        )}

        <div className="first-person-hud">
          {activeVideoSource && !replay.video.povSteamId ? (
            <span className="hud-chip">视角未确认</span>
          ) : null}
          {frame ? (
            <span className="hud-chip">
              <RadioTower size={13} />
              第 {frame.roundNumber} 回合
            </span>
          ) : null}
          <span className="hud-chip">{speed}x</span>
          <span className="hud-chip">{playing ? "播放中" : "已暂停"}</span>
        </div>
        <RenderStatusOverlay
          video={replay.video}
          mediaUnavailable={mediaUnavailable || invalidMediaReference}
          hasFrames={frame !== null}
          playbackState={playbackState}
          povLabel={povLabel}
        />
        <div className="video-progress" aria-hidden="true">
          <span style={{ width: `${progress * 100}%` }} />
        </div>
      </div>
    </section>
  );
});

function RenderStatusOverlay({
  hasFrames,
  mediaUnavailable,
  video,
  playbackState,
  povLabel
}: {
  hasFrames: boolean;
  mediaUnavailable: boolean;
  video: ReplayData["video"];
  playbackState: VideoPlaybackState;
  povLabel: string;
}) {
  if (mediaUnavailable) {
    return (
      <div className="render-status-overlay failed">
        <span>视频暂不可用</span>
        <strong>未能加载这段视频</strong>
        <p>可以继续使用战术回放，或重新生成这段视频。</p>
      </div>
    );
  }

  if (video.status === "ready" && video.url) {
    if (playbackState === "active") return null;
    return (
      <div className="render-status-overlay pending">
        <span>第一人称视频：{povLabel}</span>
        <strong>{playbackState === "different-player" ? "这段视频来自其他玩家的视角" : "当前时刻不在视频范围内"}</strong>
        <p>
          这段视频长 {formatTime((video.tickEnd - video.tickStart) / video.tickRate)}。继续使用战术回放，或从已保存的视频中观看。
        </p>
      </div>
    );
  }

  if (isMockVideoPlaceholder(video)) {
    return (
      <div className="render-status-overlay pending">
        <span>第一人称视频</span>
        <strong>{hasFrames ? "战术回放已就绪" : "暂无回放视频"}</strong>
        <p>
          尚未生成第一人称视频。
          {hasFrames ? "可先用战术地图、回合和时间轴开始复盘。" : "该比赛暂未提供可用的位置数据。"}
        </p>
      </div>
    );
  }

  const overlayCopy: Record<string, { heading: string; body: string }> = {
    pending: {
      heading: "尚未生成视频",
      body: "选择值得复盘的一刻，点击生成视频。战术回放现在即可使用。"
    },
    queued: {
      heading: "视频等待生成",
      body: "完成后会保存到已保存的视频。你可以继续使用战术回放。"
    },
    processing: {
      heading: "正在生成视频",
      body: "完成后会保存到已保存的视频。你可以继续复盘其他时刻。"
    },
    rendering: {
      heading: "正在生成视频",
      body: "完成后会保存到已保存的视频。你可以继续复盘其他时刻。"
    },
    ready: {
      heading: "视频文件尚未就绪",
      body: "任务已结束，但视频文件暂不可用。可稍后刷新，或继续使用战术回放。"
    },
    failed: {
      heading: "视频生成未完成",
      body: `${renderFailureMessage(video.errorCode)}战术回放仍可正常使用。`
    }
  };

  const copy = overlayCopy[video.status] ?? {
    heading: "暂无回放视频",
    body: "可以使用战术回放，或生成当前时刻的视频。"
  };

  return (
    <div className={`render-status-overlay ${video.status}`}>
      <span>回放视频</span>
      <strong>{copy.heading}</strong>
      <p>{copy.body}</p>
    </div>
  );
}

function isMockVideoPlaceholder(video: ReplayData["video"]): boolean {
  return video.source === "mock" && video.status === "ready" && !video.url;
}

function EmptyFirstPersonFrame() {
  return (
    <div className="mock-fps-frame empty-replay-frame">
      <strong>暂无位置数据</strong>
      <p>这场比赛暂未提供可用的玩家位置。</p>
    </div>
  );
}

function MockFirstPersonFrame({
  frame,
  progress
}: {
  frame: ReplayFrame;
  progress: number;
}) {
  const aliveEnemies = frame.players.filter((player) => player.side === "CT" && player.alive).length;
  const strafeOffset = Math.sin(progress * Math.PI * 12) * 24;
  const recoilOffset = Math.max(0, Math.sin(progress * Math.PI * 8)) * 10;

  return (
    <div className="mock-fps-frame">
      <div className="mock-fps-sky" />
      <div className="mock-fps-corridor">
        <div className="mock-wall mock-wall-left" />
        <div className="mock-wall mock-wall-right" />
        <div className="mock-site-callout">模拟场景</div>
      </div>
      <div
        className="mock-enemy-silhouette"
        style={{ transform: `translate(${strafeOffset}px, ${recoilOffset}px)` }}
      />
      <div className="mock-weapon">
        <div className="mock-rifle" />
        <div className="mock-hand" />
      </div>
      <div className="mock-crosshair" style={{ transform: `translateY(${recoilOffset * -0.4}px)` }}>
        <Crosshair size={44} strokeWidth={1.6} />
      </div>
      <div className="mock-render-label">模拟占位画面，非真实游戏画面</div>
      <div className="mock-fps-stats">
        <span>CT 存活：{aliveEnemies}</span>
      </div>
    </div>
  );
}

function getFrameForTick(frames: ReplayFrame[], tick: number): ReplayFrame | null {
  if (frames.length === 0) {
    return null;
  }
  let selected = frames[0];
  for (const frame of frames) {
    if (frame.tick > tick) {
      break;
    }
    selected = frame;
  }
  return selected;
}

function videoLabel(source: ReplayData["video"]["source"]): string {
  return source === "manual_upload" ? "导入的视频" : "已保存视频";
}

function statusLabel(status: string): string {
  return ({ pending: "等待生成", queued: "等待生成", processing: "生成中", rendering: "生成中", ready: "可以观看", completed: "已完成", failed: "生成失败" } as Record<string, string>)[status] ?? "状态待更新";
}

const formatTime = formatRoundTime;
