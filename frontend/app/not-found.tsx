import Link from "next/link";

import { AuthPanel, AuthShell } from "@/components/layout/AuthShell";

export default function NotFound() {
  return (
    <AuthShell>
      <AuthPanel title="找不到这个页面" actions={<Link href="/dashboard">返回我的比赛</Link>}>
        <p>链接可能有误，或页面已被移除。</p>
      </AuthPanel>
    </AuthShell>
  );
}
