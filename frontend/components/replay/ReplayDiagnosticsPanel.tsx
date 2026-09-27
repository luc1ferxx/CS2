"use client";

import { memo } from "react";

import type { ReplayDetailDiagnostics } from "@/lib/replay-diagnostics";

export const ReplayDiagnosticsPanel = memo(function ReplayDiagnosticsPanel({ diagnostics }: { diagnostics: ReplayDetailDiagnostics }) {
  return (
    <section className="panel replay-diagnostics-panel" aria-label="回放数据检查">
      <div className="panel-bar replay-diagnostics-header">
        <h2 className="panel-bar-title">回放数据检查</h2>
        <span className={`panel-bar-meta replay-contract-version ${diagnostics.normalizedLegacy ? "legacy" : "current"}`}
          title="回放数据版本">
          {diagnostics.contractVersion}
        </span>
      </div>
      <p className="replay-diagnostics-state">
        {diagnostics.normalizedLegacy
          ? "旧版或不完整的回放数据，已整理后用于复盘"
          : "回放数据已载入"}
      </p>
      <div className="replay-diagnostics-grid">
        <DiagnosticMetric label="解析事件" value={diagnostics.counts.parserEvents} />
        <DiagnosticMetric label="建议" value={diagnostics.counts.coachingEvents} />
        <DiagnosticMetric label="回合" value={diagnostics.counts.rounds} />
        <DiagnosticMetric label="玩家" value={diagnostics.counts.players} />
        <DiagnosticMetric label="位置帧" value={diagnostics.counts.frames} />
        <DiagnosticMetric label="回放内视频" value={renderStateLabel(diagnostics.renderState.label)} tone={diagnostics.renderState.tone} />
      </div>
      {diagnostics.warnings.length > 0 ? (
        <ul className="replay-diagnostics-warnings">
          {diagnostics.warnings.map((warning) => (
            <li key={warning}>{diagnosticWarning(warning)}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
});

function DiagnosticMetric({
  label,
  value,
  tone
}: {
  label: string;
  value: number | string;
  tone?: ReplayDetailDiagnostics["renderState"]["tone"];
}) {
  return (
    <div className={`diagnostic-metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

const WARNINGS: Record<string, string> = {
  "legacy replay contract normalized at load time.": "旧版回放数据已在载入时整理。",
  "No rounds are available; quick jumps and timeline range are limited.": "没有回合数据，回合跳转和时间轴范围受限。",
  "No frames are available; tactical map and mock first-person replay are paused.": "没有位置帧，战术回放无法播放。",
  "No parser events are available; parser markers and event-derived round metrics are empty.": "没有解析事件，时间轴事件标记和回合统计为空。",
  "No coaching events are available for this replay.": "这场比赛没有建议。"
};

// The diagnostics helper speaks English; known lines are shown in Chinese, anything new as-is.
function diagnosticWarning(warning: string): string {
  if (Object.prototype.hasOwnProperty.call(WARNINGS, warning)) return WARNINGS[warning];
  const missing = /^Missing optional fields: (.+)\.$/.exec(warning);
  if (missing) return `缺少可选字段：${missing[1]}。`;
  const malformed = /^Malformed optional fields ignored: (.+)\.$/.exec(warning);
  if (malformed) return `已忽略格式错误的可选字段：${malformed[1]}。`;
  return warning;
}

function renderStateLabel(label: string): string {
  if (label === "Render failed") return "生成失败";
  if (label === "Render ready") return "已就绪";
  if (label === "Render not requested") return "未生成";
  const active = /^Render (.+)$/.exec(label);
  return active ? `进行中（${active[1]}）` : label;
}
