import type { CoachingEvent } from "@/types/coaching";
import type { DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayVideo } from "@/types/replay";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ApiErrorCode = "unauthenticated" | "request_failed";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = status === 401 ? "unauthenticated" : "request_failed";
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

interface RenderJobCreated extends RenderJobStatus {
  video: ReplayVideo;
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

export interface AuthSession {
  authenticated: true;
  expires_at?: string | null;
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

export function getAuthSession(): Promise<AuthSession> {
  return requestJson<AuthSession>("/auth/session");
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
  return `${API_BASE_URL}/auth/login?${query.toString()}`;
}

export function createMockUpload(): Promise<DemoSummary> {
  return requestJson<DemoSummary>("/uploads/mock", { method: "POST" });
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

export function getCoaching(demoId: string): Promise<CoachingEvent[]> {
  return requestJson<CoachingEvent[]>(`/demos/${demoId}/coaching`);
}

async function responseErrorMessage(response: Response): Promise<string> {
  const body = await response.text();
  if (!body) {
    return "";
  }

  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
  } catch {
    return body;
  }

  return body;
}

async function throwResponseError(response: Response): Promise<never> {
  const detail = await responseErrorMessage(response);
  if (response.status === 401) {
    for (const listener of unauthorizedListeners) {
      listener();
    }
  }
  throw new ApiError(
    response.status,
    detail || `Request failed with ${response.status}`
  );
}
