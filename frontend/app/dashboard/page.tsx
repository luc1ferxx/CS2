"use client";

import Link from "next/link";
import {
  Activity,
  Archive,
  ArrowDownUp,
  Check,
  CircleCheck,
  Clock3,
  ExternalLink,
  FileUp,
  Loader2,
  Pencil,
  RefreshCcw,
  Search,
  UploadCloud,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { DemoUploader } from "@/components/upload/DemoUploader";
import { archiveDemo, createDemoUpload, createMockUpload, listDemos, retryDemoParse, updateDemo } from "@/lib/api";
import {
  canRetryParse,
  demoLibraryFilterOptions,
  countActiveLibraryDemos,
  demoStatusLabel,
  filterAndSortDemos,
  friendlyErrorMessage,
  ingestionPhaseLabel,
  libraryEmptyState,
  parseFailureReason,
  renderStatusLabel,
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
  const [demos, setDemos] = useState<DemoSummary[]>([]);
  const [filters, setFilters] = useState<DemoLibraryFilters>(DEFAULT_FILTERS);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyDemoId, setBusyDemoId] = useState<string | null>(null);
  const [renamingDemoId, setRenamingDemoId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<LibraryNotice | null>(null);
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
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to load demos"));
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

    const intervalId = window.setInterval(() => {
      void loadDemos();
    }, 1800);
    return () => window.clearInterval(intervalId);
  }, [activeJobs, creating, loadDemos, loading]);

  async function handleMockUpload() {
    setCreating(true);
    try {
      const demo = await createMockUpload();
      setNotice({
        message: `Mock demo queued: ${demo.name}. This synthetic replay is for fast UI smoke checks.`,
        demoId: demo.id
      });
      setError(null);
      await loadDemos();
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to create mock upload"));
    } finally {
      setCreating(false);
    }
  }

  async function handleDemoUpload(file: File) {
    setCreating(true);
    try {
      const demo = await createDemoUpload(file);
      setNotice({
        message: `Real .dem upload queued for parser review: ${demo.original_filename}. Open the demo to watch parse status.`,
        demoId: demo.id
      });
      setError(null);
      await loadDemos();
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to upload demo"));
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
      setNotice({ message: `Renamed demo to ${updated.name}`, demoId: updated.id });
      setRenamingDemoId(null);
      setRenameValue("");
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to rename demo"));
    } finally {
      setBusyDemoId(null);
    }
  }

  async function handleArchive(demo: DemoSummary) {
    const confirmed = window.confirm(
      `Archive "${demo.name}"? It will be hidden from the default demo library.`
    );
    if (!confirmed) {
      return;
    }

    setBusyDemoId(demo.id);
    invalidateLibraryLoads();
    try {
      const archived = await archiveDemo(demo.id);
      invalidateLibraryLoads();
      setDemos((current) =>
        filters.includeArchived
          ? current.map((item) => (item.id === archived.id ? archived : item))
          : current.filter((item) => item.id !== archived.id)
      );
      setNotice({ message: `Archived ${archived.name}`, demoId: archived.id });
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to archive demo"));
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
      setNotice({ message: `Retry queued: ${updated.name}`, demoId: updated.id });
      setError(null);
    } catch (err) {
      setError(friendlyErrorMessage(err instanceof Error ? err.message : "Failed to retry parse"));
    } finally {
      setBusyDemoId(null);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <span>CS2 Demo Coach</span>
        </div>
        <div className="topbar-actions">
          <span className="status-pill">
            <span className="status-dot" />
            Mock API
          </span>
          <span className="status-pill">
            <Activity size={15} />
            {activeJobs} active jobs
          </span>
        </div>
      </header>

      <section className="page">
        <div className="page-header library-page-header">
          <div>
            <h1 className="page-title">Demo Library</h1>
            <p className="page-subtitle">
              Manage uploaded demos, parser status, review readiness, and render clip state.
            </p>
          </div>
          <DemoUploader disabled={creating} onMockUpload={handleMockUpload} onDemoUpload={handleDemoUpload} />
        </div>

        {error ? <div className="error-panel">{error}</div> : null}
        {notice ? (
          <div className="library-notice">
            <span>{notice.message}</span>
            {notice.demoId ? (
              <Link className="secondary-button compact-button" href={`/demos/${notice.demoId}`}>
                <ExternalLink size={14} />
                Open demo
              </Link>
            ) : null}
          </div>
        ) : null}

        <section className="panel library-toolbar" aria-label="Demo library controls">
          <label className="library-search">
            <Search size={16} />
            <input
              type="search"
              value={filters.search}
              onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
              placeholder="Search name, file, or map"
              aria-label="Search demos"
            />
          </label>

          <label className="library-filter">
            <span>Status</span>
            <select
              value={filters.status}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  status: event.target.value as DemoLibraryFilters["status"]
                }))
              }
              aria-label="Filter by status"
            >
              <option value="all">All statuses</option>
              {filterOptions.statuses.map((status) => (
                <option key={status} value={status}>
                  {demoStatusLabel(status)}
                </option>
              ))}
            </select>
          </label>

          <label className="library-filter">
            <span>Map</span>
            <select
              value={filters.map}
              onChange={(event) => setFilters((current) => ({ ...current, map: event.target.value }))}
              aria-label="Filter by map"
            >
              <option value="all">All maps</option>
              {filterOptions.maps.map((map) => (
                <option key={map} value={map}>
                  {map}
                </option>
              ))}
            </select>
          </label>

          <label className="library-filter">
            <span>Sort</span>
            <select
              value={filters.sort}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  sort: event.target.value as DemoLibraryFilters["sort"],
                  order: event.target.value === "recent" ? "desc" : current.order
                }))
              }
              aria-label="Sort demos"
            >
              <option value="recent">Recently uploaded</option>
              <option value="name">Name</option>
              <option value="map">Map</option>
              <option value="status">Status</option>
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
            {filters.order === "asc" ? "Ascending" : "Descending"}
          </button>

          <button
            className="secondary-button compact-button"
            type="button"
            onClick={() => void loadDemos()}
          >
            <RefreshCcw size={14} />
            Refresh
          </button>

          <label className="include-archived-toggle">
            <input
              type="checkbox"
              checked={filters.includeArchived}
              onChange={(event) =>
                setFilters((current) => ({ ...current, includeArchived: event.target.checked }))
              }
            />
            Show archived
          </label>
        </section>

        <section className="library-stats" aria-label="Demo library summary">
          <LibraryStat label="Visible" value={visibleDemos.length} />
          <LibraryStat label="Total loaded" value={demos.length} />
          <LibraryStat label="Active jobs" value={activeJobs} />
          <LibraryStat label="Failed" value={failedDemos} />
        </section>

        <div className="panel table-panel demo-library-panel">
          <table className="demo-table demo-library-table">
            <thead>
              <tr>
                <th>Demo</th>
                <th>Map</th>
                <th>Review</th>
                <th>Status</th>
                <th>Render</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {emptyState ? (
                <tr>
                  <td colSpan={7}>
                    <LibraryEmptyStateRow
                      state={emptyState}
                      creating={creating}
                      onClearFilters={() => setFilters(DEFAULT_FILTERS)}
                      onMockUpload={() => void handleMockUpload()}
                      onRefresh={() => void loadDemos()}
                      onShowArchived={() =>
                        setFilters((current) => ({ ...current, includeArchived: true }))
                      }
                    />
                  </td>
                </tr>
              ) : (
                visibleDemos.map((demo) => (
                  <tr key={demo.id} className={demo.archived ? "archived-row" : undefined}>
                    <td data-label="Demo">
                      {renamingDemoId === demo.id ? (
                        <form className="rename-form" onSubmit={(event) => saveRename(event, demo)}>
                          <input
                            value={renameValue}
                            onChange={(event) => setRenameValue(event.target.value)}
                            aria-label={`Rename ${demo.name}`}
                            autoFocus
                          />
                          <button className="icon-button" type="submit" disabled={busyDemoId === demo.id}>
                            <Check size={15} />
                          </button>
                          <button
                            className="icon-button"
                            type="button"
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
                          <span>{demo.name}</span>
                          <span>{demo.original_filename}</span>
                          {demo.archived ? <span className="archived-label">Archived</span> : null}
                        </div>
                      )}
                    </td>
                    <td data-label="Map">{demo.map_name}</td>
                    <td data-label="Review">
                      <div className="library-review-counts">
                        <span>{demo.round_count || "-"} rounds</span>
                        <span>{demo.coaching_event_count || "-"} coaching</span>
                      </div>
                    </td>
                    <td data-label="Status">
                      <StatusBadge status={demo.status} />
                      <IngestionMeta demo={demo} />
                      {parseFailureReason(demo) ? (
                        <p className="library-error-text">{parseFailureReason(demo)}</p>
                      ) : null}
                    </td>
                    <td data-label="Render">
                      <span className={`mini-pill library-render-pill ${demo.latest_render_status ?? demo.video_status ?? "pending"}`}>
                        {renderStatusLabel(demo)}
                      </span>
                    </td>
                    <td data-label="Updated">
                      <div className="library-date">
                        <span>{formatDate(demo.updated_at)}</span>
                        <span>Uploaded {formatDate(demo.created_at)}</span>
                      </div>
                    </td>
                    <td data-label="Actions">
                      <div className="library-actions">
                        <Link className="secondary-button compact-button" href={`/demos/${demo.id}`}>
                          <ExternalLink size={14} />
                          Open
                        </Link>
                        <button
                          className="secondary-button compact-button"
                          type="button"
                          onClick={() => startRename(demo)}
                          disabled={busyDemoId === demo.id}
                        >
                          <Pencil size={14} />
                          Rename
                        </button>
                        {canRetryParse(demo) ? (
                          <button
                            className="secondary-button compact-button"
                            type="button"
                            onClick={() => void handleRetryParse(demo)}
                            disabled={busyDemoId === demo.id}
                          >
                            <RefreshCcw size={14} />
                            Retry
                          </button>
                        ) : null}
                        <button
                          className="secondary-button compact-button"
                          type="button"
                          onClick={() => void handleArchive(demo)}
                          disabled={busyDemoId === demo.id || demo.archived}
                        >
                          <Archive size={14} />
                          Archive
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

function LibraryStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="panel library-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function LibraryEmptyStateRow({
  state,
  creating,
  onClearFilters,
  onMockUpload,
  onRefresh,
  onShowArchived
}: {
  state: LibraryEmptyState;
  creating: boolean;
  onClearFilters: () => void;
  onMockUpload: () => void;
  onRefresh: () => void;
  onShowArchived: () => void;
}) {
  return (
    <div className={`library-empty-state ${state.kind}`}>
      <div>
        <strong>{state.title}</strong>
        <p>{state.message}</p>
      </div>
      <div className="library-empty-actions">
        {state.showMockAction ? (
          <button
            className="primary-button compact-button"
            type="button"
            onClick={onMockUpload}
            disabled={creating}
          >
            <UploadCloud size={14} />
            Create mock demo
          </button>
        ) : null}
        {state.showUploadAction ? (
          <label
            className={`secondary-button compact-button ${creating ? "disabled-label" : ""}`}
            htmlFor="demo-upload-input"
            aria-disabled={creating}
            onClick={(event) => {
              if (creating) {
                event.preventDefault();
              }
            }}
          >
            <FileUp size={14} />
            Upload .dem
          </label>
        ) : null}
        {state.showClearFiltersAction ? (
          <button className="secondary-button compact-button" type="button" onClick={onClearFilters}>
            <X size={14} />
            Clear filters
          </button>
        ) : null}
        {state.showArchivedAction ? (
          <button className="secondary-button compact-button" type="button" onClick={onShowArchived}>
            <Archive size={14} />
            Show archived
          </button>
        ) : null}
        {state.showRefreshAction ? (
          <button className="secondary-button compact-button" type="button" onClick={onRefresh}>
            <RefreshCcw size={14} />
            Refresh library
          </button>
        ) : null}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: DemoProcessingStatus }) {
  const Icon =
    status === "completed" ? CircleCheck : status === "failed" ? Clock3 : Loader2;
  return (
    <span className={`status-badge ${status}`}>
      <Icon size={14} className={status === "completed" ? "" : "spin-icon"} />
      {demoStatusLabel(status)}
    </span>
  );
}

function IngestionMeta({ demo }: { demo: DemoSummary }) {
  const ingestion = demo.ingestion;
  if (!ingestion) {
    return null;
  }

  const labels = [ingestionPhaseLabel(demo)];
  if (ingestion.active) {
    labels.push("active");
  }
  if (ingestion.stale) {
    labels.push("stale");
  }
  if (ingestion.attemptCount > 0) {
    labels.push(`attempt ${ingestion.attemptCount}`);
  }

  return <p className="library-ingestion-meta">{labels.join(" / ")}</p>;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}
