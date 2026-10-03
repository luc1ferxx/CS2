"use client";

import Link from "next/link";
import { MoreHorizontal, X } from "lucide-react";
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
import { ConfirmDialog } from "@/components/feedback/ConfirmDialog";
import { ErrorBanner } from "@/components/feedback/ErrorBanner";
import { useAuth } from "@/components/auth/AuthProvider";
import { SessionControls } from "@/components/auth/SessionControls";
import { AppBrand } from "@/components/layout/AppBrand";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { RecentSteamMatches } from "@/components/steam/RecentSteamMatches";
import {
  archiveDemo,
  createMockUpload,
  deleteDemo,
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
import { takeLibraryNotice } from "@/lib/library-notice";
import { getTacticalMapConfig, mapDisplayName } from "@/lib/map-config";
import {
  MAX_DEMO_UPLOAD_BYTES,
  demoFileProblem,
  demoUploadErrorMessage,
  uploadQuotaSummary
} from "@/lib/upload-limits";
import { usePoll } from "@/lib/use-poll";
import { requestFailureKind, userFacingError } from "@/lib/user-errors";
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
const LIBRARY_HIDDEN_POLL_MS = 15_000;
const TAB_FLAG = /^\((?:可复盘|处理失败)\) /;
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
  // The match waiting in the delete dialog, and a failed delete (outlives polling like the two above).
  const [deleteTarget, setDeleteTarget] = useState<DemoSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
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

  // A line left by the page that sent the player here (a match deleted from its own page).
  useEffect(() => {
    const message = takeLibraryNotice();
    if (message) setNotice({ kind: "message", message });
  }, []);

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

  // Slower, not paused, in a hidden tab: the title flag below needs to see a parse finish there.
  // Browsers may stretch or suspend hidden-tab timers, so the flag is best-effort; the refresh
  // on return is what always shows the outcome.
  usePoll(loadDemos, shouldPollLibrary({ loading, creating, activeJobs }) ? 1800 : null, {
    hiddenDelayMs: LIBRARY_HIDDEN_POLL_MS
  });

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
  // Read the way the notice reads it: any failure is a failure, else done is ready.
  const uploadTabFlag = !noticeDemo
    ? null
    : demoFailureState(noticeDemo)
      ? "(处理失败)"
      : noticeDemo.status === "completed"
        ? "(可复盘)"
        : null;

  // A player who switched tabs during a long upload sees how it ended in the tab
  // title until they come back. An ending they watched happen needs no flag.
  // The flag lasts until the player is back (or the page goes), not as long as
  // the notice: a later notice while they are away must not take it down.
  const tabFlagRef = useRef<{ flagged: string; original: string } | null>(null);
  useEffect(() => {
    const restore = () => {
      const flag = tabFlagRef.current;
      tabFlagRef.current = null;
      // A title the page set in the meantime is newer than ours: leave it.
      if (flag && document.title === flag.flagged) document.title = flag.original;
    };
    const onVisibilityChange = () => {
      if (!document.hidden) restore();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      restore();
    };
  }, []);
  useEffect(() => {
    if (!uploadTabFlag || !document.hidden) return;
    const original = document.title.replace(TAB_FLAG, "");
    const flagged = `${uploadTabFlag} ${original}`;
    document.title = flagged;
    tabFlagRef.current = { flagged, original };
  }, [uploadTabFlag]);

  useEffect(() => {
    function closeOutside(event: PointerEvent) {
      const target = event.target instanceof Node ? event.target : null;
      document
        .querySelectorAll<HTMLDetailsElement>(".library-app :is(.lib-filters, .lib-menu, .lib-help-pop)[open]")
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
    pendingFocusRef.current = [rowSelector(demo.id, ".lib-menu > summary")];
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
      pendingFocusRef.current = [rowSelector(updated.id, ".lib-menu > summary")];
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
          ? [rowSelector(updated.id, ".lib-menu > summary")]
          : [...(neighbour ? [rowSelector(neighbour.id, ".lib-name a")] : []), NOTICE_UNDO];
        setNotice({ kind: "archived", demo: updated });
      } else {
        pendingFocusRef.current = [rowSelector(updated.id, ".lib-name a")];
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
        rowSelector(updated.id, ".lib-enter"),
        rowSelector(updated.id, ".lib-menu > summary")
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

  async function handleDelete(demo: DemoSummary) {
    const title = libraryDisplayTitle(demo, mapDisplayName).title;
    const index = visibleDemos.findIndex((item) => item.id === demo.id);
    const neighbour = visibleDemos[index + 1] ?? visibleDemos[index - 1] ?? null;
    setDeleting(true);
    setBusyDemoId(demo.id);
    setDeleteError(null);
    // A list request already in flight must not bring the row back.
    invalidateLibraryLoads();
    try {
      await deleteDemo(demo.id);
    } catch (err) {
      // Already gone (another tab): that is the outcome the player asked for.
      if (requestFailureKind(err) !== "not_found") {
        invalidateLibraryLoads();
        pendingFocusRef.current = [rowSelector(demo.id, ".lib-menu > summary")];
        setDeleteError(userFacingError(err, `删除「${title}」失败，请重试。`));
        setDeleteTarget(null);
        setDeleting(false);
        setBusyDemoId(null);
        return;
      }
    }
    invalidateLibraryLoads();
    setDemos((current) => current.filter((item) => item.id !== demo.id));
    if (renamingDemoId === demo.id) {
      setRenamingDemoId(null);
      setRenameValue("");
      setRenameError(null);
    }
    // No link: the match no longer exists. This also replaces any notice that pointed at it.
    setNotice({ kind: "message", message: `已永久删除「${title}」` });
    pendingFocusRef.current = [...(neighbour ? [rowSelector(neighbour.id, ".lib-name a")] : []), UPLOAD_BUTTON];
    setDeleteTarget(null);
    setDeleting(false);
    setBusyDemoId(null);
    void refreshQuota();
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

      <div className="page lib-page">
        {uploadError ? (
          <ErrorBanner message={uploadError.message} onDismiss={() => setUploadError(null)} />
        ) : null}
        {retryError ? <ErrorBanner message={retryError} onDismiss={() => setRetryError(null)} /> : null}
        {deleteError ? <ErrorBanner message={deleteError} onDismiss={() => setDeleteError(null)} /> : null}
        {/* With no rows, the ledger's own error row says it once. */}
        {error && demos.length > 0 ? (
          <ErrorBanner message={error} onRetry={() => void loadDemos()} onDismiss={() => setError(null)} />
        ) : null}
        <div className="lib-notices" aria-live="polite">
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

        <section className="panel lib-panel" aria-labelledby="library-title">
          <header className="panel-bar lib-head">
            <h1 id="library-title" className="panel-bar-title">我的比赛</h1>
            <p className="panel-bar-meta lib-count" role="status">
              <span>{loading ? "（加载中…）" : `（${visibleDemos.length} 场）`}</span>
              {parsingJobs > 0 ? <span>{parsingJobs} 场正在处理，完成后自动更新</span> : null}
              {videoJobs > 0 ? <span>{videoJobs} 场正在生成视频</span> : null}
              {failedDemos > 0 ? <span className="lib-count-failed">{failedDemos} 场处理失败</span> : null}
            </p>
            <DemoUploader
              disabled={uploadDisabled}
              busyLabel={uploadBusyLabel}
              hint={quotaSummary.hint}
              disabledReason={quotaSummary.blockedReason}
              onMockUpload={devTools ? handleMockUpload : undefined}
              onDemoUpload={(file) => void handleDemoUpload(file)}
            />
          </header>

          {quotaSummary.blockedReason && !uploadError ? (
            <p className="lib-quota">{quotaSummary.blockedReason}</p>
          ) : null}

          <div className="lib-toolbar" role="group" aria-label="搜索与筛选比赛">
            <input
              className="lib-search"
              type="search"
              value={filters.search}
              onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
              placeholder="搜索比赛、文件、地图或状态"
              aria-label="搜索比赛"
            />
            <select
              className="lib-select"
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

            <details className="lib-filters" onKeyDown={closeDetailsOnEscape}>
              <summary className="secondary-button compact-button">
                筛选与排序
                {filters.status !== "all" || filters.includeArchived || filters.sort !== "recent" || filters.order !== "desc"
                  ? <span className="lib-filters-on">（已应用）</span>
                  : null}
              </summary>
              <div className="popover lib-filter-pop">
                <label className="lib-field">
                  <span>状态</span>
                  <select
                    className="lib-select"
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
                <label className="lib-field">
                  <span>排序</span>
                  <span className="lib-field-row">
                    <select
                      className="lib-select"
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
                      {filters.order === "asc" ? "升序" : "降序"}
                    </button>
                  </span>
                </label>
                <label className="lib-check">
                  <input
                    type="checkbox"
                    checked={filters.includeArchived}
                    onChange={(event) =>
                      setFilters((current) => ({ ...current, includeArchived: event.target.checked }))
                    }
                  />
                  显示已归档
                </label>
                <button className="text-button" type="button" onClick={() => setFilters(DEFAULT_FILTERS)}>
                  重置筛选
                </button>
              </div>
            </details>
            <span className="lib-toolbar-end">
              {firstRun ? null : <DemFileHelp popover quotaNote={quotaSummary.helpNote} />}
              <button
                className="secondary-button compact-button"
                type="button"
                onClick={() => void loadDemos()}
                aria-label="刷新比赛列表"
              >
                刷新
              </button>
            </span>
          </div>

          <section
            className={`lib-table${dragActive ? " drop-active" : ""}`}
            aria-label="比赛列表"
            aria-busy={emptyState?.kind === "loading"}
          >
            {showEmptyState && emptyState?.kind !== "loading" && !upload ? null : (
              <div className="lib-row lib-row-head" aria-hidden="true">
                <span className="c-thumb" />
                <span className="c-name">比赛</span>
                <span className="c-map">地图</span>
                <span className="c-score">比分</span>
                <span className="c-num c-rounds">回合</span>
                {/* The rows are one link each, so the column's explanation lives on its head. */}
                <span className="c-num c-signals" title="所有玩家合计；进入比赛后只显示你的玩家的建议">全场建议</span>
                <span className="c-date">上传时间</span>
                <span className="c-state">状态</span>
                <span className="c-actions" />
              </div>
            )}
            {dragActive ? (
              <div className="lib-drop" aria-hidden="true">
                {upload ? "正在上传另一场比赛，请稍后再拖入" : "松开即可上传 .dem"}
              </div>
            ) : null}
            <div className="data-rows lib-rows">
              {upload ? <UploadProgressRow upload={upload} onCancel={() => cancelDemoUpload()} /> : null}
              {emptyState?.kind === "loading" ? (
                <LibrarySkeletonRows />
              ) : showEmptyState ? null : (
                visibleDemos.map((demo) => (
                  <LibraryRow
                    key={demo.id}
                    demo={demo}
                    now={now}
                    renaming={renamingDemoId === demo.id}
                    renameValue={renameValue}
                    renameError={renameError}
                    busy={busyDemoId === demo.id}
                    uploadDisabled={uploadDisabled}
                    onRenameValue={setRenameValue}
                    onSaveRename={(event) => saveRename(event, demo)}
                    onCancelRename={() => cancelRename(demo)}
                    onStartRename={() => startRename(demo)}
                    onArchive={() => void handleArchive(demo)}
                    onDelete={() => {
                      setDeleteError(null);
                      setDeleteTarget(demo);
                    }}
                    onRetryParse={() => void handleRetryParse(demo)}
                    onReupload={openUploadPicker}
                  />
                ))
              )}
            </div>
            {emptyState?.kind !== "loading" && showEmptyState && emptyState ? (
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
            ) : null}
          </section>
        </section>

        <details
          className="panel lib-steam"
          onToggle={(event) => {
            if (event.currentTarget.open) setImportOptionsLoaded(true);
          }}
        >
          <summary className="panel-bar lib-head">
            <span className="panel-bar-title">Steam 比赛记录</span>
            <span className="lib-steam-toggle" aria-hidden="true" />
          </summary>
          {importOptionsLoaded ? <RecentSteamMatches /> : null}
        </details>
      </div>
      <SiteFooter />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="永久删除这场比赛？"
        confirmLabel="永久删除"
        busy={deleting}
        busyLabel="正在删除…"
        onConfirm={() => {
          if (deleteTarget) void handleDelete(deleteTarget);
        }}
        onCancel={() => setDeleteTarget(null)}
      >
        {deleteTarget ? (
          <>
            <p>「{libraryDisplayTitle(deleteTarget, mapDisplayName).title}」的这些内容会被永久删除：</p>
            <ul>
              <li>比赛文件 .dem</li>
              <li>回放数据</li>
              <li>复盘建议和你的评价</li>
            </ul>
            <p className="confirm-dialog-note">此操作无法撤销。</p>
            {typeof quota?.dailyLimit === "number" ? (
              <p className="confirm-dialog-note">删除不会恢复今天的上传次数。</p>
            ) : null}
          </>
        ) : null}
      </ConfirmDialog>
    </main>
  );
}

function statusTone(status: DemoProcessingStatus): "ok" | "failed" | "progress" {
  if (status === "completed") return "ok";
  if (status === "failed") return "failed";
  return "progress";
}

function LibraryRow({
  demo,
  now,
  renaming,
  renameValue,
  renameError,
  busy,
  uploadDisabled,
  onRenameValue,
  onSaveRename,
  onCancelRename,
  onStartRename,
  onArchive,
  onDelete,
  onRetryParse,
  onReupload
}: {
  demo: DemoSummary;
  now: number;
  renaming: boolean;
  renameValue: string;
  renameError: string | null;
  busy: boolean;
  uploadDisabled: boolean;
  onRenameValue: (value: string) => void;
  onSaveRename: (event: FormEvent<HTMLFormElement>) => void;
  onCancelRename: () => void;
  onStartRename: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onRetryParse: () => void;
  onReupload: () => void;
}) {
  const failure = demoFailureState(demo);
  const display = libraryDisplayTitle(demo, mapDisplayName);
  const completed = demo.status === "completed";
  // "战术回放可用" would repeat on every parsed row; only a video is worth a word.
  const videoLabel = playbackReadiness(demo) === "none" ? null : libraryVideoLabel(demo);
  const retryable = canRetryParse(demo);
  const reupload = failure?.next.action === "reupload";
  return (
    <article
      data-demo-id={demo.id}
      className={`lib-row${demo.archived ? " is-archived" : ""}${display.composed ? " is-composed" : ""}`}
    >
      <MapThumb mapName={demo.map_name} />
      <div className="c-name">
        {renaming ? (
          <>
            <form className="lib-rename" onSubmit={onSaveRename}>
              <input
                value={renameValue}
                onChange={(event) => onRenameValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    event.preventDefault();
                    onCancelRename();
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
                className="secondary-button compact-button"
                type="submit"
                disabled={busy || !renameValue.trim()}
                aria-label={`保存 ${demo.name} 的名称`}
              >
                保存
              </button>
              <button
                className="text-button"
                type="button"
                aria-label={`取消重命名 ${demo.name}`}
                onClick={onCancelRename}
              >
                取消
              </button>
            </form>
            {renameError ? (
              <p className="lib-rename-error" id={`rename-error-${demo.id}`} role="alert">{renameError}</p>
            ) : null}
          </>
        ) : (
          <div className="lib-name">
            {/* Stretched over the row: the whole row opens the match. */}
            <Link
              className="stretched-link"
              href={`/demos/${demo.id}`}
              title={display.filename ? `${display.title}（${display.filename}）` : display.title}
            >
              {display.title}
            </Link>
            {display.filename || demo.archived ? (
              <span className="lib-file">
                {display.filename ? <span>{display.filename}</span> : null}
                {demo.archived ? <span className="lib-archived">已归档</span> : null}
              </span>
            ) : null}
          </div>
        )}
      </div>
      <div className="c-map">{mapDisplayName(demo.map_name)}</div>
      <LibraryScore demo={demo} />
      <div className="c-num c-rounds num">
        <span className="lib-cell-label">回合</span>
        {completed ? demo.round_count : "—"}
      </div>
      <div className="c-num c-signals num" title="所有玩家合计；进入比赛后只显示你的玩家的建议">
        {completed ? (
          <>
            <span className="lib-cell-label">全场建议</span>
            {demo.coaching_event_count}
          </>
        ) : null}
      </div>
      <div className="c-date" title={`最近更新：${formatLibraryDate(demo.updated_at)}`}>
        <span className="lib-cell-label">上传</span>
        {formatLibraryDate(demo.created_at)}
      </div>
      <div className="c-state">
        <span className={`status-text ${statusTone(demo.status)}`}>{demoStatusDisplayLabel(demo.status)}</span>
        <IngestionMeta demo={demo} now={now} />
        {videoLabel ? <span className="lib-video">{videoLabel}</span> : null}
        {failure ? <p className="lib-reason">{failure.failure.message}</p> : null}
        {retryable && !reupload ? (
          <button className="text-button" type="button" onClick={onRetryParse} disabled={busy}>
            重新处理
          </button>
        ) : null}
      </div>
      <div className="c-actions">
        {reupload ? (
          <button
            className="text-button"
            type="button"
            disabled={uploadDisabled}
            onClick={onReupload}
            aria-label={`重新上传：${display.title}`}
          >
            重新上传
          </button>
        ) : failure ? null : <PlayEntry demo={demo} title={display.title} />}
        <details className="lib-menu" onKeyDown={closeDetailsOnEscape}>
          <summary className="icon-button compact-button" aria-label={`${display.title} 的更多操作`}>
            <MoreHorizontal size={14} aria-hidden="true" />
          </summary>
          <div className="menu lib-menu-items">
            {retryable && reupload ? (
              <button
                className="menu-item"
                type="button"
                onClick={(event) => {
                  closeRecordMenu(event.currentTarget);
                  onRetryParse();
                }}
                disabled={busy}
              >
                重新处理
              </button>
            ) : null}
            <button
              className="menu-item"
              type="button"
              onClick={(event) => {
                closeRecordMenu(event.currentTarget);
                onStartRename();
              }}
              disabled={busy}
            >
              重命名
            </button>
            <button
              className="menu-item"
              type="button"
              onClick={(event) => {
                closeRecordMenu(event.currentTarget);
                onArchive();
              }}
              disabled={busy}
            >
              {demo.archived ? "恢复到比赛库" : "归档比赛"}
            </button>
            <button
              className="menu-item danger"
              type="button"
              onClick={(event) => {
                closeRecordMenu(event.currentTarget);
                onDelete();
              }}
              disabled={busy}
            >
              删除比赛…
            </button>
          </div>
        </details>
      </div>
    </article>
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
        <Link className="text-button" href={`/demos/${liveDemo.id}#player`}>
          进入复盘
        </Link>
      );
    } else if (failure) {
      tone = " error";
      message = `「${liveDemo.name}」处理失败：${failure.failure.message}`;
      action = (
        <Link className="text-button" href={`/demos/${liveDemo.id}`}>
          查看原因
        </Link>
      );
    } else {
      message = `「${liveDemo.name}」已上传，${processingNoticeLabel(liveDemo.status)}，完成后会在这里提示。`;
      action = (
        <Link className="text-button" href={`/demos/${liveDemo.id}`}>
          查看进度
        </Link>
      );
    }
  } else if (notice.kind === "archived") {
    message = `已归档「${notice.demo.name}」，可在筛选中显示并恢复。`;
    action = (
      <button
        id="library-notice-undo"
        className="text-button"
        type="button"
        disabled={busy}
        onClick={() => onUndoArchive(notice.demo)}
      >
        撤销
      </button>
    );
  } else if (notice.kind === "message") {
    message = notice.message;
    action = notice.demoId ? (
      <Link className="text-button" href={`/demos/${notice.demoId}`}>
        查看比赛
      </Link>
    ) : null;
  } else {
    return null;
  }

  return (
    <div className={`notice lib-notice${tone}`}>
      <span className="lib-notice-text">{message}</span>
      {action}
      <button className="icon-button compact-button lib-notice-close" type="button" aria-label="关闭提示" onClick={onDismiss}>
        <X size={14} aria-hidden="true" />
      </button>
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
    <article className="lib-row is-uploading" aria-label={`正在上传：${upload.fileName}`}>
      <span className="lib-thumb" aria-hidden="true" />
      <div className="c-name">
        <div className="lib-name">
          <span className="lib-name-text" title={upload.fileName}>{upload.fileName}</span>
        </div>
        {/* Percent, size and time left as separate readings, not one joined string. */}
        <div className="lib-progress-facts">
          {verifying ? (
            <span>{label}</span>
          ) : (
            <>
              <span className="lib-progress-pct">上传中 {percent}%</span>
              <span>{formatMegabytes(upload.loaded)} / {formatMegabytes(upload.total)} MB</span>
              {eta ? <span>剩余{eta}</span> : null}
            </>
          )}
        </div>
        <div
          className={`lib-progress${verifying ? " verifying" : ""}`}
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
      <div className="c-map" />
      <div className="c-num c-rounds" />
      <div className="c-num c-signals" />
      <div className="c-date" />
      <div className="c-state">
        <span className="status-text progress">{verifying ? "校验中" : "上传中"}</span>
      </div>
      <div className="c-actions">
        {verifying ? null : (
          <button className="text-button" type="button" onClick={onCancel}>
            取消上传
          </button>
        )}
      </div>
    </article>
  );
}

function LibrarySkeletonRows() {
  return (
    <>
      {[0, 1, 2].map((row) => (
        <div key={row} className="lib-row lib-skel-row" aria-hidden="true">
          <span className="lib-thumb" />
          <div className="c-name"><span className="skeleton-bar wide" /></div>
          <div className="c-map"><span className="skeleton-bar" /></div>
          <div className="c-num c-rounds"><span className="skeleton-bar" /></div>
          <div className="c-num c-signals"><span className="skeleton-bar" /></div>
          <div className="c-date"><span className="skeleton-bar" /></div>
          <div className="c-state"><span className="skeleton-bar" /></div>
          <div className="c-actions" />
        </div>
      ))}
    </>
  );
}

// The match's radar overview, so a row is recognisable before its name is read.
// Unknown maps get a blank tile rather than another map's radar.
function MapThumb({ mapName }: { mapName: string | null | undefined }) {
  const radar = getTacticalMapConfig(mapName)?.radarImagePath ?? null;
  return radar ? (
    <span className="lib-thumb" aria-hidden="true" style={{ backgroundImage: `url("${radar}")` }} />
  ) : (
    <span className="lib-thumb" aria-hidden="true" />
  );
}

function PlayEntry({ demo, title }: { demo: DemoSummary; title: string }) {
  if (playbackReadiness(demo) !== "unavailable") {
    return (
      <Link className="secondary-button compact-button lib-enter" href={`/demos/${demo.id}#player`} aria-label={`进入复盘：${title}`}>
        进入复盘
      </Link>
    );
  }
  return (
    <Link className="secondary-button compact-button lib-enter" href={`/demos/${demo.id}`} aria-label={`查看处理状态：${title}`}>
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
    <div className={`lib-empty is-${state.kind}`} role={isError ? "alert" : undefined}>
      <strong>{copy.title}</strong>
      {isError && error ? <p>{error}</p> : null}
      <p>{state.kind === "empty" && showMockAction ? `${copy.message}${MOCK_DEMO_HINT}` : copy.message}</p>
      {state.kind === "empty" ? <DemFileHelp open quotaNote={quotaNote} /> : null}
      <div className="lib-empty-actions">
        {state.showUploadAction ? (
          <button className="text-button" type="button" disabled={uploadDisabled} onClick={onUpload}>
            上传比赛 .dem
          </button>
        ) : null}
        {showMockAction ? (
          <button className="text-button" type="button" onClick={onMockUpload} disabled={uploadDisabled}>
            示例比赛（模拟数据）
          </button>
        ) : null}
        {state.showClearFiltersAction ? (
          <button className="text-button" type="button" onClick={onClearFilters}>
            清除筛选
          </button>
        ) : null}
        {state.showArchivedAction ? (
          <button className="text-button" type="button" onClick={onShowArchived}>
            显示已归档
          </button>
        ) : null}
        {state.showRefreshAction ? (
          <button className="text-button" type="button" onClick={onRefresh}>
            刷新比赛列表
          </button>
        ) : null}
      </div>
    </div>
  );
}

// The stored final score, team A (started T) first; "—" until the summary exists.
function LibraryScore({ demo }: { demo: DemoSummary }) {
  const teams = demo.matchSummary?.teams ?? [];
  const teamA = teams.find((team) => team.key === "A");
  const teamB = teams.find((team) => team.key === "B");
  if (!teamA || !teamB) {
    return <div className="c-score is-missing"><span className="lib-cell-label">比分</span>—</div>;
  }
  return (
    <div className="c-score" title={teamA.name && teamB.name ? `${teamA.name} ${teamA.score} : ${teamB.score} ${teamB.name}` : undefined}>
      <span className="lib-cell-label">比分</span>
      <span className="lib-score">
        {scorePart(teamA.score, teamB.score)}<span>:</span>{scorePart(teamB.score, teamA.score)}
      </span>
    </div>
  );
}

function scorePart(value: number, other: number) {
  return value > other ? <b>{value}</b> : <span>{value}</span>;
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
  // A finished parse needs no retry history in the row.
  if (ingestion && ingestion.attemptCount > 1 && demo.status !== "completed") {
    labels.push(`第 ${ingestion.attemptCount} 次处理`);
  }
  return labels.length > 0 ? (
    <span className="lib-meta">
      {labels.map((label) => <span key={label}>{label}</span>)}
    </span>
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
