import Link from "next/link";

// The top bar's left side: the plain wordmark, then the one nav link. Sign-in,
// callback and session-check shells pass nav={false}: the wordmark only.
export function AppBrand({ nav = true, current }: { nav?: boolean; current?: "library" | "account" }) {
  return (
    <div className="brand">
      <span className="brand-wordmark">CS2 复盘</span>
      {nav ? (
        <nav className="topbar-nav" aria-label="主导航">
          <Link href="/dashboard" aria-current={current === "library" ? "page" : undefined}>我的比赛</Link>
        </nav>
      ) : null}
    </div>
  );
}
