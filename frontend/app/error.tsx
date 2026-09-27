"use client";

import Link from "next/link";

import { AuthPanel, AuthShell } from "@/components/layout/AuthShell";

export default function AppError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <AuthShell>
      <AuthPanel
        title="页面出错了"
        role="alert"
        actions={
          <>
            <button className="primary-button" type="button" onClick={() => reset()}>
              重新加载
            </button>
            <Link href="/dashboard">返回我的比赛</Link>
          </>
        }
      >
        <p>页面加载时出现问题。可以重新加载，或返回我的比赛。</p>
      </AuthPanel>
    </AuthShell>
  );
}
