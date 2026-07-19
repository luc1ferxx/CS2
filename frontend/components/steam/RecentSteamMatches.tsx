"use client";

import Link from "next/link";
import {
  Clock3,
  ExternalLink,
  FileUp,
  Link2,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  Unplug
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  deleteSteamConnection,
  getSteamConnection,
  isApiError,
  listSteamMatches,
  saveSteamConnectionCredentials,
  syncSteamMatches
} from "@/lib/api";
import {
  buildSteamConnectionDisplay,
  buildSteamMatchDisplay,
  steamRequestErrorMessage,
  steamSyncResultMessage,
  type SteamRequestAction
} from "@/lib/steam-matches";
import type { SteamConnection, SteamMatch } from "@/types/steam";

type BusyAction = "save" | "sync" | "disconnect" | "refresh";

export function RecentSteamMatches() {
  const [connection, setConnection] = useState<SteamConnection | null>(null);
  const [matches, setMatches] = useState<SteamMatch[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction | null>(null);
  const [editingCredentials, setEditingCredentials] = useState(false);
  const [gameAuthCode, setGameAuthCode] = useState("");
  const [initialMatchSharingCode, setInitialMatchSharingCode] = useState("");
  const [, setRetryTimerVersion] = useState(0);
  const loadRequestIdRef = useRef(0);

  const loadSteamData = useCallback(async (showLoading = false) => {
    const requestId = loadRequestIdRef.current + 1;
    loadRequestIdRef.current = requestId;
    if (showLoading) {
      setInitialLoading(true);
    }

    try {
      const [connectionResult, matchesResult] = await Promise.allSettled([
        getSteamConnection(),
        listSteamMatches()
      ]);
      if (requestId !== loadRequestIdRef.current) {
        return;
      }

      const errors: string[] = [];
      if (connectionResult.status === "fulfilled") {
        setConnection(connectionResult.value);
      } else {
        errors.push(requestErrorMessage(connectionResult.reason, "load"));
      }
      if (matchesResult.status === "fulfilled") {
        setMatches(matchesResult.value);
      } else {
        errors.push("Could not load recent Steam matches. Refresh to retry.");
      }
      setLoadError(errors.length > 0 ? [...new Set(errors)].join(" ") : null);
    } finally {
      if (requestId === loadRequestIdRef.current) {
        setInitialLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadSteamData(true);
  }, [loadSteamData]);

  useEffect(() => {
    if (!connection?.next_retry_at) {
      return;
    }
    const retryAt = new Date(connection.next_retry_at).getTime();
    if (!Number.isFinite(retryAt) || retryAt <= Date.now()) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      setRetryTimerVersion((current) => current + 1);
    }, retryAt - Date.now() + 50);
    return () => window.clearTimeout(timeoutId);
  }, [connection?.next_retry_at]);

  const connectionDisplay = useMemo(
    () => (connection ? buildSteamConnectionDisplay(connection) : null),
    [connection]
  );
  const matchDisplays = useMemo(
    () => matches.map((match) => buildSteamMatchDisplay(match)),
    [matches]
  );
  const credentialsConfigured = connectionDisplay?.credentialsConfigured ?? false;
  const connected = connectionDisplay?.connected ?? false;
  const showCredentialsForm =
    connection !== null && (!credentialsConfigured || editingCredentials);
  const syncRequiresRepair = connection?.status === "authorization_required";
  const syncAlreadyRunning = connection?.status === "syncing";
  const retryBlocked =
    connection?.status === "retry_wait" &&
    connection.next_retry_at !== null &&
    new Date(connection.next_retry_at).getTime() > Date.now();
  const canSync =
    connected &&
    credentialsConfigured &&
    !syncRequiresRepair &&
    !syncAlreadyRunning &&
    !retryBlocked &&
    busyAction === null;

  async function handleSaveCredentials(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextGameAuthCode = gameAuthCode.trim();
    const nextMatchSharingCode = initialMatchSharingCode.trim();
    if (!nextGameAuthCode || !nextMatchSharingCode) {
      setActionError("Enter both Steam codes before saving the connection.");
      return;
    }

    setBusyAction("save");
    setActionError(null);
    setNotice(null);
    try {
      const nextConnection = await saveSteamConnectionCredentials({
        game_auth_code: nextGameAuthCode,
        initial_match_sharing_code: nextMatchSharingCode
      });
      setConnection(nextConnection);
      setEditingCredentials(false);
      setNotice("Steam match-history authorization saved. The codes are never shown back here.");
      await loadSteamData();
    } catch (error) {
      setActionError(requestErrorMessage(error, "connect"));
    } finally {
      setGameAuthCode("");
      setInitialMatchSharingCode("");
      setBusyAction(null);
    }
  }

  async function handleSync() {
    setBusyAction("sync");
    setActionError(null);
    setNotice(null);
    try {
      const result = await syncSteamMatches();
      setNotice(steamSyncResultMessage(result));
      await loadSteamData();
    } catch (error) {
      setActionError(requestErrorMessage(error, "sync"));
      await loadSteamData();
    } finally {
      setBusyAction(null);
    }
  }

  async function handleDisconnect() {
    const confirmed = window.confirm(
      "Disconnect Steam match history? Saved authorization codes and discovered Steam match records will be deleted. Existing imported demos are retained."
    );
    if (!confirmed) {
      return;
    }

    setBusyAction("disconnect");
    setActionError(null);
    setNotice(null);
    try {
      await deleteSteamConnection();
      setConnection(disconnectedConnection());
      setMatches([]);
      setEditingCredentials(false);
      setGameAuthCode("");
      setInitialMatchSharingCode("");
      setNotice(
        "Steam connection and discovered match records were deleted. Imported demos remain in the Demo Library."
      );
    } catch (error) {
      setActionError(requestErrorMessage(error, "disconnect"));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleRefresh() {
    setBusyAction("refresh");
    setActionError(null);
    await loadSteamData();
    setBusyAction(null);
  }

  return (
    <section className="steam-match-sync" aria-labelledby="recent-steam-matches-title">
      <header className="steam-sync-header">
        <div>
          <span className="workspace-kicker">Steam match discovery</span>
          <div className="steam-sync-title-row">
            <h2 id="recent-steam-matches-title">Recent Steam Matches</h2>
            {connectionDisplay ? (
              <span className={`steam-state-pill ${connectionDisplay.statusTone}`}>
                {connectionDisplay.statusLabel}
              </span>
            ) : null}
          </div>
          <p>
            Discover official match-history records first. Automatic Demo import is not implemented in this phase.
          </p>
        </div>
        <div className="steam-sync-actions">
          {credentialsConfigured && !showCredentialsForm ? (
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={busyAction !== null}
              onClick={() => {
                setEditingCredentials(true);
                setActionError(null);
                setNotice(null);
              }}
            >
              <ShieldCheck size={14} />
              Replace codes
            </button>
          ) : null}
          <button
            className="secondary-button compact-button"
            type="button"
            disabled={busyAction !== null}
            onClick={() => void handleRefresh()}
          >
            <RefreshCcw size={14} className={busyAction === "refresh" ? "spin-icon" : ""} />
            Refresh
          </button>
          <button
            className="primary-button compact-button"
            type="button"
            disabled={!canSync}
            onClick={() => void handleSync()}
          >
            {busyAction === "sync" ? (
              <Loader2 size={14} className="spin-icon" />
            ) : (
              <Link2 size={14} />
            )}
            {busyAction === "sync" ? "Syncing" : "Sync now"}
          </button>
          {connected ? (
            <button
              className="secondary-button compact-button steam-disconnect-button"
              type="button"
              disabled={busyAction !== null}
              onClick={() => void handleDisconnect()}
            >
              <Unplug size={14} />
              {busyAction === "disconnect" ? "Disconnecting" : "Disconnect"}
            </button>
          ) : null}
        </div>
      </header>

      {loadError ? (
        <div className="steam-sync-message danger" role="alert">
          <span>{loadError}</span>
          <button
            className="secondary-button compact-button"
            type="button"
            disabled={busyAction !== null}
            onClick={() => void handleRefresh()}
          >
            <RefreshCcw size={14} />
            Retry
          </button>
        </div>
      ) : null}
      {actionError ? (
        <div className="steam-sync-message danger" role="alert">{actionError}</div>
      ) : null}
      {notice ? (
        <div className="steam-sync-message success" aria-live="polite">{notice}</div>
      ) : null}

      {initialLoading && !connection ? (
        <div className="steam-sync-loading" aria-live="polite">
          <Loader2 size={16} className="spin-icon" />
          Loading Steam connection and recent matches
        </div>
      ) : (
        <div className="steam-sync-layout">
          <div className="steam-connection-pane">
            <div className="steam-pane-heading">
              <div>
                <span>Connection</span>
                <strong>{connectionDisplay?.statusLabel ?? "Unavailable"}</strong>
              </div>
              <span className="steam-schedule-label">
                Scheduled sync {connectionDisplay?.scheduledSyncEnabled ? "enabled" : "off"}
              </span>
            </div>

            {connectionDisplay ? (
              <dl className="steam-connection-meta">
                <div>
                  <dt>Last sync</dt>
                  <dd>{formatSteamDate(connectionDisplay.lastSyncCompletedAt)}</dd>
                </div>
                <div>
                  <dt>Last attempt</dt>
                  <dd>{formatSteamDate(connectionDisplay.lastSyncStartedAt)}</dd>
                </div>
                <div>
                  <dt>Next retry</dt>
                  <dd>{formatSteamDate(connectionDisplay.nextRetryAt)}</dd>
                </div>
              </dl>
            ) : null}

            {connectionDisplay?.errorMessage ? (
              <p className="steam-connection-warning">{connectionDisplay.errorMessage}</p>
            ) : null}

            {connection === null ? (
              <p className="steam-connection-warning">
                Connection status is unavailable. Refresh before entering or replacing codes.
              </p>
            ) : showCredentialsForm ? (
              <form className="steam-credentials-form" onSubmit={handleSaveCredentials}>
                <div className="steam-credentials-heading">
                  <strong>{credentialsConfigured ? "Replace authorization" : "Connect match history"}</strong>
                  <a
                    href="https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Manage codes on Steam
                    <ExternalLink size={12} />
                  </a>
                </div>
                <label>
                  <span>Game Authentication Code</span>
                  <input
                    type="password"
                    value={gameAuthCode}
                    onChange={(event) => setGameAuthCode(event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="AAAA-AAAAA-AAAA"
                    required
                  />
                </label>
                <label>
                  <span>Initial Match Sharing Code (no more than 1 month old)</span>
                  <input
                    type="password"
                    value={initialMatchSharingCode}
                    onChange={(event) => setInitialMatchSharingCode(event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="CSGO-…"
                    required
                  />
                </label>
                <p>
                  Both codes go directly to the server and are never returned by the API or stored in browser storage.
                </p>
                <div className="steam-credentials-actions">
                  <button
                    className="primary-button compact-button"
                    type="submit"
                    disabled={busyAction !== null || !gameAuthCode.trim() || !initialMatchSharingCode.trim()}
                  >
                    {busyAction === "save" ? (
                      <Loader2 size={14} className="spin-icon" />
                    ) : (
                      <ShieldCheck size={14} />
                    )}
                    {busyAction === "save" ? "Saving" : "Save connection"}
                  </button>
                  {credentialsConfigured ? (
                    <button
                      className="secondary-button compact-button"
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => {
                        setEditingCredentials(false);
                        setGameAuthCode("");
                        setInitialMatchSharingCode("");
                      }}
                    >
                      Cancel
                    </button>
                  ) : null}
                </div>
              </form>
            ) : (
              <p className="steam-credentials-saved">
                <ShieldCheck size={14} />
                Authorization is encrypted server-side and is not exposed by this page.
              </p>
            )}
          </div>

          <div className="steam-matches-pane">
            <div className="steam-pane-heading">
              <div>
                <span>Discovered records</span>
                <strong>{matchDisplays.length} recent matches</strong>
              </div>
              <span className="steam-source-label">Steam match history</span>
            </div>

            {matchDisplays.length === 0 ? (
              <div className="steam-matches-empty">
                <Clock3 size={17} />
                <div>
                  <strong>No discovered matches yet</strong>
                  <p>Save both codes, then use Sync now. Manual .dem upload remains available.</p>
                </div>
              </div>
            ) : (
              <div className="steam-match-list" role="list">
                <div className="steam-match-list-head" aria-hidden="true">
                  <span>Discovery</span>
                  <span>Discovered</span>
                  <span>Status</span>
                  <span>Demo</span>
                </div>
                {matchDisplays.map((match) => (
                  <article className="steam-match-record" key={match.id} role="listitem">
                    <div>
                      <strong>Match record</strong>
                      <span>{match.sourceLabel}</span>
                    </div>
                    <time dateTime={match.discoveredAt}>{formatSteamDate(match.discoveredAt)}</time>
                    <span className={`steam-state-pill ${match.statusTone}`}>
                      {match.statusLabel}
                    </span>
                    <div className="steam-match-demo-action">
                      {match.demoHref ? (
                        <Link className="secondary-button compact-button" href={match.demoHref}>
                          <ExternalLink size={13} />
                          Open demo
                        </Link>
                      ) : (
                        <span>Not imported</span>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <footer className="steam-sync-footer">
        <span>
          <FileUp size={13} />
          Manual .dem upload remains the reliable fallback; match discovery does not depend on automatic Demo access.
        </span>
        <span>
          Disconnecting deletes saved codes and discovered records. Imported demos remain in the Demo Library.
        </span>
      </footer>
    </section>
  );
}

function requestErrorMessage(error: unknown, action: SteamRequestAction): string {
  return steamRequestErrorMessage(
    isApiError(error) ? error.status : null,
    action,
    isApiError(error) ? error.detailCode : null
  );
}

function formatSteamDate(value: string | null): string {
  if (!value) {
    return "Not yet";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown";
  }
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function disconnectedConnection(): SteamConnection {
  return {
    connected: false,
    status: "disconnected",
    credentials_configured: false,
    scheduled_sync_enabled: false,
    last_sync_started_at: null,
    last_sync_completed_at: null,
    next_retry_at: null,
    last_error_code: null,
    last_error_message: null
  };
}
