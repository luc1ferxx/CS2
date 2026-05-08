import type { CoachingEvent } from "@/types/coaching";
import type { DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayVideo } from "@/types/replay";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface RenderJobCreated {
  job_id: string;
  demo_id: string;
  job_type: string;
  status: string;
  video: ReplayVideo;
}

export interface VideoCalibrationUpdate {
  durationSeconds?: number;
  tickStart?: number;
  tickEnd?: number;
  tickRate?: number;
  timeOriginSeconds?: number;
}

async function requestJson<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
    cache: "no-store"
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function listDemos(): Promise<DemoSummary[]> {
  return requestJson<DemoSummary[]>("/demos");
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

export function getCoaching(demoId: string): Promise<CoachingEvent[]> {
  return requestJson<CoachingEvent[]>(`/demos/${demoId}/coaching`);
}
