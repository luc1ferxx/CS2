import Link from "next/link";

export const VALVE_DISCLAIMER = "本站与 Valve Corporation 无关联。Counter-Strike、CS2 和 Steam 是 Valve 的商标。";

// The small line under every page: the privacy link and the Valve disclaimer.
export function SiteFooter({ current }: { current?: "privacy" }) {
  return (
    <footer className="site-footer">
      <Link href="/privacy" aria-current={current === "privacy" ? "page" : undefined}>隐私说明</Link>
      <span>{VALVE_DISCLAIMER}</span>
    </footer>
  );
}
