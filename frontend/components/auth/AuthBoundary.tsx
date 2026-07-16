"use client";

import { LogIn, RefreshCw, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/AuthProvider";

export function AuthBoundary({ children }: { children: ReactNode }) {
  const { refreshSession, signIn, state } = useAuth();

  if (state.status === "authenticated") {
    return children;
  }

  const copy = authBoundaryCopy(state.status, state.message);

  return (
    <main className="auth-shell">
      <section className="panel auth-panel" aria-live="polite">
        <span className="auth-icon">
          <ShieldCheck size={24} />
        </span>
        <div>
          <h1>{copy.title}</h1>
          <p>{copy.message}</p>
        </div>
        {state.status === "anonymous" || state.status === "expired" ? (
          <button className="primary-button" type="button" onClick={() => signIn()}>
            <LogIn size={16} />
            Sign in
          </button>
        ) : null}
        {state.status === "error" ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => void refreshSession()}
          >
            <RefreshCw size={16} />
            Retry session check
          </button>
        ) : null}
      </section>
    </main>
  );
}

function authBoundaryCopy(status: string, message?: string) {
  if (status === "checking") {
    return {
      title: "Checking your session",
      message: "Confirming access to your private demo library."
    };
  }
  if (status === "expired") {
    return {
      title: "Session expired",
      message: "Sign in again to continue reviewing your private demos."
    };
  }
  if (status === "error") {
    return {
      title: "Session check unavailable",
      message: message || "The authentication service could not be reached."
    };
  }
  return {
    title: "Sign in to CS2 Demo Coach",
    message: "Your demo library, replay data, coaching, and media are private."
  };
}
