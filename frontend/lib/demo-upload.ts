import type { UploadDemoFileOptions } from "@/lib/api";
import type { DemoSummary } from "@/types/demo";

// One .dem upload at a time, held outside the dashboard so a client-side
// navigation does not drop it; leaving or reloading the page asks first.

export type DemoUploader = (file: File, options: UploadDemoFileOptions) => Promise<DemoSummary>;

export interface DemoUploadSnapshot {
  fileName: string;
  loaded: number;
  total: number;
  startedAt: number;
  // "verifying": every byte is sent and the server is still checking the file.
  phase: "sending" | "verifying";
}

// How the last upload ended. Kept until a mounted dashboard takes it, so a
// rejection that settles while the player is on another page still shows up.
export type DemoUploadOutcome =
  | { kind: "landed"; fileName: string; demo: DemoSummary }
  | { kind: "failed"; fileName: string; error: unknown };

let current: { snapshot: DemoUploadSnapshot; controller: AbortController } | null = null;
let outcome: DemoUploadOutcome | null = null;
const listeners = new Set<() => void>();

export function subscribeDemoUpload(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getDemoUploadSnapshot(): DemoUploadSnapshot | null {
  return current?.snapshot ?? null;
}

export function takeDemoUploadOutcome(): DemoUploadOutcome | null {
  const settled = outcome;
  outcome = null;
  return settled;
}

export function startDemoUpload(file: File, upload: DemoUploader, now: () => number = Date.now): Promise<DemoSummary> {
  if (current) {
    return Promise.reject(new Error("An upload is already running"));
  }
  const controller = new AbortController();
  const snapshot: DemoUploadSnapshot = {
    fileName: file.name,
    loaded: 0,
    total: file.size,
    startedAt: now(),
    phase: "sending"
  };
  const session = { snapshot, controller };
  current = session;
  outcome = null;
  addLeaveGuard();
  emit();

  const settle = (result: DemoUploadOutcome) => {
    if (current !== session) return;
    outcome = result;
    current = null;
    removeLeaveGuard();
    emit();
  };

  return upload(file, {
    signal: controller.signal,
    onProgress: ({ loaded, total }) => {
      if (current !== session) return;
      const size = total > 0 ? total : session.snapshot.total;
      const phase = size > 0 && loaded >= size ? "verifying" : "sending";
      // Whole percents are all the row shows; skip the events in between.
      if (phase === session.snapshot.phase && percent(loaded, size) === percent(session.snapshot.loaded, session.snapshot.total)) {
        return;
      }
      session.snapshot = { ...session.snapshot, loaded, total: size, phase };
      emit();
    }
  }).then(
    (demo) => {
      settle({ kind: "landed", fileName: file.name, demo });
      return demo;
    },
    (error: unknown) => {
      settle({ kind: "failed", fileName: file.name, error });
      throw error;
    }
  );
}

export function cancelDemoUpload(): boolean {
  if (!current) return false;
  current.controller.abort();
  return true;
}

export function uploadPercent(snapshot: Pick<DemoUploadSnapshot, "loaded" | "total">): number {
  return percent(snapshot.loaded, snapshot.total);
}

export function formatMegabytes(bytes: number): string {
  const megabytes = Math.max(0, bytes) / (1024 * 1024);
  return megabytes >= 100 || megabytes === 0 ? String(Math.round(megabytes)) : megabytes.toFixed(1);
}

export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) {
    const gigabytes = bytes / (1024 * 1024 * 1024);
    return `${Number.isInteger(gigabytes) ? gigabytes : gigabytes.toFixed(1)} GB`;
  }
  return `${formatMegabytes(bytes)} MB`;
}

// Needs a few seconds of throughput before an estimate means anything.
export function uploadEtaLabel(snapshot: Pick<DemoUploadSnapshot, "loaded" | "total" | "startedAt">, nowMs: number): string | null {
  const elapsedSeconds = (nowMs - snapshot.startedAt) / 1000;
  if (elapsedSeconds < 2 || snapshot.loaded <= 0 || snapshot.loaded >= snapshot.total) {
    return null;
  }
  const remaining = ((snapshot.total - snapshot.loaded) / snapshot.loaded) * elapsedSeconds;
  if (!Number.isFinite(remaining)) return null;
  if (remaining < 60) return `约 ${Math.max(5, Math.ceil(remaining / 5) * 5)} 秒`;
  return `约 ${Math.ceil(remaining / 60)} 分钟`;
}

export function uploadProgressLabel(snapshot: DemoUploadSnapshot, nowMs: number): string {
  if (snapshot.phase === "verifying") {
    return "上传完成，服务器校验中…";
  }
  const parts = [
    `上传中 ${uploadPercent(snapshot)}%`,
    `${formatMegabytes(snapshot.loaded)} / ${formatMegabytes(snapshot.total)} MB`
  ];
  const eta = uploadEtaLabel(snapshot, nowMs);
  if (eta) parts.push(eta);
  return parts.join("，");
}

function percent(loaded: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.floor((loaded / total) * 100)));
}

function emit() {
  for (const listener of listeners) {
    listener();
  }
}

function leaveGuard(event: BeforeUnloadEvent) {
  event.preventDefault();
  // Older browsers only prompt when returnValue is set.
  event.returnValue = "";
}

function addLeaveGuard() {
  if (typeof window !== "undefined") window.addEventListener("beforeunload", leaveGuard);
}

function removeLeaveGuard() {
  if (typeof window !== "undefined") window.removeEventListener("beforeunload", leaveGuard);
}
