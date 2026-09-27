"use client";

import Link from "next/link";

import { AppBrand } from "@/components/layout/AppBrand";

// A crash inside the review workspace keeps the topbar (with its 我的比赛 link)
// and a way back, instead of replacing the whole page.
export default function DemoDetailError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="app-shell review-detail-shell review-app">
      <header className="topbar">
        <AppBrand />
      </header>
      <section className="page">
        <section className="panel demo-error-panel" role="alert" aria-labelledby="demo-error-title">
          <div className="panel-bar">
            <h2 className="panel-bar-title" id="demo-error-title">复盘页面出错了</h2>
          </div>
          <div className="panel-body">
            <p>显示这场比赛时出现问题，可以重新加载复盘。</p>
            <div className="auth-panel-actions">
              <button className="primary-button" type="button" onClick={() => reset()}>
                重新加载
              </button>
              <Link href="/dashboard">返回我的比赛</Link>
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}
