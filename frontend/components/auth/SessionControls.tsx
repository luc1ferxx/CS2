"use client";

import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";

// The account name at the right of the top bar (a link to 账户与数据), and a text link to sign out.
export function SessionControls({ current }: { current?: "account" }) {
  const { signOut, state } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const account = state.account;
  const displayName = account?.provider === "development" && account.displayName === "Local development"
    ? "本地账户"
    : account?.displayName ?? "当前账户";

  return (
    <div className="session-controls">
      <Link
        className="account-name"
        href="/account"
        title={accountTitle(displayName, account?.provider)}
        aria-current={current === "account" ? "page" : undefined}
      >
        {displayName}
      </Link>
      {account?.provider !== "development" ? (
        <button
          className="topbar-link account-sign-out"
          type="button"
          disabled={signingOut}
          onClick={async () => {
            setSigningOut(true);
            await signOut();
            setSigningOut(false);
          }}
        >
          {signingOut ? "正在退出" : "退出登录"}
        </button>
      ) : null}
    </div>
  );
}

function accountTitle(name: string, provider: "steam" | "oidc" | "development" | undefined) {
  if (provider === "steam") {
    return `${name}（Steam 账号）`;
  }
  if (provider === "oidc") {
    return `${name}（OIDC 账号）`;
  }
  return name;
}
