"use client";

import type { CoachingEvent } from "@/types/coaching";
import type { RenderJobStatus } from "@/lib/api";
import { CoachingEventCard } from "./CoachingEventCard";

interface CoachingPanelProps {
  events: CoachingEvent[];
  currentTick: number;
  selectedRound: number;
  renderJobByEventId: Map<string, RenderJobStatus>;
  requestingEventId: string | null;
  onSeek: (tick: number) => void;
  onGenerateClip: (event: CoachingEvent) => void;
}

export function CoachingPanel({
  events,
  currentTick,
  selectedRound,
  renderJobByEventId,
  requestingEventId,
  onSeek,
  onGenerateClip
}: CoachingPanelProps) {
  const roundEvents = events.filter((event) => event.round_number === selectedRound);
  const activeEventIds = new Set(
    roundEvents
      .filter((event) => currentTick >= event.tick_start - 128 && currentTick <= event.tick_end + 128)
      .map((event) => event.id)
  );
  const activeCount = activeEventIds.size;

  return (
    <aside className="panel coaching-panel" aria-label="Coaching panel">
      <div className="coaching-header">
        <h2>Coaching</h2>
        <span className="mini-pill">
          {activeCount > 0 ? `${activeCount} current` : "No current flag"}
        </span>
      </div>
      <div className="coaching-body">
        {roundEvents.length === 0 ? (
          <div className="loading-panel">No coaching events in this round.</div>
        ) : (
          roundEvents.map((event) => (
            <CoachingEventCard
              key={event.id}
              event={event}
              active={activeEventIds.has(event.id)}
              renderJob={renderJobByEventId.get(event.id)}
              clipRequesting={requestingEventId === event.id}
              onSeek={onSeek}
              onGenerateClip={onGenerateClip}
            />
          ))
        )}
      </div>
    </aside>
  );
}
