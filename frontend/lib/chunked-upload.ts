import type { DemoSummary } from "@/types/demo";
import type {
  UploadCompleteResult,
  UploadPartReceipt,
  UploadPartRequest,
  UploadResumeRecord,
  UploadSessionCreated,
  UploadSessionCreateRequest,
  UploadSessionStatus,
  UploadSessionTokenResponse,
  UploadStatus
} from "@/types/upload";
import {
  clearUploadResumeRecord,
  readUploadResumeRecord,
  writeUploadResumeRecord
} from "@/lib/demo-upload";

// The chunked, parallel, resumable .dem upload (spec S22 §3.12). Pure logic:
// the HTTP calls come in as a transport (lib/api.ts), and timers, the network
// state, hashing, file reads and the resume record as an environment, so the
// tests drive it with fakes and fake timers.
//
// Flow: refuse an archive by its first bytes -> resume the owner's unfinished
// session when it is this file, else open a new one -> PUT the missing parts,
// a few at a time, each retried -> complete, waiting out a busy server or the
// in-flight cap. The engine never discards a session on its own: a different
// file meets the server's 409 upload_session_exists, and the page asks first.

export interface ChunkedUploadTransport {
  getCurrentSession(): Promise<UploadSessionStatus | null>;
  getSession(sessionId: string): Promise<UploadSessionStatus>;
  createSession(request: UploadSessionCreateRequest): Promise<UploadSessionCreated>;
  refreshToken(sessionId: string): Promise<UploadSessionTokenResponse>;
  putPart(request: UploadPartRequest): Promise<UploadPartReceipt>;
  completeSession(sessionId: string): Promise<UploadCompleteResult>;
  deleteSession(sessionId: string): Promise<void>;
}

export interface UploadResumeStore {
  read(): UploadResumeRecord | null;
  write(record: UploadResumeRecord): void;
  clear(): void;
}

export interface UploadEnvironment {
  // Resolves after `ms`, rejects with an AbortError once `signal` aborts.
  sleep(ms: number, signal: AbortSignal): Promise<void>;
  random(): number;
  // Lower-case hex sha256, or null where WebCrypto is unavailable (plain-http LAN preview).
  digest(data: ArrayBuffer): Promise<string | null>;
  readSlice(blob: Blob): Promise<ArrayBuffer>;
  isOnline(): boolean;
  onOnlineChange(listener: (online: boolean) => void): () => void;
  resume: UploadResumeStore;
}

export interface ChunkedUploadOptions {
  transport: ChunkedUploadTransport;
  env?: Partial<UploadEnvironment>;
  signal?: AbortSignal;
  onProgress?: (progress: { loaded: number; total: number }) => void;
  onStatus?: (status: UploadStatus) => void;
  resumeSessionId?: string | null;
  replace?: boolean;
}

export interface PartRange {
  index: number;
  start: number;
  end: number;
}

// Refusals the engine makes itself; lib/api.ts turns them into an ApiError.
export class UploadEngineError extends Error {
  readonly status: number;
  readonly detailCode: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, detailCode: string | null, retryAfterSeconds: number | null = null) {
    super(message);
    this.name = "UploadEngineError";
    this.status = status;
    this.detailCode = detailCode;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export const SIGNATURE_BYTES = 512;
export const MAX_PART_ATTEMPTS = 8;
export const RETRY_BASE_MS = 1000;
export const RETRY_MAX_MS = 30_000;
export const COMPLETE_POLL_MS = 2000;
export const DEFAULT_PARALLEL_PARTS = 4;
export const MAX_PARALLEL_PARTS = 6;
// A full part pool or another upload's completion: the server's admission, not
// a failure of this upload, so it waits on Retry-After outside the 8-try budget,
// with its own (much larger) bound.
export const MAX_BUSY_WAITS = 200;
const PART_BUSY_RETRY_SECONDS = 2;
const COMPLETE_BUSY_RETRY_SECONDS = 5;
const IN_FLIGHT_RETRY_SECONDS = 1;
const QUOTA_RETRY_SECONDS = 30;
const QUOTA_MIN_RETRY_SECONDS = 5;
const REAUTH_RETRY_MS = 15_000;
const REAUTH_MAX_RETRY_MS = 60_000;
const OFFLINE_RECHECK_MS = 30_000;
const MAX_TOKEN_REFRESHES_PER_PART = 3;
const MAX_MISSING_ROUNDS = 3;
const MAX_STATE_CHECKS = 3;
const RESUME_SESSION_ID = /^[0-9a-f]{32}$/;

// The in-flight cap and a full queue clear on their own; the daily count does not.
const QUOTA_WAIT_CODES = new Set(["active_parse_limit", "parse_queue_full", "upload_quota_unavailable"]);

export function planParts(size: number, partSize: number): PartRange[] {
  if (!Number.isFinite(size) || size <= 0 || !Number.isFinite(partSize) || partSize <= 0) return [];
  const parts: PartRange[] = [];
  for (let start = 0, index = 0; start < size; start += partSize, index += 1) {
    parts.push({ index, start, end: Math.min(size, start + partSize) });
  }
  return parts;
}

// Full jitter: anywhere from 0 up to 1 s, 2 s, 4 s ... capped at 30 s.
export function backoffDelayMs(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** Math.max(0, attempt - 1));
  return Math.floor(Math.min(Math.max(random(), 0), 0.999999) * ceiling);
}

// Never sooner than the server's Retry-After.
export function retryDelayMs(attempt: number, retryAfterSeconds: number | null, random: () => number = Math.random): number {
  const jittered = backoffDelayMs(attempt, random);
  return retryAfterSeconds && retryAfterSeconds > 0 ? Math.max(retryAfterSeconds * 1000, jittered) : jittered;
}

export function clampParallelParts(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_PARALLEL_PARTS;
  return Math.min(MAX_PARALLEL_PARTS, Math.max(1, Math.floor(value)));
}

const FORBIDDEN_PREFIXES: number[][] = [
  [0x50, 0x4b, 0x03, 0x04], // zip
  [0x50, 0x4b, 0x05, 0x06], // empty zip
  [0x50, 0x4b, 0x07, 0x08], // spanned zip
  [0x1f, 0x8b], // gzip
  [0x42, 0x5a, 0x68], // bzip2 "BZh"
  [0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c], // 7z
  [0x52, 0x61, 0x72, 0x21, 0x1a, 0x07], // rar
  [0x4d, 0x5a], // Windows executable "MZ"
  [0x7f, 0x45, 0x4c, 0x46] // ELF
];
const TAR_MAGIC = [0x75, 0x73, 0x74, 0x61, 0x72]; // "ustar" at offset 257

// An archive or a program picked as a .dem, told apart before any byte is sent.
// The server checks part 0 the same way and the intake again at completion.
export function hasForbiddenSignature(bytes: Uint8Array): boolean {
  const startsWith = (prefix: number[], offset = 0) =>
    bytes.length >= offset + prefix.length && prefix.every((value, index) => bytes[offset + index] === value);
  return FORBIDDEN_PREFIXES.some((prefix) => startsWith(prefix)) || startsWith(TAR_MAGIC, 257);
}

export function readBlob(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === "function") return blob.arrayBuffer();
  // jsdom and some older engines lack Blob.arrayBuffer.
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsArrayBuffer(blob);
  });
}

// WebCrypto exists only in a secure context (HTTPS, localhost); elsewhere the
// part goes without its digest header and the server's own check stands.
export async function webCryptoSha256(data: ArrayBuffer): Promise<string | null> {
  const subtle = typeof crypto === "undefined" ? undefined : crypto.subtle;
  if (!subtle || typeof subtle.digest !== "function") return null;
  try {
    const hash = await subtle.digest("SHA-256", new Uint8Array(data));
    return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

function abortError(): Error {
  const error = new Error("Upload cancelled");
  error.name = "AbortError";
  return error;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, Math.max(0, ms));
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function browserStorage(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

export function browserUploadEnvironment(): UploadEnvironment {
  return {
    sleep,
    random: Math.random,
    digest: webCryptoSha256,
    readSlice: readBlob,
    isOnline: () => typeof navigator === "undefined" || navigator.onLine !== false,
    onOnlineChange: (listener) => {
      if (typeof window === "undefined") return () => {};
      const online = () => listener(true);
      const offline = () => listener(false);
      window.addEventListener("online", online);
      window.addEventListener("offline", offline);
      return () => {
        window.removeEventListener("online", online);
        window.removeEventListener("offline", offline);
      };
    },
    resume: {
      read: () => readUploadResumeRecord(browserStorage),
      write: (record) => writeUploadResumeRecord(browserStorage, record),
      clear: () => clearUploadResumeRecord(browserStorage)
    }
  };
}

interface HttpFailure {
  status: number;
  detailCode: string | null;
  retryAfterSeconds: number | null;
  detailData: Readonly<Record<string, unknown>> | null;
}

// Duck-typed: the transport raises lib/api.ts's ApiError.
function httpFailure(error: unknown): HttpFailure | null {
  if (typeof error !== "object" || error === null) return null;
  const fields = error as Partial<HttpFailure>;
  if (typeof fields.status !== "number" || fields.status <= 0) return null;
  return {
    status: fields.status,
    detailCode: typeof fields.detailCode === "string" ? fields.detailCode : null,
    retryAfterSeconds:
      typeof fields.retryAfterSeconds === "number" && fields.retryAfterSeconds > 0 ? fields.retryAfterSeconds : null,
    detailData: fields.detailData && typeof fields.detailData === "object" ? fields.detailData : null
  };
}

// What fetch and the part XHR raise when the request never got an answer.
function isNetworkError(error: unknown): boolean {
  return error instanceof TypeError && /failed to fetch|networkerror|network request failed|load failed/i.test(error.message);
}

// The session is gone (expired, abandoned elsewhere, or the account deleted).
class SessionGone extends Error {
  constructor() {
    super("Upload session not found");
    this.name = "SessionGone";
  }
}

// The session left `open` (completed, failed or completing elsewhere).
class SessionNotOpen extends Error {
  constructor() {
    super("Upload session is not open");
    this.name = "SessionNotOpen";
  }
}

function linkAbort(parent: AbortSignal, child: AbortController): () => void {
  if (parent.aborted) {
    child.abort();
    return () => {};
  }
  const onAbort = () => child.abort();
  parent.addEventListener("abort", onAbort, { once: true });
  return () => parent.removeEventListener("abort", onAbort);
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      }
    );
  });
}

function missingPartsFrom(data: Readonly<Record<string, unknown>> | null): number[] | null {
  const value = data?.missingParts;
  if (!Array.isArray(value)) return null;
  return value.filter((index): index is number => Number.isInteger(index) && index >= 0);
}

function sessionError(status: UploadSessionStatus): UploadEngineError {
  const raw = status.error;
  const code = typeof raw === "string" ? raw : raw?.code ?? null;
  const message = typeof raw === "object" && raw?.message ? raw.message : "Upload failed";
  return new UploadEngineError(code === "INTAKE_TOO_LARGE" ? 413 : 400, message, code ?? "INTAKE_REJECTED");
}

function sessionGoneError(): UploadEngineError {
  return new UploadEngineError(404, "Upload session not found", "upload_session_gone");
}

// The session cannot be completed any more, so a stored resume record for it only misleads.
function sessionIsDead(error: unknown): boolean {
  if (error instanceof SessionGone) return true;
  const failure = httpFailure(error);
  if (!failure) return false;
  if (failure.detailCode === "upload_session_gone" || failure.detailCode === "account_deleted") return true;
  return (failure.status === 400 || failure.status === 413) && (failure.detailCode ?? "").startsWith("INTAKE_");
}

interface RequestTicket {
  controller: AbortController;
  offline: boolean;
}

type Stage = "preparing" | "sending" | "verifying" | "waiting";

class UploadRun {
  private readonly transport: ChunkedUploadTransport;
  private readonly env: UploadEnvironment;
  private readonly options: ChunkedUploadOptions;
  private readonly file: File | null;
  private readonly controller = new AbortController();
  private readonly userSignal: AbortSignal;

  private sessionId: string | null = null;
  private sessionState: "open" | "completing" = "open";
  private token: string | null = null;
  private refreshing: Promise<void> | null = null;
  private parts: PartRange[] = [];
  private received = new Set<number>();
  private maxParallel = DEFAULT_PARALLEL_PARTS;
  private restarted = false;

  private confirmedBytes = 0;
  private inflight = new Map<number, number>();
  private reported = -1;
  private total = 0;

  private stage: Stage = "preparing";
  private waitInfo: { code: string | null; retryAfterSeconds: number | null } = { code: null, retryAfterSeconds: null };
  private transferring = 0;
  private backingOff = 0;
  private offline = false;
  private reauth = false;
  private lastStatusKey = "";
  private readonly tickets = new Set<RequestTicket>();
  private readonly wakers = new Set<() => void>();
  private unsubscribeOnline: (() => void) | null = null;
  private unlinkUser: (() => void) | null = null;

  constructor(options: ChunkedUploadOptions, file: File | null) {
    this.options = options;
    this.transport = options.transport;
    this.env = { ...browserUploadEnvironment(), ...(options.env ?? {}) };
    this.file = file;
    this.total = file?.size ?? 0;
    this.userSignal = options.signal ?? new AbortController().signal;
  }

  private get signal(): AbortSignal {
    return this.controller.signal;
  }

  async upload(): Promise<DemoSummary> {
    this.begin();
    try {
      this.emitStatus();
      await this.refuseArchive();
      await this.openSession();
      return await this.transferAndComplete();
    } catch (error) {
      throw this.settleFailure(error, true);
    } finally {
      this.end();
    }
  }

  async finish(sessionId: string): Promise<DemoSummary> {
    this.begin();
    try {
      this.sessionId = sessionId;
      this.sessionState = "completing";
      return await this.transferAndComplete();
    } catch (error) {
      throw this.settleFailure(error, false);
    } finally {
      this.end();
    }
  }

  private begin() {
    this.unlinkUser = linkAbort(this.userSignal, this.controller);
    this.offline = !this.env.isOnline();
    this.unsubscribeOnline = this.env.onOnlineChange((online) => {
      if (online) {
        this.offline = false;
        this.wakeAll();
      } else {
        this.offline = true;
        // A transfer that cannot finish goes back to the queue, uncounted.
        for (const ticket of this.tickets) {
          ticket.offline = true;
          ticket.controller.abort();
        }
      }
      this.emitStatus();
    });
  }

  private end() {
    this.unsubscribeOnline?.();
    this.unlinkUser?.();
    this.wakeAll();
  }

  private settleFailure(error: unknown, ownsSession: boolean): unknown {
    if (this.userSignal.aborted) {
      // Cancelled: the parts on the server go too, best effort.
      if (ownsSession && this.sessionId) {
        const sessionId = this.sessionId;
        this.clearRecordFor(sessionId);
        this.transport.deleteSession(sessionId).catch(() => {});
      }
      return abortError();
    }
    if (this.sessionId && sessionIsDead(error)) {
      this.clearRecordFor(this.sessionId);
    }
    if (error instanceof SessionGone) return sessionGoneError();
    if (error instanceof SessionNotOpen) return new UploadEngineError(409, "Upload session is not open", "upload_session_not_open");
    return error;
  }

  private async refuseArchive() {
    const file = this.requireFile();
    const head = await this.env.readSlice(file.slice(0, SIGNATURE_BYTES));
    this.throwIfAborted();
    if (hasForbiddenSignature(new Uint8Array(head))) {
      throw new UploadEngineError(400, "The file is not a CS2 demo", "INTAKE_CONTENT_MISMATCH");
    }
  }

  private async openSession() {
    const file = this.requireFile();
    if (this.options.replace) {
      await this.createSession(true);
      return;
    }
    const candidate = await this.findCandidate();
    if (candidate && (candidate.state === "open" || candidate.state === "completing") && (await this.isSameFile(candidate, file))) {
      this.adopt(candidate);
      this.sessionId = candidate.sessionId;
      this.writeRecord();
      if (candidate.state === "completing") {
        this.sessionState = "completing";
        return;
      }
      this.sessionState = "open";
      try {
        await this.refreshToken(null, this.signal);
      } catch (error) {
        if (error instanceof SessionGone) {
          // Gone between the lookup and now: start over, once.
          this.clearRecordFor(candidate.sessionId);
          this.restarted = true;
          await this.createSession(false);
          return;
        }
        throw error;
      }
      return;
    }
    await this.createSession(false);
  }

  private async findCandidate(): Promise<UploadSessionStatus | null> {
    const wanted = this.options.resumeSessionId;
    try {
      if (wanted && RESUME_SESSION_ID.test(wanted)) {
        try {
          return await abortable(this.transport.getSession(wanted), this.signal);
        } catch (error) {
          if (this.signal.aborted) throw error;
        }
      }
      return await abortable(this.transport.getCurrentSession(), this.signal);
    } catch {
      if (this.signal.aborted) throw abortError();
      // Advisory: without an answer a new session is opened, and the server says if one is in the way.
      return null;
    }
  }

  // The size must match; then part 0's digest decides when it can be checked,
  // else the player's own choice, the stored record, or the file name.
  private async isSameFile(candidate: UploadSessionStatus, file: File): Promise<boolean> {
    if (candidate.size !== file.size) return false;
    const partSize = candidate.partSize > 0 ? candidate.partSize : file.size;
    if (candidate.part0Sha256) {
      const head = await this.env.readSlice(file.slice(0, Math.min(partSize, file.size)));
      const digest = await this.env.digest(head);
      this.throwIfAborted();
      if (digest) return digest.toLowerCase() === candidate.part0Sha256.toLowerCase();
    }
    if (this.options.resumeSessionId === candidate.sessionId) return true;
    const record = this.env.resume.read();
    if (record && record.sessionId === candidate.sessionId) {
      return record.name === file.name && record.size === file.size && record.lastModified === file.lastModified;
    }
    return candidate.filename === file.name;
  }

  private async createSession(replace: boolean) {
    this.throwIfAborted();
    const file = this.requireFile();
    const request: UploadSessionCreateRequest = { filename: file.name, size: file.size };
    if (file.type) request.contentType = file.type;
    if (replace) request.replace = true;
    // Not cut short by a cancel: the session it creates must be known to be deleted.
    const created = await this.transport.createSession(request);
    this.sessionId = created.sessionId;
    this.sessionState = "open";
    this.token = created.uploadToken;
    this.adopt(created);
    this.writeRecord();
    this.throwIfAborted();
  }

  private adopt(session: {
    partSize: number;
    partCount: number;
    receivedParts: number[];
    maxParallelParts?: number | null;
  }) {
    const size = this.file?.size ?? this.total;
    const parts = planParts(size, session.partSize);
    if (parts.length === 0 || (session.partCount > 0 && parts.length !== session.partCount)) {
      throw new UploadEngineError(400, "The server's part plan does not fit this file", "upload_part_invalid");
    }
    this.parts = parts;
    if (session.maxParallelParts !== undefined && session.maxParallelParts !== null) {
      this.maxParallel = clampParallelParts(session.maxParallelParts);
    }
    this.received = new Set();
    this.confirmedBytes = 0;
    this.inflight.clear();
    for (const index of session.receivedParts ?? []) {
      const range = parts[index];
      if (range && !this.received.has(index)) {
        this.received.add(index);
        this.confirmedBytes += range.end - range.start;
      }
    }
    this.reportProgress();
  }

  private async transferAndComplete(): Promise<DemoSummary> {
    let stateChecks = 0;
    for (;;) {
      try {
        if (this.sessionState === "open") {
          await this.uploadMissing();
        }
        return await this.completeLoop();
      } catch (error) {
        if (this.signal.aborted) throw error;
        if (error instanceof SessionGone) {
          if (!this.file || this.restarted) throw error;
          // Expired or abandoned elsewhere: forget it and send the file again, once.
          this.restarted = true;
          if (this.sessionId) this.clearRecordFor(this.sessionId);
          this.reported = -1;
          this.stage = "preparing";
          this.emitStatus();
          await this.createSession(false);
          continue;
        }
        if (error instanceof SessionNotOpen) {
          stateChecks += 1;
          if (stateChecks > MAX_STATE_CHECKS || !this.sessionId) throw error;
          const status = await this.lookupSession(this.sessionId);
          if (status.state === "completed" && status.demo) {
            this.clearRecordFor(status.sessionId);
            return status.demo;
          }
          if (status.state === "failed" || status.state === "completed") {
            throw sessionError(status);
          }
          if (status.state === "open" && this.file) {
            this.sessionState = "open";
            this.adopt(status);
            this.token = null;
            await this.refreshToken(null, this.signal);
          } else if (status.state === "completing") {
            this.sessionState = "completing";
          }
          continue;
        }
        throw error;
      }
    }
  }

  private async lookupSession(sessionId: string): Promise<UploadSessionStatus> {
    try {
      return await abortable(this.transport.getSession(sessionId), this.signal);
    } catch (error) {
      if (httpFailure(error)?.status === 404) throw new SessionGone();
      throw error;
    }
  }

  private async uploadMissing() {
    const queue = this.parts.filter((part) => !this.received.has(part.index)).map((part) => part.index);
    if (queue.length === 0) return;
    this.stage = "sending";
    this.emitStatus();
    const workers = Math.min(this.maxParallel, queue.length);
    const group = new AbortController();
    const unlink = linkAbort(this.signal, group);
    let failure: unknown = null;
    try {
      await Promise.all(
        Array.from({ length: workers }, async () => {
          try {
            while (queue.length > 0 && !group.signal.aborted) {
              const index = queue.shift() as number;
              await this.sendPart(index, group.signal);
            }
          } catch (error) {
            if (failure === null) {
              failure = error;
              group.abort();
            }
          }
        })
      );
    } finally {
      unlink();
    }
    if (failure !== null) throw failure;
    this.throwIfAborted();
  }

  private async sendPart(index: number, signal: AbortSignal) {
    const range = this.parts[index];
    const file = this.requireFile();
    let attempts = 0;
    let busyWaits = 0;
    let refreshes = 0;
    for (;;) {
      await this.whenOnline(signal);
      if (this.refreshing) await abortable(this.refreshing, signal);
      const token = this.token;
      if (!token) throw new SessionGone();
      const ticket: RequestTicket = { controller: new AbortController(), offline: false };
      const unlink = linkAbort(signal, ticket.controller);
      this.tickets.add(ticket);
      this.transferring += 1;
      this.emitStatus();
      let next: { kind: "now" } | { kind: "wait"; ms: number; busy: boolean } | { kind: "refresh" };
      try {
        const body = await this.env.readSlice(file.slice(range.start, range.end));
        const sha256 = await this.env.digest(body);
        if (ticket.controller.signal.aborted) throw abortError();
        await this.transport.putPart({
          sessionId: this.sessionId as string,
          index,
          token,
          body,
          sha256,
          signal: ticket.controller.signal,
          onProgress: (loaded) => this.setInflight(index, loaded)
        });
        this.confirmPart(range);
        return;
      } catch (error) {
        this.setInflight(index, 0);
        if (signal.aborted) throw isAbortError(error) ? error : abortError();
        if (ticket.offline || (isNetworkError(error) && !this.env.isOnline())) {
          // Offline: not this part's fault; wait for the network, uncounted.
          if (!this.env.isOnline()) this.offline = true;
          next = { kind: "now" };
        } else {
          const failure = httpFailure(error);
          if (!failure) {
            if (!isNetworkError(error)) throw error;
            attempts += 1;
            if (attempts >= MAX_PART_ATTEMPTS) throw error;
            next = { kind: "wait", ms: retryDelayMs(attempts, null, this.env.random), busy: false };
          } else if (failure.status === 401 || failure.status === 404) {
            // The token, or the session: the cookie route tells which.
            refreshes += 1;
            if (refreshes > MAX_TOKEN_REFRESHES_PER_PART) throw new SessionGone();
            next = { kind: "refresh" };
          } else if (failure.status === 409 && failure.detailCode === "upload_session_not_open") {
            throw new SessionNotOpen();
          } else if (failure.status === 503 && failure.detailCode === "INTAKE_BUSY") {
            busyWaits += 1;
            if (busyWaits > MAX_BUSY_WAITS) throw error;
            next = { kind: "wait", ms: this.busyDelay(failure.retryAfterSeconds, PART_BUSY_RETRY_SECONDS), busy: true };
          } else if (
            failure.status === 422 ||
            failure.status === 408 ||
            failure.status === 429 ||
            failure.status >= 500
          ) {
            attempts += 1;
            if (attempts >= MAX_PART_ATTEMPTS) throw error;
            next = { kind: "wait", ms: retryDelayMs(attempts, failure.retryAfterSeconds, this.env.random), busy: false };
          } else {
            // The file (part 0 is an archive), the request (a wrong length) or the origin: no retry fixes it.
            throw error;
          }
        }
      } finally {
        this.transferring -= 1;
        this.tickets.delete(ticket);
        unlink();
        this.emitStatus();
      }
      if (next.kind === "refresh") {
        await this.refreshToken(token, signal);
      } else if (next.kind === "wait") {
        await this.backoff(next.ms, signal);
      }
    }
  }

  private busyDelay(retryAfterSeconds: number | null, fallbackSeconds: number): number {
    return (retryAfterSeconds ?? fallbackSeconds) * 1000 + Math.floor(this.env.random() * 1000);
  }

  // One refresh at a time; a worker holding an older token just waits for it.
  private refreshToken(staleToken: string | null, signal: AbortSignal): Promise<void> {
    if (staleToken !== null && this.token !== staleToken) return Promise.resolve();
    if (!this.refreshing) {
      this.refreshing = this.fetchToken(signal).finally(() => {
        this.refreshing = null;
      });
    }
    return abortable(this.refreshing, signal);
  }

  private async fetchToken(signal: AbortSignal) {
    let attempts = 0;
    let reauthWaits = 0;
    for (;;) {
      await this.whenOnline(signal);
      try {
        const response = await abortable(this.transport.refreshToken(this.sessionId as string), signal);
        this.token = response.uploadToken;
        if (this.reauth) {
          this.reauth = false;
          this.emitStatus();
        }
        return;
      } catch (error) {
        if (signal.aborted) throw abortError();
        const failure = httpFailure(error);
        if (!failure) {
          if (!isNetworkError(error)) throw error;
          if (!this.env.isOnline()) {
            this.offline = true;
            continue;
          }
          attempts += 1;
          if (attempts >= MAX_PART_ATTEMPTS) throw error;
          await this.backoff(retryDelayMs(attempts, null, this.env.random), signal);
          continue;
        }
        if (failure.status === 401 && failure.detailCode !== "account_deleted") {
          // Signed out: hold every part until a sign-in (in any tab) makes the cookie good again.
          this.reauth = true;
          this.emitStatus();
          await this.pause(this.reauthDelay(reauthWaits), signal);
          reauthWaits += 1;
          continue;
        }
        if (failure.status === 404) throw new SessionGone();
        if (failure.status === 409) throw new SessionNotOpen();
        if (failure.status >= 500 || failure.status === 408 || failure.status === 429) {
          attempts += 1;
          if (attempts >= MAX_PART_ATTEMPTS) throw error;
          await this.backoff(retryDelayMs(attempts, failure.retryAfterSeconds, this.env.random), signal);
          continue;
        }
        throw error;
      }
    }
  }

  private reauthDelay(waits: number): number {
    return Math.min(REAUTH_MAX_RETRY_MS, REAUTH_RETRY_MS * 2 ** waits);
  }

  private async completeLoop(): Promise<DemoSummary> {
    const sessionId = this.sessionId as string;
    this.stage = "verifying";
    this.reportProgress();
    this.emitStatus();
    let attempts = 0;
    let busyWaits = 0;
    let missingRounds = 0;
    let reauthWaits = 0;
    for (;;) {
      await this.whenOnline(this.signal);
      try {
        const result = await abortable(this.transport.completeSession(sessionId), this.signal);
        if (this.reauth) {
          this.reauth = false;
          this.emitStatus();
        }
        if (result.kind === "demo") {
          this.clearRecordFor(sessionId);
          return result.demo;
        }
        // Still completing (this or an earlier request): ask again shortly.
        // Completing again, not just reading the state, lets a lease left by a
        // restarted API be taken over.
        this.sessionState = "completing";
        await this.pause(COMPLETE_POLL_MS, this.signal);
        continue;
      } catch (error) {
        if (this.signal.aborted) throw isAbortError(error) ? error : abortError();
        const failure = httpFailure(error);
        if (!failure) {
          if (!isNetworkError(error)) throw error;
          if (!this.env.isOnline()) {
            this.offline = true;
            this.emitStatus();
            continue;
          }
          attempts += 1;
          if (attempts >= MAX_PART_ATTEMPTS) throw error;
          await this.backoff(retryDelayMs(attempts, null, this.env.random), this.signal);
          continue;
        }
        const { status, detailCode: code, retryAfterSeconds } = failure;
        if (status === 409 && code === "upload_parts_missing") {
          missingRounds += 1;
          if (!this.file || missingRounds > MAX_MISSING_ROUNDS) throw error;
          this.sessionState = "open";
          await this.requeueMissing(missingPartsFrom(failure.detailData));
          await this.uploadMissing();
          this.stage = "verifying";
          this.reportProgress();
          this.emitStatus();
          continue;
        }
        if (status === 409 && code === "upload_parts_in_flight") {
          busyWaits += 1;
          if (busyWaits > MAX_BUSY_WAITS) throw error;
          await this.pause((retryAfterSeconds ?? IN_FLIGHT_RETRY_SECONDS) * 1000, this.signal);
          continue;
        }
        if (status === 409 && code === "upload_session_not_open") throw new SessionNotOpen();
        if (status === 503 && code === "INTAKE_BUSY") {
          // Another completion (perhaps this upload's own earlier request) holds the intake.
          busyWaits += 1;
          if (busyWaits > MAX_BUSY_WAITS) throw error;
          await this.pause(this.busyDelay(retryAfterSeconds, COMPLETE_BUSY_RETRY_SECONDS), this.signal);
          continue;
        }
        if ((status === 429 || status === 503) && code && QUOTA_WAIT_CODES.has(code)) {
          // Every byte is kept on the server; complete again once the cap or queue may have room.
          this.stage = "waiting";
          this.waitInfo = { code, retryAfterSeconds };
          this.emitStatus();
          await this.pause(Math.max(QUOTA_MIN_RETRY_SECONDS, retryAfterSeconds ?? QUOTA_RETRY_SECONDS) * 1000, this.signal);
          this.stage = "verifying";
          this.emitStatus();
          continue;
        }
        if (status === 401 && code !== "account_deleted") {
          this.reauth = true;
          this.emitStatus();
          await this.pause(this.reauthDelay(reauthWaits), this.signal);
          reauthWaits += 1;
          continue;
        }
        if (status === 404) throw new SessionGone();
        if (status >= 500 || status === 408) {
          attempts += 1;
          if (attempts >= MAX_PART_ATTEMPTS) throw error;
          await this.backoff(retryDelayMs(attempts, retryAfterSeconds, this.env.random), this.signal);
          continue;
        }
        // The daily count (the session stays open until it expires), the intake's
        // refusal, a deleted account: settled, not retried.
        throw error;
      }
    }
  }

  private async requeueMissing(missing: number[] | null) {
    let indexes = missing;
    if (!indexes) {
      const status = await this.lookupSession(this.sessionId as string);
      const received = new Set(status.receivedParts ?? []);
      indexes = this.parts.filter((part) => !received.has(part.index)).map((part) => part.index);
    }
    for (const index of indexes) {
      const range = this.parts[index];
      if (range && this.received.delete(index)) {
        this.confirmedBytes -= range.end - range.start;
      }
    }
  }

  private confirmPart(range: PartRange) {
    this.inflight.delete(range.index);
    if (!this.received.has(range.index)) {
      this.received.add(range.index);
      this.confirmedBytes += range.end - range.start;
    }
    this.reportProgress();
  }

  private setInflight(index: number, loaded: number) {
    if (loaded > 0) this.inflight.set(index, loaded);
    else this.inflight.delete(index);
    this.reportProgress();
  }

  // Confirmed bytes plus what is in flight, never backwards, and short of the
  // total until the server has every part (the page reads a full bar as "checking").
  private reportProgress() {
    if (!this.file || this.parts.length === 0) return;
    const total = this.file.size;
    let loaded = this.confirmedBytes;
    for (const value of this.inflight.values()) loaded += value;
    loaded = this.received.size === this.parts.length ? total : Math.min(loaded, Math.max(0, total - 1));
    loaded = Math.max(loaded, this.reported);
    if (loaded === this.reported) return;
    this.reported = loaded;
    this.options.onProgress?.({ loaded, total });
  }

  private currentStatus(): UploadStatus {
    if (this.reauth) return { phase: "paused", reason: "reauth" };
    if (this.offline) return { phase: "paused", reason: "offline" };
    if (this.stage === "waiting") {
      return { phase: "waiting", reason: "quota", code: this.waitInfo.code, retryAfterSeconds: this.waitInfo.retryAfterSeconds };
    }
    if (this.backingOff > 0 && this.transferring === 0) return { phase: "paused", reason: "retrying" };
    if (this.stage === "sending") return { phase: "sending" };
    if (this.stage === "verifying") return { phase: "verifying" };
    return { phase: "preparing" };
  }

  private emitStatus() {
    const status = this.currentStatus();
    const key = JSON.stringify(status);
    if (key === this.lastStatusKey) return;
    this.lastStatusKey = key;
    this.options.onStatus?.(status);
  }

  private async backoff(ms: number, signal: AbortSignal) {
    this.backingOff += 1;
    this.emitStatus();
    try {
      await this.pause(ms, signal);
    } finally {
      this.backingOff -= 1;
      this.emitStatus();
    }
  }

  // A sleep the network coming back cuts short.
  private async pause(ms: number, signal: AbortSignal) {
    const wake = new AbortController();
    const unlink = linkAbort(signal, wake);
    const waker = () => wake.abort();
    this.wakers.add(waker);
    try {
      await this.env.sleep(ms, wake.signal);
    } catch (error) {
      if (signal.aborted) throw isAbortError(error) ? error : abortError();
    } finally {
      this.wakers.delete(waker);
      unlink();
    }
  }

  private async whenOnline(signal: AbortSignal) {
    while (this.offline) {
      this.emitStatus();
      await this.pause(OFFLINE_RECHECK_MS, signal);
      if (this.env.isOnline()) this.offline = false;
    }
    this.emitStatus();
  }

  private wakeAll() {
    for (const waker of Array.from(this.wakers)) waker();
  }

  private writeRecord() {
    if (!this.file || !this.sessionId) return;
    this.env.resume.write({
      sessionId: this.sessionId,
      name: this.file.name,
      size: this.file.size,
      lastModified: this.file.lastModified
    });
  }

  private clearRecordFor(sessionId: string) {
    const record = this.env.resume.read();
    if (record && record.sessionId === sessionId) this.env.resume.clear();
  }

  private requireFile(): File {
    if (!this.file) throw new UploadEngineError(409, "Choose the file to continue", "upload_parts_missing");
    return this.file;
  }

  private throwIfAborted() {
    if (this.signal.aborted) throw abortError();
  }
}

// Uploads `file` through an upload session and returns the new match.
export function runChunkedUpload(file: File, options: ChunkedUploadOptions): Promise<DemoSummary> {
  return new UploadRun(options, file).upload();
}

// Completes a session whose parts are all on the server (the page's "完成上传"), without the file.
export function finishChunkedUpload(sessionId: string, options: ChunkedUploadOptions): Promise<DemoSummary> {
  return new UploadRun(options, null).finish(sessionId);
}
