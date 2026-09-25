"use client";

import { ChevronDown, Crosshair, Play, Video } from "lucide-react";

import type { ReviewEvent } from "@/lib/coaching-review";
import type { RenderJobStatus } from "@/lib/api";
import { COACHING_VERDICT_LABELS, coachingCopy, coachingEvidenceLabel, coachingSeverityLabel } from "@/lib/coaching-copy";
import { isRenderActiveStatus } from "@/lib/demo-library";
import { playableClipVideo } from "@/lib/render-clips";
import type { CoachingEvent, CoachingVerdict } from "@/types/coaching";

const VERDICTS: CoachingVerdict[] = ["helpful", "irrelevant", "unsure"];

interface CoachingEventCardProps {
  reviewEvent: ReviewEvent;
  active: boolean;
  inspected: boolean;
  locationLabel?: string;
  renderJob?: RenderJobStatus;
  clipRequesting: boolean;
  onToggleInspect: () => void;
  onSeek: (tick: number) => void;
  onGenerateClip?: (event: CoachingEvent) => void;
  onFeedback: (event: CoachingEvent, verdict: CoachingVerdict | null) => void;
}

export function CoachingEventCard({
  reviewEvent, active, inspected, locationLabel, renderJob, clipRequesting,
  onToggleInspect, onSeek, onGenerateClip, onFeedback
}: CoachingEventCardProps) {
  const { event } = reviewEvent;
  const currentVerdict = event.feedback?.verdict ?? null;
  const copy = coachingCopy(event);
  const clipBusy = clipRequesting || isRenderActiveStatus(renderJob?.status);
  const clipReady = Boolean(playableClipVideo(renderJob));
  const clipFailed = renderJob?.status === "failed";
  const originalAction = event.structured_context_json.action;
  const originalLimitation = event.structured_context_json.limitation;
  const clipLabel = clipRequesting ? "等待生成" : clipReady ? "观看视频" :
    renderJob?.status === "rendering" || renderJob?.status === "processing" ? "生成中" :
      clipBusy ? "等待生成" : clipFailed ? "重试" : "生成视频";
  const inspectorId = `coaching-event-${event.id}-evidence`;

  return (
    <article
      className={`event-card evidence-ledger-item ${event.severity} ${active ? "active" : ""} ${inspected ? "inspected" : ""}`}
      aria-label={copy.title}
    >
      <div className="coaching-card-summary">
        <div className="coaching-card-location">
          <span title="从回合开始计时">{locationLabel ?? `第 ${event.round_number} 回合`}</span>
          <span className={`event-severity-pill ${event.severity}`}>{coachingSeverityLabel(event.severity)}</span>
        </div>
        <h3 className="event-title">{copy.title}</h3>
        <p className="coaching-card-guidance">{copy.guidance}</p>
      </div>

      <div className="event-card-actions">
        <button
          className="primary-button compact-button locate-tick-button"
          type="button"
          onClick={() => onSeek(event.tick_start)}
          aria-label={`查看这一刻：${copy.title}`}
        >
          <Crosshair size={14} aria-hidden="true" />
          查看这一刻
        </button>
        {onGenerateClip ? (
          <button
            className="secondary-button compact-button generate-clip-button"
            type="button"
            onClick={() => onGenerateClip(event)}
            disabled={clipBusy}
            title={clipReady ? "播放已保存的视频" : clipFailed ? "重新生成这段视频" : "首次生成后保存，之后可以直接重播"}
          >
            {clipReady ? <Play size={14} aria-hidden="true" /> : <Video size={14} aria-hidden="true" />}
            {clipLabel}
          </button>
        ) : null}
      </div>

      <div className="coaching-feedback" role="group" aria-label={`这条建议是否有帮助：${copy.title}`}>
        <span>对你有帮助吗</span>
        {VERDICTS.map((verdict) => (
          <button
            key={verdict}
            className={`filter-button ${currentVerdict === verdict ? "active" : ""}`}
            type="button"
            aria-pressed={currentVerdict === verdict}
            onClick={() => onFeedback(event, currentVerdict === verdict ? null : verdict)}
          >
            {COACHING_VERDICT_LABELS[verdict]}
          </button>
        ))}
      </div>

      <button
        className="coaching-evidence-toggle"
        type="button"
        onClick={onToggleInspect}
        aria-expanded={inspected}
        aria-controls={inspectorId}
        aria-label={`${inspected ? "收起" : "查看"}依据：${copy.title}`}
      >
        {inspected ? "收起依据" : "查看依据"}
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {inspected ? (
        <div id={inspectorId} className="event-ledger-inspector">
          <p className="event-message">{event.message}</p>
          {copy.limitation ? (
            <div className="coaching-guidance limitation"><strong>判断边界</strong><p>{copy.limitation}</p></div>
          ) : null}
          {(typeof originalAction === "string" && originalAction !== copy.guidance) ||
            (typeof originalLimitation === "string" && originalLimitation !== copy.limitation) ? (
              <details className="coaching-original-context">
                <summary>原始分析记录</summary>
                {typeof originalAction === "string" ? <p>{originalAction}</p> : null}
                {typeof originalLimitation === "string" ? <p>{originalLimitation}</p> : null}
              </details>
            ) : null}
          <div className="event-meta">
            {event.structured_context_json.assessment === "review_candidate" ? <span className="mini-pill">待复盘线索</span> : null}
            <span className="mini-pill">选手 {event.player_name || event.player_id || "未知"}</span>
            <span className="mini-pill">规则 {reviewEvent.ruleId}</span>
            <span className="mini-pill">证据 tick {event.tick_start}</span>
          </div>
          <div className="event-involved">
            <span>相关选手</span>
            <strong>{reviewEvent.involvedPlayers.length > 0 ? reviewEvent.involvedPlayers.join("、") : "未知"}</strong>
          </div>
          {reviewEvent.evidence.length > 0 ? (
            <dl className="event-evidence">
              {reviewEvent.evidence.map((item) => (
                <div key={`${event.id}-${item.label}`} className="event-evidence-chip">
                  <dt>{coachingEvidenceLabel(item.label)}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {clipFailed ? (
            <p className="coaching-clip-error">{onGenerateClip ? "视频生成失败，可以点击重试。" : "视频生成失败。"}</p>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
