import type { DemoSummary } from "@/types/demo";

// The chunked upload session API (backend schemas/upload_session.py). Every
// session response is private, no-store.

export type UploadSessionState = "open" | "completing" | "completed" | "failed";

export interface UploadSessionCreateRequest {
  filename: string;
  size: number;
  contentType?: string | null;
  // Discard the owner's open session first (after the player confirmed it).
  replace?: boolean;
}

// POST /uploads/sessions (201).
export interface UploadSessionCreated {
  sessionId: string;
  uploadToken: string;
  partSize: number;
  partCount: number;
  maxParallelParts: number;
  expiresAt: string;
  receivedParts: number[];
}

export interface UploadSessionError {
  code: string;
  message: string | null;
}

// GET /uploads/sessions/{id} and the `session` of GET /uploads/sessions/current.
export interface UploadSessionStatus {
  sessionId: string;
  state: UploadSessionState;
  filename: string;
  size: number;
  partSize: number;
  partCount: number;
  receivedParts: number[];
  receivedBytes: number;
  // Only once part 0 is on the server: checks a resumed file is the same file.
  part0Sha256?: string | null;
  expiresAt: string;
  // Not part of the status contract; a create response or a newer API may send it.
  maxParallelParts?: number | null;
  // The match, once completed.
  demo?: DemoSummary | null;
  // Why a failed session failed (a string code from older builds is accepted too).
  error?: UploadSessionError | string | null;
}

export interface UploadSessionCurrent {
  session: UploadSessionStatus | null;
}

// POST /uploads/sessions/{id}/token: a new token (the old one stops working) and the status.
export interface UploadSessionTokenResponse extends UploadSessionStatus {
  uploadToken: string;
}

// PUT /uploads/sessions/{id}/parts/{index} (200; re-sending a part is fine).
export interface UploadPartReceipt {
  index: number;
  sizeBytes: number;
  sha256: string;
  receivedCount: number;
}

// One part PUT. Authenticated by the session's upload token only, never the cookie.
export interface UploadPartRequest {
  sessionId: string;
  index: number;
  token: string;
  body: Blob | ArrayBuffer;
  // Lower-case hex sha256 of the body; null where WebCrypto is unavailable (plain-http LAN preview).
  sha256: string | null;
  signal?: AbortSignal;
  onProgress?: (loaded: number) => void;
}

// POST /uploads/sessions/{id}/complete: 201/200 carry the match, 202 means a
// completion is still running (poll by completing again).
export type UploadCompleteResult =
  | { kind: "demo"; demo: DemoSummary; created: boolean }
  | { kind: "completing" };

// 409 upload_session_exists names the session that is in the way.
export interface ExistingUploadSession {
  sessionId: string | null;
  filename: string | null;
  size: number | null;
  receivedBytes: number | null;
}

export type UploadPauseReason = "offline" | "retrying" | "reauth";

// What the upload is doing, beyond its byte progress.
export type UploadStatus =
  | { phase: "preparing" }
  | { phase: "sending" }
  | { phase: "paused"; reason: UploadPauseReason }
  | { phase: "verifying" }
  // Every byte is on the server; the in-flight cap or a full queue holds the
  // completion back and it is retried on its own.
  | { phase: "waiting"; reason: "quota"; code: string | null; retryAfterSeconds: number | null };

export interface UploadResumeRecord {
  sessionId: string;
  name: string;
  size: number;
  lastModified: number;
}
