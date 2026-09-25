"use client";

import Link from "next/link";
import { ArrowLeft, RefreshCw, TriangleAlert } from "lucide-react";

import { AuthShell } from "@/components/layout/AuthShell";

export default function AppError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <AuthShell>
      <section className="panel auth-panel" role="alert">
        <span className="auth-icon">
          <TriangleAlert size={20} aria-hidden="true" />
        </span>
        <div>
          <h1>页面出错了</h1>
          <p>页面加载时出现问题。可以重新加载，或返回我的比赛。</p>
        </div>
        <div className="auth-panel-actions">
          <button className="primary-button" type="button" onClick={() => reset()}>
            <RefreshCw size={16} aria-hidden="true" />
            重新加载
          </button>
          <Link className="secondary-button" href="/dashboard">
            <ArrowLeft size={16} aria-hidden="true" />
            返回我的比赛
          </Link>
        </div>
      </section>
    </AuthShell>
  );
}
