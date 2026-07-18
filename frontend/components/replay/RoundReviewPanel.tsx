"use client";

import { Bomb, Crosshair, Flag, TimerReset } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import {
  buildRoundReviewModel,
  jumpTargetsForRound,
  type RoundJumpTarget
} from "@/lib/round-review";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayData } from "@/types/replay";

interface RoundReviewPanelProps {
  replay: ReplayData;
  coachingEvents: CoachingEvent[];
  currentTick: number;
  selectedRound: number;
  onSelectRound: (roundNumber: number) => void;
  onSeek: (tick: number) => void;
}

export function RoundReviewPanel({
  replay,
  coachingEvents,
  currentTick,
  selectedRound,
  onSelectRound,
  onSeek
}: RoundReviewPanelProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const roundButtonRefs = useRef(new Map<number, HTMLButtonElement>());
  const model = useMemo(
    () =>
      buildRoundReviewModel({
        rounds: replay.rounds,
        parserEvents: replay.events ?? [],
        coachingEvents,
        currentTick,
        selectedRoundNumber: selectedRound,
        tickRate: replay.tickRate
      }),
    [coachingEvents, currentTick, replay.events, replay.rounds, replay.tickRate, selectedRound]
  );
  const selectedSummary = model.selectedRound;
  const jumpTargets = jumpTargetsForRound(selectedSummary);

  useEffect(() => {
    const track = trackRef.current;
    const selectedButton = roundButtonRefs.current.get(selectedRound);
    if (!track || !selectedButton) {
      return;
    }

    const trackRect = track.getBoundingClientRect();
    const itemRect = selectedButton.getBoundingClientRect();
    const edgePadding = 8;
    let nextScrollLeft: number | null = null;

    if (itemRect.left < trackRect.left + edgePadding) {
      nextScrollLeft = Math.max(
        0,
        track.scrollLeft + itemRect.left - trackRect.left - edgePadding
      );
    } else if (itemRect.right > trackRect.right - edgePadding) {
      nextScrollLeft = Math.max(
        0,
        track.scrollLeft + itemRect.right - trackRect.right + edgePadding
      );
    }

    if (nextScrollLeft !== null) {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      track.scrollTo({
        left: nextScrollLeft,
        behavior: reducedMotion ? "auto" : "smooth"
      });
    }
  }, [selectedRound]);

  function handleRoundKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement>,
    roundIndex: number
  ) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowLeft") {
      nextIndex = Math.max(0, roundIndex - 1);
    } else if (event.key === "ArrowRight") {
      nextIndex = Math.min(model.rounds.length - 1, roundIndex + 1);
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = model.rounds.length - 1;
    }

    if (nextIndex === null || nextIndex < 0) {
      return;
    }

    event.preventDefault();
    if (nextIndex === roundIndex) {
      return;
    }
    const nextRound = model.rounds[nextIndex];
    roundButtonRefs.current.get(nextRound.roundNumber)?.focus();
    onSelectRound(nextRound.roundNumber);
  }

  return (
    <section className="round-ribbon-panel" aria-label="Round review">
      <div className="round-ribbon-heading">
        <div>
          <span className="workspace-kicker">Match narrative</span>
          <h2>Round rail</h2>
        </div>
        {selectedSummary ? (
          <div className="round-ribbon-state" aria-label={`Selected round ${selectedSummary.roundNumber} state`}>
            <span className="round-ribbon-selected">R{selectedSummary.roundNumber}</span>
            <span className={`round-outcome-pill ${selectedSummary.winnerSide.toLowerCase()}`}>
              {selectedSummary.winnerSide} won
            </span>
            <span className="round-ribbon-state-detail">
              {selectedSummary.coachingEventCount} coaching · {selectedSummary.killCount} kills
            </span>
          </div>
        ) : null}
      </div>

      {selectedSummary ? (
        <div className="round-ribbon-detail">
          <div className="round-ribbon-metrics" aria-label={`Round ${selectedSummary.roundNumber} evidence summary`}>
            <RoundRibbonMetric label="Start" value={selectedSummary.startTick} />
            <RoundRibbonMetric label="Live" value={selectedSummary.freezeEndTick} />
            <RoundRibbonMetric label="First kill" value={formatEventReference(selectedSummary.firstKill.tick, selectedSummary.firstKill.playerName)} />
            <RoundRibbonMetric label="Plant" value={formatEventReference(selectedSummary.bombPlant.tick, selectedSummary.bombPlant.site)} />
            <RoundRibbonMetric label="End" value={selectedSummary.endTick} />
          </div>
          <div className="round-ribbon-actions" aria-label="Round quick jumps">
            {jumpTargets.map((target) => (
              <button
                key={target.id}
                className="round-ribbon-jump"
                type="button"
                disabled={!target.available || target.tick === null}
                onClick={() => {
                  if (target.tick !== null) {
                    onSeek(target.tick);
                  }
                }}
                title={target.tick === null ? `${target.label} unavailable` : `Jump to tick ${target.tick}`}
              >
                <JumpTargetIcon target={target} />
                <span>{target.label}</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="round-review-empty">
          <strong>No round data</strong>
          <span>Round summaries and quick jumps are unavailable for this replay contract.</span>
        </div>
      )}

      <div ref={trackRef} className="round-ribbon-track" aria-label="Round rail">
        {model.rounds.map((round, roundIndex) => (
          <button
            key={round.roundNumber}
            ref={(element) => {
              if (element) {
                roundButtonRefs.current.set(round.roundNumber, element);
              } else {
                roundButtonRefs.current.delete(round.roundNumber);
              }
            }}
            className={`round-ribbon-item ${round.isSelected ? "selected" : ""} ${
              round.isCurrent ? "current" : ""
            } ${round.coachingEventCount > 0 ? "has-coaching" : ""}`}
            type="button"
            onClick={() => onSelectRound(round.roundNumber)}
            onKeyDown={(event) => handleRoundKeyDown(event, roundIndex)}
            aria-current={round.isCurrent ? "step" : undefined}
            aria-pressed={round.isSelected}
            tabIndex={round.isSelected ? 0 : -1}
            aria-label={`Round ${round.roundNumber}, ${round.winnerSide} won, ${round.coachingEventCount} coaching events${round.isCurrent ? ", current playback round" : ""}${round.isSelected ? ", selected" : ""}`}
            title={`Select round ${round.roundNumber}: ${round.killCount} kills, ${round.bombEventCount} bomb events, ${round.utilityEventCount} utility events, ${round.coachingEventCount} coaching events`}
          >
            <div className="round-ribbon-item-top">
              <strong>R{round.roundNumber}</strong>
              <span className={`round-side-chip ${round.winnerSide.toLowerCase()}`}>
                {round.winnerSide}
              </span>
            </div>
            <div className="round-ribbon-event-line">
              <span>K {round.killCount}</span>
              <span>B {round.bombEventCount}</span>
              <span>U {round.utilityEventCount}</span>
            </div>
            <div className="round-ribbon-review-count">
              <span aria-hidden="true" />
              <strong>{round.coachingEventCount}</strong>
              <small>review</small>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function RoundRibbonMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="round-ribbon-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function JumpTargetIcon({ target }: { target: RoundJumpTarget }) {
  if (target.id === "round_start") {
    return <Flag size={14} aria-hidden="true" />;
  }
  if (target.id === "live_start") {
    return <TimerReset size={14} aria-hidden="true" />;
  }
  if (target.id === "first_kill") {
    return <Crosshair size={14} aria-hidden="true" />;
  }
  return <Bomb size={14} aria-hidden="true" />;
}

function formatEventReference(tick: number | null, detail?: string | null) {
  if (tick === null) {
    return "-";
  }
  return detail ? `${tick} / ${detail}` : String(tick);
}
