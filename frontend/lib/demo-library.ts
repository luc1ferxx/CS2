import type { RenderJobStatus } from "@/lib/api";
import type { DemoIngestionStatus, DemoProcessingStatus, DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayMapConfidence, ReplayVideoSource } from "@/types/replay";

export type DemoLibrarySort = "recent" | "name" | "map" | "status";
export type DemoLibraryOrder = "asc" | "desc";
export type DemoLibraryStatusFilter = DemoProcessingStatus | "all";

export interface DemoLibraryFilters {
  search: string;
  status: DemoLibraryStatusFilter;
  map: string;
  sort: DemoLibrarySort;
  order: DemoLibraryOrder;
  includeArchived: boolean;
}

export interface DemoLibraryFilterOptions {
  statuses: DemoProcessingStatus[];
  maps: string[];
}

export interface LibraryEmptyState {
  kind: "loading" | "error" | "empty" | "archived" | "search" | "filtered";
  title: string;
  message: string;
  showMockAction: boolean;
  showUploadAction: boolean;
  showRefreshAction: boolean;
  showClearFiltersAction: boolean;
  showArchivedAction: boolean;
}

export interface LibraryEmptyStateInput {
  loading: boolean;
  error: string | null;
  demos: DemoSummary[];
  visibleDemos: DemoSummary[];
  filters: DemoLibraryFilters;
}

export interface PollLibraryInput {
  loading: boolean;
  creating: boolean;
  activeJobs: number;
}

export interface DetailSummaryInput {
  status: DemoStatus | null;
  replay: ReplayData | null;
  latestRenderJob?: Pick<RenderJobStatus, "job_type" | "status" | "error_message"> | null;
}

export interface DetailSummaryItem {
  label: string;
  value: string;
  tone?: "default" | "good" | "warning" | "danger";
  detail?: string;
}

interface ParseFailureSource {
  ingestion: DemoIngestionStatus | null;
  error_message: string | null;
}

interface RetrySource {
  ingestion: DemoIngestionStatus | null;
}

// The detail page holds a DemoStatus and the library a DemoSummary; both carry
// everything this needs, so ask for the fields instead of one of the shapes.
type ReplayNoticeSource = ParseFailureSource & RetrySource & { status: DemoProcessingStatus };

export interface ReplayUnavailableNotice {
  message: string;
  retryable: boolean;
}

const STATUS_ORDER: DemoProcessingStatus[] = ["queued", "parsing", "analyzing", "completed", "failed"];
const ACTIVE_DEMO_STATUSES = new Set<DemoProcessingStatus>(["queued", "parsing", "analyzing"]);
const PREPARING_REPLAY_MESSAGE = "正在准备回放，完成后会自动显示。";
const ACTIVE_RENDER_STATUSES = new Set(["queued", "processing", "rendering"]);
const STATUS_LABELS: Record<DemoProcessingStatus, string> = {
  queued: "uploaded",
  parsing: "parsing",
  analyzing: "analyzing",
  completed: "ready",
  failed: "failed"
};

export function filterAndSortDemos(
  demos: DemoSummary[],
  filters: DemoLibraryFilters
): DemoSummary[] {
  const search = filters.search.trim().toLowerCase();
  const searchTokens = search.split(/\s+/).filter(Boolean);
  const filtered = demos.filter((demo) => {
    if (!filters.includeArchived && demo.archived) {
      return false;
    }
    if (filters.status !== "all" && demo.status !== filters.status) {
      return false;
    }
    if (filters.map !== "all" && demo.map_name !== filters.map) {
      return false;
    }
    if (searchTokens.length === 0) {
      return true;
    }
    const searchableText = searchableDemoFields(demo).join(" ").toLowerCase();
    return searchTokens.every((token) => searchableText.includes(token));
  });

  return filtered.sort((left, right) => compareDemos(left, right, filters.sort, filters.order));
}

export function demoLibraryFilterOptions(demos: DemoSummary[]): DemoLibraryFilterOptions {
  const maps = new Set<string>();
  for (const demo of demos) {
    if (demo.map_name && demo.map_name !== "unknown") {
      maps.add(demo.map_name);
    }
  }

  return {
    statuses: [...STATUS_ORDER],
    maps: [...maps].sort((left, right) => left.localeCompare(right))
  };
}

export function friendlyErrorMessage(message: string | null | undefined): string {
  const fallback = "Request failed. Refresh the library or check /diagnostics.";
  const trimmed = message?.trim();
  if (!trimmed) {
    return fallback;
  }

  const lower = trimmed.toLowerCase();
  if (
    lower.includes("failed to fetch") ||
    lower.includes("networkerror") ||
    lower.includes("load failed") ||
    lower.includes("err_connection") ||
    lower.includes("econnrefused")
  ) {
    return "API is unreachable. Check the backend, Redis worker, and /diagnostics.";
  }

  return trimmed;
}

export function shouldPollLibrary(input: PollLibraryInput): boolean {
  return !input.loading && (input.creating || input.activeJobs > 0);
}

export function libraryEmptyState(input: LibraryEmptyStateInput): LibraryEmptyState | null {
  const { loading, error, demos, visibleDemos, filters } = input;
  if (visibleDemos.length > 0) {
    return null;
  }

  const hasSearch = filters.search.trim().length > 0;
  const hasStatusFilter = filters.status !== "all";
  const hasMapFilter = filters.map !== "all";
  const baseActions = {
    showMockAction: true,
    showUploadAction: true,
    showRefreshAction: true,
    showClearFiltersAction: false,
    showArchivedAction: false
  };

  if (loading) {
    return {
      kind: "loading",
      title: "Loading demos",
      message: "Checking the local library, parser queue, and render jobs.",
      ...baseActions
    };
  }

  if (error && demos.length === 0) {
    return {
      kind: "error",
      title: "Library unavailable",
      message: friendlyErrorMessage(error),
      ...baseActions
    };
  }

  if (demos.length === 0) {
    return {
      kind: "empty",
      title: "No demos yet",
      message: "Upload a real .dem file for parser review, or create a mock demo for a fast UI smoke test.",
      ...baseActions
    };
  }

  if (!filters.includeArchived && demos.every((demo) => demo.archived)) {
    return {
      kind: "archived",
      title: "Only archived demos match",
      message: "Archived demos stay openable by ID. Show archived items to restore this library view.",
      ...baseActions,
      showMockAction: false,
      showUploadAction: false,
      showClearFiltersAction: hasSearch || hasStatusFilter || hasMapFilter,
      showArchivedAction: true
    };
  }

  if (hasSearch) {
    return {
      kind: "search",
      title: "No demos match this search",
      message: `No visible demo matches "${filters.search.trim()}". Clear search or filters to return to the library.`,
      ...baseActions,
      showMockAction: false,
      showUploadAction: false,
      showClearFiltersAction: true
    };
  }

  return {
    kind: "filtered",
    title: "No demos match these filters",
    message: "Clear the status, map, or archive filters to return to the library.",
    ...baseActions,
    showMockAction: false,
    showUploadAction: false,
    showClearFiltersAction: true,
    showArchivedAction: !filters.includeArchived && demos.some((demo) => demo.archived)
  };
}

export function demoStatusLabel(status: DemoProcessingStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function ingestionPhaseLabel(demo: DemoSummary): string {
  const phase = demo.ingestion?.phase;
  if (!phase) {
    return demoStatusLabel(demo.status);
  }
  if (phase === "ready") {
    return "ready";
  }
  if (phase === "uploaded") {
    return "uploaded";
  }
  return phase;
}

export function parseFailureReason(demo: ParseFailureSource): string | null {
  const failure = demo.ingestion?.failure;
  if (!failure) {
    return demo.error_message ?? null;
  }

  const labels = [`${failure.errorCode}: ${failure.message}`];
  if (failure.retryable || demo.ingestion?.retryable) {
    labels.push("retry available");
  }
  if (failure.attemptCount > 0) {
    labels.push(`attempt ${failure.attemptCount}`);
  }
  return labels.join(" / ");
}

export function canRetryParse(demo: RetrySource): boolean {
  return Boolean(demo.ingestion?.retryable);
}

// `loadFailed` is the caller's own verdict on its last replay fetch. Status turns
// "completed" a beat before that fetch resolves, so without it every cold load of
// a healthy demo flashes a failure the backend never reported.
export function replayUnavailableNotice(
  demo: ReplayNoticeSource | null,
  loadFailed = false
): ReplayUnavailableNotice {
  if (!demo || ACTIVE_DEMO_STATUSES.has(demo.status) || demo.ingestion?.active) {
    return { message: PREPARING_REPLAY_MESSAGE, retryable: false };
  }

  const retryable = canRetryParse(demo);
  const reason = parseFailureReason(demo);
  if (demo.status === "failed") {
    return { message: `比赛处理失败。${reason ?? "暂时无法加载回放。"}`, retryable };
  }
  if (reason) {
    // The parse already finished, so "正在准备回放" would send the user off to
    // wait for something that is never coming. A demo whose replay artifact
    // went missing lands here: say what broke and offer the one fix.
    return { message: `回放暂时无法打开。${reason}`, retryable };
  }
  if (loadFailed) {
    // Nothing upstream is wrong, so there is no re-parse to offer: the fetch
    // itself failed and a reload is the honest exit.
    return { message: "回放暂时无法打开，请刷新页面重试。", retryable };
  }
  return { message: PREPARING_REPLAY_MESSAGE, retryable: false };
}

export function isRenderActiveStatus(status: string | null | undefined): boolean {
  return typeof status === "string" && ACTIVE_RENDER_STATUSES.has(status);
}

export function isDemoLibraryActive(demo: DemoSummary): boolean {
  return (
    Boolean(demo.ingestion?.active) ||
    ACTIVE_DEMO_STATUSES.has(demo.status) ||
    isRenderActiveStatus(demo.latest_render_status) ||
    isRenderActiveStatus(demo.video_status)
  );
}

export function countActiveLibraryDemos(demos: DemoSummary[]): number {
  return demos.filter(isDemoLibraryActive).length;
}

export function renderStatusLabel(demo: DemoSummary): string {
  if (demo.latest_render_status) {
    return `render ${demo.latest_render_status}`;
  }
  if (demo.video_source === "manual_upload" && demo.video_status) {
    return `manual ${demo.video_status}`;
  }
  if (demo.video_source === "rendered" && demo.video_status) {
    return `render ${demo.video_status}`;
  }
  if (demo.video_status && demo.video_status !== "pending") {
    return `${demo.video_source ?? "video"} ${demo.video_status}`;
  }
  return "not requested";
}

export type PlaybackReadiness = "ready" | "rendering" | "none" | "unavailable";

/** Whether a demo can be watched, and in what form.
 *  - ready: a replay video is available to play
 *  - rendering: a video is being generated (2D replay watchable meanwhile)
 *  - none: no video yet, but the parsed 2D tactical replay is watchable
 *  - unavailable: demo hasn't finished parsing, nothing to watch yet */
export function playbackReadiness(demo: DemoSummary): PlaybackReadiness {
  const hasRealVideoSource =
    demo.video_source === "rendered" ||
    demo.video_source === "manual_upload" ||
    demo.video_source == null;
  // A mock replay's ready state describes the tactical replay, not real footage.
  // Older summaries can omit video status/source and expose only the completed job.
  const videoReady =
    hasRealVideoSource &&
    (demo.video_status === "ready" ||
      (demo.video_status == null && demo.latest_render_status === "completed"));
  if (videoReady) {
    return "ready";
  }
  if (
    isRenderActiveStatus(demo.latest_render_status) ||
    isRenderActiveStatus(demo.video_status)
  ) {
    return "rendering";
  }
  if (demo.status === "completed") {
    return "none";
  }
  return "unavailable";
}

const PLAYBACK_LABELS: Record<PlaybackReadiness, string> = {
  ready: "Play",
  rendering: "Rendering",
  none: "Watch 2D",
  unavailable: "Not ready"
};

export function playbackActionLabel(readiness: PlaybackReadiness): string {
  return PLAYBACK_LABELS[readiness];
}

export function detailSummaryItems(input: DetailSummaryInput): DetailSummaryItem[] {
  const { status, replay, latestRenderJob } = input;
  const video = replay?.video ?? null;
  const mapMetadata = replay?.mapMetadata;
  const parserFailure = status?.ingestion?.failure ?? null;
  const parserValue = parserFailure?.errorCode ?? parserStatusValue(status);
  const parserTone: DetailSummaryItem["tone"] =
    parserFailure || status?.status === "failed" ? "danger" : status?.ingestion?.active ? "warning" : "good";
  const renderValue = latestRenderJob
    ? `${latestRenderJob.job_type} ${latestRenderJob.status}`
    : video?.source === "rendered"
      ? `render ${video.status}`
      : "not requested";
  const renderTone = latestRenderJob ? statusTone(latestRenderJob.status) : statusTone(video?.status);

  return [
    {
      label: "File",
      value: status?.original_filename || status?.name || replay?.demoId || "unknown"
    },
    {
      label: "Map",
      value: mapMetadata?.displayName || status?.map_name || replay?.mapName || "unknown",
      detail: replay?.mapName && mapMetadata?.displayName ? replay.mapName : undefined
    },
    {
      label: "Calibration",
      value: calibrationLabel(mapMetadata?.confidence, mapMetadata?.calibrated)
    },
    {
      label: "Rounds",
      value: String(status?.round_count ?? replay?.rounds.length ?? 0)
    },
    {
      label: "Coaching",
      value: eventCountLabel(status?.coaching_event_count ?? 0)
    },
    {
      label: "Parser",
      value: parserValue,
      tone: parserTone,
      detail: parserFailure?.message ?? status?.error_message ?? undefined
    },
    {
      label: "Media",
      value: mediaStatusValue(replay),
      tone: replay ? statusTone(video?.status) : "danger",
      detail: video?.errorMessage ?? undefined
    },
    {
      label: "Render",
      value: renderValue,
      tone: renderTone,
      detail: latestRenderJob?.error_message ?? undefined
    }
  ];
}

function searchableDemoFields(demo: DemoSummary): string[] {
  return [
    demo.id,
    demo.name,
    demo.original_filename,
    demo.map_name,
    demo.status,
    demoStatusLabel(demo.status),
    ingestionPhaseLabel(demo),
    demo.ingestion?.stale ? "stale" : "",
    canRetryParse(demo) ? "retry retryable" : "",
    demo.ingestion?.jobStatus ?? "",
    parseFailureReason(demo) ?? "",
    renderStatusLabel(demo),
    demo.created_at,
    demo.updated_at,
    `${demo.round_count} rounds`,
    `${demo.coaching_event_count} coaching`
  ];
}

function parserStatusValue(status: DemoStatus | null): string {
  if (!status) {
    return "unknown";
  }
  if (status.ingestion?.phase === "ready" || status.status === "completed") {
    return "ready";
  }
  return status.ingestion?.phase || demoStatusLabel(status.status);
}

function mediaStatusValue(replay: ReplayData | null): string {
  if (!replay) {
    return "replay unavailable";
  }
  return `${videoSourceLabel(replay.video.source)} ${replay.video.status}`;
}

function videoSourceLabel(source: ReplayVideoSource | string): string {
  if (source === "manual_upload") {
    return "manual";
  }
  return source;
}

function calibrationLabel(
  confidence: ReplayMapConfidence | null | undefined,
  calibrated: boolean | null | undefined
): string {
  if (confidence) {
    return confidence;
  }
  if (calibrated === true) {
    return "calibrated";
  }
  if (calibrated === false) {
    return "fallback";
  }
  return "unknown";
}

function eventCountLabel(count: number): string {
  return `${count} ${count === 1 ? "event" : "events"}`;
}

function statusTone(status: string | null | undefined): DetailSummaryItem["tone"] {
  if (!status || status === "pending") {
    return "default";
  }
  if (status === "ready" || status === "completed") {
    return "good";
  }
  if (status === "failed") {
    return "danger";
  }
  return "warning";
}

function compareDemos(
  left: DemoSummary,
  right: DemoSummary,
  sort: DemoLibrarySort,
  order: DemoLibraryOrder
): number {
  let result = 0;

  if (sort === "recent") {
    result = compareTimestamps(left.created_at, right.created_at, order);
  } else if (sort === "name") {
    result = compareText(left.name, right.name, order);
  } else if (sort === "map") {
    result = compareText(left.map_name, right.map_name, order);
  } else {
    result = compareNumbers(statusRank(left.status), statusRank(right.status), order);
  }

  return result || compareDemoTiebreakers(left, right);
}

function compareDemoTiebreakers(left: DemoSummary, right: DemoSummary): number {
  return (
    compareText(left.name, right.name, "asc") ||
    compareText(left.original_filename, right.original_filename, "asc") ||
    compareText(left.map_name, right.map_name, "asc") ||
    compareNumbers(statusRank(left.status), statusRank(right.status), "asc") ||
    compareTimestamps(left.updated_at, right.updated_at, "desc") ||
    compareTimestamps(left.created_at, right.created_at, "desc") ||
    compareText(left.id, right.id, "asc")
  );
}

function compareText(left: string | null | undefined, right: string | null | undefined, order: DemoLibraryOrder): number {
  const direction = order === "asc" ? 1 : -1;
  return String(left ?? "").localeCompare(String(right ?? ""), undefined, { sensitivity: "base" }) * direction;
}

function compareNumbers(left: number, right: number, order: DemoLibraryOrder): number {
  const direction = order === "asc" ? 1 : -1;
  return (left - right) * direction;
}

function compareTimestamps(left: string | null | undefined, right: string | null | undefined, order: DemoLibraryOrder): number {
  const leftTimestamp = safeTimestamp(left);
  const rightTimestamp = safeTimestamp(right);

  if (leftTimestamp === null && rightTimestamp === null) {
    return 0;
  }
  if (leftTimestamp === null) {
    return 1;
  }
  if (rightTimestamp === null) {
    return -1;
  }
  return compareNumbers(leftTimestamp, rightTimestamp, order);
}

function safeTimestamp(value: string | null | undefined): number | null {
  const timestamp = Date.parse(value ?? "");
  return Number.isFinite(timestamp) ? timestamp : null;
}

function statusRank(status: DemoProcessingStatus): number {
  const index = STATUS_ORDER.indexOf(status);
  return index === -1 ? STATUS_ORDER.length : index;
}
