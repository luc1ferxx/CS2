"use client";

import { useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";

// The account as plain text at the right of the top bar, and a text link to sign out.
export function SessionControls() {
  const { signOut, state } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const account = state.account;
  const displayName = account?.provider === "development" && account.displayName === "Local development"
    ? "本地账户"
    : account?.displayName ?? "当前账户";

  return (
    <div className="session-controls">
      <span className="account-name" title={accountTitle(displayName, account?.provider)}>
        {displayName}
      </span>
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
