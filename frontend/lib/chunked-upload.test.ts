import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import {
  MAX_PART_ATTEMPTS,
  backoffDelayMs,
  clampParallelParts,
  finishChunkedUpload,
  hasForbiddenSignature,
  planParts,
  retryDelayMs,
  runChunkedUpload,
  webCryptoSha256,
  type ChunkedUploadTransport,
  type UploadEnvironment
} from "@/lib/chunked-upload";
import { demoSummary } from "@/lib/test-fixtures/review";
import type { DemoSummary } from "@/types/demo";
import type {
  UploadPartReceipt,
  UploadPartRequest,
  UploadResumeRecord,
  UploadSessionCreated,
  UploadSessionStatus,
  UploadStatus
} from "@/types/upload";

const SID = "0123456789abcdef0123456789abcdef";
const SID2 = "fedcba9876543210fedcba9876543210";

// A stand-in File: the engine only reads name, size, type, lastModified and slice().
function fakeFile(data: Uint8Array, name = "match.dem", lastModified = 1000): File {
  return {
    name,
    size: data.length,
    type: "",
    lastModified,
    slice: (start = 0, end = data.length) => ({ bytes: data.slice(start, end) })
  } as unknown as File;
}

function fileBytes(size: number): Uint8Array {
  // Starts 03 0a 11 ...: no archive signature.
  return Uint8Array.from({ length: size }, (_, index) => (index * 7 + 3) % 251);
}

const readSlice = async (blob: Blob) => (blob as unknown as { bytes: Uint8Array }).bytes.slice().buffer as ArrayBuffer;
const fakeDigest = async (data: ArrayBuffer) => `d:${Array.from(new Uint8Array(data)).join(",")}`;

function abortError(): Error {
  const error = new Error("Upload cancelled");
  error.name = "AbortError";
  return error;
}

interface PendingPut {
  request: UploadPartRequest;
  settled: boolean;
  resolve: () => void;
  reject: (error: unknown) => void;
}

interface HarnessOptions {
  size?: number;
  partSize?: number;
  maxParallelParts?: number;
  hold?: boolean;
  current?: UploadSessionStatus | null;
  record?: UploadResumeRecord | null;
  digest?: UploadEnvironment["digest"];
  random?: () => number;
}

function harness(options: HarnessOptions = {}) {
  const size = options.size ?? 10;
  const partSize = options.partSize ?? 4;
  const partCount = Math.ceil(size / partSize);
  const data = fileBytes(size);
  const file = fakeFile(data);
  const demo = demoSummary({ id: "demo-1", name: "match.dem", status: "queued" });

  const listeners = new Set<(online: boolean) => void>();
  const network = {
    online: true,
    set(online: boolean) {
      network.online = online;
      for (const listener of Array.from(listeners)) listener(online);
    }
  };
  const resume = {
    record: options.record ?? (null as UploadResumeRecord | null),
    read: vi.fn((): UploadResumeRecord | null => resume.record),
    write: vi.fn((record: UploadResumeRecord) => {
      resume.record = record;
    }),
    clear: vi.fn(() => {
      resume.record = null;
    })
  };
  const env: Partial<UploadEnvironment> = {
    random: options.random ?? (() => 0.5),
    digest: options.digest ?? fakeDigest,
    readSlice,
    isOnline: () => network.online,
    onOnlineChange: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    resume
  };

  const pending: PendingPut[] = [];
  const stored = new Set<number>();
  let inFlight = 0;
  let maxInFlight = 0;
  let hold = options.hold ?? false;

  const receipt = (request: UploadPartRequest): UploadPartReceipt => {
    stored.add(request.index);
    return {
      index: request.index,
      sizeBytes: request.body instanceof ArrayBuffer ? request.body.byteLength : 0,
      sha256: request.sha256 ?? "server",
      receivedCount: stored.size
    };
  };
  const created = (overrides: Partial<UploadSessionCreated> = {}): UploadSessionCreated => ({
    sessionId: SID,
    uploadToken: "token-1",
    partSize,
    partCount,
    maxParallelParts: options.maxParallelParts ?? 2,
    expiresAt: "2026-10-05T00:00:00Z",
    receivedParts: [],
    ...overrides
  });
  const status = (overrides: Partial<UploadSessionStatus> = {}): UploadSessionStatus => ({
    sessionId: SID,
    state: "open",
    filename: "match.dem",
    size,
    partSize,
    partCount,
    receivedParts: [],
    receivedBytes: 0,
    expiresAt: "2026-10-05T00:00:00Z",
    ...overrides
  });

  const transport = {
    getCurrentSession: vi.fn(async (): Promise<UploadSessionStatus | null> => options.current ?? null),
    getSession: vi.fn<ChunkedUploadTransport["getSession"]>(async () => {
      throw new ApiError(404, "Not found");
    }),
    createSession: vi.fn<ChunkedUploadTransport["createSession"]>(async () => created()),
    refreshToken: vi.fn<ChunkedUploadTransport["refreshToken"]>(async () => ({
      ...(options.current ?? status()),
      uploadToken: "token-2"
    })),
    putPart: vi.fn((request: UploadPartRequest): Promise<UploadPartReceipt> => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      if (!hold) {
        inFlight -= 1;
        return Promise.resolve(receipt(request));
      }
      return new Promise<UploadPartReceipt>((resolve, reject) => {
        const entry: PendingPut = {
          request,
          settled: false,
          resolve: () => {
            if (entry.settled) return;
            entry.settled = true;
            inFlight -= 1;
            resolve(receipt(request));
          },
          reject: (error) => {
            if (entry.settled) return;
            entry.settled = true;
            inFlight -= 1;
            reject(error);
          }
        };
        pending.push(entry);
        request.signal?.addEventListener("abort", () => entry.reject(abortError()), { once: true });
      });
    }),
    completeSession: vi.fn<ChunkedUploadTransport["completeSession"]>(async () => ({ kind: "demo", demo, created: true })),
    deleteSession: vi.fn<ChunkedUploadTransport["deleteSession"]>(async () => {})
  } satisfies ChunkedUploadTransport;

  const statuses: UploadStatus[] = [];
  const progress: number[] = [];
  const controller = new AbortController();

  function settle(promise: Promise<DemoSummary>) {
    const outcome: { done: boolean; value?: DemoSummary; error?: unknown } = { done: false };
    promise.then(
      (value) => {
        outcome.done = true;
        outcome.value = value;
      },
      (error: unknown) => {
        outcome.done = true;
        outcome.error = error;
      }
    );
    return outcome;
  }

  return {
    file,
    data,
    demo,
    size,
    partSize,
    partCount,
    network,
    resume,
    transport,
    pending,
    statuses,
    progress,
    controller,
    created,
    status,
    get maxInFlight() {
      return maxInFlight;
    },
    open: () => pending.filter((entry) => !entry.settled),
    setHold(value: boolean) {
      hold = value;
    },
    run(extra: { resumeSessionId?: string; replace?: boolean } = {}) {
      return settle(
        runChunkedUpload(file, {
          transport,
          env,
          signal: controller.signal,
          onStatus: (value) => statuses.push(value),
          onProgress: ({ loaded }) => progress.push(loaded),
          ...extra
        })
      );
    },
    finish(sessionId = SID) {
      return settle(
        finishChunkedUpload(sessionId, {
          transport,
          env,
          signal: controller.signal,
          onStatus: (value) => statuses.push(value)
        })
      );
    }
  };
}

async function flush(times = 6) {
  for (let index = 0; index < times; index += 1) {
    await vi.advanceTimersByTimeAsync(0);
  }
}

const sentIndexes = (h: ReturnType<typeof harness>) => h.transport.putPart.mock.calls.map(([request]) => request.index);

describe("planning and pure helpers", () => {
  it("cuts a file into server-sized parts with a short last one", () => {
    expect(planParts(10, 4)).toEqual([
      { index: 0, start: 0, end: 4 },
      { index: 1, start: 4, end: 8 },
      { index: 2, start: 8, end: 10 }
    ]);
    expect(planParts(8, 4)).toHaveLength(2);
    expect(planParts(1024 * 1024 * 1024, 8 * 1024 * 1024)).toHaveLength(128);
    expect(planParts(0, 4)).toEqual([]);
    expect(planParts(10, 0)).toEqual([]);
  });

  it("backs off with full jitter, doubling from 1 s up to 30 s", () => {
    expect(backoffDelayMs(1, () => 0)).toBe(0);
    expect(backoffDelayMs(1, () => 0.5)).toBe(500);
    expect(backoffDelayMs(2, () => 0.5)).toBe(1000);
    expect(backoffDelayMs(3, () => 0.999)).toBeLessThan(4000);
    for (let attempt = 1; attempt <= MAX_PART_ATTEMPTS; attempt += 1) {
      for (const random of [0, 0.25, 0.75, 0.9999]) {
        const delay = backoffDelayMs(attempt, () => random);
        expect(delay).toBeGreaterThanOrEqual(0);
        expect(delay).toBeLessThan(Math.min(30_000, 1000 * 2 ** (attempt - 1)));
      }
    }
    expect(backoffDelayMs(20, () => 0.9999)).toBeLessThan(30_000);
    // Retry-After is a floor, never shortened by the jitter.
    expect(retryDelayMs(1, 7, () => 0.9)).toBe(7000);
    expect(retryDelayMs(6, 2, () => 0.5)).toBe(15_000);
  });

  it("knows archives and programs by their first bytes", () => {
    const head = (...values: number[]) => Uint8Array.from(values);
    expect(hasForbiddenSignature(head(0x50, 0x4b, 0x03, 0x04, 0))).toBe(true);
    expect(hasForbiddenSignature(head(0x1f, 0x8b, 8))).toBe(true);
    expect(hasForbiddenSignature(head(0x42, 0x5a, 0x68, 0x39))).toBe(true);
    expect(hasForbiddenSignature(head(0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c))).toBe(true);
    expect(hasForbiddenSignature(head(0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0))).toBe(true);
    expect(hasForbiddenSignature(head(0x4d, 0x5a, 0x90))).toBe(true);
    expect(hasForbiddenSignature(head(0x7f, 0x45, 0x4c, 0x46))).toBe(true);
    const tar = new Uint8Array(512);
    tar.set([0x75, 0x73, 0x74, 0x61, 0x72], 257);
    expect(hasForbiddenSignature(tar)).toBe(true);
    expect(hasForbiddenSignature(new TextEncoder().encode("PBDEMS2\0rest"))).toBe(false);
    expect(hasForbiddenSignature(new TextEncoder().encode("HL2DEMO\0rest"))).toBe(false);
  });

  it("keeps the parallel parts between 1 and 6", () => {
    expect(clampParallelParts(4)).toBe(4);
    expect(clampParallelParts(0)).toBe(1);
    expect(clampParallelParts(12)).toBe(6);
    expect(clampParallelParts(null)).toBe(4);
  });

  it("hashes with WebCrypto, and sends no digest where it is missing", async () => {
    expect(await webCryptoSha256(new TextEncoder().encode("abc").buffer as ArrayBuffer)).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
    vi.stubGlobal("crypto", undefined);
    try {
      expect(await webCryptoSha256(new ArrayBuffer(3))).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("runChunkedUpload", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens a session, sends every part with its digest and completes into the match", async () => {
    const h = harness();
    const outcome = h.run();
    await flush();

    expect(outcome.done).toBe(true);
    expect(outcome.value).toBe(h.demo);
    expect(h.transport.getCurrentSession).toHaveBeenCalledTimes(1);
    expect(h.transport.createSession).toHaveBeenCalledWith({ filename: "match.dem", size: 10 });
    expect(sentIndexes(h).sort()).toEqual([0, 1, 2]);
    const first = h.transport.putPart.mock.calls.find(([request]) => request.index === 0)?.[0];
    expect(first).toMatchObject({ sessionId: SID, token: "token-1", sha256: "d:3,10,17,24" });
    expect(new Uint8Array(first?.body as ArrayBuffer)).toEqual(h.data.slice(0, 4));
    expect(h.transport.completeSession).toHaveBeenCalledWith(SID);
    expect(h.statuses.map((status) => status.phase)).toEqual(["preparing", "sending", "verifying"]);
    // The record lasts as long as the session, and goes once the match exists.
    expect(h.resume.write).toHaveBeenCalledWith({ sessionId: SID, name: "match.dem", size: 10, lastModified: 1000 });
    expect(h.resume.record).toBeNull();
  });

  it("refuses an archive picked as a .dem before any request", async () => {
    const zip = Uint8Array.from([0x50, 0x4b, 0x03, 0x04, 1, 2, 3, 4]);
    const h = harness();
    const outcome: { error?: unknown } = {};
    runChunkedUpload(fakeFile(zip, "match.dem"), { transport: h.transport, env: { readSlice, resume: h.resume } }).catch(
      (error: unknown) => {
        outcome.error = error;
      }
    );
    await flush();

    expect(outcome.error).toMatchObject({ name: "UploadEngineError", status: 400, detailCode: "INTAKE_CONTENT_MISMATCH" });
    expect(h.transport.getCurrentSession).not.toHaveBeenCalled();
    expect(h.transport.createSession).not.toHaveBeenCalled();
  });

  it("keeps no more parts in flight than the server allows", async () => {
    const h = harness({ size: 40, partSize: 4, maxParallelParts: 3, hold: true });
    const outcome = h.run();
    await flush();
    expect(h.open()).toHaveLength(3);

    while (h.open().length > 0) {
      h.open()[0].resolve();
      await flush();
      expect(h.open().length).toBeLessThanOrEqual(3);
    }
    expect(h.maxInFlight).toBe(3);
    expect(new Set(sentIndexes(h)).size).toBe(10);
    expect(outcome.value).toBe(h.demo);
  });

  it("reports confirmed plus in-flight bytes, never backwards and short of the total until every part is in", async () => {
    const h = harness({ size: 10, partSize: 4, maxParallelParts: 2, hold: true });
    const outcome = h.run();
    await flush();
    const [first, second] = h.open();
    first.request.onProgress?.(2);
    second.request.onProgress?.(3);
    expect(h.progress.at(-1)).toBe(5);
    // A failed transfer drops its bytes, but the bar does not go back.
    second.reject(new ApiError(500, "boom"));
    await flush();
    expect(h.progress.at(-1)).toBe(5);
    first.resolve();
    await flush();
    expect(h.progress.at(-1)).toBe(5);
    await vi.advanceTimersByTimeAsync(1000);
    for (const entry of h.open()) {
      entry.request.onProgress?.(entry.request.index === 2 ? 2 : 4);
    }
    // Every byte in flight, not yet confirmed: one short of the total.
    expect(h.progress.at(-1)).toBe(9);
    for (const entry of h.open()) entry.resolve();
    await flush();

    expect(h.progress.at(-1)).toBe(10);
    expect(h.progress).toEqual([...h.progress].sort((a, b) => a - b));
    expect(h.progress.slice(0, -1).every((value) => value < 10)).toBe(true);
    expect(outcome.value).toBe(h.demo);
  });

  it("retries a failed part after a jittered backoff, then gives up after 8 tries and keeps the session", async () => {
    const h = harness({ size: 4, partSize: 4, random: () => 0.5 });
    h.transport.putPart.mockRejectedValue(new TypeError("Failed to fetch"));
    const outcome = h.run();
    await flush();
    expect(h.transport.putPart).toHaveBeenCalledTimes(1);

    // Attempt 1 waits half of 1 s with random = 0.5.
    await vi.advanceTimersByTimeAsync(499);
    expect(h.transport.putPart).toHaveBeenCalledTimes(1);
    expect(h.statuses.at(-1)).toEqual({ phase: "paused", reason: "retrying" });
    await vi.advanceTimersByTimeAsync(1);
    expect(h.transport.putPart).toHaveBeenCalledTimes(2);
    // Attempt 2 waits half of 2 s.
    await vi.advanceTimersByTimeAsync(999);
    expect(h.transport.putPart).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.transport.putPart).toHaveBeenCalledTimes(3);

    await vi.advanceTimersByTimeAsync(120_000);
    expect(h.transport.putPart).toHaveBeenCalledTimes(MAX_PART_ATTEMPTS);
    expect(outcome.error).toBeInstanceOf(TypeError);
    // Still open on the server: the resume line can pick it up.
    expect(h.resume.record?.sessionId).toBe(SID);
    expect(h.transport.deleteSession).not.toHaveBeenCalled();
  });

  it("waits out Retry-After, and a busy part pool does not use up the tries", async () => {
    const h = harness({ size: 4, partSize: 4, random: () => 0 });
    h.transport.putPart.mockRejectedValueOnce(new ApiError(429, "Slow down", null, 7));
    for (let index = 0; index < MAX_PART_ATTEMPTS + 2; index += 1) {
      h.transport.putPart.mockRejectedValueOnce(new ApiError(503, "Busy", "INTAKE_BUSY", 2));
    }
    const outcome = h.run();
    await flush();
    await vi.advanceTimersByTimeAsync(6999);
    expect(h.transport.putPart).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.transport.putPart).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1999);
    expect(h.transport.putPart).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.transport.putPart).toHaveBeenCalledTimes(3);

    await vi.advanceTimersByTimeAsync(2000 * (MAX_PART_ATTEMPTS + 2));
    expect(outcome.value).toBe(h.demo);
    expect(h.transport.putPart).toHaveBeenCalledTimes(MAX_PART_ATTEMPTS + 4);
  });

  it("pauses while offline without counting it, and carries on as soon as the network is back", async () => {
    const h = harness({ size: 8, partSize: 4, maxParallelParts: 2, hold: true });
    const outcome = h.run();
    await flush();
    expect(h.open()).toHaveLength(2);

    h.network.set(false);
    await flush();
    expect(h.open()).toHaveLength(0);
    expect(h.statuses.at(-1)).toEqual({ phase: "paused", reason: "offline" });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(h.transport.putPart).toHaveBeenCalledTimes(2);

    h.network.set(true);
    await flush();
    expect(h.open()).toHaveLength(2);
    expect(h.statuses.at(-1)).toEqual({ phase: "sending" });
    for (const entry of h.open()) entry.resolve();
    await flush();
    expect(outcome.value).toBe(h.demo);

    // Many drops later the parts still have every try left.
    const again = harness({ size: 4, partSize: 4, hold: true });
    const second = again.run();
    await flush();
    for (let drop = 0; drop < MAX_PART_ATTEMPTS + 2; drop += 1) {
      again.network.set(false);
      await flush();
      again.network.set(true);
      await flush();
    }
    again.open()[0].resolve();
    await flush();
    expect(second.value).toBe(again.demo);
  });

  it("swaps in a new token through the cookie when a part's token is refused", async () => {
    const h = harness({ size: 8, partSize: 4, maxParallelParts: 2 });
    h.transport.putPart
      .mockRejectedValueOnce(new ApiError(404, "Not found"))
      .mockRejectedValueOnce(new ApiError(404, "Not found"));
    const outcome = h.run();
    await flush();

    expect(outcome.value).toBe(h.demo);
    // Both parts were refused together; one refresh served them both.
    expect(h.transport.refreshToken).toHaveBeenCalledTimes(1);
    const tokens = h.transport.putPart.mock.calls.map(([request]) => request.token);
    expect(tokens).toEqual(["token-1", "token-1", "token-2", "token-2"]);
  });

  it("pauses for a new sign-in when the cookie is refused too, and carries on once it works again", async () => {
    const h = harness({ size: 4, partSize: 4 });
    h.transport.putPart.mockRejectedValueOnce(new ApiError(401, "Bad token"));
    h.transport.refreshToken
      .mockRejectedValueOnce(new ApiError(401, "Not authenticated"))
      .mockRejectedValueOnce(new ApiError(401, "Not authenticated"));
    const outcome = h.run();
    await flush();
    expect(h.statuses.at(-1)).toEqual({ phase: "paused", reason: "reauth" });
    expect(h.transport.putPart).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(15_000);
    expect(h.transport.refreshToken).toHaveBeenCalledTimes(2);
    expect(h.statuses.at(-1)).toEqual({ phase: "paused", reason: "reauth" });
    await vi.advanceTimersByTimeAsync(30_000);
    await flush();
    expect(h.transport.refreshToken).toHaveBeenCalledTimes(3);
    expect(outcome.value).toBe(h.demo);
    expect(h.transport.putPart.mock.calls.at(-1)?.[0].token).toBe("token-2");
  });

  it("starts over once when the session is gone, forgetting its record", async () => {
    const h = harness({ size: 8, partSize: 4, maxParallelParts: 1 });
    h.transport.createSession
      .mockResolvedValueOnce(h.created())
      .mockResolvedValueOnce(h.created({ sessionId: SID2, uploadToken: "token-new" }));
    h.transport.putPart.mockResolvedValueOnce({ index: 0, sizeBytes: 4, sha256: "x", receivedCount: 1 });
    h.transport.putPart.mockRejectedValueOnce(new ApiError(404, "Not found"));
    h.transport.refreshToken.mockRejectedValueOnce(new ApiError(404, "Not found"));
    const outcome = h.run();
    await flush();

    expect(outcome.value).toBe(h.demo);
    expect(h.transport.createSession).toHaveBeenCalledTimes(2);
    expect(h.transport.createSession.mock.calls[1][0]).toEqual({ filename: "match.dem", size: 8 });
    // The new session gets every part again.
    const resent = h.transport.putPart.mock.calls.slice(2).map(([request]) => [request.sessionId, request.index]);
    expect(resent).toEqual([
      [SID2, 0],
      [SID2, 1]
    ]);
    expect(h.transport.completeSession).toHaveBeenCalledWith(SID2);
    expect(h.resume.write).toHaveBeenLastCalledWith(expect.objectContaining({ sessionId: SID2 }));
  });

  it("polls a completion that is still running every 2 s until the match exists", async () => {
    const h = harness();
    h.transport.completeSession
      .mockResolvedValueOnce({ kind: "completing" })
      .mockRejectedValueOnce(new ApiError(503, "Busy", "INTAKE_BUSY", 5))
      .mockResolvedValueOnce({ kind: "completing" });
    const outcome = h.run();
    await flush();
    expect(h.transport.completeSession).toHaveBeenCalledTimes(1);
    expect(h.statuses.at(-1)).toEqual({ phase: "verifying" });

    await vi.advanceTimersByTimeAsync(2000);
    expect(h.transport.completeSession).toHaveBeenCalledTimes(2);
    // Its own earlier request may hold the intake: 5 s plus jitter.
    await vi.advanceTimersByTimeAsync(5499);
    expect(h.transport.completeSession).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.transport.completeSession).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(2000);
    expect(outcome.value).toBe(h.demo);
    expect(h.statuses.map((status) => status.phase)).not.toContain("paused");
  });

  it("sends the parts the server says are missing, then completes again", async () => {
    const h = harness({ size: 12, partSize: 4, maxParallelParts: 3 });
    h.transport.completeSession.mockRejectedValueOnce(
      new ApiError(409, "Parts missing", "upload_parts_missing", null, { missingParts: [1] })
    );
    const outcome = h.run();
    await flush();

    expect(outcome.value).toBe(h.demo);
    expect(sentIndexes(h).slice(3)).toEqual([1]);
    expect(h.transport.completeSession).toHaveBeenCalledTimes(2);
  });

  it("waits for room under the in-flight cap and completes on its own", async () => {
    const h = harness();
    h.transport.completeSession.mockRejectedValueOnce(
      new ApiError(429, "Too many demos are processing.", "active_parse_limit", 60)
    );
    const outcome = h.run();
    await flush();
    expect(h.statuses.at(-1)).toEqual({ phase: "waiting", reason: "quota", code: "active_parse_limit", retryAfterSeconds: 60 });

    await vi.advanceTimersByTimeAsync(59_999);
    expect(h.transport.completeSession).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(outcome.value).toBe(h.demo);
  });

  it("stops at the daily limit but keeps the session for a later 完成上传", async () => {
    const h = harness();
    const refusal = new ApiError(429, "Daily upload limit reached.", "upload_daily_limit", 5400);
    h.transport.completeSession.mockRejectedValueOnce(refusal);
    const outcome = h.run();
    await flush();

    expect(outcome.error).toBe(refusal);
    expect(h.resume.record?.sessionId).toBe(SID);
    expect(h.transport.deleteSession).not.toHaveBeenCalled();
  });

  it("forgets the session the server failed on the intake's refusal", async () => {
    const h = harness();
    const refusal = new ApiError(400, "Uploaded demo is incomplete", "INTAKE_TRUNCATED");
    h.transport.completeSession.mockRejectedValueOnce(refusal);
    const outcome = h.run();
    await flush();

    expect(outcome.error).toBe(refusal);
    expect(h.resume.record).toBeNull();
  });

  it("cancels: aborts the transfers, deletes the session and forgets it", async () => {
    const h = harness({ size: 12, partSize: 4, maxParallelParts: 2, hold: true });
    const outcome = h.run();
    await flush();
    const inFlight = h.open().map((entry) => entry.request);
    expect(inFlight).toHaveLength(2);

    h.controller.abort();
    await flush();

    expect(inFlight.every((request) => request.signal?.aborted)).toBe(true);
    expect(outcome.error).toMatchObject({ name: "AbortError" });
    expect(h.transport.deleteSession).toHaveBeenCalledWith(SID);
    expect(h.resume.record).toBeNull();
    expect(h.transport.putPart).toHaveBeenCalledTimes(2);
    expect(h.transport.completeSession).not.toHaveBeenCalled();
  });

  it("resumes the unfinished session of the same file, sending only the missing parts", async () => {
    const data = fileBytes(12);
    const current: UploadSessionStatus = {
      sessionId: SID,
      state: "open",
      filename: "match.dem",
      size: 12,
      partSize: 4,
      partCount: 3,
      receivedParts: [0, 2],
      receivedBytes: 8,
      part0Sha256: await fakeDigest(data.slice(0, 4).buffer as ArrayBuffer),
      expiresAt: "2026-10-05T00:00:00Z"
    };
    const h = harness({ size: 12, partSize: 4, current });
    const outcome = h.run();
    await flush();

    expect(outcome.value).toBe(h.demo);
    expect(h.transport.createSession).not.toHaveBeenCalled();
    expect(h.transport.refreshToken).toHaveBeenCalledWith(SID);
    expect(sentIndexes(h)).toEqual([1]);
    expect(h.transport.putPart.mock.calls[0][0].token).toBe("token-2");
    // It started from the bytes already there.
    expect(h.progress[0]).toBe(8);
  });

  it("opens a new session (and lets the server object) when part 0 differs", async () => {
    const current: UploadSessionStatus = {
      sessionId: SID2,
      state: "open",
      filename: "match.dem",
      size: 12,
      partSize: 4,
      partCount: 3,
      receivedParts: [0],
      receivedBytes: 4,
      part0Sha256: "d:9,9,9,9",
      expiresAt: "2026-10-05T00:00:00Z"
    };
    const record = { sessionId: SID2, name: "match.dem", size: 12, lastModified: 1000 };
    const h = harness({ size: 12, partSize: 4, current, record });
    const exists = new ApiError(409, "An upload is already open", "upload_session_exists", null, {
      sessionId: SID2,
      filename: "match.dem",
      size: 12,
      receivedBytes: 4
    });
    h.transport.createSession.mockRejectedValueOnce(exists);
    const outcome = h.run();
    await flush();

    expect(h.transport.refreshToken).not.toHaveBeenCalled();
    // Never discards it on its own: no replace.
    expect(h.transport.createSession).toHaveBeenCalledWith({ filename: "match.dem", size: 12 });
    expect(outcome.error).toBe(exists);
    // The other upload's record stays.
    expect(h.resume.record).toEqual(record);
  });

  it("discards the old session only when asked to replace it", async () => {
    const h = harness();
    const outcome = h.run({ replace: true });
    await flush();

    expect(h.transport.getCurrentSession).not.toHaveBeenCalled();
    expect(h.transport.createSession).toHaveBeenCalledWith({ filename: "match.dem", size: 10, replace: true });
    expect(outcome.value).toBe(h.demo);
  });

  it("matches by size and the stored record when part 0 cannot be hashed, and sends no digest", async () => {
    const current: UploadSessionStatus = {
      sessionId: SID,
      state: "open",
      filename: "renamed.dem",
      size: 8,
      partSize: 4,
      partCount: 2,
      receivedParts: [0],
      receivedBytes: 4,
      part0Sha256: "d:anything",
      expiresAt: "2026-10-05T00:00:00Z"
    };
    const record = { sessionId: SID, name: "match.dem", size: 8, lastModified: 1000 };
    const h = harness({ size: 8, partSize: 4, current, record, digest: async () => null });
    const outcome = h.run();
    await flush();

    expect(outcome.value).toBe(h.demo);
    expect(h.transport.createSession).not.toHaveBeenCalled();
    expect(sentIndexes(h)).toEqual([1]);
    expect(h.transport.putPart.mock.calls[0][0].sha256).toBeNull();
  });

  it("follows a session that is already completing instead of sending anything", async () => {
    const current: UploadSessionStatus = {
      sessionId: SID,
      state: "completing",
      filename: "match.dem",
      size: 8,
      partSize: 4,
      partCount: 2,
      receivedParts: [0, 1],
      receivedBytes: 8,
      expiresAt: "2026-10-05T00:00:00Z"
    };
    const h = harness({ size: 8, partSize: 4, current });
    const outcome = h.run({ resumeSessionId: SID });
    h.transport.getSession.mockResolvedValueOnce(current);
    await flush();

    expect(h.transport.putPart).not.toHaveBeenCalled();
    expect(h.transport.completeSession).toHaveBeenCalledWith(SID);
    expect(outcome.value).toBe(h.demo);
  });
});

describe("finishChunkedUpload", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("completes a session whose parts are all on the server, with no file", async () => {
    const h = harness();
    h.resume.record = { sessionId: SID, name: "match.dem", size: 10, lastModified: 1000 };
    h.transport.completeSession.mockResolvedValueOnce({ kind: "completing" });
    const outcome = h.finish();
    await flush();
    expect(h.statuses).toEqual([{ phase: "verifying" }]);
    await vi.advanceTimersByTimeAsync(2000);

    expect(outcome.value).toBe(h.demo);
    expect(h.transport.putPart).not.toHaveBeenCalled();
    expect(h.resume.record).toBeNull();
  });

  it("asks for the file when parts are missing, and says when the session is gone", async () => {
    const h = harness();
    const missing = new ApiError(409, "Parts missing", "upload_parts_missing", null, { missingParts: [2] });
    h.transport.completeSession.mockRejectedValueOnce(missing).mockRejectedValueOnce(new ApiError(404, "Not found"));
    const first = h.finish();
    await flush();
    expect(first.error).toBe(missing);

    const second = h.finish();
    await flush();
    expect(second.error).toMatchObject({ status: 404, detailCode: "upload_session_gone" });
    // Finishing never deletes the session.
    expect(h.transport.deleteSession).not.toHaveBeenCalled();
  });
});
