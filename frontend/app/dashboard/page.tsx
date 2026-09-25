"use client";

import Link from "next/link";
import {
  Archive,
  ArrowDownUp,
  Check,
  CircleAlert,
  CircleCheck,
  ChevronDown,
  Clock3,
  ExternalLink,
  FileUp,
  Loader2,
  Map as MapIcon,
  MoreHorizontal,
  Pencil,
  Play,
  RefreshCcw,
  Search,
  Undo2,
  UploadCloud,
  X
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode
} from "react";

import { DemFileHelp } from "@/components/upload/DemFileHelp";
import { DemoUploader } from "@/components/upload/DemoUploader";
import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { ErrorBanner } from "@/components/feedback/ErrorBanner";
import { useAuth } from "@/components/auth/AuthProvider";
import { SessionControls } from "@/components/auth/SessionControls";
import { AppBrand } from "@/components/layout/AppBrand";
import { RecentSteamMatches } from "@/components/steam/RecentSteamMatches";
import {
  archiveDemo,
  createMockUpload,
  getUploadQuota,
  isApiError,
  isUploadAbortError,
  listDemos,
  retryDemoParse,
  updateDemo,
  uploadDemoFile,
  type UploadQuota
} from "@/lib/api";
import { NO_CAPABILITIES } from "@/lib/auth";
import {
  canRetryParse,
  countActiveLibraryDemos,
  countParsingLibraryDemos,
  countVideoLibraryDemos,
  demoFailureState,
  demoLibraryFilterOptions,
  demoStatusDisplayLabel,
  filterAndSortDemos,
  formatLibraryDate,
  isDemoParseActive,
  libraryDisplayTitle,
  libraryEmptyState,
  libraryVideoLabel,
  playbackReadiness,
  processingElapsedLabel,
  processingNoticeLabel,
  shouldPollLibrary,
  type DemoLibraryFilters,
  type LibraryEmptyState
} from "@/lib/demo-library";
import {
  cancelDemoUpload,
  getDemoUploadSnapshot,
  startDemoUpload,
  subscribeDemoUpload,
  takeDemoUploadOutcome,
  formatMegabytes,
  uploadEtaLabel,
  uploadPercent,
  uploadProgressLabel,
  type DemoUploadOutcome,
  type DemoUploadSnapshot
} from "@/lib/demo-upload";
import { getTacticalMapConfig, mapDisplayName } from "@/lib/map-config";
import {
  MAX_DEMO_UPLOAD_BYTES,
  demoFileProblem,
  demoUploadErrorMessage,
  uploadQuotaSummary
} from "@/lib/upload-limits";
import { usePoll } from "@/lib/use-poll";
import { userFacingError } from "@/lib/user-errors";
import type { DemoProcessingStatus, DemoSummary } from "@/types/demo";

const DEFAULT_FILTERS: DemoLibraryFilters = {
  search: "",
  status: "all",
  map: "all",
  sort: "recent",
  order: "desc",
  includeArchived: false
};

const UPLOAD_BUTTON = "#demo-upload-input-button";
const QUOTA_RESET_SLACK_MS = 5000;
const QUOTA_RECHECK_MS = 5 * 60 * 1000;
const NOTICE_UNDO = "#library-notice-undo";
const LIBRARY_LABELS = { mapLabel: mapDisplayName };

type LibraryNotice =
  | { kind: "message"; message: string; demoId?: string }
  // Text follows the live row, so it tracks the parse through to done or failed.
  | { kind: "upload"; demo: DemoSummary }
  | { kind: "archived"; demo: DemoSummary };

interface UploadFailure {
  message: string;
  code: string | null;
}

export default function DashboardPage() {
  return (
    <AuthBoundary>
      <DashboardContent />
    </AuthBoundary>
  );
}

function serverUploadSnapshot(): DemoUploadSnapshot | null {
  return null;
}

function rowSelector(demoId: string, inner: string): string {
  return `[data-demo-id="${demoId}"] ${inner}`;
}

function DashboardContent() {
  const { state: authState } = useAuth();
  const { devTools } = authState.capabilities ?? NO_CAPABILITIES;
  const [demos, setDemos] = useState<DemoSummary[]>([]);
  const [filters, setFilters] = useState<DemoLibraryFilters>(DEFAULT_FILTERS);
  const [loading, setLoading] = useState(true);
  // Mock creation only; a real upload lives in the upload store below.
  const [creating, setCreating] = useState(false);
  const [busyDemoId, setBusyDemoId] = useState<string | null>(null);
  const [renamingDemoId, setRenamingDemoId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Library polling clears `error` on every successful fetch; a quota rejection
  // must outlive that, so upload failures stay here until the next attempt.
  const [uploadError, setUploadError] = useState<UploadFailure | null>(null);
  // Same for a refused parse retry: the in-flight demos behind the limit keep polling on.
  const [retryError, setRetryError] = useState<string | null>(null);
  const [notice, setNotice] = useState<LibraryNotice | null>(null);
  const [importOptionsLoaded, setImportOptionsLoaded] = useState(false);
  const [quota, setQuota] = useState<UploadQuota | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const upload = useSyncExternalStore(subscribeDemoUpload, getDemoUploadSnapshot, serverUploadSnapshot);
  const loadRequestIdRef = useRef(0);
  // Where focus goes once the row that held it re-renders or disappears; the
  // first selector that matches wins.
  const pendingFocusRef = useRef<string[] | null>(null);

  const invalidateLibraryLoads = useCallback(() => {
    loadRequestIdRef.current += 1;
  }, []);

  const loadDemos = useCallback(async () => {
    const requestId = loadRequestIdRef.current + 1;
    loadRequestIdRef.current = requestId;
    try {
      const nextDemos = await listDemos({ includeArchived: filters.includeArchived });
      if (requestId !== loadRequestIdRef.current) {
        return;
      }
      setDemos(nextDemos);
      setNow(Date.now());
      setError(null);
    } catch (err) {
      if (requestId !== loadRequestIdRef.current) {
        return;
      }
      setError(userFacingError(err, "加载比赛失败，请刷新重试。"));
    } finally {
      if (requestId === loadRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [filters.includeArchived]);

  const refreshQuota = useCallback(async () => {
    try {
      setQuota(await getUploadQuota());
    } catch {
      // Advisory only: the upload itself still enforces the limits.
    }
  }, []);

  useEffect(() => {
    void loadDemos();
  }, [loadDemos]);

  useEffect(() => {
    const selectors = pendingFocusRef.current;
    if (!selectors) return;
    pendingFocusRef.current = null;
    for (const selector of selectors) {
      const target = document.querySelector<HTMLElement>(selector);
      if (target) {
        target.focus();
        return;
      }
    }
  });

  const activeJobs = useMemo(() => countActiveLibraryDemos(demos), [demos]);
  const parsingJobs = useMemo(() => countParsingLibraryDemos(demos), [demos]);
  const videoJobs = useMemo(() => countVideoLibraryDemos(demos), [demos]);
  const failedDemos = useMemo(
    () => demos.filter((demo) => demo.status === "failed").length,
    [demos]
  );
  const filterOptions = useMemo(() => demoLibraryFilterOptions(demos), [demos]);
  const visibleDemos = useMemo(
    () => filterAndSortDemos(demos, filters, LIBRARY_LABELS),
    [demos, filters]
  );
  const emptyState = useMemo(
    () => libraryEmptyState({ loading, error, demos, visibleDemos, filters }),
    [demos, error, filters, loading, visibleDemos]
  );
  const quotaSummary = useMemo(() => uploadQuotaSummary(quota), [quota]);
  const maxUploadBytes = quota?.maxUploadBytes ?? MAX_DEMO_UPLOAD_BYTES;
  const uploadBlocked = Boolean(quotaSummary.blockedReason);
  const uploadDisabled = creating || upload !== null || uploadBlocked;

  usePoll(loadDemos, shouldPollLibrary({ loading, creating, activeJobs }) ? 1800 : null);

  // The in-flight count moves with every finished parse and every new upload.
  useEffect(() => {
    void refreshQuota();
  }, [parsingJobs, refreshQuota]);

  // A used-up daily quota lifts on its own: look again once it should have
  // reset (and every few minutes, so the stated wait stays current) and when
  // the player comes back to the tab.
  useEffect(() => {
    if (!uploadBlocked) return;
    const resetSeconds = quota?.dailyResetSeconds;
    const untilReset =
      typeof resetSeconds === "number" && Number.isFinite(resetSeconds)
        ? Math.max(0, resetSeconds) * 1000 + QUOTA_RESET_SLACK_MS
        : QUOTA_RECHECK_MS;
    const timer = window.setTimeout(() => void refreshQuota(), Math.min(untilReset, QUOTA_RECHECK_MS));
    const onVisibilityChange = () => {
      if (!document.hidden) void refreshQuota();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [quota, refreshQuota, uploadBlocked]);

  // The quota's refusal goes with it.
  const wasBlockedRef = useRef(uploadBlocked);
  useEffect(() => {
    if (wasBlockedRef.current && !uploadBlocked) {
      setUploadError((current) => (current?.code === "upload_daily_limit" ? null : current));
    }
    wasBlockedRef.current = uploadBlocked;
  }, [uploadBlocked]);

  // A wait for the in-flight parse ends when that parse does.
  useEffect(() => {
    if (uploadError?.code === "active_parse_limit" && !loading && parsingJobs === 0) {
      setUploadError(null);
      setNotice({ kind: "message", message: "当前比赛已处理完成，可以继续上传。" });
    }
  }, [loading, parsingJobs, uploadError]);

  const noticeDemo =
    notice?.kind === "upload" ? demos.find((demo) => demo.id === notice.demo.id) ?? notice.demo : null;
  const uploadReady = noticeDemo?.status === "completed";

  // A player who switched tabs during a long upload sees it in the tab title.
  useEffect(() => {
    if (!uploadReady || !document.hidden) return;
    const original = document.title;
    document.title = `(可复盘) ${original}`;
    const restore = () => {
      if (!document.hidden) document.title = original;
    };
    document.addEventListener("visibilitychange", restore);
    return () => {
      document.removeEventListener("visibilitychange", restore);
      document.title = original;
    };
  }, [uploadReady]);

  useEffect(() => {
    function closeOutside(event: PointerEvent) {
      const target = event.target instanceof Node ? event.target : null;
      document
        .querySelectorAll<HTMLDetailsElement>(".library-app :is(.library-more-filters, .library-record-menu)[open]")
        .forEach((details) => {
          if (!target || !details.contains(target)) details.open = false;
        });
    }
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, []);

  async function handleMockUpload() {
    setCreating(true);
    try {
      const demo = await createMockUpload();
      setNotice({
        kind: "message",
        message: `示例比赛已创建：${demo.name}。这是模拟数据，可用来体验复盘。`,
        demoId: demo.id
      });
      setError(null);
      await loadDemos();
    } catch (err) {
      setError(userFacingError(err, "创建示例失败，请重试。"));
    } finally {
      setCreating(false);
    }
  }

  async function handleDemoUpload(file: File) {
    if (getDemoUploadSnapshot()) {
      setNotice({ kind: "message", message: "正在上传另一场比赛，完成后再添加下一场。" });
      return;
    }
    setRetryError(null);
    if (quotaSummary.blockedReason) {
      setUploadError({ message: quotaSummary.blockedReason, code: "upload_daily_limit" });
      return;
    }
    const problem = demoFileProblem(file, maxUploadBytes);
    if (problem) {
      setUploadError({ message: problem, code: null });
      return;
    }
    setUploadError(null);
    // The outcome is read back from the upload store below, so it still shows
    // when this page remounted while the upload ran.
    startDemoUpload(file, uploadDemoFile).catch(() => {});
  }

  function showUploadOutcome(outcome: DemoUploadOutcome) {
    if (outcome.kind === "landed") {
      const { demo } = outcome;
      setDemos((current) => (current.some((item) => item.id === demo.id) ? current : [demo, ...current]));
      setNotice({ kind: "upload", demo });
      setError(null);
    } else if (isUploadAbortError(outcome.error)) {
      pendingFocusRef.current = [UPLOAD_BUTTON];
      setNotice({ kind: "message", message: `已取消上传「${outcome.fileName}」。` });
    } else {
      const err = outcome.error;
      const message =
        (isApiError(err)
          ? demoUploadErrorMessage(err.status, err.detailCode, err.retryAfterSeconds, maxUploadBytes)
          : null) ?? userFacingError(err, "上传比赛失败，请检查文件后重试。");
      setUploadError({ message, code: isApiError(err) ? err.detailCode : null });
    }
    void refreshQuota();
  }

  const uploadHandlerRef = useRef(handleDemoUpload);
  uploadHandlerRef.current = handleDemoUpload;
  const uploadOutcomeRef = useRef(showUploadOutcome);
  uploadOutcomeRef.current = showUploadOutcome;

  // Reload and report once an upload settles, even one started before this page
  // last unmounted or one that settled while the player was on another page.
  const wasUploadingRef = useRef(upload !== null);
  useEffect(() => {
    const settled = upload === null ? takeDemoUploadOutcome() : null;
    if (settled) {
      uploadOutcomeRef.current(settled);
    }
    if (settled || (wasUploadingRef.current && upload === null)) {
      void loadDemos();
    }
    wasUploadingRef.current = upload !== null;
  }, [loadDemos, upload]);

  // Dropping a .dem anywhere on the page uploads it instead of letting the
  // browser download or open the file.
  useEffect(() => {
    let hideTimer: number | undefined;
    const carriesFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");
    function onDragOver(event: DragEvent) {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      setDragActive(true);
      window.clearTimeout(hideTimer);
      hideTimer = window.setTimeout(() => setDragActive(false), 250);
    }
    function onDrop(event: DragEvent) {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      window.clearTimeout(hideTimer);
      setDragActive(false);
      const file = event.dataTransfer?.files?.[0];
      if (file) void uploadHandlerRef.current(file);
    }
    window.addEventListener("dragenter", onDragOver);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.clearTimeout(hideTimer);
      window.removeEventListener("dragenter", onDragOver);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  function openUploadPicker() {
    document.getElementById("demo-upload-input")?.click();
  }

  function startRename(demo: DemoSummary) {
    setRenamingDemoId(demo.id);
    setRenameValue(demo.name);
    setRenameError(null);
  }

  function cancelRename(demo: DemoSummary) {
    pendingFocusRef.current = [rowSelector(demo.id, ".library-record-menu > summary")];
    setRenamingDemoId(null);
    setRenameValue("");
    setRenameError(null);
  }

  async function saveRename(event: FormEvent<HTMLFormElement>, demo: DemoSummary) {
    event.preventDefault();
    setBusyDemoId(demo.id);
    setRenameError(null);
    invalidateLibraryLoads();
    try {
      const updated = await updateDemo(demo.id, { name: renameValue });
      invalidateLibraryLoads();
      pendingFocusRef.current = [rowSelector(updated.id, ".library-record-menu > summary")];
      setDemos((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setNotice({ kind: "message", message: `已重命名为「${updated.name}」`, demoId: updated.id });
      setRenamingDemoId(null);
      setRenameValue("");
      setError(null);
    } catch (err) {
      setRenameError(userFacingError(err, "重命名失败，请重试。"));
    } finally {
      setBusyDemoId(null);
    }
  }

  async function handleArchive(demo: DemoSummary) {
    const index = visibleDemos.findIndex((item) => item.id === demo.id);
    const neighbour = visibleDemos[index + 1] ?? visibleDemos[index - 1] ?? null;
    setBusyDemoId(demo.id);
    invalidateLibraryLoads();
    try {
      const updated = demo.archived
        ? await updateDemo(demo.id, { archived: false })
        : await archiveDemo(demo.id);
      invalidateLibraryLoads();
      setDemos((current) => {
        if (current.some((item) => item.id === updated.id)) {
          return updated.archived && !filters.includeArchived
            ? current.filter((item) => item.id !== updated.id)
            : current.map((item) => (item.id === updated.id ? updated : item));
        }
        // An undo brings back a row the hidden-archive view had dropped.
        return updated.archived ? current : [updated, ...current];
      });
      if (updated.archived) {
        pendingFocusRef.current = filters.includeArchived
          ? [rowSelector(updated.id, ".library-record-menu > summary")]
          : [...(neighbour ? [rowSelector(neighbour.id, ".demo-name a")] : []), NOTICE_UNDO];
        setNotice({ kind: "archived", demo: updated });
      } else {
        pendingFocusRef.current = [rowSelector(updated.id, ".demo-name a")];
        setNotice({ kind: "message", message: `已恢复「${updated.name}」`, demoId: updated.id });
      }
      setError(null);
    } catch (err) {
      setError(userFacingError(err, "更新归档状态失败，请重试。"));
    } finally {
      setBusyDemoId(null);
    }
  }

  async function handleRetryParse(demo: DemoSummary) {
    setBusyDemoId(demo.id);
    setRetryError(null);
    invalidateLibraryLoads();
    try {
      const updated = await retryDemoParse(demo.id);
      invalidateLibraryLoads();
      pendingFocusRef.current = [
        rowSelector(updated.id, ".library-play-button"),
        rowSelector(updated.id, ".library-record-menu > summary")
      ];
      setDemos((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setNotice({ kind: "message", message: `正在重新处理「${updated.name}」`, demoId: updated.id });
      setError(null);
    } catch (err) {
      setRetryError(userFacingError(err, "重新处理失败，请重试。"));
    } finally {
      setBusyDemoId(null);
    }
  }

  const uploadBusyLabel = upload
    ? upload.phase === "verifying"
      ? "校验中…"
      : `上传中 ${uploadPercent(upload)}%`
    : creating
      ? "正在添加…"
      : null;
  const showEmptyState = emptyState !== null && !(upload && emptyState.kind === "empty");
  const firstRun = showEmptyState && emptyState?.kind === "empty";

  return (
    <main className="app-shell library-app">
      <header className="topbar">
        <AppBrand />
        <div className="topbar-actions">
          <SessionControls />
        </div>
      </header>

      <section className="page">
        <div className="page-header library-page-header archive-command-bar">
          <div className="library-header-copy">
            <h1 className="page-title">我的比赛</h1>
            <p className="page-subtitle">
              选择比赛，找到值得复盘的一刻。
            </p>
            {firstRun ? null : <DemFileHelp quotaNote={quotaSummary.helpNote} />}
          </div>
          <DemoUploader
            disabled={uploadDisabled}
            busyLabel={uploadBusyLabel}
            hint={quotaSummary.hint}
            disabledReason={quotaSummary.blockedReason}
            onMockUpload={devTools ? handleMockUpload : undefined}
            onDemoUpload={(file) => void handleDemoUpload(file)}
          />
        </div>

        {quotaSummary.blockedReason && !uploadError ? (
          <p className="library-quota-note">{quotaSummary.blockedReason}</p>
        ) : null}
        {uploadError ? (
          <ErrorBanner message={uploadError.message} onDismiss={() => setUploadError(null)} />
        ) : null}
        {retryError ? <ErrorBanner message={retryError} onDismiss={() => setRetryError(null)} /> : null}
        {/* With no rows, the ledger's own error row says it once. */}
        {error && demos.length > 0 ? (
          <ErrorBanner message={error} onRetry={() => void loadDemos()} onDismiss={() => setError(null)} />
        ) : null}
        <div className="library-notice-region" aria-live="polite">
          {notice ? (
            <LibraryNoticeView
              notice={notice}
              liveDemo={noticeDemo}
              busy={busyDemoId !== null}
              onUndoArchive={(demo) => void handleArchive(demo)}
              onDismiss={() => setNotice(null)}
            />
          ) : null}
        </div>

        <section className="library-toolbar library-controls" aria-label="搜索与筛选比赛">
          <label className="library-search">
            <Search size={16} />
            <input
              type="search"
              value={filters.search}
              onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
              placeholder="搜索比赛、文件、地图或状态"
              aria-label="搜索比赛"
            />
          </label>

          <label className="library-filter">
            <span>地图</span>
            <select
              value={filters.map}
              onChange={(event) => setFilters((current) => ({ ...current, map: event.target.value }))}
              aria-label="筛选地图"
            >
              <option value="all">全部地图</option>
              {filterOptions.maps.map((map) => (
                <option key={map} value={map}>
                  {mapDisplayName(map)}
                </option>
              ))}
            </select>
          </label>

          <details className="library-more-filters" onKeyDown={closeDetailsOnEscape}>
            <summary className="secondary-button compact-button">
              <ArrowDownUp size={14} />
              筛选与排序
              {filters.status !== "all" || filters.includeArchived || filters.sort !== "recent" || filters.order !== "desc"
                ? <span className="library-filter-active">已应用</span>
                : null}
              <ChevronDown size={14} />
            </summary>
            <div className="library-filter-options">
              <label className="library-filter">
                <span>状态</span>
                <select
                  value={filters.status}
                  onChange={(event) => setFilters((current) => ({
                    ...current,
                    status: event.target.value as DemoLibraryFilters["status"]
                  }))}
                  aria-label="筛选状态"
                >
                  <option value="all">全部状态</option>
                  {filterOptions.statuses.map((status) => (
                    <option key={status} value={status}>{demoStatusDisplayLabel(status)}</option>
                  ))}
                </select>
              </label>
              <label className="library-filter">
                <span>排序</span>
                <select
                  value={filters.sort}
                  onChange={(event) =>
                    setFilters((current) => ({
                      ...current,
                      sort: event.target.value as DemoLibraryFilters["sort"],
                      order: event.target.value === "recent" ? "desc" : current.order
                    }))
                  }
                  aria-label="比赛排序"
                >
                  <option value="recent">上传时间</option>
                  <option value="name">比赛名称</option>
                  <option value="map">地图</option>
                  <option value="status">处理状态</option>
                </select>
              </label>
              <button
                className="secondary-button compact-button"
                type="button"
                onClick={() =>
                  setFilters((current) => ({
                    ...current,
                    order: current.order === "asc" ? "desc" : "asc"
                  }))
                }
              >
                <ArrowDownUp size={14} />
                {filters.order === "asc" ? "升序" : "降序"}
              </button>
              <label className="include-archived-toggle">
                <input
                  type="checkbox"
                  checked={filters.includeArchived}
                  onChange={(event) =>
                    setFilters((current) => ({ ...current, includeArchived: event.target.checked }))
                  }
                />
                显示已归档
              </label>
              <button
                className="secondary-button compact-button"
                type="button"
                onClick={() => setFilters(DEFAULT_FILTERS)}
              >
                重置筛选
              </button>
            </div>
          </details>
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={() => void loadDemos()}
            aria-label="刷新比赛列表"
          >
            <RefreshCcw size={14} />
            刷新
          </button>
        </section>

        <div className="library-count-summary" role="status">
          <span>{loading ? "正在加载比赛…" : `${visibleDemos.length} 场比赛`}</span>
          {parsingJobs > 0 ? <span>{parsingJobs} 场正在处理，完成后自动更新</span> : null}
          {videoJobs > 0 ? <span>{videoJobs} 场正在生成视频</span> : null}
          {failedDemos > 0 ? <span className="library-attention-count">{failedDemos} 场处理失败</span> : null}
        </div>

        <section
          className={`archive-ledger${dragActive ? " drop-active" : ""}`}
          aria-label="比赛列表"
          aria-busy={emptyState?.kind === "loading"}
        >
          <div className="archive-ledger-head" aria-hidden="true">
            <span className="ledger-head-thumb" />
            <span className="ledger-head-identity">比赛</span>
            <span className="ledger-head-map">地图</span>
            <span className="ledger-head-rounds">回合</span>
            <span className="ledger-head-signals">全场复盘线索</span>
            <span className="ledger-head-state">状态</span>
            <span className="ledger-head-actions">操作</span>
          </div>
          {dragActive ? (
            <div className="library-drop-overlay" aria-hidden="true">
              <FileUp size={20} />
              {upload ? "正在上传另一场比赛，请稍后再拖入" : "松开即可上传 .dem"}
            </div>
          ) : null}
          {upload ? <UploadProgressRow upload={upload} onCancel={() => cancelDemoUpload()} /> : null}
          {emptyState?.kind === "loading" ? (
            <LibrarySkeletonRows />
          ) : showEmptyState && emptyState ? (
            <LibraryEmptyStateRow
              state={emptyState}
              error={error}
              uploadDisabled={uploadDisabled}
              quotaNote={quotaSummary.helpNote}
              onClearFilters={() => setFilters(DEFAULT_FILTERS)}
              onMockUpload={devTools ? () => void handleMockUpload() : undefined}
              onUpload={openUploadPicker}
              onRefresh={() => void loadDemos()}
              onShowArchived={() =>
                setFilters((current) => ({ ...current, includeArchived: true }))
              }
            />
          ) : (
            visibleDemos.map((demo) => {
              const failure = demoFailureState(demo);
              const display = libraryDisplayTitle(demo, mapDisplayName);
              const mapName = mapDisplayName(demo.map_name);
              const completed = demo.status === "completed";
              const videoLabel = libraryVideoLabel(demo);
              return (
              <article
                key={demo.id}
                data-demo-id={demo.id}
                className={`archive-record ${demo.archived ? "archived-row" : ""} ${demo.status}`}
              >
                <MapThumb mapName={demo.map_name} />
                <div className="archive-record-identity">
                  {renamingDemoId === demo.id ? (
                    <>
                      <form className="rename-form" onSubmit={(event) => saveRename(event, demo)}>
                        <input
                          value={renameValue}
                          onChange={(event) => setRenameValue(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") {
                              event.preventDefault();
                              cancelRename(demo);
                            }
                          }}
                          aria-label={`重命名 ${demo.name}`}
                          aria-invalid={renameError ? true : undefined}
                          aria-describedby={renameError ? `rename-error-${demo.id}` : undefined}
                          maxLength={255}
                          required
                          autoFocus
                        />
                        <button
                          className="icon-button"
                          type="submit"
                          disabled={busyDemoId === demo.id || !renameValue.trim()}
                          aria-label={`保存 ${demo.name} 的名称`}
                        >
                          <Check size={15} />
                        </button>
                        <button
                          className="icon-button"
                          type="button"
                          aria-label={`取消重命名 ${demo.name}`}
                          onClick={() => cancelRename(demo)}
                        >
                          <X size={15} />
                        </button>
                      </form>
                      {renameError ? (
                        <p className="rename-error" id={`rename-error-${demo.id}`} role="alert">{renameError}</p>
                      ) : null}
                    </>
                  ) : (
                    <div className="demo-name">
                      <span>
                        <Link
                          href={`/demos/${demo.id}`}
                          title={display.filename ? `${display.title}（${display.filename}）` : display.title}
                        >
                          {display.title}
                        </Link>
                      </span>
                      {display.filename ? <span>{display.filename}</span> : null}
                      {demo.archived ? <span className="archived-label">已归档</span> : null}
                    </div>
                  )}
                  <div className="archive-record-date">
                    {/* Narrow screens drop the map and round columns; the facts move here. */}
                    {display.composed ? null : (
                      <span className="archive-record-map-inline">
                        <span>{mapName}</span>
                        {completed ? <span>{demo.round_count} 回合</span> : null}
                      </span>
                    )}
                    <span title={`最近更新：${formatLibraryDate(demo.updated_at)}`}>{formatLibraryDate(demo.created_at)} 上传</span>
                  </div>
                </div>
                <div className="archive-record-map">
                  <span className="map-anchor">{mapName}</span>
                </div>
                <div className="archive-record-rounds">
                  <span className="library-round-count">
                    <strong>{completed ? demo.round_count : "—"}</strong>
                    {completed ? " 回合" : null}
                  </span>
                </div>
                <div className="archive-record-signals">
                  {completed ? (
                    <span className="library-clue-count" title="所有玩家合计；进入比赛后只显示你的玩家的建议">
                      全场 <strong>{demo.coaching_event_count}</strong> 条复盘线索
                    </span>
                  ) : null}
                </div>
                <div className="archive-record-state">
                  <div className="library-readiness">
                    <StatusBadge status={demo.status} />
                    <IngestionMeta demo={demo} now={now} />
                    {failure ? <p className="library-failure-reason">{failure.failure.message}</p> : null}
                    {canRetryParse(demo) && failure?.next.action !== "reupload" ? (
                      <button
                        className="secondary-button compact-button library-retry-button"
                        type="button"
                        onClick={() => void handleRetryParse(demo)}
                        disabled={busyDemoId === demo.id}
                      >
                        <RefreshCcw size={14} />
                        重新处理
                      </button>
                    ) : null}
                  </div>
                  {videoLabel ? (
                    <span className={`mini-pill library-render-pill readiness-${playbackReadiness(demo)}`}>
                      {videoLabel}
                    </span>
                  ) : null}
                </div>
                <div className="archive-record-actions">
                  <div className="library-actions">
                    {failure?.next.action === "reupload" ? (
                      <button
                        className="secondary-button compact-button"
                        type="button"
                        disabled={uploadDisabled}
                        onClick={openUploadPicker}
                        aria-label={`重新上传：${display.title}`}
                      >
                        <FileUp size={14} />
                        重新上传
                      </button>
                    ) : failure ? null : <PlayEntry demo={demo} title={display.title} />}
                    <details className="library-record-menu" onKeyDown={closeDetailsOnEscape}>
                      <summary className="icon-button" aria-label={`${display.title} 的更多操作`}>
                        <MoreHorizontal size={18} />
                      </summary>
                      <div className="library-record-menu-items">
                        {canRetryParse(demo) && failure?.next.action === "reupload" ? (
                          <button
                            className="secondary-button compact-button"
                            type="button"
                            onClick={(event) => {
                              closeRecordMenu(event.currentTarget);
                              void handleRetryParse(demo);
                            }}
                            disabled={busyDemoId === demo.id}
                          >
                            <RefreshCcw size={14} />
                            重新处理
                          </button>
                        ) : null}
                        <button
                          className="secondary-button compact-button"
                          type="button"
                          onClick={(event) => {
                            closeRecordMenu(event.currentTarget);
                            startRename(demo);
                          }}
                          disabled={busyDemoId === demo.id}
                        >
                          <Pencil size={14} />
                          重命名
                        </button>
                        <button
                          className="secondary-button compact-button"
                          type="button"
                          onClick={(event) => {
                            closeRecordMenu(event.currentTarget);
                            void handleArchive(demo);
                          }}
                          disabled={busyDemoId === demo.id}
                        >
                          <Archive size={14} />
                          {demo.archived ? "恢复到比赛库" : "归档比赛"}
                        </button>
                      </div>
                    </details>
                  </div>
                </div>
              </article>
              );
            })
          )}
        </section>
        <details
          className="library-import-options"
          onToggle={(event) => {
            if (event.currentTarget.open) setImportOptionsLoaded(true);
          }}
        >
          <summary>
            <span>Steam 比赛记录</span>
            <span>当前版本暂不支持自动导入，请手动上传 .dem</span>
            <ChevronDown size={16} />
          </summary>
          {importOptionsLoaded ? <RecentSteamMatches /> : null}
        </details>
      </section>
    </main>
  );
}

function LibraryNoticeView({
  notice,
  liveDemo,
  busy,
  onUndoArchive,
  onDismiss
}: {
  notice: LibraryNotice;
  liveDemo: DemoSummary | null;
  busy: boolean;
  onUndoArchive: (demo: DemoSummary) => void;
  onDismiss: () => void;
}) {
  let message: string;
  let action: ReactNode = null;
  let tone = "";
  if (notice.kind === "upload" && liveDemo) {
    const failure = demoFailureState(liveDemo);
    if (liveDemo.status === "completed" && !failure) {
      message = `「${liveDemo.name}」可以复盘了。`;
      action = (
        <Link className="primary-button compact-button" href={`/demos/${liveDemo.id}#player`}>
          <Play size={14} />
          进入复盘
        </Link>
      );
    } else if (failure) {
      tone = " failed";
      message = `「${liveDemo.name}」处理失败：${failure.failure.message}`;
      action = (
        <Link className="secondary-button compact-button" href={`/demos/${liveDemo.id}`}>
          <ExternalLink size={14} />
          查看原因
        </Link>
      );
    } else {
      message = `「${liveDemo.name}」已上传，${processingNoticeLabel(liveDemo.status)}，完成后会在这里提示。`;
      action = (
        <Link className="secondary-button compact-button" href={`/demos/${liveDemo.id}`}>
          <ExternalLink size={14} />
          查看进度
        </Link>
      );
    }
  } else if (notice.kind === "archived") {
    message = `已归档「${notice.demo.name}」，可在筛选中显示并恢复。`;
    action = (
      <button
        id="library-notice-undo"
        className="secondary-button compact-button"
        type="button"
        disabled={busy}
        onClick={() => onUndoArchive(notice.demo)}
      >
        <Undo2 size={14} />
        撤销
      </button>
    );
  } else if (notice.kind === "message") {
    message = notice.message;
    action = notice.demoId ? (
      <Link className="secondary-button compact-button" href={`/demos/${notice.demoId}`}>
        <ExternalLink size={14} />
        查看比赛
      </Link>
    ) : null;
  } else {
    return null;
  }

  return (
    <div className={`library-notice${tone}`}>
      <span>{message}</span>
      <div className="library-notice-actions">
        {action}
        <button className="icon-button" type="button" aria-label="关闭提示" onClick={onDismiss}>
          <X size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function UploadProgressRow({ upload, onCancel }: { upload: DemoUploadSnapshot; onCancel: () => void }) {
  const verifying = upload.phase === "verifying";
  const percent = uploadPercent(upload);
  const now = Date.now();
  const label = uploadProgressLabel(upload, now);
  const eta = verifying ? null : uploadEtaLabel(upload, now);
  return (
    <article className="archive-record uploading" aria-label={`正在上传：${upload.fileName}`}>
      <span className="library-map-thumb upload-thumb" aria-hidden="true">
        <FileUp size={18} />
      </span>
      <div className="archive-record-identity">
        <div className="demo-name">
          <span title={upload.fileName}>{upload.fileName}</span>
        </div>
        {/* Percent, size and time left as separate readings, not one joined string. */}
        <div className="archive-record-date upload-progress-facts">
          {verifying ? (
            <span>{label}</span>
          ) : (
            <>
              <span className="upload-progress-percent">上传中 {percent}%</span>
              <span>{formatMegabytes(upload.loaded)} / {formatMegabytes(upload.total)} MB</span>
              {eta ? <span>剩余{eta}</span> : null}
            </>
          )}
        </div>
        <div
          className={`upload-progress-track${verifying ? " verifying" : ""}`}
          role="progressbar"
          aria-label="上传进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={verifying ? undefined : percent}
          aria-valuetext={label}
        >
          <span style={{ width: `${verifying ? 100 : percent}%` }} />
        </div>
      </div>
      <div className="archive-record-map" />
      <div className="archive-record-rounds" />
      <div className="archive-record-signals" />
      <div className="archive-record-state">
        <span className="status-badge queued">
          <Loader2 size={14} className="spin-icon" />
          {verifying ? "校验中" : "上传中"}
        </span>
      </div>
      <div className="archive-record-actions">
        <div className="library-actions">
          {verifying ? null : (
            <button className="secondary-button compact-button" type="button" onClick={onCancel}>
              <X size={14} />
              取消上传
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

function LibrarySkeletonRows() {
  return (
    <div className="library-skeleton" aria-hidden="true">
      {[0, 1, 2].map((row) => (
        <div key={row} className="archive-record library-skeleton-row">
          <span className="library-map-thumb" />
          <div className="archive-record-identity">
            <span className="skeleton-bar wide" />
            <span className="skeleton-bar" />
          </div>
          <div className="archive-record-map"><span className="skeleton-bar" /></div>
          <div className="archive-record-rounds"><span className="skeleton-bar" /></div>
          <div className="archive-record-signals"><span className="skeleton-bar" /></div>
          <div className="archive-record-state"><span className="skeleton-bar" /></div>
          <div className="archive-record-actions"><span className="skeleton-bar" /></div>
        </div>
      ))}
    </div>
  );
}

// The match's radar overview, so a row is recognisable before its name is read.
// Unknown maps get a neutral tile rather than another map's radar.
function MapThumb({ mapName }: { mapName: string | null | undefined }) {
  const radar = getTacticalMapConfig(mapName)?.radarImagePath ?? null;
  return radar ? (
    <span
      className="library-map-thumb"
      aria-hidden="true"
      style={{ backgroundImage: `url("${radar}")` }}
    />
  ) : (
    <span className="library-map-thumb unknown" aria-hidden="true">
      <MapIcon size={16} />
    </span>
  );
}

function PlayEntry({ demo, title }: { demo: DemoSummary; title: string }) {
  const readiness = playbackReadiness(demo);
  const href = `/demos/${demo.id}#player`;
  if (readiness !== "unavailable") {
    return (
      <Link
        className="secondary-button compact-button library-play-button"
        href={href}
        aria-label={`进入复盘：${title}`}
      >
        <Play size={14} />
        进入复盘
      </Link>
    );
  }

  return (
    <Link
      className="secondary-button compact-button library-play-button"
      href={`/demos/${demo.id}`}
      aria-label={`查看处理状态：${title}`}
    >
      <Clock3 size={14} />
      查看状态
    </Link>
  );
}

function LibraryEmptyStateRow({
  state,
  error,
  uploadDisabled,
  quotaNote,
  onClearFilters,
  onMockUpload,
  onUpload,
  onRefresh,
  onShowArchived
}: {
  state: LibraryEmptyState;
  error: string | null;
  uploadDisabled: boolean;
  quotaNote: string | null;
  onClearFilters: () => void;
  onMockUpload?: () => void;
  onUpload: () => void;
  onRefresh: () => void;
  onShowArchived: () => void;
}) {
  const copy = EMPTY_STATE_COPY[state.kind];
  const showMockAction = state.showMockAction && onMockUpload !== undefined;
  const isError = state.kind === "error";
  return (
    <div className={`library-empty-state ${state.kind}`} role={isError ? "alert" : undefined}>
      <div>
        <strong>{copy.title}</strong>
        {isError && error ? <p>{error}</p> : null}
        <p>{state.kind === "empty" && showMockAction ? `${copy.message}${MOCK_DEMO_HINT}` : copy.message}</p>
      </div>
      {state.kind === "empty" ? <DemFileHelp open quotaNote={quotaNote} /> : null}
      <div className="library-empty-actions">
        {state.showUploadAction ? (
          <button
            className="primary-button compact-button"
            type="button"
            disabled={uploadDisabled}
            onClick={onUpload}
          >
            <FileUp size={14} />
            上传比赛 .dem
          </button>
        ) : null}
        {showMockAction ? (
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onMockUpload}
            disabled={uploadDisabled}
          >
            <UploadCloud size={14} />
            示例比赛（模拟数据）
          </button>
        ) : null}
        {state.showClearFiltersAction ? (
          <button className="secondary-button compact-button" type="button" onClick={onClearFilters}>
            <X size={14} />
            清除筛选
          </button>
        ) : null}
        {state.showArchivedAction ? (
          <button className="secondary-button compact-button" type="button" onClick={onShowArchived}>
            <Archive size={14} />
            显示已归档
          </button>
        ) : null}
        {state.showRefreshAction ? (
          <button className="secondary-button compact-button" type="button" onClick={onRefresh}>
            <RefreshCcw size={14} />
            刷新比赛列表
          </button>
        ) : null}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: DemoProcessingStatus }) {
  const Icon =
    status === "completed" ? CircleCheck : status === "failed" ? CircleAlert : Loader2;
  const active = status === "queued" || status === "parsing" || status === "analyzing";
  return (
    <span className={`status-badge ${status}`}>
      <Icon size={14} className={active ? "spin-icon" : ""} />
      {demoStatusDisplayLabel(status)}
    </span>
  );
}

function IngestionMeta({ demo, now }: { demo: DemoSummary; now: number }) {
  const ingestion = demo.ingestion;
  const labels: string[] = [];
  if (isDemoParseActive(demo)) {
    const elapsed = processingElapsedLabel(ingestion?.startedAt ?? demo.created_at, now);
    if (elapsed) labels.push(elapsed);
  }
  if (ingestion?.stale) {
    labels.push("处理时间较长");
  }
  if (ingestion && ingestion.attemptCount > 1) {
    labels.push(`第 ${ingestion.attemptCount} 次处理`);
  }
  return labels.length > 0 ? (
    <p className="library-ingestion-meta">
      {labels.map((label) => <span key={label}>{label}</span>)}
    </p>
  ) : null;
}

const EMPTY_STATE_COPY: Record<LibraryEmptyState["kind"], { title: string; message: string }> = {
  loading: { title: "正在加载比赛", message: "比赛准备好后，会显示在这里。" },
  error: { title: "暂时无法加载比赛", message: "已上传的比赛会保留，恢复连接后刷新即可。" },
  empty: { title: "开始你的第一场复盘", message: "上传 .dem 比赛文件，即可查看战术回放和复盘建议。" },
  archived: { title: "比赛已归档", message: "显示已归档比赛，即可继续复盘或恢复到比赛库。" },
  search: { title: "没有找到这场比赛", message: "试试其他比赛名称或地图，也可以清除筛选查看全部比赛。" },
  filtered: { title: "没有符合条件的比赛", message: "调整地图、状态或归档筛选，查看其他比赛。" }
};

const MOCK_DEMO_HINT = "也可以先用模拟比赛体验。";

function closeDetailsOnEscape(event: ReactKeyboardEvent<HTMLDetailsElement>) {
  if (event.key === "Escape" && event.currentTarget.open) {
    event.currentTarget.open = false;
    event.currentTarget.querySelector("summary")?.focus();
  }
}

function closeRecordMenu(button: HTMLButtonElement) {
  const details = button.closest("details");
  if (details) {
    details.open = false;
    details.querySelector("summary")?.focus();
  }
}
