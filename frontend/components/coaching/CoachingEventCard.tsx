"use client";

import { ChevronsRight, Scissors } from "lucide-react";

import type { ReviewEvent } from "@/lib/coaching-review";
import type { RenderJobStatus } from "@/lib/api";
import { isRenderActiveStatus } from "@/lib/demo-library";
import type { CoachingEvent } from "@/types/coaching";

interface CoachingEventCardProps {
  reviewEvent: ReviewEvent;
  active: boolean;
  renderJob?: RenderJobStatus;
  clipRequesting: boolean;
  onSeek: (tick: number) => void;
  onGenerateClip: (event: CoachingEvent) => void;
}

export function CoachingEventCard({
  reviewEvent,
  active,
  renderJob,
  clipRequesting,
  onSeek,
  onGenerateClip
}: CoachingEventCardProps) {
  const { event } = reviewEvent;
  const clipBusy = clipRequesting || isRenderActiveStatus(renderJob?.status);

  return (
    <article
      className={`event-card ${event.severity} ${active ? "active" : ""}`}
    >
      <button
        className="event-card-seek"
        type="button"
        onClick={() => onSeek(event.tick_start)}
        title={`Jump to tick ${event.tick_start}`}
      >
        <div className="event-card-top">
          <div className="event-title-block">
            <span className={`event-severity-pill ${event.severity}`}>{event.severity}</span>
            <h3 className="event-title">{event.title}</h3>
          </div>
          <ChevronsRight size={16} />
        </div>
        <div className="event-rule-row">
          <span className="event-rule-label">{reviewEvent.ruleLabel}</span>
          <span className="event-rule-id">{reviewEvent.ruleId}</span>
        </div>
        <p className="event-message">{event.message}</p>
        <div className="event-meta">
          <span className="mini-pill">R{event.round_number}</span>
          <span className="mini-pill">Tick {event.tick_start}</span>
          <span className="mini-pill">{event.category}</span>
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
      </button>
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
    </article>
  );
}
