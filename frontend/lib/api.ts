import type { CoachingEvent, CoachingFeedback, CoachingVerdict } from "@/types/coaching";
import type { DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayVideo } from "@/types/replay";
import type {
  SteamConnection,
  SteamConnectionCredentials,
  SteamMatch,
  SteamSyncResult
} from "@/types/steam";
import type { AuthAccount, AuthCapabilities } from "@/lib/auth";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const AUTH_PROVIDER = configuredAuthProvider(
  process.env.NEXT_PUBLIC_AUTH_PROVIDER ?? "steam"
);

export type ConfiguredAuthProvider = "steam" | "oidc";

export type ApiErrorCode = "unauthenticated" | "request_failed";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly detailCode: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    message: string,
    detailCode: string | null = null,
    retryAfterSeconds: number | null = null
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = status === 401 ? "unauthenticated" : "request_failed";
    this.detailCode = detailCode;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

type UnauthorizedListener = () => void;

const unauthorizedListeners = new Set<UnauthorizedListener>();

export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export interface RenderClipRequest {
  eventId?: string;
  playerId?: string;
  povSteamId?: string;
  tickStart: number;
  tickEnd: number;
  tickRate: number;
  roundNumber?: number;
  renderPreset?: string;
}

export interface RenderClipMetadata {
  eventId?: string;
  playerId?: string;
  povSteamId?: string;
  tickStart?: number;
  tickEnd?: number;
  tickRate?: number;
  roundNumber?: number;
  renderPreset?: string;
  durationSeconds?: number;
  maxDurationSeconds?: number;
  [key: string]: unknown;
}

export interface RenderJobStatus {
  job_id: string;
  demo_id: string;
  job_type: string;
  status: string;
  source: string;
  video_status?: ReplayVideo["status"] | string | null;
  video?: ReplayVideo | null;
  tick_start?: number | null;
  tick_end?: number | null;
  tick_rate?: number | null;
  duration_seconds?: number | null;
  event_id?: string | null;
  player_id?: string | null;
  pov_steam_id?: string | null;
  round_number?: number | null;
  render_preset?: string | null;
  metadata: RenderClipMetadata;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface RenderWorkerStatus {
  mode: string;
  required: boolean;
  connected: boolean;
  status: "connected" | "rendering" | "offline" | "never_seen" | string;
  last_seen_at?: string | null;
  age_seconds?: number | null;
  busy_rendering: boolean;
}

export interface RenderJobCreated extends RenderJobStatus {
  video: ReplayVideo;
  render_worker?: RenderWorkerStatus | null;
}

export interface VideoCalibrationUpdate {
  durationSeconds?: number;
  tickStart?: number;
  tickEnd?: number;
  tickRate?: number;
  timeOriginSeconds?: number;
}

export interface DemoListParams {
  search?: string;
  status?: string;
  map?: string;
  sort?: string;
  order?: string;
  includeArchived?: boolean;
}

export interface DemoUpdateRequest {
  name?: string;
  archived?: boolean;
}

export interface AuthMe {
  authenticated: true;
  account: AuthAccount;
  // Optional so an older API still parses; the auth reducer treats it as all off.
  capabilities?: AuthCapabilities;
}

async function requestJson<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    await throwResponseError(response);
  }

  return response.json() as Promise<T>;
}

async function requestNoContent(path: string, init?: RequestInit): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    await throwResponseError(response);
  }
}

async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
    credentials: "include",
    cache: "no-store"
  });

  if (!response.ok) {
    await throwResponseError(response);
  }

  return response.json() as Promise<T>;
}

export function listDemos(params: DemoListParams = {}): Promise<DemoSummary[]> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === "all") {
      continue;
    }
    query.set(key, String(value));
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<DemoSummary[]>(`/demos${suffix}`);
}

export function getAuthMe(): Promise<AuthMe> {
  return requestJson<AuthMe>("/auth/me");
}

export async function logoutAuthSession(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    credentials: "include",
    cache: "no-store"
  });
  if (!response.ok) {
    await throwResponseError(response);
  }
}

export function getAuthLoginUrl(returnTo: string): string {
  const query = new URLSearchParams({ return_to: returnTo });
  const path = AUTH_PROVIDER === "steam" ? "/auth/steam/login" : "/auth/login";
  return `${API_BASE_URL}${path}?${query.toString()}`;
}

export function getConfiguredAuthProvider(): ConfiguredAuthProvider {
  return AUTH_PROVIDER;
}

export function getSteamConnection(): Promise<SteamConnection> {
  return requestJson<SteamConnection>("/steam/connection");
}

export function deleteSteamConnection(): Promise<void> {
  return requestNoContent("/steam/connection", { method: "DELETE" });
}

export function saveSteamConnectionCredentials(
  credentials: SteamConnectionCredentials
): Promise<SteamConnection> {
  return requestJson<SteamConnection>("/steam/connection/credentials", {
    method: "POST",
    body: JSON.stringify(credentials)
  });
}

export function syncSteamMatches(): Promise<SteamSyncResult> {
  return requestJson<SteamSyncResult>("/steam/sync", { method: "POST" });
}

export function listSteamMatches(): Promise<SteamMatch[]> {
  return requestJson<SteamMatch[]>("/steam/matches");
}

export function importSteamMatch(matchId: string): Promise<SteamMatch> {
  return requestJson<SteamMatch>(`/steam/matches/${encodeURIComponent(matchId)}/import`, {
    method: "POST"
  });
}

export function createMockUpload(): Promise<DemoSummary> {
  return requestJson<DemoSummary>("/uploads/mock", { method: "POST" });
}

function configuredAuthProvider(value: string): ConfiguredAuthProvider {
  if (value === "steam" || value === "oidc") {
    return value;
  }
  throw new Error("NEXT_PUBLIC_AUTH_PROVIDER must be steam or oidc");
}

export function createDemoUpload(file: File): Promise<DemoSummary> {
  const formData = new FormData();
  formData.append("file", file);
  return requestForm<DemoSummary>("/uploads/demo", formData);
}

export function getDemoStatus(demoId: string): Promise<DemoStatus> {
  return requestJson<DemoStatus>(`/demos/${demoId}/status`);
}

export function updateDemo(
  demoId: string,
  request: DemoUpdateRequest
): Promise<DemoSummary> {
  return requestJson<DemoSummary>(`/demos/${demoId}`, {
    method: "PATCH",
    body: JSON.stringify(request)
  });
}

// Permanent: the match, its replay, suggestions and verdicts. A 404 means it is already gone.
export function deleteDemo(demoId: string): Promise<void> {
  return requestNoContent(`/demos/${encodeURIComponent(demoId)}`, { method: "DELETE" });
}

export const ACCOUNT_DELETION_CONFIRMATION = "delete-my-account";

// Permanent: the account and all of its data; every session of it ends.
export function deleteAccount(): Promise<void> {
  return requestNoContent("/auth/account", {
    method: "DELETE",
    body: JSON.stringify({ confirm: ACCOUNT_DELETION_CONFIRMATION })
  });
}

export function archiveDemo(demoId: string): Promise<DemoSummary> {
  return requestJson<DemoSummary>(`/demos/${demoId}/archive`, { method: "POST" });
}

export function retryDemoParse(demoId: string): Promise<DemoSummary> {
  return requestJson<DemoSummary>(`/demos/${demoId}/parse/retry`, { method: "POST" });
}

export function getReplay(demoId: string): Promise<ReplayData> {
  return requestJson<ReplayData>(`/demos/${demoId}/replay`);
}

export function getDemoVideo(demoId: string): Promise<ReplayVideo> {
  return requestJson<ReplayVideo>(`/demos/${demoId}/video`);
}

export function uploadDemoVideo(demoId: string, file: File): Promise<ReplayVideo> {
  const formData = new FormData();
  formData.append("file", file);
  return requestForm<ReplayVideo>(`/demos/${demoId}/video/upload`, formData);
}

export function saveVideoCalibration(
  demoId: string,
  calibration: VideoCalibrationUpdate
): Promise<ReplayVideo> {
  return requestJson<ReplayVideo>(`/demos/${demoId}/video/calibration`, {
    method: "POST",
    body: JSON.stringify(calibration)
  });
}

export function createMockRenderJob(demoId: string): Promise<RenderJobCreated> {
  return requestJson<RenderJobCreated>(`/demos/${demoId}/render/mock`, { method: "POST" });
}

export function createRenderClipJob(
  demoId: string,
  request: RenderClipRequest
): Promise<RenderJobCreated> {
  return requestJson<RenderJobCreated>(`/demos/${demoId}/render/clip`, {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function retryRenderClipJob(demoId: string, jobId: string): Promise<RenderJobCreated> {
  return requestJson<RenderJobCreated>(
    `/demos/${demoId}/render/jobs/${encodeURIComponent(jobId)}/retry`,
    { method: "POST" }
  );
}

export function getRenderJobs(demoId: string): Promise<RenderJobStatus[]> {
  return requestJson<RenderJobStatus[]>(`/demos/${demoId}/render/jobs`);
}

export function getRenderWorkerStatus(): Promise<RenderWorkerStatus> {
  return requestJson<RenderWorkerStatus>("/render/worker");
}

export interface CoachingFeedbackRequest {
  verdict: CoachingVerdict;
  note?: string | null;
}

export function saveCoachingFeedback(
  demoId: string,
  eventId: string,
  request: CoachingFeedbackRequest
): Promise<CoachingFeedback> {
  return requestJson<CoachingFeedback>(`/demos/${demoId}/coaching/${eventId}/feedback`, {
    method: "PUT",
    body: JSON.stringify(request)
  });
}

export function clearCoachingFeedback(demoId: string, eventId: string): Promise<void> {
  return requestNoContent(`/demos/${demoId}/coaching/${eventId}/feedback`, { method: "DELETE" });
}

export function getCoaching(demoId: string): Promise<CoachingEvent[]> {
  return requestJson<CoachingEvent[]>(`/demos/${demoId}/coaching`);
}

interface ResponseErrorDetails {
  message: string;
  detailCode: string | null;
  retryAfterSeconds: number | null;
}

async function responseErrorDetails(response: Response): Promise<ResponseErrorDetails> {
  return errorDetailsFromBody(await response.text());
}

function errorDetailsFromBody(body: string): ResponseErrorDetails {
  if (!body) {
    return { message: "", detailCode: null, retryAfterSeconds: null };
  }

  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return { message: parsed.detail, detailCode: null, retryAfterSeconds: null };
    }
    if (isStructuredApiDetail(parsed.detail)) {
      return {
        message: parsed.detail.message,
        detailCode: parsed.detail.code,
        retryAfterSeconds: positiveSeconds(parsed.detail.retryAfterSeconds)
      };
    }
  } catch {
    return { message: body, detailCode: null, retryAfterSeconds: null };
  }

  return { message: body, detailCode: null, retryAfterSeconds: null };
}

async function throwResponseError(response: Response): Promise<never> {
  const detail = await responseErrorDetails(response);
  if (response.status === 401) {
    for (const listener of unauthorizedListeners) {
      listener();
    }
  }
  throw new ApiError(
    response.status,
    detail.message || `Request failed with ${response.status}`,
    detail.detailCode,
    // The body is read first: a cross-origin dev setup may not expose the header.
    detail.retryAfterSeconds ?? retryAfterHeaderSeconds(response.headers.get("Retry-After"))
  );
}

function isStructuredApiDetail(
  value: unknown
): value is { code: string; message: string; retryAfterSeconds?: unknown } {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const detail = value as { code?: unknown; message?: unknown };
  return typeof detail.code === "string" && typeof detail.message === "string";
}

function positiveSeconds(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.ceil(value)
    : null;
}

// Only the delay-seconds form; the HTTP-date form is not sent by this API.
function retryAfterHeaderSeconds(value: string | null): number | null {
  const trimmed = value?.trim() ?? "";
  return /^\d+$/.test(trimmed) ? positiveSeconds(Number(trimmed)) : null;
}

// The owner's upload allowance. A null limit means that limit is off
// (always the case outside production). Advisory: the upload re-checks.
export interface UploadQuota {
  dailyLimit: number | null;
  dailyUsed: number;
  dailyResetSeconds: number | null;
  activeLimit: number | null;
  activeCount: number;
  maxUploadBytes?: number | null;
}

export function getUploadQuota(): Promise<UploadQuota> {
  return requestJson<UploadQuota>("/uploads/quota");
}

export interface UploadProgress {
  loaded: number;
  total: number;
}

export interface UploadDemoFileOptions {
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

// createDemoUpload over XMLHttpRequest, which reports upload progress and can
// be aborted. Failures raise the same ApiError as every other request, plus
// the intake's sibling `errorCode` as detailCode when there is no structured one.
export function uploadDemoFile(file: File, options: UploadDemoFileOptions = {}): Promise<DemoSummary> {
  const { onProgress, signal } = options;
  return new Promise<DemoSummary>((resolve, reject) => {
    if (signal?.aborted) {
      reject(uploadAbortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    const onAbortSignal = () => xhr.abort();
    const settle = () => signal?.removeEventListener("abort", onAbortSignal);

    xhr.open("POST", `${API_BASE_URL}/uploads/demo`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      onProgress?.({ loaded: event.loaded, total: event.lengthComputable ? event.total : file.size });
    };
    xhr.onload = () => {
      settle();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as DemoSummary);
        } catch {
          reject(new ApiError(xhr.status, "Upload response was not valid JSON"));
        }
        return;
      }
      if (xhr.status === 401) {
        for (const listener of unauthorizedListeners) {
          listener();
        }
      }
      const detail = errorDetailsFromBody(xhr.responseText ?? "");
      reject(
        new ApiError(
          xhr.status,
          detail.message || `Request failed with ${xhr.status}`,
          detail.detailCode ?? siblingErrorCode(xhr.responseText ?? ""),
          detail.retryAfterSeconds ?? retryAfterHeaderSeconds(xhr.getResponseHeader("Retry-After"))
        )
      );
    };
    // Same message fetch uses, so the shared error copy reads it as a network failure.
    xhr.onerror = () => {
      settle();
      reject(new TypeError("Failed to fetch"));
    };
    xhr.onabort = () => {
      settle();
      reject(uploadAbortError());
    };
    signal?.addEventListener("abort", onAbortSignal);

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export function isUploadAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function uploadAbortError(): Error {
  const error = new Error("Upload cancelled");
  error.name = "AbortError";
  return error;
}

function siblingErrorCode(body: string): string | null {
  try {
    const parsed = JSON.parse(body) as { errorCode?: unknown };
    return typeof parsed.errorCode === "string" ? parsed.errorCode : null;
  } catch {
    return null;
  }
}
