import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  completeUploadSession,
  createUploadSession,
  deleteUploadSession,
  finishDemoUpload,
  getCurrentUploadSession,
  isUploadAbortError,
  onUnauthorized,
  putUploadPart,
  refreshUploadSessionToken,
  uploadDemoFile
} from "@/lib/api";
import { UPLOAD_RESUME_KEY } from "@/lib/demo-upload";

const SID = "0123456789abcdef0123456789abcdef";

// A scriptable stand-in for XMLHttpRequest: each test drives its requests.
class FakeXhr {
  static last: FakeXhr | null = null;
  static all: FakeXhr[] = [];
  static autoRespond: ((xhr: FakeXhr) => void) | null = null;
  method = "";
  url = "";
  withCredentials = true;
  status = 0;
  responseText = "";
  requestHeaders: Record<string, string> = {};
  headers: Record<string, string> = {};
  body: unknown = null;
  aborted = false;
  upload: { onprogress: ((event: { loaded: number; total: number; lengthComputable: boolean }) => void) | null } = {
    onprogress: null
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    FakeXhr.last = this;
    FakeXhr.all.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.requestHeaders[name] = value;
  }
  send(body: unknown) {
    this.body = body;
    const respond = FakeXhr.autoRespond;
    if (respond) queueMicrotask(() => respond(this));
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  getResponseHeader(name: string) {
    return this.headers[name.toLowerCase()] ?? null;
  }
  respond(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.headers = Object.fromEntries(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]));
    this.onload?.();
  }
}

function currentXhr(): FakeXhr {
  if (!FakeXhr.last) throw new Error("no request was sent");
  return FakeXhr.last;
}

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers }
  });
}

const part = (overrides: Partial<Parameters<typeof putUploadPart>[0]> = {}) =>
  putUploadPart({
    sessionId: SID,
    index: 3,
    token: "secret-token",
    body: new ArrayBuffer(8),
    sha256: "ab".repeat(32),
    ...overrides
  });

describe("putUploadPart", () => {
  beforeEach(() => {
    FakeXhr.last = null;
    FakeXhr.all = [];
    FakeXhr.autoRespond = null;
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PUTs the raw bytes with the upload token and digest, without the cookie, and reports progress", async () => {
    const onProgress = vi.fn();
    const body = new ArrayBuffer(8);
    const result = part({ body, onProgress });
    const xhr = currentXhr();

    expect(xhr.method).toBe("PUT");
    expect(xhr.url).toMatch(new RegExp(`/uploads/sessions/${SID}/parts/3$`));
    expect(xhr.withCredentials).toBe(false);
    expect(xhr.requestHeaders).toEqual({
      "Content-Type": "application/octet-stream",
      "X-Upload-Token": "secret-token",
      "X-Part-SHA256": "ab".repeat(32)
    });
    expect(xhr.body).toBe(body);

    xhr.upload.onprogress?.({ loaded: 5, total: 8, lengthComputable: true });
    expect(onProgress).toHaveBeenCalledWith(5);

    xhr.respond(200, { index: 3, sizeBytes: 8, sha256: "ab".repeat(32), receivedCount: 4 });
    await expect(result).resolves.toEqual({ index: 3, sizeBytes: 8, sha256: "ab".repeat(32), receivedCount: 4 });
  });

  it("leaves the digest header out where WebCrypto gave none", () => {
    void part({ sha256: null }).catch(() => {});
    expect(currentXhr().requestHeaders).not.toHaveProperty("X-Part-SHA256");
  });

  it("reads structured codes, the intake's sibling errorCode and Retry-After", async () => {
    const busy = part();
    currentXhr().respond(503, { detail: { code: "INTAKE_BUSY", message: "Part pool is full." } }, { "Retry-After": "2" });
    await expect(busy).rejects.toMatchObject({ status: 503, detailCode: "INTAKE_BUSY", retryAfterSeconds: 2 });

    const archive = part({ index: 0 });
    currentXhr().respond(400, { detail: "Uploaded demo content does not match", errorCode: "INTAKE_CONTENT_MISMATCH" });
    const error = await archive.catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, detailCode: "INTAKE_CONTENT_MISMATCH" });

    const mismatch = part();
    currentXhr().respond(422, { detail: { code: "upload_part_digest_mismatch", message: "Digest mismatch." } });
    await expect(mismatch).rejects.toMatchObject({ status: 422, detailCode: "upload_part_digest_mismatch" });
  });

  it("never treats a refused token as a signed-out session", async () => {
    const listener = vi.fn();
    const stop = onUnauthorized(listener);
    const result = part();
    currentXhr().respond(401, { detail: "Invalid upload token" });
    await expect(result).rejects.toMatchObject({ status: 401 });
    expect(listener).not.toHaveBeenCalled();
    stop();
  });

  it("rejects a network failure the way fetch does, and an abort as an AbortError", async () => {
    const offline = part();
    currentXhr().onerror?.();
    await expect(offline).rejects.toThrow("Failed to fetch");

    const controller = new AbortController();
    const cancelled = part({ signal: controller.signal });
    const xhr = currentXhr();
    controller.abort();
    const error = await cancelled.catch((caught: unknown) => caught);
    expect(xhr.aborted).toBe(true);
    expect(isUploadAbortError(error)).toBe(true);
  });
});

describe("upload session requests", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a session with the cookie and returns its plan", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(201, { sessionId: SID, uploadToken: "t", partSize: 8, partCount: 2, maxParallelParts: 4, expiresAt: "x", receivedParts: [] })
    );
    const created = await createUploadSession({ filename: "match.dem", size: 16 });
    expect(created).toMatchObject({ sessionId: SID, partSize: 8 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/uploads\/sessions$/);
    expect(init).toMatchObject({ method: "POST", credentials: "include", cache: "no-store" });
    expect(JSON.parse(init.body)).toEqual({ filename: "match.dem", size: 16 });
  });

  it("answers the current session or null", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { session: null }));
    await expect(getCurrentUploadSession()).resolves.toBeNull();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { session: { sessionId: SID, state: "open" } }));
    await expect(getCurrentUploadSession()).resolves.toMatchObject({ sessionId: SID });
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/uploads\/sessions\/current$/);
  });

  it("keeps the session in the way of a new upload on the error", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(409, {
        detail: {
          code: "upload_session_exists",
          message: "An upload is already open.",
          sessionId: SID,
          filename: "old.dem",
          size: 100,
          receivedBytes: 40
        }
      })
    );
    const error = await createUploadSession({ filename: "new.dem", size: 5 }).catch((caught: unknown) => caught);
    expect(error).toMatchObject({ status: 409, detailCode: "upload_session_exists" });
    expect((error as ApiError).detailData).toEqual({ sessionId: SID, filename: "old.dem", size: 100, receivedBytes: 40 });
  });

  it("reads the intake's sibling errorCode on a session route", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(400, { detail: "Only .dem files are accepted", errorCode: "INTAKE_TYPE_REJECTED" }));
    await expect(createUploadSession({ filename: "a.zip", size: 5 })).rejects.toMatchObject({
      status: 400,
      detailCode: "INTAKE_TYPE_REJECTED"
    });
  });

  it("tells completed (201 or 200) from still completing (202)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: "demo-1", name: "match.dem" }));
    await expect(completeUploadSession(SID)).resolves.toMatchObject({ kind: "demo", created: true, demo: { id: "demo-1" } });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "demo-1", name: "match.dem" }));
    await expect(completeUploadSession(SID)).resolves.toMatchObject({ kind: "demo", created: false });
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { state: "completing" }));
    await expect(completeUploadSession(SID)).resolves.toEqual({ kind: "completing" });
    expect(fetchMock.mock.calls[0][0]).toMatch(new RegExp(`/uploads/sessions/${SID}/complete$`));
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST", credentials: "include" });
  });

  it("keeps the missing parts on the error", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(409, { detail: { code: "upload_parts_missing", message: "Parts missing.", missingParts: [2, 5] } })
    );
    const error = (await completeUploadSession(SID).catch((caught: unknown) => caught)) as ApiError;
    expect(error.detailData).toEqual({ missingParts: [2, 5] });
  });

  it("pauses quietly on a signed-out token refresh, but a plain request still reports the 401", async () => {
    const listener = vi.fn();
    const stop = onUnauthorized(listener);
    fetchMock.mockImplementation(async () => jsonResponse(401, { detail: "Not authenticated" }));

    await expect(refreshUploadSessionToken(SID, { notifyUnauthorized: false })).rejects.toMatchObject({ status: 401 });
    expect(listener).not.toHaveBeenCalled();
    await expect(getCurrentUploadSession()).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });

  it("deletes a session", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await deleteUploadSession(SID);
    expect(fetchMock.mock.calls[0][0]).toMatch(new RegExp(`/uploads/sessions/${SID}$`));
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE", credentials: "include" });
  });
});

describe("uploadDemoFile through upload sessions", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    FakeXhr.last = null;
    FakeXhr.all = [];
    FakeXhr.autoRespond = (xhr) => {
      const index = Number(xhr.url.split("/").pop());
      const size = xhr.body instanceof ArrayBuffer ? xhr.body.byteLength : 0;
      xhr.upload.onprogress?.({ loaded: size, total: size, lengthComputable: true });
      xhr.respond(200, { index, sizeBytes: size, sha256: "x", receivedCount: index + 1 });
    };
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("sends a small file in parts and resolves with the match", async () => {
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith("/uploads/sessions/current")) return jsonResponse(200, { session: null });
      if (url.endsWith("/uploads/sessions") && init.method === "POST") {
        return jsonResponse(201, {
          sessionId: SID,
          uploadToken: "token-1",
          partSize: 4,
          partCount: 3,
          maxParallelParts: 2,
          expiresAt: "2026-10-05T00:00:00Z",
          receivedParts: []
        });
      }
      if (url.endsWith(`/uploads/sessions/${SID}/complete`)) {
        // The record is there while the session is open.
        expect(JSON.parse(window.localStorage.getItem(UPLOAD_RESUME_KEY) ?? "null")).toMatchObject({ sessionId: SID, size: 12 });
        return jsonResponse(201, { id: "demo-1", name: "match.dem" });
      }
      throw new Error(`unexpected request ${init.method} ${url}`);
    });
    const progress: number[] = [];
    const phases: string[] = [];

    const demo = await uploadDemoFile(new File(["HL2DEMO\0rest"], "match.dem"), {
      onProgress: ({ loaded }) => progress.push(loaded),
      onStatus: (status) => phases.push(status.phase)
    });

    expect(demo).toMatchObject({ id: "demo-1" });
    expect(FakeXhr.all.map((xhr) => xhr.url.split("/").pop()).sort()).toEqual(["0", "1", "2"]);
    expect(FakeXhr.all.every((xhr) => xhr.method === "PUT" && !xhr.withCredentials)).toBe(true);
    expect(FakeXhr.all.every((xhr) => xhr.requestHeaders["X-Upload-Token"] === "token-1")).toBe(true);
    expect(progress.at(-1)).toBe(12);
    expect(phases).toEqual(["preparing", "sending", "verifying"]);
    expect(window.localStorage.getItem(UPLOAD_RESUME_KEY)).toBeNull();
  });

  it("refuses an archive as an ApiError with the intake's code, before any request", async () => {
    const zip = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0])], "match.dem");
    const error = await uploadDemoFile(zip).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, detailCode: "INTAKE_CONTENT_MISMATCH" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("completes an unfinished session without its file", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "demo-2", name: "old.dem" }));
    await expect(finishDemoUpload(SID)).resolves.toMatchObject({ id: "demo-2" });
    expect(fetchMock.mock.calls[0][0]).toMatch(new RegExp(`/uploads/sessions/${SID}/complete$`));
    expect(FakeXhr.all).toHaveLength(0);
  });
});
