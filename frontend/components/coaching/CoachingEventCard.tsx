"use client";

import { ChevronDown, Crosshair, Scissors } from "lucide-react";

import type { ReviewEvent } from "@/lib/coaching-review";
import type { RenderJobStatus } from "@/lib/api";
import { isRenderActiveStatus } from "@/lib/demo-library";
import type { CoachingEvent } from "@/types/coaching";

interface CoachingEventCardProps {
  reviewEvent: ReviewEvent;
  active: boolean;
  inspected: boolean;
  renderJob?: RenderJobStatus;
  clipRequesting: boolean;
  onToggleInspect: () => void;
  onSeek: (tick: number) => void;
  onGenerateClip: (event: CoachingEvent) => void;
}

export function CoachingEventCard({
  reviewEvent,
  active,
  inspected,
  renderJob,
  clipRequesting,
  onToggleInspect,
  onSeek,
  onGenerateClip
}: CoachingEventCardProps) {
  const { event } = reviewEvent;
  const clipBusy = clipRequesting || isRenderActiveStatus(renderJob?.status);
  const primaryEvidence = reviewEvent.evidence[0];
  const inspectorId = `coaching-event-${event.id}-evidence`;

  return (
    <article
      className={`event-card evidence-ledger-item ${event.severity} ${active ? "active" : ""} ${inspected ? "inspected" : ""}`}
    >
      <button
        className="event-card-seek event-card-inspect evidence-ledger-seek evidence-ledger-inspect"
        type="button"
        onClick={onToggleInspect}
        aria-expanded={inspected}
        aria-controls={inspectorId}
        aria-label={`${inspected ? "Hide" : "Inspect"} evidence for ${event.title} at tick ${event.tick_start}`}
        title={`${inspected ? "Hide" : "Inspect"} evidence without changing the review tick`}
      >
        <div className="evidence-ledger-leading">
          <span className={`event-severity-pill ${event.severity}`}>{event.severity}</span>
          <div className="event-title-block">
            <h3 className="event-title">{event.title}</h3>
            <span className="event-rule-label">{reviewEvent.ruleLabel}</span>
          </div>
        </div>
        <div className="evidence-ledger-coordinates">
          <span>R{event.round_number}</span>
          <span>Tick {event.tick_start}</span>
          <span className="event-rule-id">{reviewEvent.ruleId}</span>
          {primaryEvidence ? (
            <span className="event-ledger-evidence-preview">
              {primaryEvidence.label}: {primaryEvidence.value}
            </span>
          ) : null}
        </div>
        <ChevronDown size={16} className="event-ledger-seek-icon" aria-hidden="true" />
      </button>
      {inspected ? (
        <div id={inspectorId} className="event-ledger-inspector">
          <p className="event-message">{event.message}</p>
          <div className="event-meta">
            <span className="mini-pill">{event.category}</span>
            <span className="mini-pill">Evidence tick {event.tick_start}</span>
          </div>
          <div className="event-involved">
            <span>Players</span>
            <strong>
              {reviewEvent.involvedPlayers.length > 0
                ? reviewEvent.involvedPlayers.join(", ")
                : "Unknown"}
            </strong>
          </div>
          {reviewEvent.evidence.length > 0 ? (
            <dl className="event-evidence">
              {reviewEvent.evidence.map((item) => (
                <div key={`${event.id}-${item.label}`} className="event-evidence-chip">
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          <div className="event-card-actions">
            {renderJob ? (
              <span
                className={`mini-pill clip-job-pill ${renderJob.status}`}
                title={renderJob.error_message ?? `Clip job ${renderJob.status}`}
              >
                Clip {renderJob.status}
              </span>
            ) : null}
            <button
              className="secondary-button compact-button locate-tick-button"
              type="button"
              onClick={() => onSeek(event.tick_start)}
              title={`Locate the shared review coordinate at tick ${event.tick_start}`}
            >
              <Crosshair size={14} aria-hidden="true" />
              Locate at Tick {event.tick_start}
            </button>
            <button
              className="secondary-button compact-button generate-clip-button"
              type="button"
              onClick={() => onGenerateClip(event)}
              disabled={clipBusy}
              title="Create a first-person clip job for this coaching event"
            >
              <Scissors size={14} />
              {clipRequesting ? "Queuing" : "Generate Clip for this event"}
            </button>
          </div>
        </div>
      ) : renderJob ? (
        <div className="event-ledger-render-state">
          <span
            className={`mini-pill clip-job-pill ${renderJob.status}`}
            title={renderJob.error_message ?? `Clip job ${renderJob.status}`}
          >
            Clip {renderJob.status}
          </span>
        </div>
      ) : null}
    </article>
  );
}
