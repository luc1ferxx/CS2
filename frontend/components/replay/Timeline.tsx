"use client";

import type { CSSProperties } from "react";

import { timelineMarkersForRound } from "@/lib/coaching-review";
import { timelineParserEventMarkersForRound } from "@/lib/replay-events";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayEvent, ReplayRound } from "@/types/replay";

interface TimelineProps {
  currentTick: number;
  selectedRound: number;
  rounds: ReplayRound[];
  events: CoachingEvent[];
  parserEvents?: ReplayEvent[];
  onSeek: (tick: number) => void;
}

export function Timeline({
  currentTick,
  selectedRound,
  rounds,
  events,
  parserEvents = [],
  onSeek
}: TimelineProps) {
  const round = rounds.find((item) => item.roundNumber === selectedRound) ?? rounds[0];
  const minTick = round?.startTick ?? 0;
  const maxTick = round?.endTick ?? 0;
  const markers = timelineMarkersForRound(events, selectedRound, minTick, maxTick);
  const parserEventMarkers = timelineParserEventMarkersForRound(
    parserEvents,
    selectedRound,
    minTick,
    maxTick
  );
  const hasRounds = rounds.length > 0;
  const currentTickPercent = hasRounds
    ? Math.max(0, Math.min(100, ((currentTick - minTick) / Math.max(1, maxTick - minTick)) * 100))
    : 0;
  const timelineStyle = { "--timeline-current-tick": `${currentTickPercent}%` } as CSSProperties;

  return (
    <section className="timeline-panel evidence-timeline" aria-label="Replay timeline">
      <div className="timeline-heading">
        <div>
          <span>Evidence timeline</span>
          <strong>{parserEventMarkers.length} parser events · {markers.length} findings</strong>
        </div>
      </div>

      {hasRounds ? (
        <div className="timeline-lane-stack" style={timelineStyle}>
          <div className="timeline-lane-labels" aria-hidden="true">
            <div className="timeline-lane-label">
              <span>Round</span>
              <small>R{selectedRound}</small>
            </div>
            <div className="timeline-lane-label">
              <span>Parser</span>
              <small>{parserEventMarkers.length}</small>
            </div>
            <div className="timeline-lane-label">
              <span>Coaching</span>
              <small>{markers.length}</small>
            </div>
          </div>
          <div className="timeline-lane-tracks">
            <div className="timeline-current-spine" aria-hidden="true">
              <span>Tick {Math.round(currentTick)}</span>
            </div>
            <div className="timeline-lane-track round-lane">
              <button
                className="round-boundary-marker start"
                type="button"
                onClick={() => onSeek(minTick)}
                aria-label={`Jump to round ${selectedRound} start at tick ${minTick}`}
                title={`Round start at tick ${minTick}`}
              >
                Start
              </button>
              <span className="round-duration-track" aria-hidden="true" />
              <button
                className="round-boundary-marker end"
                type="button"
                onClick={() => onSeek(maxTick)}
                aria-label={`Jump to round ${selectedRound} end at tick ${maxTick}`}
                title={`Round end at tick ${maxTick}`}
              >
                End
              </button>
            </div>
            <div className="timeline-lane-track parser-lane parser-event-markers" aria-label="Parser event markers">
              {parserEventMarkers.map((marker) => (
                <button
                  key={marker.event.id}
                  className={`parser-event-marker ${marker.presentation.tone}`}
                  style={{ left: `${marker.leftPercent}%` }}
                  type="button"
                  onClick={() => onSeek(marker.seekTick)}
                  aria-label={`Jump to ${marker.presentation.label} parser event at tick ${marker.seekTick}`}
                  title={`${marker.event.label} at tick ${marker.seekTick}`}
                >
                  {marker.presentation.shortLabel}
                </button>
              ))}
            </div>
            <div className="timeline-lane-track coaching-lane event-markers" aria-label="Coaching event markers">
              {markers.map((marker) => (
                <button
                  key={marker.event.id}
                  className={`event-marker ${marker.event.severity}`}
                  style={{ left: `${marker.leftPercent}%` }}
                  type="button"
                  onClick={() => onSeek(marker.event.tick_start)}
                  aria-label={`Jump to ${marker.event.severity} coaching event at tick ${marker.event.tick_start}`}
                  title={`${marker.event.title} at tick ${marker.event.tick_start}`}
                >
                  <span className="visually-hidden">{marker.event.title}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="timeline-empty-state">No round timeline is available for this replay.</div>
      )}

      {hasRounds ? (
        <div className="timeline-scrubber">
          <input
            className="timeline-slider"
            type="range"
            min={minTick}
            max={maxTick}
            step={1}
            value={Math.min(maxTick, Math.max(minTick, currentTick))}
            onChange={(event) => onSeek(Number(event.target.value))}
            aria-label="Seek replay"
          />
        </div>
      ) : null}
    </section>
  );
}
