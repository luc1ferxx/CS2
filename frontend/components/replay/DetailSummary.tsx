"use client";

import { memo } from "react";

import type { DetailSummaryItem } from "@/lib/demo-library";

// The summary items arrive with English keys and some English status values;
// the strip shows them in Chinese and leaves anything it does not know as-is.
const LABELS: Record<string, string> = {
  File: "文件",
  Map: "地图",
  Calibration: "地图校准",
  Rounds: "回合",
  Coaching: "全场复盘线索",
  Parser: "处理状态",
  Media: "视频",
  Render: "视频生成"
};

const VALUES: Record<string, string> = {
  unknown: "未知",
  ready: "已完成",
  calibrated: "已校准",
  approximate: "近似校准",
  fallback: "未校准（通用坐标）",
  "replay unavailable": "未载入回放",
  "not requested": "未生成"
};

const VIDEO_SOURCES: Record<string, string> = { mock: "无视频", manual: "手动上传", rendered: "已生成视频", render: "已生成视频", render_clip: "视频片段" };
const VIDEO_STATES: Record<string, string> = {
  pending: "未生成", queued: "排队中", rendering: "生成中", processing: "处理中", ready: "可播放", completed: "已完成", failed: "失败"
};

export function detailSummaryLabel(label: string): string {
  return Object.prototype.hasOwnProperty.call(LABELS, label) ? LABELS[label] : label;
}

export function detailSummaryValue(label: string, value: string): string {
  if (Object.prototype.hasOwnProperty.call(VALUES, value)) return VALUES[value];
  if (label === "Coaching") {
    const count = /^(\d+) events?$/.exec(value);
    if (count) return `${count[1]} 条`;
  }
  if (label === "Media" || label === "Render") {
    const [source, state, ...rest] = value.split(" ");
    if (rest.length === 0 && Object.prototype.hasOwnProperty.call(VIDEO_SOURCES, source) &&
      Object.prototype.hasOwnProperty.call(VIDEO_STATES, state)) {
      if (source === "mock") return state === "pending" ? "无视频" : VIDEO_STATES[state];
      return `${VIDEO_SOURCES[source]}，${VIDEO_STATES[state]}`;
    }
  }
  return value;
}

export const DetailSummary = memo(function DetailSummary({ items }: { items: DetailSummaryItem[] }) {
  return (
    <section className="detail-summary-strip" aria-label="比赛状态摘要">
      {items.map((item) => (
        <div className={`detail-summary-item ${item.tone ?? "default"}`} key={item.label}>
          <span>{detailSummaryLabel(item.label)}</span>
          <strong>{detailSummaryValue(item.label, item.value)}</strong>
          {item.detail ? <small>{item.detail}</small> : null}
        </div>
      ))}
    </section>
  );
});
