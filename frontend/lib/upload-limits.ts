// Chinese copy for the production upload quotas. Takes primitives, not an
// ApiError, so the node helper tests can load it without the API client.
export function uploadLimitMessage(
  status: number,
  detailCode: string | null | undefined,
  retryAfterSeconds: number | null | undefined
): string | null {
  if (status === 429 && detailCode === "upload_daily_limit") {
    const wait = waitDuration(retryAfterSeconds);
    return wait
      ? `今天的上传次数已用完，约 ${wait} 后可以继续上传。`
      : "今天的上传次数已用完，请稍后再继续上传。";
  }
  if (status === 429 && detailCode === "active_parse_limit") {
    return "已有比赛正在处理，请等当前比赛处理完成后再试。";
  }
  if (status === 503 && detailCode === "parse_queue_full") {
    return "服务繁忙，处理队列已满，请稍后再试。";
  }
  if (status === 503 && detailCode === "upload_quota_unavailable") {
    return "暂时无法检查上传额度，请稍后再试。";
  }
  return null;
}

// Rounds up to whole minutes so the copy never promises an earlier time than the API.
function waitDuration(seconds: number | null | undefined): string | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  const totalMinutes = Math.ceil(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) {
    return `${minutes} 分钟`;
  }
  return minutes === 0 ? `${hours} 小时` : `${hours} 小时 ${minutes} 分钟`;
}

// The API never accepts more than this (MAX_DEMO_UPLOAD_BYTES is capped at 1 GiB).
export const MAX_DEMO_UPLOAD_BYTES = 1024 * 1024 * 1024;

const SERVER_INTAKE_MESSAGE = "服务暂时无法接收文件，你的文件没有问题，请稍后重试。";
const NOT_A_DEMO_MESSAGE = "这不是 CS2 的 .dem 比赛文件。压缩包（.zip、.rar、.gz、.bz2 等）请先解压，再上传里面的 .dem 文件。";

// Keyed by the intake's error codes (backend artifact_intake.py, request_limits.py, api/uploads.py).
const INTAKE_ERROR_COPY: Record<string, string> = {
  INTAKE_TYPE_REJECTED: NOT_A_DEMO_MESSAGE,
  INTAKE_CONTENT_MISMATCH: NOT_A_DEMO_MESSAGE,
  INTAKE_EMPTY: "文件是空的，请重新下载这场比赛的 .dem 文件后再上传。",
  INTAKE_TRUNCATED: "文件不完整，可能还没下载完。请重新下载这场比赛的 .dem 文件后再上传。",
  INTAKE_INTEGRITY_FAILED: "文件校验没有通过，请重新上传。",
  INTAKE_REJECTED: "这个文件无法作为比赛录像上传，请确认选择的是 CS2 的 .dem 文件。",
  INTAKE_STORAGE_UNAVAILABLE: SERVER_INTAKE_MESSAGE,
  INTAKE_UNAVAILABLE: SERVER_INTAKE_MESSAGE,
  INTAKE_BUSY: "上传的人较多，服务正忙，请稍后重试。你的文件没有问题。"
};

// Keyed by the upload session codes (backend api/uploads.py), plus the
// engine's own upload_session_gone (lib/chunked-upload.ts).
const SESSION_ERROR_COPY: Record<string, string> = {
  upload_session_exists: "你还有一个未完成的上传。请先在上方继续或放弃它，再上传新的文件。",
  upload_capacity_busy: "同时上传的人较多，请稍后再试。你的文件没有问题。",
  upload_storage_full: "服务器暂存空间不足，请稍后再试。你的文件没有问题。",
  upload_part_invalid: "上传的数据和文件对不上，请重新上传。",
  upload_part_digest_mismatch: "上传过程中数据校验没有通过，请重新上传。",
  upload_session_not_open: "这次上传已经结束，请刷新比赛列表查看结果。",
  upload_parts_missing: "文件还没有全部传完。请选择同一个文件继续上传。",
  upload_parts_in_flight: "文件还在传送中，请稍后再试。",
  upload_session_gone: "这次上传已过期或已被放弃，请重新上传。",
  account_deleted: "账户已删除，上传已停止。"
};

// Copy for a rejected .dem upload, or null to fall back to the generic request copy.
export function demoUploadErrorMessage(
  status: number,
  detailCode: string | null | undefined,
  retryAfterSeconds: number | null | undefined,
  maxUploadBytes: number = MAX_DEMO_UPLOAD_BYTES
): string | null {
  // Quota codes first: the quota's 503s must not read as an intake outage.
  const limit = uploadLimitMessage(status, detailCode, retryAfterSeconds);
  if (limit) return limit;
  if (detailCode === "INTAKE_TOO_LARGE" || status === 413) {
    return tooLargeMessage(maxUploadBytes);
  }
  if (detailCode && INTAKE_ERROR_COPY[detailCode]) {
    return INTAKE_ERROR_COPY[detailCode];
  }
  if (detailCode && SESSION_ERROR_COPY[detailCode]) {
    return SESSION_ERROR_COPY[detailCode];
  }
  if (status >= 500) return SERVER_INTAKE_MESSAGE;
  return null;
}

// Checked before sending, so a wrong file never waits out a full transfer.
export function demoFileProblem(
  file: { name: string; size: number },
  maxUploadBytes: number = MAX_DEMO_UPLOAD_BYTES
): string | null {
  if (!file.name.toLowerCase().endsWith(".dem")) {
    return NOT_A_DEMO_MESSAGE;
  }
  if (file.size <= 0) {
    return INTAKE_ERROR_COPY.INTAKE_EMPTY;
  }
  if (file.size > maxUploadBytes) {
    return tooLargeMessage(maxUploadBytes);
  }
  return null;
}

function tooLargeMessage(maxUploadBytes: number): string {
  return `文件超过上传上限（${sizeLabel(maxUploadBytes)}），请确认选择的是单场比赛的 .dem 文件。`;
}

function sizeLabel(bytes: number): string {
  const gigabytes = bytes / (1024 * 1024 * 1024);
  if (gigabytes >= 1) {
    return `${Number.isInteger(gigabytes) ? gigabytes : gigabytes.toFixed(1)} GB`;
  }
  return `${Math.max(1, Math.round(bytes / (1024 * 1024)))} MB`;
}

export interface UploadQuotaCounts {
  dailyLimit: number | null;
  dailyUsed: number;
  dailyResetSeconds: number | null;
  activeLimit: number | null;
  activeCount: number;
}

export interface UploadQuotaSummary {
  // The line under the upload button, or null for the default.
  hint: string | null;
  // Set when the daily quota is used up: the upload stays off until it resets.
  blockedReason: string | null;
  // The allowance rules, for the .dem help.
  helpNote: string | null;
}

// What the dashboard says about the quota before a file is picked. Only
// production has limits; without them (or without an answer) it says nothing.
export function uploadQuotaSummary(quota: UploadQuotaCounts | null | undefined): UploadQuotaSummary {
  if (!quota) {
    return { hint: null, blockedReason: null, helpNote: null };
  }
  const helpNote = quota.dailyLimit
    ? `每个账号 24 小时内最多上传 ${quota.dailyLimit} 场，处理失败或已归档的上传也计入次数。`
    : null;
  if (quota.dailyLimit) {
    const remaining = Math.max(0, quota.dailyLimit - quota.dailyUsed);
    if (remaining === 0) {
      return {
        hint: "今天的次数已用完",
        blockedReason: uploadLimitMessage(429, "upload_daily_limit", quota.dailyResetSeconds),
        helpNote
      };
    }
    if (quota.activeLimit && quota.activeCount >= quota.activeLimit) {
      return { hint: "等当前比赛处理完再上传", blockedReason: null, helpNote };
    }
    return { hint: `今天还可上传 ${remaining} 场`, blockedReason: null, helpNote };
  }
  if (quota.activeLimit && quota.activeCount >= quota.activeLimit) {
    return { hint: "等当前比赛处理完再上传", blockedReason: null, helpNote };
  }
  return { hint: null, blockedReason: null, helpNote };
}
