"use client";

import { ChevronsRight, Scissors } from "lucide-react";

import type { RenderJobStatus } from "@/lib/api";
import type { CoachingEvent } from "@/types/coaching";

interface CoachingEventCardProps {
  event: CoachingEvent;
  active: boolean;
  renderJob?: RenderJobStatus;
  clipRequesting: boolean;
  onSeek: (tick: number) => void;
  onGenerateClip: (event: CoachingEvent) => void;
}

export function CoachingEventCard({
  event,
  active,
  renderJob,
  clipRequesting,
  onSeek,
  onGenerateClip
}: CoachingEventCardProps) {
  const clipBusy = clipRequesting || renderJob?.status === "queued" || renderJob?.status === "rendering";

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
          <h3 className="event-title">{event.title}</h3>
          <ChevronsRight size={16} />
        </div>
        <p className="event-message">{event.message}</p>
        <div className="event-meta">
          <span className="mini-pill">R{event.round_number}</span>
          <span className="mini-pill">Tick {event.tick_start}</span>
          <span className="mini-pill">{event.category}</span>
          <span className="mini-pill">{event.severity}</span>
        </div>
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
          {clipRequesting ? "Queuing" : "Generate Clip"}
        </button>
      </div>
    </article>
  );
}
