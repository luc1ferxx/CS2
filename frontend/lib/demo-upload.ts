import type { UploadDemoFileOptions } from "@/lib/api";
import type { DemoSummary } from "@/types/demo";
import type { UploadPauseReason, UploadResumeRecord, UploadStatus } from "@/types/upload";

// One .dem upload at a time, held outside the dashboard so a client-side
// navigation does not drop it; leaving or reloading the page asks first (and
// only pauses it: the session stays on the server for the resume banner).
// Type imports only: lib/demo-upload.test.mjs loads this file on its own.

// A picked File, or just the name and size of an unfinished session that is
// completed without its file (the banner's 完成上传).
export interface DemoUploadSource {
  name: string;
  size: number;
}

export type DemoUploader<S extends DemoUploadSource = File> = (
  file: S,
  options: UploadDemoFileOptions
) => Promise<DemoSummary>;

// Which unfinished session this upload continues, or that the player chose to discard it.
export interface DemoUploadRequest {
  resumeSessionId?: string | null;
  replace?: boolean;
}

export type DemoUploadPhase = "preparing" | "sending" | "paused" | "verifying" | "waiting";

export interface DemoUploadSnapshot {
  fileName: string;
  loaded: number;
  total: number;
  // When the current stretch of sending began, and the bytes already there then
  // (a resumed upload starts part-way): the time-left estimate reads only that stretch.
  startedAt: number;
  startedLoaded?: number;
  // "verifying": every byte is sent and the server is still checking the file.
  phase: DemoUploadPhase;
  pauseReason?: UploadPauseReason | null;
  // The quota code a "waiting" upload is held back by.
  waitCode?: string | null;
}

// How the last upload ended. Kept until a mounted dashboard takes it, so a
// rejection that settles while the player is on another page still shows up.
export type DemoUploadOutcome =
  | { kind: "landed"; fileName: string; demo: DemoSummary }
  | {
      kind: "failed";
      fileName: string;
      error: unknown;
      // What was being uploaded and how, so the page can run it again (e.g. with replace).
      source?: DemoUploadSource;
      request?: DemoUploadRequest;
    };

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

export function startDemoUpload<S extends DemoUploadSource>(
  file: S,
  upload: DemoUploader<S>,
  now: () => number = Date.now,
  request: DemoUploadRequest = {}
): Promise<DemoSummary> {
  if (current) {
    return Promise.reject(new Error("An upload is already running"));
  }
  const controller = new AbortController();
  const snapshot: DemoUploadSnapshot = {
    fileName: file.name,
    loaded: 0,
    total: file.size,
    startedAt: now(),
    startedLoaded: 0,
    phase: "preparing",
    pauseReason: null,
    waitCode: null
  };
  const session = { snapshot, controller };
  // An uploader that reports its own status owns the phase; one that only
  // reports bytes gets it read from them.
  let reportsStatus = false;
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

  const update = (next: DemoUploadSnapshot) => {
    // A new stretch of sending restarts the rate the estimate uses.
    if (next.phase === "sending" && session.snapshot.phase !== "sending") {
      next = { ...next, startedAt: now(), startedLoaded: next.loaded };
    }
    session.snapshot = next;
    emit();
  };

  const options: UploadDemoFileOptions = {
    signal: controller.signal,
    onProgress: ({ loaded, total }) => {
      if (current !== session) return;
      const previous = session.snapshot;
      const size = total > 0 ? total : previous.total;
      let phase = previous.phase;
      if (!reportsStatus && (phase === "preparing" || phase === "sending")) {
        phase = size > 0 && loaded >= size ? "verifying" : "sending";
      }
      // Whole percents are all the row shows; skip the events in between.
      if (phase === previous.phase && percent(loaded, size) === percent(previous.loaded, previous.total)) {
        return;
      }
      update({ ...previous, loaded, total: size, phase });
    },
    onStatus: (status: UploadStatus) => {
      if (current !== session) return;
      reportsStatus = true;
      const previous = session.snapshot;
      const pauseReason = status.phase === "paused" ? status.reason : null;
      const waitCode = status.phase === "waiting" ? status.code : null;
      if (status.phase === previous.phase && pauseReason === (previous.pauseReason ?? null) && waitCode === (previous.waitCode ?? null)) {
        return;
      }
      update({ ...previous, phase: status.phase, pauseReason, waitCode });
    }
  };
  if (request.resumeSessionId) options.resumeSessionId = request.resumeSessionId;
  if (request.replace) options.replace = true;

  return upload(file, options).then(
    (demo) => {
      settle({ kind: "landed", fileName: file.name, demo });
      return demo;
    },
    (error: unknown) => {
      settle({ kind: "failed", fileName: file.name, error, source: file, request });
      throw error;
    }
  );
}

export function cancelDemoUpload(): boolean {
  if (!current) return false;
  current.controller.abort();
  return true;
}

// The unfinished upload this browser started, for matching a re-picked file
// after a reload. Every read and write tolerates storage that is missing or
// throws (private mode, blocked site data): matching then falls back to the
// size and part 0's digest.
export const UPLOAD_RESUME_KEY = "cs2-upload-resume:v1";

export interface ResumeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const SESSION_ID = /^[0-9a-f]{32}$/;

export function readUploadResumeRecord(getStorage: () => ResumeStorage | null): UploadResumeRecord | null {
  try {
    const raw = getStorage()?.getItem(UPLOAD_RESUME_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<UploadResumeRecord> | null;
    if (
      !parsed ||
      typeof parsed.sessionId !== "string" ||
      !SESSION_ID.test(parsed.sessionId) ||
      typeof parsed.name !== "string" ||
      typeof parsed.size !== "number" ||
      !Number.isFinite(parsed.size) ||
      typeof parsed.lastModified !== "number" ||
      !Number.isFinite(parsed.lastModified)
    ) {
      return null;
    }
    return { sessionId: parsed.sessionId, name: parsed.name, size: parsed.size, lastModified: parsed.lastModified };
  } catch {
    return null;
  }
}

export function writeUploadResumeRecord(getStorage: () => ResumeStorage | null, record: UploadResumeRecord): void {
  try {
    getStorage()?.setItem(
      UPLOAD_RESUME_KEY,
      JSON.stringify({
        sessionId: record.sessionId,
        name: record.name,
        size: record.size,
        lastModified: record.lastModified
      })
    );
  } catch {
    // Not kept: a later resume matches by size and part 0 instead.
  }
}

export function clearUploadResumeRecord(getStorage: () => ResumeStorage | null): void {
  try {
    getStorage()?.removeItem(UPLOAD_RESUME_KEY);
  } catch {
    // Private mode or blocked site data: nothing was kept to clear.
  }
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
export function uploadEtaLabel(
  snapshot: Pick<DemoUploadSnapshot, "loaded" | "total" | "startedAt" | "startedLoaded">,
  nowMs: number
): string | null {
  const elapsedSeconds = (nowMs - snapshot.startedAt) / 1000;
  const sent = snapshot.loaded - (snapshot.startedLoaded ?? 0);
  if (elapsedSeconds < 2 || sent <= 0 || snapshot.loaded >= snapshot.total) {
    return null;
  }
  const remaining = ((snapshot.total - snapshot.loaded) / sent) * elapsedSeconds;
  if (!Number.isFinite(remaining)) return null;
  if (remaining < 60) return `约 ${Math.max(5, Math.ceil(remaining / 5) * 5)} 秒`;
  return `约 ${Math.ceil(remaining / 60)} 分钟`;
}

// Why the upload is stopped, or what it waits for; null while it is moving.
export function uploadHoldLabel(snapshot: DemoUploadSnapshot): string | null {
  if (snapshot.phase === "paused") {
    if (snapshot.pauseReason === "offline") return "网络已断开，恢复连接后会自动继续";
    if (snapshot.pauseReason === "reauth") return "登录已过期，上传已暂停。请重新登录后继续上传";
    return "传输中断，正在自动重试";
  }
  if (snapshot.phase === "waiting") {
    if (snapshot.waitCode === "parse_queue_full") return "文件已传完。处理队列已满，稍后会自动完成上传";
    if (snapshot.waitCode === "upload_quota_unavailable") return "文件已传完。暂时无法检查上传额度，稍后会自动重试";
    return "文件已传完。等当前比赛处理完，会自动完成上传";
  }
  return null;
}

// The status column's word for the row.
export function uploadStateLabel(snapshot: DemoUploadSnapshot): string {
  switch (snapshot.phase) {
    case "preparing":
      return "准备中";
    case "paused":
      return snapshot.pauseReason === "retrying" ? "重试中" : "已暂停";
    case "verifying":
      return "校验中";
    case "waiting":
      return "排队中";
    default:
      return "上传中";
  }
}

// The upload button's label while an upload runs.
export function uploadBusyLabel(snapshot: DemoUploadSnapshot): string {
  switch (snapshot.phase) {
    case "preparing":
      return "准备中…";
    case "paused":
      return `已暂停 ${uploadPercent(snapshot)}%`;
    case "verifying":
      return "校验中…";
    case "waiting":
      return "排队中…";
    default:
      return `上传中 ${uploadPercent(snapshot)}%`;
  }
}

export function uploadProgressLabel(snapshot: DemoUploadSnapshot, nowMs: number): string {
  if (snapshot.phase === "verifying") {
    return "上传完成，服务器校验中…";
  }
  if (snapshot.phase === "preparing") {
    return "正在准备上传…";
  }
  const sizes = `${formatMegabytes(snapshot.loaded)} / ${formatMegabytes(snapshot.total)} MB`;
  const hold = uploadHoldLabel(snapshot);
  if (hold) {
    return snapshot.phase === "waiting" ? hold : `${hold}，已传 ${uploadPercent(snapshot)}%，${sizes}`;
  }
  const parts = [`上传中 ${uploadPercent(snapshot)}%`, sizes];
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
