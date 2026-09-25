import { CircleHelp } from "lucide-react";

// Where a first-time player gets the file the library needs. Kept generic:
// install paths differ between machines and are not verified here.
export function DemFileHelp({ open = false, quotaNote = null }: { open?: boolean; quotaNote?: string | null }) {
  return (
    <details className="library-dem-help" open={open}>
      <summary>
        <CircleHelp size={14} aria-hidden="true" />
        在哪里找到 .dem？
      </summary>
      <ul>
        <li>官方匹配：在 CS2 的「观看」页面下载比赛回放，下载的 .dem 文件保存在 CS2 的游戏安装目录里。</li>
        <li>第三方平台：在比赛详情页下载录像。压缩包（.zip、.rar、.gz、.bz2 等）请先解压，再上传里面的 .dem 文件。</li>
        <li>一场比赛的 .dem 通常有 100–400 MB，上传需要几分钟，期间请保持页面打开。</li>
        {quotaNote ? <li>{quotaNote}</li> : null}
      </ul>
    </details>
  );
}
