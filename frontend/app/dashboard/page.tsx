"use client";

import Link from "next/link";
import {
  Archive,
  ArrowDownUp,
  Check,
  CircleCheck,
  ChevronDown,
  Clock3,
  ExternalLink,
  FileUp,
  Loader2,
  MoreHorizontal,
  Pencil,
  Play,
  RefreshCcw,
  Search,
  UploadCloud,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { DemoUploader } from "@/components/upload/DemoUploader";
import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { SessionControls } from "@/components/auth/SessionControls";
import { RecentSteamMatches } from "@/components/steam/RecentSteamMatches";
import { archiveDemo, createDemoUpload, createMockUpload, listDemos, retryDemoParse, updateDemo } from "@/lib/api";
import {
  canRetryParse,
  demoLibraryFilterOptions,
  countActiveLibraryDemos,
  filterAndSortDemos,
  libraryEmptyState,
  playbackReadiness,
  shouldPollLibrary,
  type DemoLibraryFilters,
  type LibraryEmptyState
} from "@/lib/demo-library";
import type { DemoProcessingStatus, DemoSummary } from "@/types/demo";

const DEFAULT_FILTERS: DemoLibraryFilters = {
  search: "",
  status: "all",
  map: "all",
  sort: "recent",
  order: "desc",
  includeArchived: false
};

interface LibraryNotice {
  message: string;
  demoId?: string;
}

export default function DashboardPage() {
  return (
    <AuthBoundary>
      <DashboardContent />
    </AuthBoundary>
  );
}

function DashboardContent() {
  const [demos, setDemos] = useState<DemoSummary[]>([]);
  const [filters, setFilters] = useState<DemoLibraryFilters>(DEFAULT_FILTERS);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyDemoId, setBusyDemoId] = useState<string | null>(null);
  const [renamingDemoId, setRenamingDemoId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<LibraryNotice | null>(null);
  const [importOptionsLoaded, setImportOptionsLoaded] = useState(false);
  const loadRequestIdRef = useRef(0);

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
      setError(null);
    } catch (err) {
      if (requestId !== loadRequestIdRef.current) {
        return;
      }
      setError(libraryRequestError(err, "加载比赛失败，请刷新重试。"));
    } finally {
      if (requestId === loadRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [filters.includeArchived]);

  useEffect(() => {
    void loadDemos();
  }, [loadDemos]);

  const activeJobs = useMemo(() => countActiveLibraryDemos(demos), [demos]);
  const failedDemos = useMemo(
    () => demos.filter((demo) => demo.status === "failed").length,
    [demos]
  );
  const filterOptions = useMemo(() => demoLibraryFilterOptions(demos), [demos]);
  const visibleDemos = useMemo(
    () => filterAndSortDemos(demos, filters),
    [demos, filters]
  );
  const emptyState = useMemo(
    () => libraryEmptyState({ loading, error, demos, visibleDemos, filters }),
    [demos, error, filters, loading, visibleDemos]
  );

  useEffect(() => {
    if (!shouldPollLibrary({ loading, creating, activeJobs })) {
      return;
    }

    let cancelled = false;
    let timeoutId = window.setTimeout(poll, 1800);
    async function poll() {
      await loadDemos();
      if (!cancelled) {
        timeoutId = window.setTimeout(poll, 1800);
      }
    }
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [activeJobs, creating, loadDemos, loading]);

  async function handleMockUpload() {
    setCreating(true);
    try {
      const demo = await createMockUpload();
      setNotice({
        message: `示例比赛已创建：${demo.name}。这是模拟数据，可用来体验复盘。`,
        demoId: demo.id
      });
      setError(null);
      await loadDemos();
    } catch (err) {
      setError(libraryRequestError(err, "创建示例失败，请重试。"));
    } finally {
      setCreating(false);
    }
  }

  async function handleDemoUpload(file: File) {
    setCreating(true);
    try {
      const demo = await createDemoUpload(file);
      setNotice({
        message: `${demo.original_filename} 已上传，正在准备复盘。`,
        demoId: demo.id
      });
      setError(null);
      await loadDemos();
    } catch (err) {
      setError(libraryRequestError(err, "上传比赛失败，请检查文件后重试。"));
    } finally {
      setCreating(false);
    }
  }

  function startRename(demo: DemoSummary) {
    setRenamingDemoId(demo.id);
    setRenameValue(demo.name);
    setError(null);
  }

  async function saveRename(event: FormEvent<HTMLFormElement>, demo: DemoSummary) {
    event.preventDefault();
    setBusyDemoId(demo.id);
    invalidateLibraryLoads();
    try {
      const updated = await updateDemo(demo.id, { name: renameValue });
      invalidateLibraryLoads();
      setDemos((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setNotice({ message: `已重命名为「${updated.name}」`, demoId: updated.id });
      setRenamingDemoId(null);
      setRenameValue("");
      setError(null);
    } catch (err) {
      setError(libraryRequestError(err, "重命名失败，请重试。"));
    } finally {
      setBusyDemoId(null);
    }
  }

  async function handleArchive(demo: DemoSummary) {
    setBusyDemoId(demo.id);
    invalidateLibraryLoads();
    try {
      const archived = demo.archived
        ? await updateDemo(demo.id, { archived: false })
        : await archiveDemo(demo.id);
      invalidateLibraryLoads();
      setDemos((current) =>
        filters.includeArchived
          ? current.map((item) => (item.id === archived.id ? archived : item))
          : current.filter((item) => item.id !== archived.id)
      );
      setNotice({
        message: archived.archived
          ? `已归档「${archived.name}」，可在筛选中显示并恢复。`
          : `已恢复「${archived.name}」`,
        demoId: archived.id
      });
      setError(null);
    } catch (err) {
      setError(libraryRequestError(err, "更新归档状态失败，请重试。"));
    } finally {
      setBusyDemoId(null);
    }
  }

  async function handleRetryParse(demo: DemoSummary) {
    setBusyDemoId(demo.id);
    invalidateLibraryLoads();
    try {
      const updated = await retryDemoParse(demo.id);
      invalidateLibraryLoads();
      setDemos((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setNotice({ message: `正在重新处理「${updated.name}」`, demoId: updated.id });
      setError(null);
    } catch (err) {
      setError(libraryRequestError(err, "重新处理失败，请重试。"));
    } finally {
      setBusyDemoId(null);
    }
  }

  return (
    <main className="app-shell library-app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <span>CS2 Coach</span>
        </div>
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
          </div>
          <DemoUploader disabled={creating} onMockUpload={handleMockUpload} onDemoUpload={handleDemoUpload} />
        </div>

        {error ? <div className="error-panel" role="alert">{error}</div> : null}
        {notice ? (
          <div className="library-notice" aria-live="polite">
            <span>{notice.message}</span>
            {notice.demoId ? (
              <Link className="secondary-button compact-button" href={`/demos/${notice.demoId}`}>
                <ExternalLink size={14} />
                查看比赛
              </Link>
            ) : null}
          </div>
        ) : null}

        <section className="library-toolbar library-controls" aria-label="搜索与筛选比赛">
          <label className="library-search">
            <Search size={16} />
            <input
              type="search"
              value={filters.search}
              onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
              placeholder="搜索比赛、文件或地图"
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
                  {mapLabel(map)}
                </option>
              ))}
            </select>
          </label>

          <details className="library-more-filters">
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
                    <option key={status} value={status}>{statusLabel(status)}</option>
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
          {activeJobs > 0 ? <span>{activeJobs} 场正在处理，完成后自动更新</span> : null}
          {failedDemos > 0 ? <span className="library-attention-count">{failedDemos} 场需要处理</span> : null}
        </div>

        <section className="archive-ledger" aria-label="比赛列表">
          <div className="archive-ledger-head" aria-hidden="true">
            <span>比赛</span>
            <span>地图</span>
            <span>复盘线索</span>
            <span>状态</span>
            <span>操作</span>
          </div>
          {emptyState ? (
            <LibraryEmptyStateRow
              state={emptyState}
              creating={creating}
              onClearFilters={() => setFilters(DEFAULT_FILTERS)}
              onMockUpload={() => void handleMockUpload()}
              onUpload={() => document.getElementById("demo-upload-input")?.click()}
              onRefresh={() => void loadDemos()}
              onShowArchived={() =>
                setFilters((current) => ({ ...current, includeArchived: true }))
              }
            />
          ) : (
            visibleDemos.map((demo) => (
              <article
                key={demo.id}
                className={`archive-record ${demo.archived ? "archived-row" : ""} ${demo.status}`}
              >
                <div className="archive-record-identity">
                  {renamingDemoId === demo.id ? (
                    <form className="rename-form" onSubmit={(event) => saveRename(event, demo)}>
                      <input
                        value={renameValue}
                        onChange={(event) => setRenameValue(event.target.value)}
                        aria-label={`重命名 ${demo.name}`}
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
                        onClick={() => {
                          setRenamingDemoId(null);
                          setRenameValue("");
                        }}
                      >
                        <X size={15} />
                      </button>
                    </form>
                  ) : (
                    <div className="demo-name">
                      <span><Link href={`/demos/${demo.id}`} title={demo.name}>{demo.name}</Link></span>
                      {demo.name !== demo.original_filename ? <span>{demo.original_filename}</span> : null}
                      {demo.archived ? <span className="archived-label">已归档</span> : null}
                    </div>
                  )}
                  <div className="archive-record-date">
                    <span title={`最近更新：${formatDate(demo.updated_at)}`}>{formatDate(demo.created_at)} 上传</span>
                  </div>
                </div>
                <div className="archive-record-map">
                  <span className="map-anchor">{mapLabel(demo.map_name)}</span>
                </div>
                <div className="archive-record-signals">
                  <div className="library-review-counts">
                    <span><strong>{demo.round_count}</strong> 回合</span>
                    <span><strong>{demo.coaching_event_count}</strong> 条线索</span>
                  </div>
                </div>
                <div className="archive-record-state">
                  <div className="library-readiness">
                    <StatusBadge status={demo.status} />
                    <IngestionMeta demo={demo} />
                    {demo.ingestion?.failure || demo.error_message ? (
                      <details className="library-failure-details">
                        <summary>查看原因</summary>
                        <p className="library-error-text">{demo.ingestion?.failure?.message || demo.error_message}</p>
                      </details>
                    ) : null}
                  </div>
                  {playbackReadiness(demo) !== "unavailable" ? <span
                    className={`mini-pill library-render-pill readiness-${playbackReadiness(demo)}`}
                  >
                    {videoAvailabilityLabel(demo)}
                  </span> : null}
                </div>
                <div className="archive-record-actions">
                  <div className="library-actions">
                    <PlayEntry demo={demo} />
                    {canRetryParse(demo) ? (
                      <button
                        className="secondary-button compact-button"
                        type="button"
                        onClick={() => void handleRetryParse(demo)}
                        disabled={busyDemoId === demo.id}
                      >
                        <RefreshCcw size={14} />
                        重新处理
                      </button>
                    ) : null}
                    <details className="library-record-menu" onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        event.currentTarget.open = false;
                        event.currentTarget.querySelector("summary")?.focus();
                      }
                    }}>
                      <summary className="icon-button" aria-label={`${demo.name} 的更多操作`}>
                        <MoreHorizontal size={18} />
                      </summary>
                      <div className="library-record-menu-items">
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
            ))
          )}
        </section>
        <details
          className="library-import-options"
          onToggle={(event) => {
            if (event.currentTarget.open) setImportOptionsLoaded(true);
          }}
        >
          <summary><span>导入选项</span><span>从 Steam 导入比赛</span><ChevronDown size={16} /></summary>
          {importOptionsLoaded ? <RecentSteamMatches /> : null}
        </details>
      </section>
    </main>
  );
}

function PlayEntry({ demo }: { demo: DemoSummary }) {
  const readiness = playbackReadiness(demo);
  const href = `/demos/${demo.id}#player`;
  if (readiness !== "unavailable") {
    return (
      <Link
        className="primary-button compact-button library-play-button"
        href={href}
        aria-label={`进入复盘：${demo.name}`}
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
      aria-label={`查看处理状态：${demo.name}`}
    >
      <Clock3 size={14} />
      查看状态
    </Link>
  );
}

function LibraryEmptyStateRow({
  state,
  creating,
  onClearFilters,
  onMockUpload,
  onUpload,
  onRefresh,
  onShowArchived
}: {
  state: LibraryEmptyState;
  creating: boolean;
  onClearFilters: () => void;
  onMockUpload: () => void;
  onUpload: () => void;
  onRefresh: () => void;
  onShowArchived: () => void;
}) {
  const copy = EMPTY_STATE_COPY[state.kind];
  return (
    <div className={`library-empty-state ${state.kind}`} aria-busy={state.kind === "loading"}>
      <div>
        <strong>{copy.title}</strong>
        <p>{copy.message}</p>
      </div>
      <div className="library-empty-actions">
        {state.showUploadAction ? (
          <button
            className="primary-button compact-button"
            type="button"
            disabled={creating}
            onClick={onUpload}
          >
            <FileUp size={14} />
            上传比赛 .dem
          </button>
        ) : null}
        {state.showMockAction ? (
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onMockUpload}
            disabled={creating}
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
    status === "completed" ? CircleCheck : status === "failed" ? Clock3 : Loader2;
  const active = status === "queued" || status === "parsing" || status === "analyzing";
  return (
    <span className={`status-badge ${status}`}>
      <Icon size={14} className={active ? "spin-icon" : ""} />
      {statusLabel(status)}
    </span>
  );
}

function IngestionMeta({ demo }: { demo: DemoSummary }) {
  const ingestion = demo.ingestion;
  if (!ingestion || (!ingestion.stale && ingestion.attemptCount <= 1)) {
    return null;
  }

  const labels: string[] = [];
  if (ingestion.stale) {
    labels.push("处理时间较长");
  }
  if (ingestion.attemptCount > 1) {
    labels.push(`第 ${ingestion.attemptCount} 次处理`);
  }

  return <p className="library-ingestion-meta">{labels.join(" · ")}</p>;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

const EMPTY_STATE_COPY: Record<LibraryEmptyState["kind"], { title: string; message: string }> = {
  loading: { title: "正在加载比赛", message: "比赛准备好后，会显示在这里。" },
  error: { title: "暂时无法加载比赛", message: "请刷新重试。已上传的比赛会保留。" },
  empty: { title: "开始你的第一场复盘", message: "上传 .dem 比赛文件，即可查看战术回放和复盘建议。也可以先用模拟比赛体验。" },
  archived: { title: "比赛已归档", message: "显示已归档比赛，即可继续复盘或恢复到比赛库。" },
  search: { title: "没有找到这场比赛", message: "试试其他比赛名称或地图，也可以清除筛选查看全部比赛。" },
  filtered: { title: "没有符合条件的比赛", message: "调整地图、状态或归档筛选，查看其他比赛。" }
};

function statusLabel(status: DemoProcessingStatus): string {
  return {
    queued: "等待处理",
    parsing: "读取比赛中",
    analyzing: "整理建议中",
    completed: "可以复盘",
    failed: "处理失败"
  }[status];
}

function videoAvailabilityLabel(demo: DemoSummary): string {
  const readiness = playbackReadiness(demo);
  if (readiness === "ready") return "有第一人称片段";
  if (readiness === "rendering") return "视频生成中";
  return "战术回放可用";
}

function mapLabel(map: string): string {
  if (!map || map === "unknown") return "地图待识别";
  const name = map.replace(/^de_/, "");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function closeRecordMenu(button: HTMLButtonElement) {
  const details = button.closest("details");
  if (details) {
    details.open = false;
    details.querySelector("summary")?.focus();
  }
}

function libraryRequestError(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : "";
  if (/failed to fetch|networkerror|load failed|err_connection|econnrefused/i.test(message)) {
    return "暂时无法连接服务，请确认应用已启动，然后刷新重试。";
  }
  if (/413|too large|maximum upload/i.test(message)) {
    return "文件超过上传大小限制，请选择较小的 .dem 文件。";
  }
  if (/\.dem|invalid file|unsupported file/i.test(message)) {
    return "请选择有效的 .dem 比赛文件后重试。";
  }
  return fallback;
}
