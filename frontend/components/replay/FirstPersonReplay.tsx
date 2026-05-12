"use client";

import { Crosshair, RadioTower, Scissors, Video } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { RenderJobStatus } from "@/lib/api";
import { friendlyErrorMessage, isRenderActiveStatus } from "@/lib/demo-library";
import { resolveMediaUrl } from "@/lib/media-url";
import { tickToVideoTime, videoTimeRange, videoTimeToTick } from "@/lib/replay-time";
import type { ReplayData, ReplayFrame } from "@/types/replay";

interface FirstPersonReplayProps {
  replay: ReplayData;
  currentTick: number;
  playing: boolean;
  speed: number;
  renderRequesting: boolean;
  renderClipRequesting: boolean;
  latestRenderClipJob: RenderJobStatus | null;
  onRequestMockRender: () => void;
  onRequestRenderClip: () => void;
  onSeekTick: (tick: number) => void;
  onVideoDurationChange?: (durationSeconds: number) => void;
  onVideoTimeChange?: (seconds: number) => void;
}

export function FirstPersonReplay({
  replay,
  currentTick,
  playing,
  speed,
  renderRequesting,
  renderClipRequesting,
  latestRenderClipJob,
  onRequestMockRender,
  onRequestRenderClip,
  onSeekTick,
  onVideoDurationChange,
  onVideoTimeChange
}: FirstPersonReplayProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastSyncedTickRef = useRef<number | null>(null);
  const [mediaUnavailable, setMediaUnavailable] = useState(false);
  const frame = useMemo(() => getFrameForTick(replay.frames, currentTick), [currentTick, replay.frames]);
  const videoSource = resolveMediaUrl(replay.video.url);
  const timeRange = videoTimeRange(replay.video);
  const videoTime = tickToVideoTime(currentTick, replay.video);
  const clipJobBusy =
    renderClipRequesting ||
    isRenderActiveStatus(latestRenderClipJob?.status);
  const progress = Math.min(
    1,
    Math.max(0, (videoTime - timeRange.start) / Math.max(1, timeRange.end - timeRange.start))
  );

  useEffect(() => {
    onVideoTimeChange?.(videoTime);
  }, [onVideoTimeChange, videoTime]);

  useEffect(() => {
    setMediaUnavailable(false);
  }, [videoSource]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !videoSource) {
      return;
    }

    element.playbackRate = speed;
    if (Math.abs(element.currentTime - videoTime) > 0.35) {
      element.currentTime = videoTime;
    }

    if (playing) {
      void element.play();
    } else {
      element.pause();
    }
  }, [playing, speed, videoSource, videoTime]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !videoSource || !playing) {
      return;
    }

    let animationFrameId = 0;
    const syncTickFromVideo = () => {
      onVideoTimeChange?.(element.currentTime);
      const nextTick = videoTimeToTick(element.currentTime, replay.video);
      const lastSyncedTick = lastSyncedTickRef.current;
      if (lastSyncedTick === null || Math.abs(nextTick - lastSyncedTick) >= 1) {
        lastSyncedTickRef.current = nextTick;
        onSeekTick(nextTick);
      }
      animationFrameId = window.requestAnimationFrame(syncTickFromVideo);
    };

    animationFrameId = window.requestAnimationFrame(syncTickFromVideo);
    return () => window.cancelAnimationFrame(animationFrameId);
  }, [onSeekTick, onVideoTimeChange, playing, replay.video, videoSource]);

  return (
    <section className="panel first-person-panel" aria-label="First-person replay player">
      <div className="first-person-header">
        <div>
          <h2>First-person Replay</h2>
          <span>
            {videoSource ? videoLabel(replay.video.source) : "Mock first-person render"} /{" "}
            {formatTime(videoTime)} / Tick {Math.round(currentTick)}
          </span>
        </div>
        <div className="render-actions">
          <span className={`mini-pill video-status-pill ${replay.video.status}`}>
            <Video size={13} />
            {replay.video.status}
          </span>
          {latestRenderClipJob ? (
            <span
              className={`mini-pill clip-job-pill ${latestRenderClipJob.status}`}
              title={latestRenderClipJob.error_message ?? `Clip job ${latestRenderClipJob.status}`}
            >
              Clip {latestRenderClipJob.status}
            </span>
          ) : null}
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onRequestRenderClip}
            disabled={clipJobBusy}
            title="Create a first-person clip job around the selected tick"
          >
            <Scissors size={14} />
            {renderClipRequesting ? "Queuing" : "Generate Tick Clip"}
          </button>
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onRequestMockRender}
            disabled={renderRequesting || isRenderActiveStatus(replay.video.status)}
            title="Create a mock render job without running CS2"
          >
            Mock Render Job
          </button>
        </div>
      </div>

      <div className="first-person-viewport">
        {videoSource ? (
          <video
            ref={videoRef}
            className="first-person-video"
            src={videoSource}
            muted
            playsInline
            preload="metadata"
            onError={() => setMediaUnavailable(true)}
            onLoadedMetadata={(event) => {
              setMediaUnavailable(false);
              const duration = event.currentTarget.duration;
              if (Number.isFinite(duration) && duration > 0) {
                onVideoDurationChange?.(duration);
              }
            }}
            onTimeUpdate={(event) => {
              onVideoTimeChange?.(event.currentTarget.currentTime);
              const nextTick = videoTimeToTick(event.currentTarget.currentTime, replay.video);
              if (Math.abs(nextTick - currentTick) > 1) {
                lastSyncedTickRef.current = nextTick;
                onSeekTick(nextTick);
              }
            }}
          />
        ) : (
          frame ? (
            <MockFirstPersonFrame frame={frame} progress={progress} />
          ) : (
            <EmptyFirstPersonFrame />
          )
        )}

        <div className="first-person-hud">
          {frame ? (
            <span className="hud-chip">
              <RadioTower size={13} />
              R{frame.roundNumber}
            </span>
          ) : null}
          <span className="hud-chip">{speed}x</span>
          <span className="hud-chip">{playing ? "Playing" : "Paused"}</span>
        </div>
        <RenderStatusOverlay video={replay.video} mediaUnavailable={mediaUnavailable} />
        <div className="video-progress" aria-hidden="true">
          <span style={{ width: `${progress * 100}%` }} />
        </div>
      </div>
    </section>
  );
}

function RenderStatusOverlay({
  mediaUnavailable,
  video
}: {
  mediaUnavailable: boolean;
  video: ReplayData["video"];
}) {
  if (mediaUnavailable) {
    return (
      <div className="render-status-overlay failed">
        <span>{video.source}</span>
        <strong>media unavailable</strong>
        <p>The media URL could not be loaded. Check the API media route and keep using the synced mock shell.</p>
      </div>
    );
  }

  if (video.status === "ready" && video.url) {
    return null;
  }

  const messageByStatus: Record<string, string> = {
    pending: "No render job has been queued yet. The interactive mock shell remains synced.",
    queued: "Render job queued. A GPU worker would pick up the selected tick range later.",
    processing: "Render job processing. No CS2 client or recorder is running in this MVP.",
    rendering: "Render job in progress. No CS2 client or recorder is running in this MVP.",
    ready: "Render metadata is ready, but no video URL exists yet, so the mock shell stays active.",
    failed: video.errorMessage
      ? friendlyErrorMessage(video.errorMessage)
      : "Render job failed; the synced mock shell remains usable."
  };

  return (
    <div className={`render-status-overlay ${video.status}`}>
      <span>{video.source}</span>
      <strong>{video.status}</strong>
      <p>{messageByStatus[video.status]}</p>
    </div>
  );
}

function EmptyFirstPersonFrame() {
  return (
    <div className="mock-fps-frame empty-replay-frame">
      <strong>No frame data</strong>
      <p>Parser frame data is unavailable, so the mock first-person shell cannot draw player state.</p>
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
        <div className="mock-site-callout">A site contact</div>
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
      <div className="mock-render-label">Mock render shell</div>
      <div className="mock-fps-stats">
        <span>Alive CT: {aliveEnemies}</span>
        <span>Bomb: {frame.bombState.status}</span>
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
  return source === "manual_upload" ? "Manual video" : "Rendered video";
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}
