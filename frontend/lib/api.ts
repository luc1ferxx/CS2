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

export function listDemos(): Promise<DemoSummary[]> {
  return requestJson<DemoSummary[]>("/demos");
}

export function createMockUpload(): Promise<DemoSummary> {
  return requestJson<DemoSummary>("/uploads/mock", { method: "POST" });
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

export function createMockRenderJob(demoId: string): Promise<RenderJobCreated> {
  return requestJson<RenderJobCreated>(`/demos/${demoId}/render/mock`, { method: "POST" });
}

export function getCoaching(demoId: string): Promise<CoachingEvent[]> {
  return requestJson<CoachingEvent[]>(`/demos/${demoId}/coaching`);
}
