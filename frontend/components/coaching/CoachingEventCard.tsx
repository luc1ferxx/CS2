"use client";

import { Fragment, memo } from "react";

import type { EvidenceSummaryItem, FeedbackSaveState, ReviewEvent } from "@/lib/coaching-review";
import type { RenderJobStatus } from "@/lib/api";
import { COACHING_VERDICT_LABELS, coachingCopy, coachingEvidenceLabel, coachingSeverityLabel } from "@/lib/coaching-copy";
import { isRenderActiveStatus } from "@/lib/demo-library";
import { playableClipVideo } from "@/lib/render-clips";
import type { CoachingEvent, CoachingVerdict } from "@/types/coaching";
import type { PlayerSide } from "@/types/replay";

const VERDICTS: CoachingVerdict[] = ["helpful", "irrelevant", "unsure"];

/** DOM id of a suggestion card; the review page scrolls back to it ("返回建议"). */
export function coachingCardId(eventId: string): string {
  return `coaching-event-${eventId}`;
}

interface CoachingEventCardProps {
  reviewEvent: ReviewEvent;
  active: boolean;
  inspected: boolean;
  // The m:ss round clock shown in the time column.
  clock?: string | null;
  // Where the card sits, spelled out ("第 2 回合 1:11"); the time column's tooltip.
  locationLabel?: string;
  // The reviewed player's side in this card's round, when known; colors the names.
  side?: PlayerSide | null;
  renderJob?: RenderJobStatus;
  clipRequesting: boolean;
  feedbackState?: FeedbackSaveState;
  onToggleInspect: (eventId: string) => void;
  onSeek: (tick: number, eventId: string) => void;
  onGenerateClip?: (event: CoachingEvent) => void;
  onFeedback: (event: CoachingEvent, verdict: CoachingVerdict | null) => void;
}

export const CoachingEventCard = memo(function CoachingEventCard({
  reviewEvent, active, inspected, clock, locationLabel, side, renderJob, clipRequesting, feedbackState,
  onToggleInspect, onSeek, onGenerateClip, onFeedback
}: CoachingEventCardProps) {
  const { event } = reviewEvent;
  const currentVerdict = event.feedback?.verdict ?? null;
  const copy = coachingCopy(event);
  const feed = reviewEvent.feed ?? { died: false, killer: null, finding: reviewEvent.facts ?? "" };
  const finding = feed.finding || copy.title;
  const evidence = reviewEvent.playerEvidence ?? [];
  const clipBusy = clipRequesting || isRenderActiveStatus(renderJob?.status);
  const clipReady = Boolean(playableClipVideo(renderJob));
  const clipFailed = renderJob?.status === "failed";
  const originalAction = event.structured_context_json.action;
  const originalLimitation = event.structured_context_json.limitation;
  const clipLabel = clipRequesting ? "等待生成" : clipReady ? "观看视频" :
    renderJob?.status === "rendering" || renderJob?.status === "processing" ? "生成中" :
      clipBusy ? "等待生成" : clipFailed ? "重试" : "生成视频";
  const cardId = coachingCardId(event.id);
  const inspectorId = `${cardId}-evidence`;
  const feedbackFailed = feedbackState?.status === "failed";
  const roundLabel = `第 ${event.round_number} 回合`;
  const ownSide = side ? `side-${side.toLowerCase()}` : "side-unknown";
  const otherSide = side ? `side-${side === "T" ? "ct" : "t"}` : "side-unknown";

  return (
    <article
      id={cardId}
      tabIndex={-1}
      className={`event-card evidence-ledger-item coaching-feed-row ${event.severity} ${active ? "active" : ""} ${inspected ? "inspected" : ""}`}
      aria-label={copy.title}
    >
      <span className="coaching-feed-clock" title={locationLabel ?? roundLabel}>{clock ?? roundLabel}</span>
      <div className="coaching-feed-main">
        <h3 className="event-title coaching-feed-line">
          {feed.died ? (
            <span className="coaching-feed-kill">
              {feed.killer ? <><span className={`coaching-feed-player ${otherSide}`}>{feed.killer}</span><span className="visually-hidden"> 击杀 </span></> : <span className="visually-hidden">阵亡：</span>}
              <span className="coaching-feed-cross" aria-hidden="true">✕</span>
              <span className={`coaching-feed-player reviewed ${ownSide}`}>{event.player_name}</span>
            </span>
          ) : null}
          <span className="coaching-feed-finding">{finding}</span>
          <span className="visually-hidden">，{coachingSeverityLabel(event.severity)}</span>
        </h3>
        {/* What to do next: shown on the row at the playhead or with its evidence open, not on every row. */}
        <p className="coaching-card-guidance">{copy.guidance}</p>
      </div>

      <div className="event-card-actions">
        <button
          className="text-button coaching-link locate-tick-button"
          type="button"
          onClick={() => onSeek(event.tick_start, event.id)}
          aria-label={`查看这一刻：${copy.title}`}
        >
          查看这一刻
        </button>
      </div>

      <div className="coaching-feed-body">
        <div className="coaching-feed-meta">
          <button
            className="coaching-evidence-toggle"
            type="button"
            onClick={() => onToggleInspect(event.id)}
            aria-expanded={inspected}
            aria-controls={inspectorId}
            aria-label={`${inspected ? "收起" : "查看"}依据：${copy.title}`}
          >
            {inspected ? "收起依据" : "查看依据"}
          </button>
          {onGenerateClip ? (
            <button
              className="text-button coaching-link generate-clip-button"
              type="button"
              onClick={() => onGenerateClip(event)}
              disabled={clipBusy}
              title={clipReady ? "播放已保存的视频" : clipFailed ? "重新生成这段视频" : "首次生成后保存，之后可以直接重播"}
            >
              {clipLabel}
            </button>
          ) : null}
          <div className="coaching-feedback" role="group" aria-label={`这条建议是否有帮助：${copy.title}`}
            aria-busy={feedbackState?.status === "saving" ? true : undefined}>
            <span>对你有帮助吗</span>
            {VERDICTS.map((verdict, index) => (
              <Fragment key={verdict}>
                {index > 0 ? <span className="coaching-verdict-divider" aria-hidden="true">/</span> : null}
                <button
                  className={`coaching-verdict ${currentVerdict === verdict ? "active" : ""}`}
                  type="button"
                  aria-pressed={currentVerdict === verdict}
                  onClick={() => onFeedback(event, currentVerdict === verdict ? null : verdict)}
                >
                  {COACHING_VERDICT_LABELS[verdict]}
                </button>
              </Fragment>
            ))}
          </div>
        </div>
        {/* Always mounted so a failed save is announced where the player tapped. */}
        <p className="coaching-feedback-status" role="status">
          {feedbackFailed ? (
            <>
              <span>评价没有保存。</span>
              <button className="text-button coaching-link" type="button" onClick={() => onFeedback(event, feedbackState.verdict)}>重新保存</button>
            </>
          ) : null}
        </p>

        {inspected ? (
          <div id={inspectorId} className="event-ledger-inspector">
            <p className="coaching-inspector-title">{copy.title}</p>
            {reviewEvent.facts && reviewEvent.facts !== finding ? <p className="coaching-card-facts">{reviewEvent.facts}</p> : null}
            {copy.limitation ? (
              <div className="coaching-guidance limitation"><strong>判断边界</strong><p>{copy.limitation}</p></div>
            ) : null}
            <dl className="coaching-inspector-facts">
              <div><dt>玩家</dt><dd>{event.player_name || "未知"}</dd></div>
              {reviewEvent.ruleLabel ? <div><dt>规则</dt><dd>{reviewEvent.ruleLabel}</dd></div> : null}
              {reviewEvent.involvedPlayers?.length ? (
                <div><dt>相关玩家</dt><dd>{reviewEvent.involvedPlayers.join("、")}</dd></div>
              ) : null}
              {evidence.map((item) => (
                <div key={`${event.id}-${item.label}`}>
                  <dt>{coachingEvidenceLabel(item.label)}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
            {clipFailed ? (
              <p className="coaching-clip-error">{onGenerateClip ? "视频生成失败，可以点击重试。" : "视频生成失败。"}</p>
            ) : null}
            <details className="coaching-technical-details">
              <summary>技术详情</summary>
              {event.message ? <p>{event.message}</p> : null}
              {typeof originalAction === "string" && originalAction !== copy.guidance ? <p>{originalAction}</p> : null}
              {typeof originalLimitation === "string" && originalLimitation !== copy.limitation ? <p>{originalLimitation}</p> : null}
              <dl className="event-evidence">
                {technicalDetails(reviewEvent).map((item) => (
                  <div key={`${event.id}-technical-${item.label}`} className="event-evidence-chip">
                    <dt>{item.label}</dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </div>
        ) : null}
      </div>
    </article>
  );
});

// Raw identifiers support staff and rule tuning need; never on the card face.
function technicalDetails({ event, ruleId }: ReviewEvent): EvidenceSummaryItem[] {
  const context = event.structured_context_json ?? {};
  const items: EvidenceSummaryItem[] = [];
  const push = (label: string, value: unknown) => {
    const text = Array.isArray(value) ? value.slice(0, 6).join(", ") : value === undefined || value === null ? "" : String(value);
    if (text) items.push({ label, value: text });
  };
  push("规则 ID", ruleId);
  push("建议 ID", event.id);
  push("Tick", event.tick_start === event.tick_end || event.tick_end === undefined ? event.tick_start : `${event.tick_start}–${event.tick_end}`);
  push("证据 tick", context.evidenceTicks);
  push("关联事件", context.relatedEventIds);
  push("炸弹事件 tick", context.bombTick);
  return items;
}
