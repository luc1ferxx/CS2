"use client";

import Link from "next/link";
import { Activity, CircleCheck, Clock3, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { DemoUploader } from "@/components/upload/DemoUploader";
import { createMockUpload, listDemos } from "@/lib/api";
import type { DemoProcessingStatus, DemoSummary } from "@/types/demo";

const ACTIVE_STATUSES: DemoProcessingStatus[] = ["queued", "parsing", "analyzing"];

export default function DashboardPage() {
  const [demos, setDemos] = useState<DemoSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDemos = useCallback(async () => {
    try {
      const nextDemos = await listDemos();
      setDemos(nextDemos);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load demos");
    } finally {
      setLoading(false);
    }
  }, []);

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

  async function handleMockUpload() {
    setCreating(true);
    try {
      await createMockUpload();
      await loadDemos();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create mock upload");
    } finally {
      setCreating(false);
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
        <div className="page-header">
          <div>
            <h1 className="page-title">Demo Dashboard</h1>
            <p className="page-subtitle">
              Queue synthetic demos, watch processing status, then open first-person coaching review.
            </p>
          </div>
          <DemoUploader disabled={creating} onMockUpload={handleMockUpload} />
        </div>

        {error ? <div className="error-panel">{error}</div> : null}

        <div className="panel table-panel">
          <table className="demo-table">
            <thead>
              <tr>
                <th style={{ width: "32%" }}>Demo</th>
                <th>Map</th>
                <th>Rounds</th>
                <th>Coaching Events</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6}>
                    <div className="empty-state">Loading demos...</div>
                  </td>
                </tr>
              ) : demos.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <div className="empty-state">
                      No demos yet. Use Mock Upload to create the first replay job.
                    </div>
                  </td>
                </tr>
              ) : (
                demos.map((demo) => (
                  <tr key={demo.id}>
                    <td>
                      {demo.status === "completed" ? (
                        <Link className="demo-name" href={`/demos/${demo.id}`}>
                          <span>{demo.name}</span>
                          <span>{demo.original_filename}</span>
                        </Link>
                      ) : (
                        <div className="demo-name">
                          <span>{demo.name}</span>
                          <span>{demo.original_filename}</span>
                        </div>
                      )}
                    </td>
                    <td>{demo.map_name}</td>
                    <td>{demo.round_count || "-"}</td>
                    <td>{demo.coaching_event_count || "-"}</td>
                    <td>
                      <StatusBadge status={demo.status} />
                    </td>
                    <td>{new Date(demo.created_at).toLocaleString()}</td>
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
