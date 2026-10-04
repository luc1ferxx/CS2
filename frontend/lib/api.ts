import type { CoachingEvent, CoachingFeedback, CoachingVerdict } from "@/types/coaching";
import type { DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayVideo } from "@/types/replay";
import type {
  UploadCompleteResult,
  UploadPartReceipt,
  UploadPartRequest,
  UploadSessionCreated,
  UploadSessionCreateRequest,
  UploadSessionCurrent,
  UploadSessionStatus,
  UploadSessionTokenResponse,
  UploadStatus
} from "@/types/upload";
import type { ChunkedUploadTransport } from "@/lib/chunked-upload";
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
  // Any other fields of a structured error (e.g. the session in the way of a
  // new upload, or the missing part numbers); null when there are none.
  readonly detailData: Readonly<Record<string, unknown>> | null;

  constructor(
    status: number,
    message: string,
    detailCode: string | null = null,
    retryAfterSeconds: number | null = null,
    detailData: Readonly<Record<string, unknown>> | null = null
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = status === 401 ? "unauthenticated" : "request_failed";
    this.detailCode = detailCode;
    this.retryAfterSeconds = retryAfterSeconds;
    this.detailData = detailData;
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
  detailData: Record<string, unknown> | null;
}

async function responseErrorDetails(response: Response): Promise<ResponseErrorDetails> {
  return errorDetailsFromBody(await response.text());
}

function errorDetailsFromBody(body: string): ResponseErrorDetails {
  if (!body) {
    return { message: "", detailCode: null, retryAfterSeconds: null, detailData: null };
  }

  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return { message: parsed.detail, detailCode: null, retryAfterSeconds: null, detailData: extraFields(parsed, {}) };
    }
    if (isStructuredApiDetail(parsed.detail)) {
      return {
        message: parsed.detail.message,
        detailCode: parsed.detail.code,
        retryAfterSeconds: positiveSeconds(parsed.detail.retryAfterSeconds),
        detailData: extraFields(parsed, parsed.detail)
      };
    }
  } catch {
    return { message: body, detailCode: null, retryAfterSeconds: null, detailData: null };
  }

  return { message: body, detailCode: null, retryAfterSeconds: null, detailData: null };
}

const KNOWN_ERROR_FIELDS = new Set(["detail", "code", "message", "retryAfterSeconds", "errorCode"]);

// Fields beside the known ones, read from the body and from a structured detail (which wins).
function extraFields(body: unknown, detail: object): Record<string, unknown> | null {
  const extras: Record<string, unknown> = {};
  for (const source of [body, detail]) {
    if (typeof source !== "object" || source === null || Array.isArray(source)) continue;
    for (const [key, value] of Object.entries(source)) {
      if (!KNOWN_ERROR_FIELDS.has(key)) extras[key] = value;
    }
  }
  return Object.keys(extras).length > 0 ? extras : null;
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
    detail.retryAfterSeconds ?? retryAfterHeaderSeconds(response.headers.get("Retry-After")),
    detail.detailData
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
  // Preparing, sending, paused (offline, retrying, signed out), verifying, waiting for the quota.
  onStatus?: (status: UploadStatus) => void;
  signal?: AbortSignal;
  // The unfinished session the player chose to continue with this file.
  resumeSessionId?: string | null;
  // The player confirmed discarding the unfinished session for this file.
  replace?: boolean;
}

// The upload session routes answer the intake's refusals as {detail, errorCode};
// read that sibling code too. `notifyUnauthorized: false` keeps a 401 from
// swapping the page for the sign-in wall while an upload only pauses for it.
export interface UploadRequestOptions {
  notifyUnauthorized?: boolean;
}

async function uploadSessionFetch(path: string, init: RequestInit, options: UploadRequestOptions = {}): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {})
    },
    cache: "no-store"
  });
  if (!response.ok) {
    const body = await response.text();
    throw uploadResponseError(
      response.status,
      body,
      response.headers.get("Retry-After"),
      options.notifyUnauthorized !== false
    );
  }
  return response;
}

function uploadResponseError(status: number, body: string, retryAfter: string | null, notifyUnauthorized: boolean): ApiError {
  if (status === 401 && notifyUnauthorized) {
    for (const listener of unauthorizedListeners) {
      listener();
    }
  }
  const detail = errorDetailsFromBody(body);
  return new ApiError(
    status,
    detail.message || `Request failed with ${status}`,
    detail.detailCode ?? siblingErrorCode(body),
    detail.retryAfterSeconds ?? retryAfterHeaderSeconds(retryAfter),
    detail.detailData
  );
}

function uploadSessionPath(sessionId: string, suffix = ""): string {
  return `/uploads/sessions/${encodeURIComponent(sessionId)}${suffix}`;
}

export async function createUploadSession(request: UploadSessionCreateRequest): Promise<UploadSessionCreated> {
  const response = await uploadSessionFetch("/uploads/sessions", {
    method: "POST",
    body: JSON.stringify(request)
  });
  return (await response.json()) as UploadSessionCreated;
}

// The owner's unfinished session (open or completing), or null.
export async function getCurrentUploadSession(): Promise<UploadSessionStatus | null> {
  const response = await uploadSessionFetch("/uploads/sessions/current", { method: "GET" });
  const body = (await response.json()) as Partial<UploadSessionCurrent> | null;
  return body?.session ?? null;
}

export async function getUploadSession(sessionId: string, options: UploadRequestOptions = {}): Promise<UploadSessionStatus> {
  const response = await uploadSessionFetch(uploadSessionPath(sessionId), { method: "GET" }, options);
  return (await response.json()) as UploadSessionStatus;
}

// A fresh upload token for an open session; the previous token stops working.
export async function refreshUploadSessionToken(
  sessionId: string,
  options: UploadRequestOptions = {}
): Promise<UploadSessionTokenResponse> {
  const response = await uploadSessionFetch(uploadSessionPath(sessionId, "/token"), { method: "POST" }, options);
  return (await response.json()) as UploadSessionTokenResponse;
}

export async function completeUploadSession(
  sessionId: string,
  options: UploadRequestOptions = {}
): Promise<UploadCompleteResult> {
  const response = await uploadSessionFetch(uploadSessionPath(sessionId, "/complete"), { method: "POST" }, options);
  const body = (await response.json()) as (Partial<DemoSummary> & { state?: unknown }) | null;
  if (response.status === 202 || !body || typeof body.id !== "string") {
    return { kind: "completing" };
  }
  return { kind: "demo", demo: body as DemoSummary, created: response.status === 201 };
}

// Abandons an unfinished upload: the session and every part on the server go.
export async function deleteUploadSession(sessionId: string): Promise<void> {
  await uploadSessionFetch(uploadSessionPath(sessionId), { method: "DELETE" });
}

// One part over XMLHttpRequest, for its upload progress. The upload token is
// the only credential the route reads (withCredentials stays off, so a
// cross-origin dev API gets no cookie either), and a 401 here says nothing
// about the sign-in session.
export function putUploadPart(request: UploadPartRequest): Promise<UploadPartReceipt> {
  const { sessionId, index, token, body, sha256, signal, onProgress } = request;
  return new Promise<UploadPartReceipt>((resolve, reject) => {
    if (signal?.aborted) {
      reject(uploadAbortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    const onAbortSignal = () => xhr.abort();
    const settle = () => signal?.removeEventListener("abort", onAbortSignal);

    xhr.open("PUT", `${API_BASE_URL}${uploadSessionPath(sessionId, `/parts/${index}`)}`);
    xhr.withCredentials = false;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Upload-Token", token);
    if (sha256) {
      xhr.setRequestHeader("X-Part-SHA256", sha256);
    }
    xhr.upload.onprogress = (event) => {
      onProgress?.(event.loaded);
    };
    xhr.onload = () => {
      settle();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadPartReceipt);
        } catch {
          reject(new ApiError(xhr.status, "Upload response was not valid JSON"));
        }
        return;
      }
      reject(uploadResponseError(xhr.status, xhr.responseText ?? "", xhr.getResponseHeader("Retry-After"), false));
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
    xhr.send(body);
  });
}

// The engine pauses on a signed-out 401 instead of handing the page to the sign-in wall.
const uploadSessionTransport: ChunkedUploadTransport = {
  getCurrentSession: () => getCurrentUploadSession(),
  getSession: (sessionId) => getUploadSession(sessionId, { notifyUnauthorized: false }),
  createSession: (request) => createUploadSession(request),
  refreshToken: (sessionId) => refreshUploadSessionToken(sessionId, { notifyUnauthorized: false }),
  putPart: (request) => putUploadPart(request),
  completeSession: (sessionId) => completeUploadSession(sessionId, { notifyUnauthorized: false }),
  deleteSession: (sessionId) => deleteUploadSession(sessionId)
};

// Uploads a .dem through an upload session: parts of a server-chosen size, a
// few at a time, retried and resumable (lib/chunked-upload.ts), then completed
// into a match. Failures raise the same ApiError as every other request (with
// the intake's sibling `errorCode` as detailCode), a network failure the
// TypeError fetch raises, and a cancel an AbortError.
export async function uploadDemoFile(file: File, options: UploadDemoFileOptions = {}): Promise<DemoSummary> {
  // Loaded on first use; lib/auth.test.mjs runs this module with no other lib module available.
  const engine = await import("@/lib/chunked-upload");
  try {
    return await engine.runChunkedUpload(file, { ...options, transport: uploadSessionTransport });
  } catch (error) {
    throw asApiError(error);
  }
}

// Completes an unfinished session whose parts are all on the server, without the file.
export async function finishDemoUpload(sessionId: string, options: UploadDemoFileOptions = {}): Promise<DemoSummary> {
  const engine = await import("@/lib/chunked-upload");
  try {
    return await engine.finishChunkedUpload(sessionId, { ...options, transport: uploadSessionTransport });
  } catch (error) {
    throw asApiError(error);
  }
}

// The engine's own refusals (an archive picked as a .dem, a session that is gone) read like the API's.
function asApiError(error: unknown): unknown {
  if (typeof error === "object" && error !== null && (error as { name?: unknown }).name === "UploadEngineError") {
    const engineError = error as { status: number; message: string; detailCode: string | null; retryAfterSeconds: number | null };
    return new ApiError(engineError.status, engineError.message, engineError.detailCode, engineError.retryAfterSeconds);
  }
  return error;
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
