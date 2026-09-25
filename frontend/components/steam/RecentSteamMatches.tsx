"use client";

import Link from "next/link";
import {
  Clock3,
  Download,
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
  importSteamMatch,
  isApiError,
  listSteamMatches,
  saveSteamConnectionCredentials,
  syncSteamMatches
} from "@/lib/api";
import { formatLibraryDate } from "@/lib/demo-library";
import { mapDisplayName } from "@/lib/map-config";
import {
  buildSteamConnectionDisplay,
  buildSteamMatchDisplay,
  steamMatchImportAction,
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
  const [importingMatchId, setImportingMatchId] = useState<string | null>(null);
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
        errors.push("暂时无法读取最近的 Steam 比赛，请刷新重试。");
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

  useEffect(() => {
    const hasActiveImport = matches.some((match) =>
      ["demo_pending", "downloading", "parsing"].includes(match.status)
    );
    if (!hasActiveImport) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void loadSteamData();
    }, 2500);
    return () => window.clearInterval(intervalId);
  }, [loadSteamData, matches]);

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
      setActionError("请先填写两个授权码，再保存连接。");
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
      setNotice("授权已保存。授权码不会再显示在这个页面上。");
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
      "断开 Steam 比赛记录？已保存的授权码和已发现的比赛记录会被删除，已导入的比赛会保留在比赛库中。"
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
      setNotice("已断开连接，授权码和比赛记录已删除。已导入的比赛仍在比赛库中。");
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

  async function handleImport(matchId: string) {
    setImportingMatchId(matchId);
    setActionError(null);
    setNotice(null);
    try {
      const imported = await importSteamMatch(matchId);
      setMatches((current) =>
        current.map((match) => (match.id === imported.id ? imported : match))
      );
      if (imported.status === "ready" && imported.demo_id) {
        setNotice("比赛已导入，可以复盘了。");
      } else {
        setNotice("比赛已导入，正在排队处理。");
      }
      await loadSteamData();
    } catch (error) {
      setActionError(requestErrorMessage(error, "import"));
      await loadSteamData();
    } finally {
      setImportingMatchId(null);
    }
  }

  function openManualUpload() {
    document.getElementById("demo-upload-input")?.click();
  }

  return (
    <section className="steam-match-sync" aria-labelledby="recent-steam-matches-title">
      <header className="steam-sync-header">
        <div>
          <span className="workspace-kicker">Steam 比赛记录</span>
          <div className="steam-sync-title-row">
            <h2 id="recent-steam-matches-title">最近的 Steam 比赛</h2>
            {connectionDisplay ? (
              <span className={`steam-state-pill ${connectionDisplay.statusTone}`}>
                {connectionDisplay.statusLabel}
              </span>
            ) : null}
          </div>
          <p>
            连接后可以同步你在官方匹配中的比赛记录。当前版本还不能自动下载比赛录像，要复盘请手动上传 .dem 文件。
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
              更换授权码
            </button>
          ) : null}
          <button
            className="secondary-button compact-button"
            type="button"
            disabled={busyAction !== null}
            onClick={() => void handleRefresh()}
          >
            <RefreshCcw size={14} className={busyAction === "refresh" ? "spin-icon" : ""} />
            刷新
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
            {busyAction === "sync" ? "同步中…" : "立即同步"}
          </button>
          {connected ? (
            <button
              className="secondary-button compact-button steam-disconnect-button"
              type="button"
              disabled={busyAction !== null}
              onClick={() => void handleDisconnect()}
            >
              <Unplug size={14} />
              {busyAction === "disconnect" ? "正在断开…" : "断开连接"}
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
            重试
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
          正在读取 Steam 连接和最近的比赛…
        </div>
      ) : (
        <div className="steam-sync-layout">
          <div className="steam-connection-pane">
            <div className="steam-pane-heading">
              <div>
                <span>连接</span>
                <strong>{connectionDisplay?.statusLabel ?? "暂时无法读取"}</strong>
              </div>
              <span className="steam-schedule-label">
                {connectionDisplay?.scheduledSyncEnabled ? "已开启定时同步" : "未开启定时同步"}
              </span>
            </div>

            {connectionDisplay ? (
              <dl className="steam-connection-meta">
                <div>
                  <dt>上次同步</dt>
                  <dd>{formatSteamDate(connectionDisplay.lastSyncCompletedAt)}</dd>
                </div>
                <div>
                  <dt>上次尝试</dt>
                  <dd>{formatSteamDate(connectionDisplay.lastSyncStartedAt)}</dd>
                </div>
                <div>
                  <dt>下次重试</dt>
                  <dd>{formatSteamDate(connectionDisplay.nextRetryAt)}</dd>
                </div>
              </dl>
            ) : null}

            {connectionDisplay?.errorMessage ? (
              <p className="steam-connection-warning">{connectionDisplay.errorMessage}</p>
            ) : null}

            {connectionDisplay?.demoImportMessage ? (
              <div className="steam-import-boundary">
                <span>{connectionDisplay.demoImportMessage}</span>
                {connectionDisplay.manualUploadSupported ? (
                  <button
                    className="secondary-button compact-button"
                    type="button"
                    onClick={openManualUpload}
                  >
                    <FileUp size={13} />
                    手动上传 .dem
                  </button>
                ) : null}
              </div>
            ) : null}

            {connection === null ? (
              <p className="steam-connection-warning">
                暂时无法读取连接状态，请先刷新，再填写或更换授权码。
              </p>
            ) : showCredentialsForm ? (
              <form className="steam-credentials-form" onSubmit={handleSaveCredentials}>
                <div className="steam-credentials-heading">
                  <strong>{credentialsConfigured ? "更换授权" : "连接比赛记录"}</strong>
                  <a
                    href="https://help.steampowered.com/zh-cn/wizard/HelpWithGameIssue/?appid=730&issueid=128"
                    target="_blank"
                    rel="noreferrer"
                  >
                    在 Steam 获取授权码
                    <ExternalLink size={12} />
                  </a>
                </div>
                <label>
                  <span>游戏验证码（Game Authentication Code）</span>
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
                  <span>最近一场比赛的分享代码（一个月内）</span>
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
                  两个授权码只发送到服务器加密保存，不会再显示在页面上，也不会存进浏览器，只用来读取比赛记录。
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
                    {busyAction === "save" ? "正在保存…" : "保存连接"}
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
                      取消
                    </button>
                  ) : null}
                </div>
              </form>
            ) : (
              <p className="steam-credentials-saved">
                <ShieldCheck size={14} />
                授权已在服务器加密保存，这个页面不会显示授权码。
              </p>
            )}
          </div>

          <div className="steam-matches-pane">
            <div className="steam-pane-heading">
              <div>
                <span>已发现的比赛</span>
                <strong>最近 {matchDisplays.length} 场</strong>
              </div>
              <span className="steam-source-label">Steam 比赛记录</span>
            </div>

            {matchDisplays.length === 0 ? (
              <div className="steam-matches-empty">
                <Clock3 size={17} />
                <div>
                  <strong>还没有发现比赛</strong>
                  <p>保存两个授权码后点「立即同步」。随时都可以手动上传 .dem 文件。</p>
                </div>
              </div>
            ) : (
              <div className="steam-match-list" role="list">
                <div className="steam-match-list-head" aria-hidden="true">
                  <span>比赛</span>
                  <span>发现时间</span>
                  <span>状态</span>
                  <span>操作</span>
                </div>
                {matchDisplays.map((match) => {
                  const importAction = steamMatchImportAction(
                    match.status,
                    match.demoId,
                    connectionDisplay?.demoImportAvailable ?? false,
                    match.errorCode,
                    match.parserDispatchPending,
                    match.importRetryable
                  );
                  const isImporting = importingMatchId === match.id;
                  const parserSummary = [
                    match.durationLabel,
                    match.sideRoundsLabel,
                    match.playersLabel
                  ].filter((value): value is string => value !== null);
                  return (
                    <article className="steam-match-record" key={match.id} role="listitem">
                      <div>
                        <strong>{match.mapName ? mapDisplayName(match.mapName) : "比赛记录"}</strong>
                        <span>
                          {parserSummary.length > 0
                            ? parserSummary.join("，")
                            : match.sourceLabel}
                        </span>
                        {match.errorMessage ? (
                          <span className="steam-match-import-error">{match.errorMessage}</span>
                        ) : null}
                      </div>
                      <time dateTime={match.discoveredAt}>{formatSteamDate(match.discoveredAt)}</time>
                      <span className={`steam-state-pill ${match.statusTone}`}>
                        {match.statusLabel}
                      </span>
                      <div className="steam-match-demo-action">
                        {match.demoHref && match.status === "ready" ? (
                          <Link className="secondary-button compact-button" href={match.demoHref}>
                            <ExternalLink size={13} />
                            进入复盘
                          </Link>
                        ) : importAction.enabled ? (
                          <button
                            className="secondary-button compact-button"
                            type="button"
                            disabled={importingMatchId !== null || busyAction !== null}
                            onClick={() => void handleImport(match.id)}
                          >
                            {isImporting ? (
                              <Loader2 size={13} className="spin-icon" />
                            ) : (
                              <Download size={13} />
                            )}
                            {isImporting ? "正在开始…" : importAction.label}
                          </button>
                        ) : connectionDisplay?.manualUploadSupported &&
                          !connectionDisplay.demoImportAvailable &&
                          !match.demoId ? (
                          <button
                            className="secondary-button compact-button"
                            type="button"
                            onClick={openManualUpload}
                          >
                            <FileUp size={13} />
                            手动上传 .dem
                          </button>
                        ) : (
                          <span>{importAction.label}</span>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      <footer className="steam-sync-footer">
        <span>
          <FileUp size={13} />
          手动上传 .dem 始终可用；同步比赛记录不需要自动下载录像。
        </span>
        <span>
          断开连接会删除已保存的授权码和比赛记录，已导入的比赛会保留在比赛库中。
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

// Same zh-CN format as the library rows, whatever the browser's locale.
function formatSteamDate(value: string | null): string {
  return value ? formatLibraryDate(value) : "尚未同步";
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
    last_error_message: null,
    demo_import_available: false,
    demo_source_provider: "disabled",
    manual_upload_supported: true
  };
}
