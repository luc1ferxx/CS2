"use client";

import Link from "next/link";
import { useState } from "react";

import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { useAuth } from "@/components/auth/AuthProvider";
import { SessionControls } from "@/components/auth/SessionControls";
import { ConfirmDialog } from "@/components/feedback/ConfirmDialog";
import { AppBrand } from "@/components/layout/AppBrand";
import { AuthPanel, AuthShell } from "@/components/layout/AuthShell";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { deleteAccount, isApiError } from "@/lib/api";
import type { AuthAccount } from "@/lib/auth";
import { cancelDemoUpload, getDemoUploadSnapshot } from "@/lib/demo-upload";
import { clearPlayerPreferences } from "@/lib/personal-review";
import { userFacingError } from "@/lib/user-errors";

const CONFIRM_TEXT = "删除账户";

export default function AccountPage() {
  // Held above the boundary: once the account is gone the session is too, and the
  // page says so instead of turning into the sign-in wall.
  const [deleted, setDeleted] = useState(false);
  if (deleted) {
    return <AccountDeletedPanel />;
  }
  return (
    <AuthBoundary>
      <AccountContent onDeleted={() => setDeleted(true)} />
    </AuthBoundary>
  );
}

function AccountContent({ onDeleted }: { onDeleted: () => void }) {
  const { markSignedOut, state } = useAuth();
  const account = state.account;
  const development = account?.provider === "development";
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    // An upload still running would only be refused once the account is gone.
    if (getDemoUploadSnapshot()) cancelDemoUpload();
    try {
      await deleteAccount();
    } catch (err) {
      // A 401 is not a deletion: the session ran out, and the sign-in wall takes over.
      setDeleting(false);
      setDeleteError(accountDeletionError(err));
      return;
    }
    clearPlayerPreferences(() => window.localStorage);
    onDeleted();
    markSignedOut();
  }

  return (
    <main className="app-shell account-app">
      <header className="topbar">
        <AppBrand current="account" />
        <div className="topbar-actions">
          <SessionControls current="account" />
        </div>
      </header>

      <div className="page account-page">
        <h1 className="visually-hidden">账户与数据</h1>

        <section className="panel account-panel" aria-labelledby="account-title">
          <div className="panel-bar">
            <h2 className="panel-bar-title" id="account-title">账户</h2>
          </div>
          <dl className="account-facts">
            <div><dt>昵称</dt><dd>{accountName(account)}</dd></div>
            <div><dt>登录方式</dt><dd>{providerLabel(account)}</dd></div>
            {account?.provider === "steam" && account.steamId ? (
              <div><dt>SteamID64</dt><dd className="num-id">{account.steamId}</dd></div>
            ) : null}
          </dl>
        </section>

        <section className="panel account-panel" aria-labelledby="account-data-title">
          <div className="panel-bar">
            <h2 className="panel-bar-title" id="account-data-title">数据</h2>
          </div>
          <div className="account-panel-body">
            <p>网站为你保存：</p>
            <ul>
              <li>你上传的比赛：原始 .dem 文件、解析出的回放数据和复盘建议，以及你对建议的评价和改过的比赛名。</li>
              <li>账户资料：SteamID64、Steam 公开昵称和头像地址。</li>
              <li>Steam 比赛记录（只在你主动关联后）：加密保存的游戏验证码和比赛分享码。</li>
              <li>这个浏览器里：你在比赛里选的玩家。</li>
            </ul>
            {development ? null : (
              <p>另外，邀请名单（你的 SteamID64）由站长保存在服务器配置里，不属于账户数据；如需移出名单，请联系站长。</p>
            )}
            <p>
              单场比赛可以在<Link href="/dashboard">我的比赛</Link>里归档或永久删除。完整说明见<Link href="/privacy">隐私说明</Link>。
            </p>
          </div>
        </section>

        <section className="panel account-panel account-danger" aria-labelledby="account-delete-title">
          <div className="panel-bar">
            <h2 className="panel-bar-title" id="account-delete-title">删除账户</h2>
          </div>
          {development ? (
            <div className="account-panel-body">
              <p>账户删除只在使用 Steam 登录时提供。本地开发账户没有账户记录，这里不提供一键清空，以免误删本地的比赛库。</p>
              <p>可以在<Link href="/dashboard">我的比赛</Link>里逐场删除比赛。</p>
            </div>
          ) : (
            <div className="account-panel-body">
              <p>永久删除你的账户和全部数据：</p>
              <ul>
                <li>所有比赛，包括 .dem 文件、回放数据、复盘建议和你的评价</li>
                <li>Steam 比赛记录的关联</li>
                <li>账户资料（SteamID64、昵称和头像地址）</li>
              </ul>
              <p>所有设备上的登录会同时退出。此操作无法撤销；每日备份里的副本最多再保留 30 天后自动清除。</p>
              <p>删除账户不会把你移出邀请名单：之后用同一个 Steam 账号登录，会得到一个全新的空账户。如需移出名单，请联系站长。</p>
              {deleteError && !confirming ? <p className="account-error" role="alert">{deleteError}</p> : null}
              <div className="account-actions">
                <button
                  className="danger-button"
                  type="button"
                  onClick={() => {
                    setDeleteError(null);
                    setConfirming(true);
                  }}
                >
                  删除账户…
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
      <SiteFooter />

      <ConfirmDialog
        open={confirming}
        title="删除账户和全部数据？"
        confirmLabel="删除账户"
        busy={deleting}
        busyLabel="正在删除…"
        error={deleteError}
        requireText={CONFIRM_TEXT}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          setConfirming(false);
          setDeleteError(null);
        }}
      >
        <p>你的所有比赛、复盘建议和评价、Steam 关联和账户资料会被永久删除，所有设备上的登录会退出。</p>
        <p className="confirm-dialog-note">此操作无法撤销。</p>
      </ConfirmDialog>
    </main>
  );
}

// The public page shown once the account is gone: no session, no workspace.
function AccountDeletedPanel() {
  return (
    <AuthShell>
      <AuthPanel
        title="账户已删除"
        live
        actions={
          <>
            <Link href="/privacy">查看隐私说明</Link>
            <Link href="/dashboard">返回登录页</Link>
          </>
        }
      >
        <p>你的账户和全部数据已从网站删除，所有设备上的登录已退出。</p>
        <p className="auth-note">每日备份里的副本会在 30 天内自动清除。这个浏览器里保存的玩家选择也已清除。</p>
        <p className="auth-note">你的 SteamID64 仍在站长的邀请名单里；如需移出，请联系站长。</p>
      </AuthPanel>
    </AuthShell>
  );
}

function accountName(account: AuthAccount | undefined): string {
  if (!account) return "当前账户";
  if (account.provider === "development" && account.displayName === "Local development") return "本地账户";
  return account.displayName;
}

function providerLabel(account: AuthAccount | undefined): string {
  if (account?.provider === "steam") return "Steam";
  if (account?.provider === "oidc") return "OIDC";
  return "本地开发账户";
}

function accountDeletionError(err: unknown): string {
  if (isApiError(err) && err.detailCode === "account_deletion_unavailable") {
    return "这个环境不提供账户删除。可以在「我的比赛」里逐场删除比赛。";
  }
  return userFacingError(err, "删除账户失败，请稍后再试。");
}
