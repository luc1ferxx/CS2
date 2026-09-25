import Link from "next/link";
import { ArrowLeft, SearchX } from "lucide-react";

import { AuthShell } from "@/components/layout/AuthShell";

export default function NotFound() {
  return (
    <AuthShell>
      <section className="panel auth-panel">
        <span className="auth-icon">
          <SearchX size={20} aria-hidden="true" />
        </span>
        <div>
          <h1>找不到这个页面</h1>
          <p>链接可能有误，或页面已被移除。</p>
        </div>
        <Link className="primary-button" href="/dashboard">
          <ArrowLeft size={16} aria-hidden="true" />
          返回我的比赛
        </Link>
      </section>
    </AuthShell>
  );
}
