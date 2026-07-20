"use client";

import { Crosshair, RadioTower, Scissors, Video } from "lucide-react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState
} from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import type { RenderJobStatus } from "@/lib/api";
import { friendlyErrorMessage, isRenderActiveStatus } from "@/lib/demo-library";
import { resolvePrivateMediaSource } from "@/lib/media-url";
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
  onVideoTickChange: (tick: number) => void;
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
  renderRequesting,
  renderClipRequesting,
  latestRenderClipJob,
  onRequestMockRender,
  onRequestRenderClip,
  onVideoTickChange,
  onVideoDurationChange,
  onVideoTimeChange
}: FirstPersonReplayProps, ref) {
  const { refreshSession } = useAuth();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastSyncedTickRef = useRef<number | null>(null);
  const pendingSeekTickRef = useRef<number | null>(null);
  const pendingSeekTimeRef = useRef<number | null>(null);
  const [mediaUnavailable, setMediaUnavailable] = useState(false);
  const frame = useMemo(() => getFrameForTick(replay.frames, currentTick), [currentTick, replay.frames]);
  const mediaSource = resolvePrivateMediaSource(replay.video.url);
  const videoSource = mediaSource?.src ?? null;
  const activeVideoSource = mediaUnavailable ? null : videoSource;
  const invalidMediaReference = Boolean(replay.video.url && !mediaSource);
  const timeRange = videoTimeRange(replay.video);
  const videoTime = tickToVideoTime(currentTick, replay.video);
  const clipJobBusy =
    renderClipRequesting ||
    isRenderActiveStatus(latestRenderClipJob?.status);
  const progress = Math.min(
    1,
    Math.max(0, (videoTime - timeRange.start) / Math.max(1, timeRange.end - timeRange.start))
  );

  const seekVideoToTick = useCallback((tick: number) => {
    if (!activeVideoSource) {
      return;
    }

    const nextVideoTime = tickToVideoTime(tick, replay.video);
    const nextVideoTick = videoTimeToTick(nextVideoTime, replay.video);
    pendingSeekTickRef.current = nextVideoTick;
    pendingSeekTimeRef.current = nextVideoTime;
    lastSyncedTickRef.current = nextVideoTick;

    const element = videoRef.current;
    if (!element) {
      return;
    }

    try {
      element.currentTime = nextVideoTime;
      pendingSeekTimeRef.current = null;
      const appliedTick = videoTimeToTick(element.currentTime, replay.video);
      if (Math.abs(appliedTick - nextVideoTick) <= 1) {
        pendingSeekTickRef.current = null;
      } else if (playing) {
        pendingSeekTickRef.current = null;
      }
      onVideoTimeChange?.(element.currentTime);
    } catch {
      // Metadata load will apply the pending seek before video feedback is accepted.
    }
  }, [activeVideoSource, onVideoTimeChange, playing, replay.video]);

  useImperativeHandle(ref, () => ({ seekToTick: seekVideoToTick }), [seekVideoToTick]);

  const publishVideoTime = useCallback((nextVideoTime: number) => {
    onVideoTimeChange?.(nextVideoTime);
    const nextTick = videoTimeToTick(nextVideoTime, replay.video);
    const pendingSeekTick = pendingSeekTickRef.current;

    if (pendingSeekTick !== null) {
      if (Math.abs(nextTick - pendingSeekTick) > 1) {
        return;
      }
      pendingSeekTickRef.current = null;
    }

    const lastSyncedTick = lastSyncedTickRef.current;
    if (lastSyncedTick === null || Math.abs(nextTick - lastSyncedTick) >= 1) {
      lastSyncedTickRef.current = nextTick;
      onVideoTickChange(nextTick);
    }
  }, [onVideoTickChange, onVideoTimeChange, replay.video]);

  useEffect(() => {
    onVideoTimeChange?.(videoTime);
  }, [onVideoTimeChange, videoTime]);

  useEffect(() => {
    setMediaUnavailable(false);
    lastSyncedTickRef.current = null;
    pendingSeekTickRef.current = null;
    pendingSeekTimeRef.current = null;
  }, [videoSource]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !activeVideoSource) {
      return;
    }

    element.playbackRate = speed;
    if (playing) {
      if (element.readyState >= HTMLMediaElement.HAVE_METADATA) {
        pendingSeekTickRef.current = null;
      }
      void element.play();
    } else {
      element.pause();
    }
  }, [activeVideoSource, playing, speed]);

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
    <section className="panel first-person-panel" aria-label="First-person replay player">
      <div className="first-person-header">
        <div>
          <h2>First-person Replay</h2>
          <span>
            {activeVideoSource ? videoLabel(replay.video.source) : "Mock playback shell — not real CS2 video"} /{" "}
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
        {activeVideoSource ? (
          <video
            ref={videoRef}
            className="first-person-video"
            src={activeVideoSource}
            crossOrigin={mediaSource?.crossOrigin}
            muted
            playsInline
            preload="metadata"
            onError={() => {
              void refreshSession().then((authenticated) => {
                if (authenticated) {
                  setMediaUnavailable(true);
                }
              });
            }}
            onLoadedMetadata={(event) => {
              setMediaUnavailable(false);
              const duration = event.currentTarget.duration;
              if (Number.isFinite(duration) && duration > 0) {
                onVideoDurationChange?.(duration);
              }
              const pendingSeekTime = pendingSeekTimeRef.current;
              if (pendingSeekTime !== null) {
                event.currentTarget.currentTime = pendingSeekTime;
                pendingSeekTimeRef.current = null;
                const pendingSeekTick = pendingSeekTickRef.current;
                if (pendingSeekTick !== null) {
                  const appliedTick = videoTimeToTick(event.currentTarget.currentTime, replay.video);
                  if (Math.abs(appliedTick - pendingSeekTick) <= 1) {
                    pendingSeekTickRef.current = null;
                  }
                }
                onVideoTimeChange?.(event.currentTarget.currentTime);
              }
            }}
            onTimeUpdate={(event) => {
              publishVideoTime(event.currentTarget.currentTime);
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
        <RenderStatusOverlay
          video={replay.video}
          mediaUnavailable={mediaUnavailable || invalidMediaReference}
        />
        <div className="video-progress" aria-hidden="true">
          <span style={{ width: `${progress * 100}%` }} />
        </div>
      </div>
    </section>
  );
});

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
        <span>Video unavailable</span>
        <strong>Can&apos;t load this video</strong>
        <p>The replay video couldn&apos;t be reached. You&apos;re watching the 2D tactical replay below — upload a video or regenerate the render to try again.</p>
      </div>
    );
  }

  if (video.status === "ready" && video.url) {
    return null;
  }

  const overlayCopy: Record<string, { heading: string; body: string }> = {
    pending: {
      heading: "No replay video yet",
      body: "This demo hasn't been rendered to video. Use Generate Tick Clip above to render a moment, or upload your own video below. The 2D tactical replay is ready to watch now."
    },
    queued: {
      heading: "Render queued",
      body: "A replay video is waiting to be generated. This view updates automatically when it's ready. Meanwhile, follow the action in the 2D tactical replay below."
    },
    processing: {
      heading: "Rendering video",
      body: "Your replay video is being generated. This view switches to it automatically when it finishes. Keep reviewing in the 2D tactical replay below."
    },
    rendering: {
      heading: "Rendering video",
      body: "Your replay video is being generated. This view switches to it automatically when it finishes. Keep reviewing in the 2D tactical replay below."
    },
    ready: {
      heading: "Video almost ready",
      body: "The render finished but the video isn't attached yet. Refresh render state below, or keep watching the 2D tactical replay."
    },
    failed: {
      heading: "Render didn't finish",
      body: video.errorMessage
        ? friendlyErrorMessage(video.errorMessage)
        : "The video couldn't be generated. Try Generate Tick Clip again or upload your own video below. The 2D tactical replay still works."
    }
  };

  const copy = overlayCopy[video.status] ?? {
    heading: "No replay video yet",
    body: "Watch the 2D tactical replay below, or generate a video from the controls above."
  };

  return (
    <div className={`render-status-overlay ${video.status}`}>
      <span>Replay video</span>
      <strong>{copy.heading}</strong>
      <p>{copy.body}</p>
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
      <div className="mock-render-label">Mock render shell · not gameplay capture</div>
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
