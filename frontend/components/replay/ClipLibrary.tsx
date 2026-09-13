"use client";

import { Play } from "lucide-react";

import type { RenderJobStatus } from "@/lib/api";
import { friendlyErrorMessage } from "@/lib/demo-library";
import { playableClipVideo } from "@/lib/render-clips";
import type { ReplayRound } from "@/types/replay";

interface ClipLibraryProps {
  jobs: RenderJobStatus[];
  playerName: string | null;
  rounds: ReplayRound[];
  selectedJobId: string | null;
  onPlay: (job: RenderJobStatus) => void;
}

export function ClipLibrary({ jobs, playerName, rounds, selectedJobId, onPlay }: ClipLibraryProps) {
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

    return (
      <li key={job.job_id} className={`clip-library-item ${selected ? "selected" : ""}`}>
        <div className="clip-library-coordinate" title={`Tick ${start ?? "?"}–${end ?? "?"}`}>
          <strong>{roundNumber ? `第 ${roundNumber} 回合` : "回合未知"}{offset !== null ? ` · ${formatClipTime(offset)}` : ""}</strong>
          <span>{duration !== null ? `${Math.round(duration)} 秒片段` : "片段时长未知"}</span>
          {job.error_message ? <small>{friendlyErrorMessage(job.error_message)}</small> : null}
        </div>
        <span className={`mini-pill clip-job-pill ${job.status}`} role="status">{label}</span>
        {video ? (
          <button className="secondary-button compact-button" type="button" onClick={() => onPlay(job)}
            aria-pressed={selected} aria-label={`Play ${playerName ?? "player"} clip at tick ${start}`}>
            <Play size={14} aria-hidden="true" />{selected ? "重新观看" : "观看视频"}
          </button>
        ) : null}
      </li>
    );
  }

  return (
    <section className="panel clip-library" aria-label="Saved first-person clips">
      <div className="clip-library-heading">
        <h2>{playerName ? `${playerName} 的片段` : "第一人称片段"}</h2>
        <span>{readyCount} 段可观看</span>
      </div>
      {jobs.length === 0 ? (
        <p className="clip-library-empty">
          {playerName ? "从建议或当前时刻生成视频，完成后会保存在这里，随时重播。" : "选择复盘玩家后，查看已保存的片段。"}
        </p>
      ) : (
        <>
          {currentJobs.length > 0 ? <ul className="clip-library-list">{currentJobs.map(renderClip)}</ul> : null}
          {historyJobs.length > 0 ? (
            <details className="clip-library-history">
              <summary>未完成与不可用的片段 · {historyJobs.length}</summary>
              <ul className="clip-library-list">{historyJobs.map(renderClip)}</ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

function formatClipTime(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}
