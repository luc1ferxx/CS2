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
  sourceLabel: "Steam 比赛记录";
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

const MANUAL_UPLOAD_LABEL = "手动上传 .dem";
// No licensed automatic source is registered in this build; say so plainly.
export const AUTOMATIC_IMPORT_UNAVAILABLE =
  "当前版本还不能从 Steam 自动下载比赛录像。同步只会列出你的比赛记录；要复盘，请手动上传这场比赛的 .dem 文件。";
const IMPORT_FAILED = "暂时无法自动导入这场比赛，可以手动上传 .dem 文件。";
const AUTHORIZATION_REJECTED = "Steam 拒绝了已保存的授权，请更换两个授权码后再同步。";
const SHARING_CODE_REJECTED = "请使用这个 Steam 账号最近一场比赛的分享代码，然后再同步。";

const MATCH_STATUS_LABELS: Record<SteamMatchStatus, string> = {
  discovered: "已发现",
  demo_pending: "等待获取",
  downloading: "下载中",
  parsing: "读取比赛中",
  ready: "可以复盘",
  unavailable: "无法导入"
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
  return MATCH_STATUS_LABELS[status] ?? "未知状态";
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
    sourceLabel: "Steam 比赛记录",
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
    errorMessage: match.status === "unavailable" ? steamImportErrorMessage(match.import_error_code) : null,
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
    return { enabled: false, label: "可以复盘" };
  }
  if (status === "demo_pending") {
    return importRetryable && demoImportAvailable
      ? { enabled: true, label: "重新导入" }
      : { enabled: false, label: demoImportAvailable ? "等待获取" : MANUAL_UPLOAD_LABEL };
  }
  if (status === "downloading") {
    return importRetryable && demoImportAvailable
      ? { enabled: true, label: "重新导入" }
      : { enabled: false, label: demoImportAvailable ? "下载中" : MANUAL_UPLOAD_LABEL };
  }
  if (status === "parsing") {
    return parserDispatchPending
      ? { enabled: true, label: "重新处理" }
      : { enabled: false, label: "读取比赛中" };
  }
  if (
    status === "unavailable" &&
    demoId &&
    importErrorCode === "parser_dispatch_unavailable"
  ) {
    return { enabled: true, label: "重新处理" };
  }
  if (demoId) {
    return { enabled: false, label: "无法导入" };
  }
  if (!demoImportAvailable) {
    return { enabled: false, label: MANUAL_UPLOAD_LABEL };
  }
  return {
    enabled: true,
    label: status === "unavailable" ? "重新导入" : "导入比赛"
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
    demoImportMessage: connection.demo_import_available ? null : AUTOMATIC_IMPORT_UNAVAILABLE
  };
}

export function steamConnectionStatusLabel(connection: SteamConnection): string {
  if (!connection.connected) {
    return "未连接";
  }
  if (!connection.credentials_configured) {
    return "需要设置";
  }

  const status = connection.status.toLowerCase();
  if (status === "syncing") {
    return "同步中";
  }
  if (status === "caught_up") {
    return "已是最新";
  }
  if (status === "retry_wait") {
    return "等待重试";
  }
  if (status === "authorization_required" || status === "error") {
    return "需要处理";
  }
  return "可以同步";
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
    return AUTHORIZATION_REJECTED;
  }
  if (
    code.includes("sharing") ||
    code.includes("knowncode") ||
    code.includes("cursor") ||
    code.includes("precondition") ||
    code.includes("412")
  ) {
    return SHARING_CODE_REJECTED;
  }
  if (code.includes("rate") || code.includes("429")) {
    return "Steam 暂时限制了同步请求，请在显示的重试时间之后再点「立即同步」。";
  }
  if (code.includes("unavailable") || code.includes("503")) {
    return "Steam 比赛记录暂时无法访问，请稍后再试。";
  }
  return "同步出了问题，请更换授权码或稍后再试。";
}

export function steamSyncResultMessage(result: SteamSyncResult): string {
  const count = Math.max(0, result.discovered_count);
  if (result.limit_reached) {
    return `发现 ${count} 场新比赛。本次同步已达上限，再同步一次可以继续。`;
  }
  if (result.caught_up && count === 0) {
    return "没有新比赛，Steam 比赛记录已是最新。";
  }
  if (result.caught_up) {
    return `发现 ${count} 场新比赛，Steam 比赛记录已是最新。`;
  }
  return `发现 ${count} 场新比赛。`;
}

// Player-level copy: no request origins, providers or parser jargon.
export function steamRequestErrorMessage(
  status: number | null,
  action: SteamRequestAction,
  detailCode: string | null = null
): string {
  if (status === 401) {
    return "登录已过期，请重新登录后继续。";
  }
  if (action === "import") {
    if (detailCode === "demo_source_unavailable" || detailCode === "downloader_not_configured") {
      return AUTOMATIC_IMPORT_UNAVAILABLE;
    }
    if (detailCode === "demo_import_in_progress") {
      return "这场比赛正在导入，请刷新查看最新状态。";
    }
    if (status === 404) {
      return "这条 Steam 比赛记录已不存在，请刷新列表。";
    }
    if (status === 409) {
      return "这场比赛现在无法导入，请刷新状态，或手动上传 .dem 文件。";
    }
    return IMPORT_FAILED;
  }
  if (detailCode === "steam_authorization_invalid") {
    return AUTHORIZATION_REJECTED;
  }
  if (detailCode === "steam_cursor_invalid") {
    return SHARING_CODE_REJECTED;
  }
  if (status === 429 || status === 503) {
    return "Steam 比赛记录暂时无法访问或请求过于频繁，请稍后再试。";
  }
  if (status === 409) {
    return "连接需要处理，或同步已在进行中。请刷新状态后再试。";
  }

  const messages: Record<SteamRequestAction, string> = {
    load: "暂时无法读取 Steam 连接状态，请刷新重试。",
    connect: "保存失败，请检查两个授权码后重试。",
    sync: "同步 Steam 比赛记录失败，请稍后再试。",
    disconnect: "断开连接失败，请稍后再试。",
    import: IMPORT_FAILED
  };
  return messages[action];
}

// The backend's import error text is operator English; the code says enough.
export function steamImportErrorMessage(code: string | null | undefined): string | null {
  if (!code) {
    return null;
  }
  if (code === "demo_source_unavailable" || code === "downloader_not_configured") {
    return AUTOMATIC_IMPORT_UNAVAILABLE;
  }
  if (code === "parser_dispatch_unavailable") {
    return "比赛已下载，但还没能开始处理，可以重新处理。";
  }
  if (code.startsWith("parse")) {
    return "比赛录像无法读取，请手动上传这场比赛的 .dem 文件。";
  }
  return IMPORT_FAILED;
}

function formatDuration(value: number | null): string | null {
  if (!Number.isInteger(value) || value === null || value < 0) {
    return null;
  }
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  if (minutes === 0) {
    return `${seconds} 秒`;
  }
  return `${minutes} 分 ${seconds.toString().padStart(2, "0")} 秒`;
}

function formatSideRounds(ctRounds: number | null, tRounds: number | null): string | null {
  if (!isRoundCount(ctRounds) && !isRoundCount(tRounds)) {
    return null;
  }
  return `CT ${isRoundCount(ctRounds) ? ctRounds : "—"} 比 T ${isRoundCount(tRounds) ? tRounds : "—"}`;
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
  const visible = names.slice(0, 3).join("、");
  return names.length > 3 ? `${visible} 等 ${names.length} 人` : visible;
}
