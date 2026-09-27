import type { ReactNode } from "react";

import { AppBrand } from "@/components/layout/AppBrand";
import { SiteFooter } from "@/components/layout/SiteFooter";

// Sign-in, callback, not-invited, error and not-found pages: the app's own top
// bar, then one panel with a header bar, so they read as the same product as the
// library; the privacy link and the Valve line underneath.
export function AuthShell({ children, beta = false }: { children: ReactNode; beta?: boolean }) {
  return (
    <main className="auth-shell">
      <header className="topbar auth-topbar">
        <AppBrand nav={false} />
        {beta ? <span className="auth-beta-tag">内测</span> : null}
      </header>
      <div className="auth-stage">{children}</div>
      <SiteFooter />
    </main>
  );
}

// The one panel on those pages: a header bar carrying the title, then the text and the actions.
export function AuthPanel({
  title,
  children,
  actions,
  role,
  live
}: {
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  role?: "alert";
  live?: boolean;
}) {
  return (
    <section className="panel auth-panel" role={role} aria-live={live ? "polite" : undefined}>
      <div className="panel-bar">
        <h1 className="panel-bar-title">{title}</h1>
      </div>
      <div className="auth-panel-body">
        {children}
        {actions ? <div className="auth-panel-actions">{actions}</div> : null}
      </div>
    </section>
  );
}
