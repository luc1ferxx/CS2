import type { CoachingEvent } from "@/types/coaching";
import type { DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayVideo } from "@/types/replay";
import type {
  SteamConnection,
  SteamConnectionCredentials,
  SteamMatch,
  SteamSyncResult
} from "@/types/steam";
import type { AuthAccount } from "@/lib/auth";

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

  constructor(status: number, message: string, detailCode: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = status === 401 ? "unauthenticated" : "request_failed";
    this.detailCode = detailCode;
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

export function getRenderJobs(demoId: string): Promise<RenderJobStatus[]> {
  return requestJson<RenderJobStatus[]>(`/demos/${demoId}/render/jobs`);
}

export function getRenderWorkerStatus(): Promise<RenderWorkerStatus> {
  return requestJson<RenderWorkerStatus>("/render/worker");
}

export function getCoaching(demoId: string): Promise<CoachingEvent[]> {
  return requestJson<CoachingEvent[]>(`/demos/${demoId}/coaching`);
}

interface ResponseErrorDetails {
  message: string;
  detailCode: string | null;
}

async function responseErrorDetails(response: Response): Promise<ResponseErrorDetails> {
  const body = await response.text();
  if (!body) {
    return { message: "", detailCode: null };
  }

  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return { message: parsed.detail, detailCode: null };
    }
    if (isStructuredApiDetail(parsed.detail)) {
      return {
        message: parsed.detail.message,
        detailCode: parsed.detail.code
      };
    }
  } catch {
    return { message: body, detailCode: null };
  }

  return { message: body, detailCode: null };
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
    detail.detailCode
  );
}

function isStructuredApiDetail(
  value: unknown
): value is { code: string; message: string } {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const detail = value as { code?: unknown; message?: unknown };
  return typeof detail.code === "string" && typeof detail.message === "string";
}
