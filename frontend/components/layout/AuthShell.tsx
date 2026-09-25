import type { ReactNode } from "react";

import { AppBrand } from "@/components/layout/AppBrand";

// Sign-in, callback, not-invited, error and not-found pages: the app's own top
// bar, then one quiet panel, so they read as the same product as the library.
export function AuthShell({ children, beta = false }: { children: ReactNode; beta?: boolean }) {
  return (
    <main className="auth-shell">
      <header className="topbar auth-topbar">
        <AppBrand />
        {beta ? <span className="auth-beta-tag">内测</span> : null}
      </header>
      <div className="auth-stage">{children}</div>
    </main>
  );
}
