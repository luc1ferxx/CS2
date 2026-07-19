"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { sanitizeReturnTo } from "@/lib/auth";

export default function AuthCallbackPage() {
  const router = useRouter();
  const { provider, refreshSession, signIn } = useAuth();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
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
