"use client";

import Link from "next/link";
import {
  Activity,
  Archive,
  Check,
  CircleCheck,
  Clock3,
  ExternalLink,
  Loader2,
  Pencil,
  RefreshCcw,
  Search,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { DemoUploader } from "@/components/upload/DemoUploader";
import { archiveDemo, createDemoUpload, createMockUpload, listDemos, updateDemo } from "@/lib/api";
import {
  demoLibraryFilterOptions,
  filterAndSortDemos,
  renderStatusLabel,
  type DemoLibraryFilters
} from "@/lib/demo-library";
import type { DemoProcessingStatus, DemoSummary } from "@/types/demo";

const ACTIVE_STATUSES: DemoProcessingStatus[] = ["queued", "parsing", "analyzing"];
const DEFAULT_FILTERS: DemoLibraryFilters = {
  search: "",
  status: "all",
  map: "all",
  sort: "recent",
  order: "desc",
  includeArchived: false
};

export default function DashboardPage() {
  const [demos, setDemos] = useState<DemoSummary[]>([]);
  const [filters, setFilters] = useState<DemoLibraryFilters>(DEFAULT_FILTERS);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyDemoId, setBusyDemoId] = useState<string | null>(null);
  const [renamingDemoId, setRenamingDemoId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadDemos = useCallback(async () => {
    try {
      const nextDemos = await listDemos({ includeArchived: filters.includeArchived });
      setDemos(nextDemos);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load demos");
    } finally {
      setLoading(false);
    }
  }, [filters.includeArchived]);

  useEffect(() => {
    void loadDemos();
    const intervalId = window.setInterval(() => {
      void loadDemos();
    }, 1800);
    return () => window.clearInterval(intervalId);
  }, [loadDemos]);

  const activeJobs = useMemo(
    () => demos.filter((demo) => ACTIVE_STATUSES.includes(demo.status)).length,
    [demos]
  );
  const failedDemos = useMemo(
    () => demos.filter((demo) => demo.status === "failed").length,
    [demos]
  );
  const filterOptions = useMemo(() => demoLibraryFilterOptions(demos), [demos]);
  const visibleDemos = useMemo(
    () => filterAndSortDemos(demos, filters),
    [demos, filters]
  );

  async function handleMockUpload() {
    setCreating(true);
    try {
      const demo = await createMockUpload();
      setNotice(`Mock upload queued: ${demo.name}`);
      await loadDemos();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create mock upload");
    } finally {
      setCreating(false);
    }
  }

  async function handleDemoUpload(file: File) {
    setCreating(true);
    try {
      const demo = await createDemoUpload(file);
      setNotice(`Real demo upload queued: ${demo.original_filename}`);
      await loadDemos();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload demo");
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
    try {
      const updated = await updateDemo(demo.id, { name: renameValue });
      setDemos((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setNotice(`Renamed demo to ${updated.name}`);
      setRenamingDemoId(null);
      setRenameValue("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename demo");
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
    try {
      const archived = await archiveDemo(demo.id);
      setDemos((current) =>
        filters.includeArchived
          ? current.map((item) => (item.id === archived.id ? archived : item))
          : current.filter((item) => item.id !== archived.id)
      );
      setNotice(`Archived ${archived.name}`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive demo");
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
        {notice ? <div className="library-notice">{notice}</div> : null}

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
                  {status}
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
            <RefreshCcw size={14} />
            {filters.order === "asc" ? "Ascending" : "Descending"}
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
              {loading ? (
                <tr>
                  <td colSpan={7}>
                    <div className="empty-state">Loading demos...</div>
                  </td>
                </tr>
              ) : demos.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    <div className="empty-state">
                      No demos yet. Use Real Demo Upload for a .dem/.zip parser job or Mock Upload for a synthetic replay.
                    </div>
                  </td>
                </tr>
              ) : visibleDemos.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    <div className="empty-state">No demos match the current library filters.</div>
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
                      {demo.error_message ? <p className="library-error-text">{demo.error_message}</p> : null}
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

function StatusBadge({ status }: { status: DemoProcessingStatus }) {
  const Icon =
    status === "completed" ? CircleCheck : status === "failed" ? Clock3 : Loader2;
  return (
    <span className={`status-badge ${status}`}>
      <Icon size={14} className={status === "completed" ? "" : "spin-icon"} />
      {status}
    </span>
  );
}

function formatDate(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}
