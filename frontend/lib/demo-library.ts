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
type DemoStateSource = ParseFailureSource & RetrySource & { status: DemoProcessingStatus };

export type ParseFailureSuggestion = "reupload" | "retry";

export interface ParseFailureCopy {
  message: string;
  // What fixes this failure: another copy of the file, or another pass over this one.
  suggestion: ParseFailureSuggestion;
}

export interface ParseFailureAction {
  action: ParseFailureSuggestion;
  hint: string;
}

export type DetailProcessingStep = "uploaded" | "parsing" | "analyzing";
export type StatusFetchFailure = "not_found" | "unreachable";

export interface DetailFailureState {
  kind: "failed" | "replay_unavailable";
  failure: ParseFailureCopy;
  next: ParseFailureAction;
  retryable: boolean;
  errorCode: string | null;
  attemptCount: number;
}

// Everything the demo page can show before the review workspace is ready.
export type DetailLoadState =
  | { kind: "connecting" }
  | { kind: "not_found" }
  | { kind: "unreachable" }
  | { kind: "processing"; step: DetailProcessingStep; stale: boolean; startedAt: string | null }
  | DetailFailureState
  | { kind: "replay_load_failed"; retryable: boolean }
  | { kind: "loading_replay" };

export interface DetailLoadStateInput {
  status: DemoStateSource | null;
  statusFailure: StatusFetchFailure | null;
  // The caller's own verdict on its last replay fetch.
  replayLoadFailed: boolean;
}

export interface ProcessingStepItem {
  key: DetailProcessingStep;
  label: string;
  state: "done" | "current" | "pending";
}

const STATUS_ORDER: DemoProcessingStatus[] = ["queued", "parsing", "analyzing", "completed", "failed"];
const ACTIVE_DEMO_STATUSES = new Set<DemoProcessingStatus>(["queued", "parsing", "analyzing"]);
const ACTIVE_RENDER_STATUSES = new Set(["queued", "processing", "rendering"]);
const STATUS_LABELS: Record<DemoProcessingStatus, string> = {
  queued: "uploaded",
  parsing: "parsing",
  analyzing: "analyzing",
  completed: "ready",
  failed: "failed"
};

export interface DemoLibraryLabels {
  // Player-facing map name; the dashboard passes map-config's mapDisplayName.
  mapLabel?: (mapName: string) => string;
}

export function filterAndSortDemos(
  demos: DemoSummary[],
  filters: DemoLibraryFilters,
  labels: DemoLibraryLabels = {}
): DemoSummary[] {
  const mapLabel = labels.mapLabel ?? fallbackMapLabel;
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
    const searchableText = searchableDemoFields(demo, mapLabel).join(" ").toLowerCase();
    const id = demo.id.toLowerCase();
    // An ID only matches a deliberate prefix, so one letter does not hit every UUID.
    return searchTokens.every(
      (token) => searchableText.includes(token) || (token.length >= 8 && id.startsWith(token))
    );
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

  // Nothing to act on yet: the rows are on their way, so no first-run CTAs.
  if (loading) {
    return {
      kind: "loading",
      title: "Loading demos",
      message: "Checking the local library, parser queue, and render jobs.",
      showMockAction: false,
      showUploadAction: false,
      showRefreshAction: false,
      showClearFiltersAction: false,
      showArchivedAction: false
    };
  }

  // An upload would fail against the same unreachable API; refreshing is the one useful action.
  if (error && demos.length === 0) {
    return {
      kind: "error",
      title: "Library unavailable",
      message: friendlyErrorMessage(error),
      ...baseActions,
      showMockAction: false,
      showUploadAction: false
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

// Keyed by the backend's parse failure codes (demo_parser.py, workers/, demo_service/constants.py).
const PARSE_FAILURE_COPY: Record<string, ParseFailureCopy> = {
  INVALID_DEMO: { message: "文件无法读取，可能不是完整的 CS2 .dem 比赛文件。", suggestion: "reupload" },
  UNSUPPORTED_PARSER_FORMAT: { message: "暂不支持这个比赛文件的格式。", suggestion: "reupload" },
  MISSING_MATCH_METADATA: { message: "比赛文件缺少必要的对局信息，可能不完整。", suggestion: "reupload" },
  MISSING_FRAMES: { message: "比赛文件里没有可用的玩家位置数据。", suggestion: "reupload" },
  PARSE_ABANDONED: { message: "处理多次中断，已停止。", suggestion: "reupload" },
  PARSE_TIMED_OUT: { message: "处理时间过长，已停止。", suggestion: "retry" },
  PARSE_OUT_OF_MEMORY: { message: "处理这场比赛时内存不足，已停止。", suggestion: "retry" },
  PARSER_CRASHED: { message: "读取比赛时处理程序意外中断。", suggestion: "retry" },
  NORMALIZATION_FAILED: { message: "比赛数据整理失败。", suggestion: "retry" },
  STORAGE_READ_FAILED: { message: "已上传的文件暂时无法读取。", suggestion: "retry" },
  REPLAY_ARTIFACT_MISSING: { message: "回放数据已失效，重新处理即可恢复。", suggestion: "retry" }
};
const UNEXPECTED_PARSE_FAILURE: ParseFailureCopy = { message: "处理时出现意外错误。", suggestion: "retry" };

export function parseFailureCopy(errorCode: string | null | undefined): ParseFailureCopy {
  return (errorCode && PARSE_FAILURE_COPY[errorCode]) || UNEXPECTED_PARSE_FAILURE;
}

// The backend decides whether a retry is possible; the code decides whether it can help.
export function parseFailureAction(copy: ParseFailureCopy, retryable: boolean): ParseFailureAction {
  if (copy.suggestion === "retry" && retryable) {
    return { action: "retry", hint: "可以重新处理，通常就能恢复。" };
  }
  if (copy.suggestion === "retry") {
    return { action: "reupload", hint: "暂时无法重新处理，请在「我的比赛」重新上传这场比赛的 .dem 文件。" };
  }
  return { action: "reupload", hint: "请重新下载这场比赛的 .dem 文件，然后在「我的比赛」重新上传。" };
}

export function demoFailureState(demo: DemoStateSource): DetailFailureState | null {
  const failure = demo.ingestion?.failure ?? null;
  if (demo.status !== "failed" && !failure) {
    return null;
  }
  const copy = parseFailureCopy(failure?.errorCode);
  const retryable = canRetryParse(demo);
  return {
    // A completed demo with a failure lost its replay after the parse succeeded.
    kind: demo.status === "failed" ? "failed" : "replay_unavailable",
    failure: copy,
    next: parseFailureAction(copy, retryable),
    retryable,
    errorCode: failure?.errorCode ?? null,
    attemptCount: failure?.attemptCount ?? demo.ingestion?.attemptCount ?? 0
  };
}

// Status turns "completed" a beat before the replay fetch resolves, so a
// completed demo without a reported failure is loading, never broken, until the
// caller's own fetch has actually failed.
export function detailLoadState(input: DetailLoadStateInput): DetailLoadState {
  const { status, statusFailure, replayLoadFailed } = input;
  if (statusFailure === "not_found") {
    return { kind: "not_found" };
  }
  if (!status) {
    return statusFailure === "unreachable" ? { kind: "unreachable" } : { kind: "connecting" };
  }
  if (ACTIVE_DEMO_STATUSES.has(status.status) || status.ingestion?.active) {
    return {
      kind: "processing",
      step: processingStep(status),
      stale: Boolean(status.ingestion?.stale),
      startedAt: status.ingestion?.startedAt ?? null
    };
  }
  const failure = demoFailureState(status);
  if (failure) {
    return failure;
  }
  return replayLoadFailed
    ? { kind: "replay_load_failed", retryable: canRetryParse(status) }
    : { kind: "loading_replay" };
}

const PROCESSING_STEPS: Array<{ key: DetailProcessingStep; label: string }> = [
  { key: "uploaded", label: "上传完成" },
  { key: "parsing", label: "解析比赛" },
  { key: "analyzing", label: "分析建议" }
];

const PROCESSING_HEADLINES: Record<DetailProcessingStep, string> = {
  uploaded: "已上传，正在排队等待解析…",
  parsing: "解析中：读取回合与玩家位置…",
  analyzing: "分析中：整理复盘建议…"
};

export function processingHeadline(step: DetailProcessingStep): string {
  return PROCESSING_HEADLINES[step];
}

// "uploaded" means the upload is done and the parse is next in line, so the
// parse step is the one in progress either way.
export function processingSteps(step: DetailProcessingStep): ProcessingStepItem[] {
  const current = step === "analyzing" ? 2 : 1;
  return PROCESSING_STEPS.map((item, index) => ({
    ...item,
    state: index < current ? "done" : index === current ? "current" : "pending"
  }));
}

export function processingElapsedLabel(startedAt: string | null | undefined, nowMs: number): string | null {
  const started = safeTimestamp(startedAt);
  if (started === null) {
    return null;
  }
  const seconds = Math.floor((nowMs - started) / 1000);
  // A skewed clock or a day-old job says nothing useful about this run.
  if (seconds < 0 || seconds > 24 * 60 * 60) {
    return null;
  }
  return `已用时 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function processingStep(demo: DemoStateSource): DetailProcessingStep {
  if (demo.status === "analyzing") return "analyzing";
  if (demo.status === "parsing") return "parsing";
  if (demo.status === "queued") return "uploaded";
  // A re-parse can be in flight while the row itself still reads failed or completed.
  const jobStatus = demo.ingestion?.jobStatus;
  return jobStatus === "queued" || jobStatus === "pending" ? "uploaded" : "parsing";
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

// What the row shows, in the words it shows it: raw IDs, ISO timestamps and
// English status tokens made every one-letter query match every row.
function searchableDemoFields(demo: DemoSummary, mapLabel: (mapName: string) => string): string[] {
  const failure = demoFailureState(demo);
  return [
    demo.name,
    demo.original_filename,
    demo.map_name,
    mapLabel(demo.map_name),
    demoStatusDisplayLabel(demo.status),
    failure?.failure.message ?? "",
    failure?.retryable ? "重新处理" : "",
    demo.ingestion?.stale ? "处理时间较长" : "",
    demo.archived ? "已归档" : "",
    libraryVideoLabel(demo) ?? ""
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

const STATUS_DISPLAY_LABELS: Record<DemoProcessingStatus, string> = {
  queued: "等待处理",
  parsing: "读取比赛中",
  analyzing: "整理建议中",
  completed: "可以复盘",
  failed: "处理失败"
};

// The Chinese status every surface shows; demoStatusLabel stays the API token.
export function demoStatusDisplayLabel(status: DemoProcessingStatus): string {
  return STATUS_DISPLAY_LABELS[status] ?? status;
}

const PROCESSING_NOTICE_LABELS: Record<DemoProcessingStatus, string> = {
  queued: "正在排队处理",
  parsing: "正在读取比赛",
  analyzing: "正在整理建议",
  completed: "可以复盘",
  failed: "处理失败"
};

export function processingNoticeLabel(status: DemoProcessingStatus): string {
  return PROCESSING_NOTICE_LABELS[status] ?? status;
}

export function isDemoParseActive(demo: DemoSummary): boolean {
  return Boolean(demo.ingestion?.active) || ACTIVE_DEMO_STATUSES.has(demo.status);
}

// Demos still being read or analyzed; video-only work is counted separately.
export function countParsingLibraryDemos(demos: DemoSummary[]): number {
  return demos.filter(isDemoParseActive).length;
}

export function countVideoLibraryDemos(demos: DemoSummary[]): number {
  return demos.filter((demo) => !isDemoParseActive(demo) && playbackReadiness(demo) === "rendering").length;
}

export function libraryVideoLabel(demo: DemoSummary): string | null {
  const readiness = playbackReadiness(demo);
  if (readiness === "ready") return "有第一人称视频";
  if (readiness === "rendering") return "视频生成中";
  if (readiness === "none") return "战术回放可用";
  return null;
}

export interface LibraryDisplayTitle {
  title: string;
  // The file name, when the title is not already it.
  filename: string | null;
  // True when the title was composed from the map and rounds, not a name.
  composed: boolean;
}

// An upload is named after its file ("match730_0037….dem"), which reads the
// same on every row; until the player renames it, a parsed demo is titled by
// what tells it apart.
export function libraryDisplayTitle(
  demo: Pick<DemoSummary, "name" | "original_filename" | "status" | "map_name" | "round_count">,
  mapLabel: (mapName: string) => string = fallbackMapLabel
): LibraryDisplayTitle {
  const autoNamed = demo.name === demo.original_filename;
  if (autoNamed && demo.status === "completed" && demo.round_count > 0) {
    return {
      title: `${mapLabel(demo.map_name)}，${demo.round_count} 回合`,
      filename: demo.original_filename,
      composed: true
    };
  }
  return {
    title: demo.name,
    filename: autoNamed ? null : demo.original_filename,
    composed: false
  };
}

export function formatLibraryDate(value: string | null | undefined): string {
  const timestamp = safeTimestamp(value);
  if (timestamp === null) {
    return "—";
  }
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function fallbackMapLabel(mapName: string): string {
  if (!mapName || mapName === "unknown") return "地图待识别";
  const name = mapName.replace(/^de_/, "");
  return name.charAt(0).toUpperCase() + name.slice(1);
}
