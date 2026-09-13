"use client";

import { LogIn, RefreshCw, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/AuthProvider";

export function AuthBoundary({ children }: { children: ReactNode }) {
  const { provider, refreshSession, signIn, state } = useAuth();

  if (state.status === "authenticated") {
    return children;
  }

  const copy = authBoundaryCopy(state.status, provider);

  return (
    <main className="auth-shell">
      <section className="panel auth-panel" aria-live="polite">
        <span className="auth-icon">
          <ShieldCheck size={24} />
        </span>
        <div>
          <h1>{copy.title}</h1>
          <p>{copy.message}</p>
          {state.status === "error" && state.message ? (
            <details>
              <summary>查看错误详情</summary>
              <p>{state.message}</p>
            </details>
          ) : null}
        </div>
        {state.status === "anonymous" || state.status === "expired" ? (
          <button className="primary-button" type="button" onClick={() => signIn()}>
            <LogIn size={16} />
            {provider === "steam" ? "通过 Steam 登录" : "登录"}
          </button>
        ) : null}
        {state.status === "error" ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => void refreshSession()}
          >
            <RefreshCw size={16} />
            重新连接
          </button>
        ) : null}
      </section>
    </main>
  );
}

function authBoundaryCopy(
  status: string,
  provider: "steam" | "oidc"
) {
  if (status === "checking") {
    return {
      title: "正在打开比赛库",
      message: "正在连接，请稍候。"
    };
  }
  if (status === "expired") {
    return {
      title: "登录已过期",
      message: "请重新登录，继续复盘你的比赛。"
    };
  }
  if (status === "error") {
    return {
      title: "暂时无法连接",
      message: "请确认应用已启动，然后重新连接。"
    };
  }
  return {
    title: provider === "steam" ? "通过 Steam 登录" : "登录",
    message:
      provider === "steam"
        ? "连接 Steam 账户，查看属于你的比赛和复盘记录。"
        : "登录账户，打开你的比赛库。"
  };
}
