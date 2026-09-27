"use client";

import { useEffect, useState, type ReactNode } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { AppBrand } from "@/components/layout/AppBrand";
import { AuthPanel, AuthShell } from "@/components/layout/AuthShell";

// Most session checks finish well inside this; only a slow one earns a message.
const CONNECTING_NOTICE_DELAY_MS = 400;

export function AuthBoundary({ children }: { children: ReactNode }) {
  const { provider, refreshSession, signIn, state } = useAuth();

  if (state.status === "authenticated") {
    return children;
  }
  if (state.status === "checking") {
    return <SessionCheckShell />;
  }

  const copy = authBoundaryCopy(state.status, provider);
  const signingIn = state.status === "anonymous" || state.status === "expired";

  return (
    <AuthShell beta>
      <AuthPanel
        title={copy.title}
        live
        actions={
          <>
            {signingIn ? (
              <button className="primary-button" type="button" onClick={() => signIn()}>
                {provider === "steam" ? "通过 Steam 登录" : "登录"}
              </button>
            ) : null}
            {state.status === "error" ? (
              <button className="secondary-button" type="button" onClick={() => void refreshSession()}>
                重新连接
              </button>
            ) : null}
          </>
        }
      >
        <p>{copy.message}</p>
        {signingIn && provider === "steam" ? (
          <p className="auth-note">
            将跳转到 Steam 官方页面（steamcommunity.com）登录。我们只会得到你的 Steam ID 和公开的昵称、头像，不会获得你的密码。
          </p>
        ) : null}
        {state.status === "error" && state.message ? (
          <details>
            <summary>查看错误详情</summary>
            <p>{state.message}</p>
          </details>
        ) : null}
      </AuthPanel>
    </AuthShell>
  );
}

// The page's own shell, empty, so a deep link does not flash a centered card
// before the workspace; the page itself stays withheld until the session is known.
function SessionCheckShell() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timeoutId = window.setTimeout(() => setSlow(true), CONNECTING_NOTICE_DELAY_MS);
    return () => window.clearTimeout(timeoutId);
  }, []);
  return (
    <main className="app-shell library-app session-check-shell">
      <header className="topbar">
        <AppBrand nav={false} />
      </header>
      <section className="page" aria-busy="true">
        <p className="session-check-notice" role="status">{slow ? "正在连接…" : ""}</p>
      </section>
    </main>
  );
}

function authBoundaryCopy(
  status: string,
  provider: "steam" | "oidc"
) {
  if (status === "expired") {
    return {
      title: "登录已过期",
      message: "请重新登录，继续复盘你的比赛。"
    };
  }
  if (status === "error") {
    return {
      title: "暂时无法连接",
      message: "网络连接中断，请检查网络后重新连接。"
    };
  }
  return {
    title: provider === "steam" ? "通过 Steam 登录" : "登录",
    message:
      provider === "steam"
        ? "上传 CS2 比赛录像（.dem），查看战术回放和复盘建议。用 Steam 账号登录后，比赛只有你自己能看到。"
        : "上传 CS2 比赛录像（.dem），查看战术回放和复盘建议。登录后打开你的比赛库。"
  };
}
