"use client";

import { CircleUserRound, LogOut } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";

export function SessionControls() {
  const { signOut, state } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const account = state.account;

  return (
    <div className="session-controls">
      <span className="account-chip" title={account?.displayName ?? "Current account"}>
        {account?.avatarUrl ? (
          <span
            aria-hidden="true"
            className="account-avatar account-avatar-image"
            style={{ backgroundImage: `url("${account.avatarUrl}")` }}
          />
        ) : (
          <span className="account-avatar" aria-hidden="true">
            <CircleUserRound size={15} />
          </span>
        )}
        <span className="account-copy">
          <strong>{account?.displayName ?? "Current account"}</strong>
          <small>{accountProviderLabel(account?.provider)}</small>
        </span>
      </span>
      {account?.provider !== "development" ? (
        <button
          aria-label="Sign out"
          className="secondary-button compact-button account-sign-out"
          type="button"
          disabled={signingOut}
          onClick={async () => {
            setSigningOut(true);
            await signOut();
            setSigningOut(false);
          }}
        >
          <LogOut size={14} />
          {signingOut ? "Signing out" : "Sign out"}
        </button>
      ) : null}
    </div>
  );
}

function accountProviderLabel(provider: "steam" | "oidc" | "development" | undefined) {
  if (provider === "steam") {
    return "Steam";
  }
  if (provider === "oidc") {
    return "OIDC";
  }
  return "Local account";
}
