"use client";

import { Pause, Play } from "lucide-react";

import { timelineMarkersForRound } from "@/lib/coaching-review";
import { timelineParserEventMarkersForRound } from "@/lib/replay-events";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayEvent, ReplayRound } from "@/types/replay";
import { RoundSelector } from "./RoundSelector";

interface TimelineProps {
  currentTick: number;
  selectedRound: number;
  rounds: ReplayRound[];
  speed: number;
  playing: boolean;
  events: CoachingEvent[];
  parserEvents?: ReplayEvent[];
  onSeek: (tick: number) => void;
  onTogglePlay: () => void;
  onSpeedChange: (speed: number) => void;
  onRoundChange: (roundNumber: number) => void;
}

const SPEEDS = [0.5, 1, 2, 4];

export function Timeline({
  currentTick,
  selectedRound,
  rounds,
  speed,
  playing,
  events,
  parserEvents = [],
  onSeek,
  onTogglePlay,
  onSpeedChange,
  onRoundChange
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

  return (
    <section className="panel timeline-panel" aria-label="Replay timeline">
      <div className="timeline-controls">
        <div className="transport">
          <button
            className="icon-button"
            type="button"
            onClick={onTogglePlay}
            disabled={!hasRounds}
            aria-label={playing ? "Pause replay" : "Play replay"}
          >
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <span className="tick-readout">Tick {Math.round(currentTick)}</span>
        </div>

        <select
          className="speed-select"
          value={speed}
          onChange={(event) => onSpeedChange(Number(event.target.value))}
          aria-label="Playback speed"
        >
          {SPEEDS.map((speedValue) => (
            <option key={speedValue} value={speedValue}>
              {speedValue}x
            </option>
          ))}
        </select>

        <div className="range-wrap">
          {hasRounds ? (
            <>
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
              <div className="event-markers" aria-label="Coaching event markers">
                {markers.map((marker) => (
                  <button
                    key={marker.event.id}
                    className={`event-marker ${marker.event.severity}`}
                    style={{ left: `${marker.leftPercent}%` }}
                    type="button"
                    onClick={() => onSeek(marker.event.tick_start)}
                    aria-label={`Jump to ${marker.event.severity} coaching event at tick ${marker.event.tick_start}`}
                    title={`${marker.event.title} at tick ${marker.event.tick_start}`}
                  />
                ))}
              </div>
              <div className="parser-event-markers" aria-label="Parser event markers">
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
            </>
          ) : (
            <div className="timeline-empty-state">No round timeline is available for this replay.</div>
          )}
        </div>

        <RoundSelector
          rounds={rounds}
          selectedRound={selectedRound}
          onSelectRound={onRoundChange}
        />
      </div>
    </section>
  );
}
