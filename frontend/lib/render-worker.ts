import type { RenderJobStatus, RenderWorkerStatus } from "@/lib/api";

export interface RenderWorkerNotice {
  label: string;
  detail: string;
}

/**
 * True when a job needs an external render worker and none is reachable.
 *
 * The fallback mode has no separate renderer at all -- it fails render jobs
 * itself with a clear error -- so "not connected" is only meaningful when the
 * backend reports `required`.
 */
export function renderWorkerOffline(worker: RenderWorkerStatus | null | undefined): boolean {
  return Boolean(worker && worker.required && !worker.connected);
}

/**
 * Notice for a job that is queued with no renderer to pick it up.
 *
 * Only `queued` qualifies: the job stays durably queued and starts on its own
 * once a renderer polls again, so the copy says that rather than implying loss.
 */
export function renderWorkerNotice(
  worker: RenderWorkerStatus | null | undefined,
  job: Pick<RenderJobStatus, "status"> | null | undefined
): RenderWorkerNotice | null {
  if (!renderWorkerOffline(worker) || job?.status !== "queued") return null;
  return {
    label: "渲染器未连接",
    detail: `${renderWorkerLastSeenText(worker)}任务已保留，渲染器启动后会自动开始。`
  };
}

export function renderWorkerLastSeenText(worker: RenderWorkerStatus | null | undefined): string {
  if (!worker) return "";
  if (worker.status === "never_seen") return "渲染器从未连接过。";
  const age = worker.age_seconds;
  if (age === null || age === undefined) return "渲染器当前未连接。";
  return `渲染器已 ${relativeDuration(age)}未轮询。`;
}

/**
 * English copy for the operator panel, which is English throughout.
 *
 * Returns null when a renderer is reachable, so callers can fall back to their
 * normal copy without repeating the offline check.
 */
export function renderWorkerOperatorHint(worker: RenderWorkerStatus | null | undefined): string | null {
  if (!renderWorkerOffline(worker)) return null;
  const seen =
    worker?.status === "never_seen"
      ? "has never polled"
      : worker?.age_seconds === null || worker?.age_seconds === undefined
        ? "is not polling"
        : `last polled ${shortDuration(worker.age_seconds)} ago`;
  return `Render worker ${seen}. The job stays queued and starts on its own once one connects.`;
}

function relativeDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 360) / 10} 小时`;
}

function shortDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 360) / 10}h`;
}
