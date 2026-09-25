"use client";

import Link from "next/link";
import { ArrowLeft, CircleAlert, RefreshCw } from "lucide-react";

import { AppBrand } from "@/components/layout/AppBrand";

// A crash inside the review workspace keeps the topbar and a way back, instead
// of replacing the whole page.
export default function DemoDetailError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="app-shell review-detail-shell review-app">
      <header className="topbar">
        <AppBrand />
        <div className="topbar-actions">
          <Link className="secondary-button" href="/dashboard">
            <ArrowLeft size={16} aria-hidden="true" />
            我的比赛
          </Link>
        </div>
      </header>
      <section className="page">
        <section className="panel demo-state-card failed" role="alert" aria-labelledby="demo-error-title">
          <div className="demo-state-heading">
            <CircleAlert size={20} aria-hidden="true" />
            <div>
              <h2 id="demo-error-title">复盘页面出错了</h2>
              <p>显示这场比赛时出现问题，可以重新加载复盘。</p>
            </div>
          </div>
          <div className="demo-state-actions">
            <button className="primary-button compact-button" type="button" onClick={() => reset()}>
              <RefreshCw size={14} aria-hidden="true" />
              重新加载
            </button>
            <Link className="secondary-button compact-button" href="/dashboard">返回我的比赛</Link>
          </div>
        </section>
      </section>
    </main>
  );
}
