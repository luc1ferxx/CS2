import type {
  SteamConnection,
  SteamMatch,
  SteamMatchStatus,
  SteamSyncResult
} from "@/types/steam";

export type SteamStatusTone = "neutral" | "waiting" | "active" | "ready" | "danger";
export type SteamRequestAction = "load" | "connect" | "sync" | "disconnect" | "import";

export interface SteamMatchImportAction {
  enabled: boolean;
  label: string;
}

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
  demoImportAvailable: boolean;
  demoSourceProvider: string;
  manualUploadSupported: boolean;
  demoImportMessage: string | null;
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
  providerId: string | null;
  mapName: string | null;
  durationLabel: string | null;
  sideRoundsLabel: string | null;
  playersLabel: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  importRetryable: boolean;
  parserDispatchPending: boolean;
  manualUploadSupported: boolean;
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
  const hasParserSummary = match.status === "ready" && match.demo_id !== null;
  return {
    id: match.id,
    status: match.status,
    statusLabel: steamMatchStatusLabel(match.status),
    statusTone: steamMatchStatusTone(match.status),
    sourceLabel: "Steam match history",
    discoveredAt: match.discovered_at,
    updatedAt: match.updated_at,
    demoId: match.demo_id,
    demoHref: match.demo_id ? `/demos/${match.demo_id}` : null,
    providerId: match.provider_id,
    mapName: hasParserSummary ? match.map_name : null,
    durationLabel: hasParserSummary ? formatDuration(match.duration_seconds) : null,
    sideRoundsLabel: hasParserSummary
      ? formatSideRounds(match.ct_round_wins, match.t_round_wins)
      : null,
    playersLabel:
      hasParserSummary && Array.isArray(match.players)
        ? playerNamesLabel(match.players)
        : null,
    errorCode: match.status === "unavailable" ? match.import_error_code : null,
    errorMessage: match.status === "unavailable" ? match.import_error_message : null,
    importRetryable: match.import_retryable === true,
    parserDispatchPending: match.parser_dispatch_pending === true,
    manualUploadSupported: match.manual_upload_supported
  };
}

export function steamMatchImportAction(
  status: SteamMatchStatus,
  demoId: string | null,
  demoImportAvailable: boolean,
  importErrorCode: string | null = null,
  parserDispatchPending = false,
  importRetryable = false
): SteamMatchImportAction {
  if (status === "ready") {
    return { enabled: false, label: "Ready" };
  }
  if (status === "demo_pending") {
    return importRetryable && demoImportAvailable
      ? { enabled: true, label: "Retry import" }
      : { enabled: false, label: demoImportAvailable ? "Pending" : "Manual upload" };
  }
  if (status === "downloading") {
    return importRetryable && demoImportAvailable
      ? { enabled: true, label: "Retry import" }
      : { enabled: false, label: demoImportAvailable ? "Downloading" : "Manual upload" };
  }
  if (status === "parsing") {
    return parserDispatchPending
      ? { enabled: true, label: "Retry parser" }
      : { enabled: false, label: "Parsing" };
  }
  if (
    status === "unavailable" &&
    demoId &&
    importErrorCode === "parser_dispatch_unavailable"
  ) {
    return { enabled: true, label: "Retry parser" };
  }
  if (demoId) {
    return { enabled: false, label: "Unavailable" };
  }
  if (!demoImportAvailable) {
    return { enabled: false, label: "Manual upload" };
  }
  return {
    enabled: true,
    label: status === "unavailable" ? "Retry import" : "Import Demo"
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
    errorMessage: steamConnectionErrorMessage(connection),
    demoImportAvailable: connection.demo_import_available,
    demoSourceProvider: connection.demo_source_provider,
    manualUploadSupported: connection.manual_upload_supported,
    demoImportMessage: connection.demo_import_available
      ? null
      : "No licensed automatic Demo provider is configured. Use manual .dem upload."
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
  if (action === "import") {
    if (detailCode === "demo_source_unavailable" || detailCode === "downloader_not_configured") {
      return "No licensed automatic Demo provider is configured. Use manual .dem upload.";
    }
    if (detailCode === "demo_import_in_progress") {
      return "This Demo import is already running. Refresh the match status.";
    }
    if (status === 404) {
      return "This Steam match no longer exists. Refresh the match list.";
    }
    if (status === 403) {
      return "This request origin was rejected. Refresh the configured app origin and try again.";
    }
    if (status === 502) {
      return "The Demo provider response failed secure download checks. Use manual .dem upload or retry later.";
    }
    if (status === 503) {
      return "Automatic Demo import is temporarily unavailable. Use manual .dem upload or retry later.";
    }
    if (status === 409) {
      return "This Demo cannot be imported in its current state. Refresh the match status or use manual .dem upload.";
    }
    return "Could not import this Demo. Use manual .dem upload or retry later.";
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
    disconnect: "Could not disconnect Steam match history. Retry later.",
    import: "Could not import this Demo. Use manual .dem upload or retry later."
  };
  return messages[action];
}

function formatDuration(value: number | null): string | null {
  if (!Number.isInteger(value) || value === null || value < 0) {
    return null;
  }
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  if (minutes === 0) {
    return `${seconds}s`;
  }
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function formatSideRounds(ctRounds: number | null, tRounds: number | null): string | null {
  if (!isRoundCount(ctRounds) && !isRoundCount(tRounds)) {
    return null;
  }
  return `CT ${isRoundCount(ctRounds) ? ctRounds : "—"} · T ${isRoundCount(tRounds) ? tRounds : "—"}`;
}

function isRoundCount(value: number | null): value is number {
  return Number.isInteger(value) && value !== null && value >= 0;
}

function playerNamesLabel(players: string[]): string | null {
  const names = players
    .filter((player): player is string => typeof player === "string")
    .map((player) => player.trim())
    .filter(Boolean);
  if (names.length === 0) {
    return null;
  }
  const visible = names.slice(0, 3).join(", ");
  return names.length > 3 ? `${visible} +${names.length - 3}` : visible;
}
