"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { sanitizeReturnTo } from "@/lib/auth";

export default function AuthCallbackPage() {
  const router = useRouter();
  const { provider, refreshSession, signIn } = useAuth();
  const [failed, setFailed] = useState(false);
  const [notInvited, setNotInvited] = useState(false);

  useEffect(() => {
    // The API sends an account outside the beta allowlist here without a
    // session; signing in again would only land on the same answer.
    if (new URLSearchParams(window.location.search).get("error") === "not_invited") {
      setNotInvited(true);
      return;
    }

    let cancelled = false;

    async function finishSignIn() {
      const authenticated = await refreshSession();
      if (cancelled) {
        return;
      }
      if (authenticated) {
        const params = new URLSearchParams(window.location.search);
        router.replace(sanitizeReturnTo(params.get("return_to")));
      } else {
        setFailed(true);
      }
    }

    void finishSignIn();
    return () => {
      cancelled = true;
    };
  }, [refreshSession, router]);

  if (notInvited) {
    return (
      <main className="auth-shell">
        <section className="panel auth-panel" aria-live="polite">
          <h1>暂未开放</h1>
          <p>这个 Steam 账号还没有获得内测资格。如需参加内测，请联系我们。</p>
          <Link className="secondary-button" href="/">
            返回首页
          </Link>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-shell">
      <section className="panel auth-panel" aria-live="polite">
        <h1>{failed ? "Sign-in could not be completed" : "Completing sign-in"}</h1>
        <p>
          {failed
            ? "The callback did not create a valid session. Start the sign-in flow again."
            : "Confirming your private session and returning to the requested review."}
        </p>
        {failed ? (
          <button
            className="primary-button"
            type="button"
            onClick={() => signIn("/dashboard")}
          >
            {provider === "steam" ? "Sign in with Steam again" : "Sign in again"}
          </button>
        ) : null}
      </section>
    </main>
  );
}
