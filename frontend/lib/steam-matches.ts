import type {
  SteamConnection,
  SteamMatch,
  SteamMatchStatus,
  SteamSyncResult
} from "@/types/steam";

export type SteamStatusTone = "neutral" | "waiting" | "active" | "ready" | "danger";
export type SteamRequestAction = "load" | "connect" | "sync" | "disconnect";

export interface SteamConnectionDisplayModel {
  connected: boolean;
  credentialsConfigured: boolean;
  scheduledSyncEnabled: boolean;
  statusLabel: string;
  statusTone: SteamStatusTone;
  lastSyncStartedAt: string | null;
  lastSyncCompletedAt: string | null;
  nextRetryAt: string | null;
  errorMessage: string | null;
}

export interface SteamMatchDisplayModel {
  id: string;
  status: SteamMatchStatus;
  statusLabel: string;
  statusTone: SteamStatusTone;
  sourceLabel: "Steam match history";
  discoveredAt: string;
  updatedAt: string;
  demoId: string | null;
  demoHref: string | null;
}

const MATCH_STATUS_LABELS: Record<SteamMatchStatus, string> = {
  discovered: "Discovered",
  demo_pending: "Demo pending",
  downloading: "Downloading",
  parsing: "Parsing",
  ready: "Ready",
  unavailable: "Unavailable"
};

const MATCH_STATUS_TONES: Record<SteamMatchStatus, SteamStatusTone> = {
  discovered: "neutral",
  demo_pending: "waiting",
  downloading: "active",
  parsing: "active",
  ready: "ready",
  unavailable: "danger"
};

export function steamMatchStatusLabel(status: SteamMatchStatus): string {
  return MATCH_STATUS_LABELS[status] ?? "Unknown";
}

export function steamMatchStatusTone(status: SteamMatchStatus): SteamStatusTone {
  return MATCH_STATUS_TONES[status] ?? "neutral";
}

export function buildSteamMatchDisplay(match: SteamMatch): SteamMatchDisplayModel {
  return {
    id: match.id,
    status: match.status,
    statusLabel: steamMatchStatusLabel(match.status),
    statusTone: steamMatchStatusTone(match.status),
    sourceLabel: "Steam match history",
    discoveredAt: match.discovered_at,
    updatedAt: match.updated_at,
    demoId: match.demo_id,
    demoHref: match.demo_id ? `/demos/${match.demo_id}` : null
  };
}

export function buildSteamConnectionDisplay(
  connection: SteamConnection
): SteamConnectionDisplayModel {
  return {
    connected: connection.connected,
    credentialsConfigured: connection.credentials_configured,
    scheduledSyncEnabled: connection.scheduled_sync_enabled,
    statusLabel: steamConnectionStatusLabel(connection),
    statusTone: steamConnectionStatusTone(connection),
    lastSyncStartedAt: connection.last_sync_started_at,
    lastSyncCompletedAt: connection.last_sync_completed_at,
    nextRetryAt: connection.next_retry_at,
    errorMessage: steamConnectionErrorMessage(connection)
  };
}

export function steamConnectionStatusLabel(connection: SteamConnection): string {
  if (!connection.connected) {
    return "Not connected";
  }
  if (!connection.credentials_configured) {
    return "Setup required";
  }

  const status = connection.status.toLowerCase();
  if (status === "syncing") {
    return "Syncing";
  }
  if (status === "caught_up") {
    return "Up to date";
  }
  if (status === "retry_wait") {
    return "Waiting to retry";
  }
  if (status === "authorization_required" || status === "error") {
    return "Needs attention";
  }
  return "Ready to sync";
}

export function steamConnectionStatusTone(connection: SteamConnection): SteamStatusTone {
  if (!connection.connected || !connection.credentials_configured) {
    return "neutral";
  }

  const status = connection.status.toLowerCase();
  if (status === "syncing") {
    return "active";
  }
  if (status === "caught_up" || status === "connected") {
    return "ready";
  }
  if (status === "retry_wait") {
    return "waiting";
  }
  if (status === "authorization_required" || status === "error") {
    return "danger";
  }
  return "ready";
}

export function steamConnectionErrorMessage(connection: SteamConnection): string | null {
  const code = connection.last_error_code?.trim().toLowerCase();
  if (!code) {
    return null;
  }
  if (
    code.includes("credential") ||
    code.includes("auth") ||
    code.includes("forbidden") ||
    code.includes("403")
  ) {
    return "Steam rejected the saved authorization. Replace both codes before syncing again.";
  }
  if (
    code.includes("sharing") ||
    code.includes("knowncode") ||
    code.includes("cursor") ||
    code.includes("precondition") ||
    code.includes("412")
  ) {
    return "Use a recent match sharing code from this Steam account before syncing again.";
  }
  if (code.includes("rate") || code.includes("429")) {
    return "Steam is rate-limiting sync requests. Use Sync now after the displayed retry time.";
  }
  if (code.includes("unavailable") || code.includes("503")) {
    return "Steam match history is temporarily unavailable. Retry later.";
  }
  return "Steam sync needs attention. Replace the saved codes or retry later.";
}

export function steamSyncResultMessage(result: SteamSyncResult): string {
  const count = Math.max(0, result.discovered_count);
  const matchWord = count === 1 ? "match" : "matches";
  if (result.limit_reached) {
    return `Discovered ${count} new ${matchWord}. The sync limit was reached; sync again to continue.`;
  }
  if (result.caught_up && count === 0) {
    return "No new matches. Steam match history is up to date.";
  }
  if (result.caught_up) {
    return `Discovered ${count} new ${matchWord}. Steam match history is up to date.`;
  }
  return `Discovered ${count} new ${matchWord}.`;
}

export function steamRequestErrorMessage(
  status: number | null,
  action: SteamRequestAction,
  detailCode: string | null = null
): string {
  if (status === 401) {
    return "Your session expired. Sign in again to continue.";
  }
  if (detailCode === "steam_authorization_invalid") {
    return "Steam rejected the saved authorization. Replace both codes before syncing again.";
  }
  if (detailCode === "steam_cursor_invalid") {
    return "Use a recent match sharing code from this Steam account before syncing again.";
  }
  if (status === 403) {
    return "This request origin was rejected. Refresh the configured app origin and try again.";
  }
  if (status === 412) {
    return "The Steam sync request precondition was rejected. Refresh its status and try again.";
  }
  if (status === 429 || status === 503) {
    return "Steam match history is temporarily rate-limited or unavailable. Retry later.";
  }
  if (status === 409) {
    return "The Steam connection needs attention or a sync is already running. Refresh its status before retrying.";
  }

  const messages: Record<SteamRequestAction, string> = {
    load: "Could not load the Steam connection. Check the API and retry.",
    connect: "Could not save the Steam connection. Check both codes and retry.",
    sync: "Could not sync Steam match history. Retry later.",
    disconnect: "Could not disconnect Steam match history. Retry later."
  };
  return messages[action];
}
