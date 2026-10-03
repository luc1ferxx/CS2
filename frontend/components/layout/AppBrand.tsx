import Link from "next/link";

// The top bar's left side: the plain wordmark, then the one nav link. Sign-in,
// callback and session-check shells pass nav={false}: the wordmark only. A page
// inside the library (the demo page) passes `trail`: the nav link becomes the
// breadcrumb "我的比赛 › <trail>", the trail being the current page.
export function AppBrand({ nav = true, current, trail }: {
  nav?: boolean;
  current?: "library" | "account";
  trail?: string;
}) {
  return (
    <div className="brand">
      <span className="brand-wordmark">CS2 复盘</span>
      {nav && trail !== undefined ? (
        <nav className="topbar-nav topbar-trail" aria-label="当前位置">
          <Link href="/dashboard">我的比赛</Link>
          <span className="topbar-trail-separator" aria-hidden="true">›</span>
          <span className="topbar-trail-current" aria-current="page" title={trail}>{trail}</span>
        </nav>
      ) : nav ? (
        <nav className="topbar-nav" aria-label="主导航">
          <Link href="/dashboard" aria-current={current === "library" ? "page" : undefined}>我的比赛</Link>
        </nav>
      ) : null}
    </div>
  );
}
