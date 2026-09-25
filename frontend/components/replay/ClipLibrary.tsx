"use client";

import { Play } from "lucide-react";
import { memo } from "react";

import type { RenderJobStatus } from "@/lib/api";
import { playableClipVideo } from "@/lib/render-clips";
import { formatRoundTime } from "@/lib/replay-time";
import { renderFailureMessage } from "@/lib/user-errors";
import type { ReplayRound } from "@/types/replay";

interface ClipLibraryProps {
  jobs: RenderJobStatus[];
  playerName: string | null;
  rounds: ReplayRound[];
  // False when clip generation is switched off: the empty state must not point at hidden controls.
  canGenerate: boolean;
  selectedJobId: string | null;
  onPlay: (job: RenderJobStatus) => void;
}

export const ClipLibrary = memo(function ClipLibrary({ jobs, playerName, rounds, canGenerate, selectedJobId, onPlay }: ClipLibraryProps) {
  const readyCount = jobs.filter((job) => playableClipVideo(job)).length;
  const currentJobs = jobs.filter((job) => job.status !== "failed" && (job.status !== "completed" || playableClipVideo(job)));
  const historyJobs = jobs.filter((job) => !currentJobs.includes(job));

  function renderClip(job: RenderJobStatus) {
    const video = playableClipVideo(job);
    const start = video?.tickStart ?? job.tick_start ?? job.metadata.tickStart;
    const end = video?.tickEnd ?? job.tick_end ?? job.metadata.tickEnd;
    const candidateRate = video?.tickRate ?? job.tick_rate ?? job.metadata.tickRate ?? 64;
    const rate = Number.isFinite(candidateRate) && candidateRate > 0 ? candidateRate : 64;
    const round = rounds.find((item) => start !== undefined && start >= item.startTick && start <= item.endTick);
    const roundNumber = job.round_number ?? job.metadata.roundNumber ?? round?.roundNumber;
    const offset = round && start !== undefined ? Math.max(0, (start - round.startTick) / rate) : null;
    const duration = start !== undefined && end !== undefined ? Math.max(0, (end - start) / rate) : null;
    const selected = job.job_id === selectedJobId;
    const label = video ? "可以观看" : job.status === "completed" ? "视频不可用"
      : job.status === "failed" ? "生成失败"
        : job.status === "rendering" || job.status === "processing" ? "生成中" : "等待生成";

    const place = `${roundNumber ? `第 ${roundNumber} 回合` : "回合未知"}${offset !== null ? ` ${formatRoundTime(offset)}` : ""}`;
    const action = selected ? "重新观看" : "观看视频";

    return (
      <li key={job.job_id} className={`clip-library-item ${selected ? "selected" : ""}`}>
        <div className="clip-library-coordinate">
          <strong>{roundNumber ? `第 ${roundNumber} 回合` : "回合未知"}{offset !== null ? ` ${formatRoundTime(offset)}` : ""}</strong>
          <span>{duration !== null ? `${Math.round(duration)} 秒视频` : "视频时长未知"}</span>
          {job.error_message || job.error_code ? <small>{renderFailureMessage(job.error_code)}</small> : null}
        </div>
        <span className={`mini-pill clip-job-pill ${job.status}`} role="status">{label}</span>
        {video ? (
          <button className="secondary-button compact-button" type="button" onClick={() => onPlay(job)}
            aria-pressed={selected} aria-label={`${action}：${playerName ?? "玩家"} · ${place}`}>
            <Play size={14} aria-hidden="true" />{action}
          </button>
        ) : null}
      </li>
    );
  }

  return (
    <section className="panel clip-library" aria-label="已保存的第一人称视频">
      <div className="clip-library-heading">
        <h2>{playerName ? `${playerName} 的视频` : "第一人称视频"}</h2>
        <span>{readyCount} 段可观看</span>
      </div>
      {jobs.length === 0 ? (
        <p className="clip-library-empty">
          {!playerName ? "选择复盘玩家后，查看已保存的视频。"
            : canGenerate ? "从建议或当前时刻生成视频，完成后会保存在这里，随时重播。" : "暂无已保存的视频。"}
        </p>
      ) : (
        <>
          {currentJobs.length > 0 ? <ul className="clip-library-list">{currentJobs.map(renderClip)}</ul> : null}
          {historyJobs.length > 0 ? (
            <details className="clip-library-history">
              <summary>未完成与不可用的视频（{historyJobs.length}）</summary>
              <ul className="clip-library-list">{historyJobs.map(renderClip)}</ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
});
