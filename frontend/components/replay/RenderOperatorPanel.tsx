"use client";

import { AlertTriangle, CheckCircle2, Clock3, RefreshCw, Wrench } from "lucide-react";

import type { RenderJobStatus } from "@/lib/api";
import type { ReplayVideo } from "@/types/replay";

interface RenderOperatorPanelProps {
  video: ReplayVideo;
  latestJob: RenderJobStatus | null;
  jobCount: number;
  refreshing: boolean;
  onRefresh: () => void;
}

export function RenderOperatorPanel({
  video,
  latestJob,
  jobCount,
  refreshing,
  onRefresh
}: RenderOperatorPanelProps) {
  const request = latestJob ? renderRequest(latestJob) : null;
  const state = operatorState(video, latestJob);

  return (
    <section className="panel render-operator-panel" aria-label="Render operator status">
      <div className="render-operator-header">
        <div>
          <h2>Render Operator</h2>
          <span>{jobCount > 0 ? `${jobCount} render_clip jobs` : "No render_clip jobs"}</span>
        </div>
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={refreshing}
          onClick={onRefresh}
        >
          <RefreshCw size={14} />
          {refreshing ? "Refreshing" : "Refresh"}
        </button>
      </div>

      <div className="operator-state-row">
        <span className={`operator-state-icon ${state.tone}`}>
          {state.tone === "ready" ? (
            <CheckCircle2 size={16} />
          ) : state.tone === "failed" ? (
            <AlertTriangle size={16} />
          ) : state.tone === "waiting" ? (
            <Clock3 size={16} />
          ) : (
            <Wrench size={16} />
          )}
        </span>
        <div>
          <strong>{state.label}</strong>
          <p>{state.nextAction}</p>
        </div>
      </div>

      <dl className="operator-metadata-grid">
        <div>
          <dt>Latest job</dt>
          <dd>{latestJob ? `${latestJob.job_type} / ${latestJob.status}` : "none"}</dd>
        </div>
        <div>
          <dt>Job id</dt>
          <dd>{latestJob ? latestJob.job_id.slice(0, 8) : "none"}</dd>
        </div>
        <div>
          <dt>Tick range</dt>
          <dd>{request ? `${request.tickStart} - ${request.tickEnd}` : "none"}</dd>
        </div>
        <div>
          <dt>Seconds</dt>
          <dd>{request ? formatSeconds(request.durationSeconds) : "none"}</dd>
        </div>
        <div>
          <dt>Event</dt>
          <dd>{latestJob?.event_id ?? fieldFromMetadata(latestJob, "eventId") ?? "none"}</dd>
        </div>
        <div>
          <dt>Player / POV</dt>
          <dd>{playerLabel(latestJob)}</dd>
        </div>
        <div>
          <dt>Video</dt>
          <dd>{videoLabel(video)}</dd>
        </div>
        <div>
          <dt>Output</dt>
          <dd className="metadata-url">{video.url ?? latestJob?.video_url ?? "not playable"}</dd>
        </div>
      </dl>

      {latestJob?.error_message || video.errorMessage ? (
        <div className="operator-error">
          {latestJob?.error_message ?? video.errorMessage}
        </div>
      ) : null}
    </section>
  );
}

function operatorState(video: ReplayVideo, latestJob: RenderJobStatus | null) {
  if (latestJob?.status === "failed" || (video.status === "failed" && latestJob?.status !== "completed")) {
    return {
      label: "Failed",
      nextAction: "Review the error and keep using the mock shell or manual upload until a worker completes a clip.",
      tone: "failed"
    } as const;
  }

  if (video.status === "ready" && video.source === "rendered" && video.url) {
    return {
      label: "Completed and playable",
      nextAction: "Rendered video is bound to replay metadata.",
      tone: "ready"
    } as const;
  }

  if (latestJob?.status === "queued") {
    return {
      label: "Queued",
      nextAction: "A render worker or operator can prepare this job from the render-worker runner.",
      tone: "waiting"
    } as const;
  }

  if (latestJob?.status === "rendering") {
    return {
      label: "Waiting for worker output",
      nextAction: "For manual probing, run prepare-job, place the MP4 at the expected output path, then complete it.",
      tone: "waiting"
    } as const;
  }

  if (video.source === "manual_upload" && video.url) {
    return {
      label: "Manual upload playable",
      nextAction: "Manual MP4 is available while render_clip output is pending.",
      tone: "ready"
    } as const;
  }

  return {
    label: "Mock shell active",
    nextAction: "Generate Clip creates a render_clip job for a worker/operator to complete.",
    tone: "idle"
  } as const;
}

function renderRequest(job: RenderJobStatus) {
  const tickStart = job.tick_start ?? numberFromMetadata(job, "tickStart");
  const tickEnd = job.tick_end ?? numberFromMetadata(job, "tickEnd");
  const tickRate = job.tick_rate ?? numberFromMetadata(job, "tickRate") ?? 64;
  if (tickStart === null || tickEnd === null) {
    return null;
  }
  const durationSeconds = job.duration_seconds ?? Math.max(0, (tickEnd - tickStart) / Math.max(1, tickRate));
  return { tickStart, tickEnd, durationSeconds };
}

function playerLabel(job: RenderJobStatus | null): string {
  if (!job) {
    return "none";
  }
  return (
    job.pov_steam_id ??
    fieldFromMetadata(job, "povSteamId") ??
    job.player_id ??
    fieldFromMetadata(job, "playerId") ??
    "operator-selected"
  );
}

function videoLabel(video: ReplayVideo): string {
  if (video.source === "manual_upload") {
    return `manual_upload / ${video.status}`;
  }
  if (video.source === "rendered") {
    return `rendered / ${video.status}`;
  }
  return `mock shell / ${video.status}`;
}

function numberFromMetadata(job: RenderJobStatus, key: string): number | null {
  const value = job.metadata[key];
  return typeof value === "number" ? value : null;
}

function fieldFromMetadata(job: RenderJobStatus | null, key: string): string | null {
  const value = job?.metadata[key];
  return typeof value === "string" ? value : null;
}

function formatSeconds(seconds: number): string {
  return `${Number(seconds || 0).toFixed(2)}s`;
}
