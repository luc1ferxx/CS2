import { uploadLimitMessage } from "@/lib/upload-limits";

// Player-facing Chinese copy for failed requests. Operator and dev-only panels
// keep friendlyErrorMessage (lib/demo-library.ts), which passes backend text through.

export const NETWORK_ERROR_MESSAGE = "网络连接中断，请检查网络后重试。";
export const SERVER_ERROR_MESSAGE = "服务暂时出错，请稍后重试。";
export const NOT_FOUND_ERROR_MESSAGE = "内容不存在或已被移除。";

export type RequestFailureKind = "network" | "not_found" | "conflict" | "too_large" | "server" | "other";

interface ApiErrorLike {
  status: number;
  detailCode?: string | null;
  retryAfterSeconds?: number | null;
}

export interface UserErrorOverrides {
  // Copy for a 409, which always means "not in the state this action needs".
  conflict?: string;
}

// Duck-typed so the node helper tests can load this without the API client.
function apiErrorFields(error: unknown): ApiErrorLike | null {
  if (typeof error !== "object" || error === null) return null;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? (error as ApiErrorLike) : null;
}

function errorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  const message = typeof error === "object" && error !== null ? (error as { message?: unknown }).message : null;
  return typeof message === "string" ? message : "";
}

export function requestFailureKind(error: unknown): RequestFailureKind {
  const apiError = apiErrorFields(error);
  if (apiError) {
    if (apiError.status === 404) return "not_found";
    if (apiError.status === 409) return "conflict";
    if (apiError.status === 413) return "too_large";
    if (apiError.status >= 500) return "server";
    return "other";
  }
  const message = errorMessage(error);
  if (/failed to fetch|networkerror|network request failed|load failed|err_connection|econnrefused/i.test(message)) {
    return "network";
  }
  return "other";
}

export function userFacingError(error: unknown, fallback: string, overrides: UserErrorOverrides = {}): string {
  const apiError = apiErrorFields(error);
  // Quota codes first: their English messages must not reach the file-validation checks below.
  const limitMessage = apiError
    ? uploadLimitMessage(apiError.status, apiError.detailCode, apiError.retryAfterSeconds)
    : null;
  if (limitMessage) {
    return limitMessage;
  }
  const kind = requestFailureKind(error);
  const message = errorMessage(error);
  if (kind === "network") return NETWORK_ERROR_MESSAGE;
  if (kind === "too_large" || /too large|maximum upload/i.test(message)) {
    return "文件超过上传大小限制，请选择较小的 .dem 文件。";
  }
  if (kind === "server") return SERVER_ERROR_MESSAGE;
  if (kind === "not_found") return NOT_FOUND_ERROR_MESSAGE;
  if (kind === "conflict" && overrides.conflict) return overrides.conflict;
  if (/\.dem|invalid file|unsupported file/i.test(message)) {
    return "请选择有效的 .dem 比赛文件后重试。";
  }
  return fallback;
}

const RENDER_FAILURE_COPY: Record<string, string> = {
  RENDER_WORKER_UNAVAILABLE: "视频生成服务暂时不可用，请稍后重试。",
  RENDER_TIMED_OUT: "生成视频超时，请重试。",
  RENDER_QUEUE_TIMED_OUT: "等待生成的时间过长，请稍后重试。",
  RENDER_FAILED: "视频生成失败，请重试。"
};

// Player-facing copy for a failed clip. The backend's public render failures
// carry one of the codes above; anything else reads as the generic failure.
export function renderFailureMessage(errorCode: string | null | undefined): string {
  return (errorCode && RENDER_FAILURE_COPY[errorCode]) || RENDER_FAILURE_COPY.RENDER_FAILED;
}
