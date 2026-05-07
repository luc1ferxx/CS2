"use client";

import { ChevronsRight } from "lucide-react";

import type { CoachingEvent } from "@/types/coaching";

interface CoachingEventCardProps {
  event: CoachingEvent;
  active: boolean;
  onSeek: (tick: number) => void;
}

export function CoachingEventCard({
  event,
  active,
  onSeek
}: CoachingEventCardProps) {
  return (
    <button
      className={`event-card ${event.severity} ${active ? "active" : ""}`}
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
  );
}
