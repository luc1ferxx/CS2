"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { AuthPanel, AuthShell } from "@/components/layout/AuthShell";
import { sanitizeReturnTo } from "@/lib/auth";

// Optional: where an uninvited player can ask for access. Hidden when unset.
const BETA_CONTACT_URL = process.env.NEXT_PUBLIC_BETA_CONTACT_URL?.trim() || null;

export default function AuthCallbackPage() {
  const router = useRouter();
  const { provider, refreshSession, signIn, state } = useAuth();
  const [failed, setFailed] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [notInvited, setNotInvited] = useState(false);

  const finishSignIn = useCallback(async (isCancelled: () => boolean = () => false) => {
    const authenticated = await refreshSession();
    if (isCancelled()) {
      return;
    }
    if (authenticated) {
      const params = new URLSearchParams(window.location.search);
      router.replace(sanitizeReturnTo(params.get("return_to")));
    } else {
      setFailed(true);
    }
  }, [refreshSession, router]);

  useEffect(() => {
    // The API sends an account outside the beta allowlist here without a
    // session; signing in again would only land on the same answer.
    if (new URLSearchParams(window.location.search).get("error") === "not_invited") {
      setNotInvited(true);
      return;
    }

    let cancelled = false;
    void finishSignIn(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [finishSignIn]);

  if (notInvited) {
    return (
      <AuthShell>
        <AuthPanel
          title="暂未开放"
          live
          actions={
            <>
              <button className="primary-button" type="button" onClick={() => signIn("/dashboard")}>
                使用其他 Steam 账号登录
              </button>
              {BETA_CONTACT_URL ? (
                <a href={BETA_CONTACT_URL} target="_blank" rel="noreferrer">
                  申请内测资格
                </a>
              ) : null}
            </>
          }
        >
          <p>这个 Steam 账号还没有获得内测资格。</p>
          <p>如果受邀的是另一个 Steam 账号，请先在 Steam 网站退出当前账号，再用受邀账号重新登录。</p>
        </AuthPanel>
      </AuthShell>
    );
  }

  // A network failure is not a failed sign-in: the session may well exist.
  const unreachable = failed && state.status === "error";

  return (
    <AuthShell>
      <AuthPanel
        title={unreachable ? "暂时无法连接" : failed ? "登录没有完成" : "正在完成登录…"}
        live
        actions={
          unreachable ? (
            <button
              className="primary-button"
              type="button"
              disabled={retrying}
              onClick={async () => {
                setRetrying(true);
                await finishSignIn();
                setRetrying(false);
              }}
            >
              {retrying ? "正在重试…" : "重试"}
            </button>
          ) : failed ? (
            <button className="primary-button" type="button" onClick={() => signIn("/dashboard")}>
              {provider === "steam" ? "重新通过 Steam 登录" : "重新登录"}
            </button>
          ) : null
        }
      >
        <p>
          {unreachable
            ? "网络连接中断，还不能确认登录状态。请检查网络后重试。"
            : failed
              ? "没有获得有效的登录状态，请重新登录。"
              : "正在确认登录状态，马上回到你的比赛。"}
        </p>
      </AuthPanel>
    </AuthShell>
  );
}
