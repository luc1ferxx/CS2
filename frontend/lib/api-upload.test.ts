import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, isUploadAbortError, onUnauthorized, uploadDemoFile } from "@/lib/api";

// A scriptable stand-in for XMLHttpRequest: each test drives one request.
class FakeXhr {
  static last: FakeXhr | null = null;
  method = "";
  url = "";
  withCredentials = false;
  status = 0;
  responseText = "";
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
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  send(body: unknown) {
    this.body = body;
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

const demoFile = () => new File(["HL2DEMO"], "match.dem");

describe("uploadDemoFile", () => {
  beforeEach(() => {
    FakeXhr.last = null;
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts the file with credentials and reports progress", async () => {
    const onProgress = vi.fn();
    const result = uploadDemoFile(demoFile(), { onProgress });
    const xhr = currentXhr();

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toMatch(/\/uploads\/demo$/);
    expect(xhr.withCredentials).toBe(true);
    expect((xhr.body as FormData).get("file")).toBeInstanceOf(File);

    xhr.upload.onprogress?.({ loaded: 50, total: 200, lengthComputable: true });
    expect(onProgress).toHaveBeenCalledWith({ loaded: 50, total: 200 });

    xhr.respond(201, { id: "demo-1", name: "match.dem" });
    await expect(result).resolves.toMatchObject({ id: "demo-1" });
  });

  it("keeps the structured quota detail and the Retry-After fallback", async () => {
    const structured = uploadDemoFile(demoFile());
    currentXhr().respond(429, {
      detail: { code: "upload_daily_limit", message: "Daily upload limit reached.", retryAfterSeconds: 5400 }
    });
    await expect(structured).rejects.toMatchObject({
      status: 429,
      detailCode: "upload_daily_limit",
      retryAfterSeconds: 5400,
      message: "Daily upload limit reached."
    });

    const headerOnly = uploadDemoFile(demoFile());
    currentXhr().respond(503, { detail: "Artifact upload capacity is busy", errorCode: "INTAKE_BUSY" }, { "Retry-After": "5" });
    const error = await headerOnly.catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, detailCode: "INTAKE_BUSY", retryAfterSeconds: 5 });
  });

  it("reads the intake's sibling errorCode when the detail is a plain string", async () => {
    const result = uploadDemoFile(demoFile());
    currentXhr().respond(413, { detail: "Uploaded demo exceeds the configured size limit", errorCode: "INTAKE_TOO_LARGE" });
    await expect(result).rejects.toMatchObject({ status: 413, detailCode: "INTAKE_TOO_LARGE" });
  });

  it("tells the session listeners about a 401", async () => {
    const listener = vi.fn();
    const stop = onUnauthorized(listener);
    const result = uploadDemoFile(demoFile());
    currentXhr().respond(401, { detail: "Not authenticated" });
    await expect(result).rejects.toMatchObject({ status: 401, code: "unauthenticated" });
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });

  it("rejects a network failure the way fetch does, and an abort as an AbortError", async () => {
    const offline = uploadDemoFile(demoFile());
    currentXhr().onerror?.();
    await expect(offline).rejects.toThrow("Failed to fetch");

    const controller = new AbortController();
    const cancelled = uploadDemoFile(demoFile(), { signal: controller.signal });
    const xhr = currentXhr();
    controller.abort();
    const error = await cancelled.catch((caught: unknown) => caught);
    expect(xhr.aborted).toBe(true);
    expect(isUploadAbortError(error)).toBe(true);
  });
});
