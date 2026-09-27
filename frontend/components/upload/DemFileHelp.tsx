"use client";

import type { KeyboardEvent } from "react";

// Where a first-time player gets the file the library needs. Kept generic:
// install paths differ between machines and are not verified here.
// `popover` floats the list under the link (toolbar); otherwise it opens in place.
export function DemFileHelp({
  open = false,
  popover = false,
  quotaNote = null
}: {
  open?: boolean;
  popover?: boolean;
  quotaNote?: string | null;
}) {
  return (
    <details
      className={popover ? "lib-help lib-help-pop" : "lib-help"}
      open={open}
      onKeyDown={popover ? closeOnEscape : undefined}
    >
      <summary>在哪里找到 .dem？</summary>
      <ul className={popover ? "popover" : undefined}>
        <li>官方匹配：在 CS2 的「观看」页面下载比赛回放，下载的 .dem 文件保存在 CS2 的游戏安装目录里。</li>
        <li>第三方平台：在比赛详情页下载录像。压缩包（.zip、.rar、.gz、.bz2 等）请先解压，再上传里面的 .dem 文件。</li>
        <li>一场比赛的 .dem 通常有 100–400 MB，上传需要几分钟，期间请保持页面打开。</li>
        {quotaNote ? <li>{quotaNote}</li> : null}
      </ul>
    </details>
  );
}

function closeOnEscape(event: KeyboardEvent<HTMLDetailsElement>) {
  if (event.key === "Escape" && event.currentTarget.open) {
    event.currentTarget.open = false;
    event.currentTarget.querySelector("summary")?.focus();
  }
}
