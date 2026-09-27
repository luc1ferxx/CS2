"use client";

import { memo } from "react";

import type { RenderJobStatus, RenderWorkerStatus } from "@/lib/api";
import { friendlyErrorMessage } from "@/lib/demo-library";
import { renderWorkerNotice } from "@/lib/render-worker";
import type { ReplayVideo } from "@/types/replay";

interface RenderOperatorPanelProps {
  video: ReplayVideo;
  latestJob: RenderJobStatus | null;
  renderWorker?: RenderWorkerStatus | null;
  jobCount: number;
  refreshing: boolean;
  onRefresh: () => void;
  // Job ids, ticks, POV ids and the manual-render CLI steps are for the
  // operator running the renderer; players only see the clip's state.
  devTools?: boolean;
}

type OperatorTone = "ready" | "failed" | "waiting" | "idle";

interface OperatorState {
  label: string;
  nextAction: string;
  operatorAction?: string;
  tone: OperatorTone;
}

const JOB_STATES: Record<string, string> = {
  queued: "排队中", processing: "处理中", rendering: "生成中", completed: "已完成", failed: "失败", ready: "可播放", pending: "未开始"
};

export const RenderOperatorPanel = memo(function RenderOperatorPanel({
  video,
  latestJob,
  renderWorker = null,
  jobCount,
  refreshing,
  onRefresh,
  devTools = false
}: RenderOperatorPanelProps) {
  const request = latestJob ? renderRequest(latestJob) : null;
  const state = operatorState(video, latestJob, renderWorker);
  const errorMessage = latestJob?.error_message ?? video.errorMessage;

  return (
    <section className="panel render-operator-panel" aria-label="视频生成状态">
      <div className="panel-bar render-operator-header">
        <h2 className="panel-bar-title">视频生成状态</h2>
        <span className="panel-bar-meta">{jobCount > 0 ? `共 ${jobCount} 个视频片段任务` : "还没有视频片段任务"}</span>
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={refreshing}
          onClick={onRefresh}
        >
          {refreshing ? "刷新中" : "刷新"}
        </button>
      </div>

      <div className="operator-state-row">
        <strong className={`operator-state-label ${state.tone}`}>{state.label}</strong>
        <p>{devTools && state.operatorAction ? state.operatorAction : state.nextAction}</p>
      </div>

      <dl className="operator-metadata-grid">
        <div>
          <dt>最近任务</dt>
          <dd>{latestJob ? jobStateLabel(latestJob.status) : "无"}</dd>
        </div>
        <div>
          <dt>片段时长</dt>
          <dd>{request ? `${formatSeconds(request.durationSeconds)} 秒` : "—"}</dd>
        </div>
        <div>
          <dt>视频</dt>
          <dd>{video.url ? "可以播放" : "暂不可播放"}</dd>
        </div>
        {devTools ? (
          <>
            <div>
              <dt>任务</dt>
              <dd>{latestJob ? `${latestJob.job_type} / ${latestJob.status}（${latestJob.job_id.slice(0, 8)}）` : "无"}</dd>
            </div>
            <div>
              <dt>Tick 范围</dt>
              <dd>{request ? `${request.tickStart} - ${request.tickEnd}` : "无"}</dd>
            </div>
            <div>
              <dt>建议 ID</dt>
              <dd>{latestJob?.event_id ?? fieldFromMetadata(latestJob, "eventId") ?? "无"}</dd>
            </div>
            <div>
              <dt>玩家 / POV</dt>
              <dd>{playerLabel(latestJob)}</dd>
            </div>
            <div>
              <dt>视频来源</dt>
              <dd>{videoLabel(video)}</dd>
            </div>
          </>
        ) : null}
      </dl>

      {devTools && errorMessage ? (
        <div className="operator-error">{friendlyErrorMessage(errorMessage)}</div>
      ) : null}
    </section>
  );
});

function operatorState(
  video: ReplayVideo,
  latestJob: RenderJobStatus | null,
  renderWorker: RenderWorkerStatus | null
): OperatorState {
  if (latestJob?.status === "failed" || (video.status === "failed" && latestJob?.status !== "completed")) {
    return {
      label: "生成失败",
      nextAction: "可以在对应的建议上重新生成这段视频。",
      operatorAction: "查看下方错误；诊断信息可以确认是本地无 GPU 回退还是外部渲染器失败。",
      tone: "failed"
    };
  }

  if (video.status === "ready" && video.source === "rendered" && video.url) {
    return {
      label: "已完成，可以播放",
      nextAction: "生成的视频已关联到这场比赛的回放。",
      tone: "ready"
    };
  }

  if (latestJob?.status === "queued") {
    const offline = renderWorkerNotice(renderWorker, latestJob);
    return {
      label: offline ? `排队中，${offline.label}` : "排队中",
      nextAction: offline?.detail ?? "等待开始生成，开始后这里会自动更新。",
      operatorAction: offline?.operatorDetail ?? "等待渲染器领取任务，领取后会自动开始。",
      tone: "waiting"
    };
  }

  if (latestJob?.status === "processing" || latestJob?.status === "rendering") {
    return {
      label: latestJob.status === "processing" ? "处理中" : "生成中",
      nextAction: "视频生成中，完成后会出现在已保存的视频里。",
      operatorAction: "手动验证：运行 prepare-job，把 MP4 放到预期的输出路径，再运行 complete-prepared-job。",
      tone: "waiting"
    };
  }

  if (video.source === "manual_upload" && video.url) {
    return {
      label: "手动上传的视频可以播放",
      nextAction: "生成的视频完成前，先使用手动上传的 MP4。",
      tone: "ready"
    };
  }

  return {
    label: "还没有生成视频",
    nextAction: "在建议上点击“生成视频”，即可生成那一刻的第一人称视频。",
    operatorAction: "回放里的“生成此刻视频”会创建 render_clip 任务，由渲染器或操作员完成。",
    tone: "idle"
  };
}

function jobStateLabel(status: string): string {
  return Object.prototype.hasOwnProperty.call(JOB_STATES, status) ? JOB_STATES[status] : status;
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
    return "无";
  }
  return (
    job.pov_steam_id ??
    fieldFromMetadata(job, "povSteamId") ??
    job.player_id ??
    fieldFromMetadata(job, "playerId") ??
    "由操作员选择"
  );
}

function videoLabel(video: ReplayVideo): string {
  if (video.source === "manual_upload") {
    return `manual_upload / ${video.status}`;
  }
  if (video.source === "rendered") {
    return `rendered / ${video.status}`;
  }
  return `mock / ${video.status}`;
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
  return Number(seconds || 0).toFixed(1).replace(/\.0$/, "");
}
