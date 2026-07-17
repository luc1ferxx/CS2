"use client";

import { Bomb, Crosshair, Flag, TimerReset } from "lucide-react";
import { useMemo } from "react";

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

  return (
    <section className="round-ribbon-panel" aria-label="Round review">
      <div className="round-ribbon-heading">
        <div>
          <span className="workspace-kicker">Match narrative</span>
          <h2>Round ribbon</h2>
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

      <div className="round-ribbon-track" aria-label="Round ribbon track">
        {model.rounds.map((round) => (
          <button
            key={round.roundNumber}
            className={`round-ribbon-item ${round.isSelected ? "selected" : ""} ${
              round.isCurrent ? "current" : ""
            } ${round.coachingEventCount > 0 ? "has-coaching" : ""}`}
            type="button"
            onClick={() => onSelectRound(round.roundNumber)}
            aria-current={round.isSelected ? "true" : undefined}
            aria-label={`Round ${round.roundNumber}, ${round.winnerSide} won, ${round.coachingEventCount} coaching events`}
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
