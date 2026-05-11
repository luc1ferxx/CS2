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
    <section className="panel round-review-panel" aria-label="Round review">
      <div className="round-review-header">
        <div>
          <h2>Round Review</h2>
          <p>
            {selectedSummary
              ? `${selectedSummary.winnerSide} won round ${selectedSummary.roundNumber}`
              : "No round selected"}
          </p>
        </div>
        <div className="round-review-header-pills">
          <span className="mini-pill">Selected R{selectedRound}</span>
          {model.currentRoundNumber ? (
            <span className="mini-pill">Current R{model.currentRoundNumber}</span>
          ) : null}
        </div>
      </div>

      {selectedSummary ? (
        <div className="round-review-summary">
          <div className="round-review-summary-main">
            <div>
              <span className={`round-outcome-pill ${selectedSummary.winnerSide.toLowerCase()}`}>
                {selectedSummary.winnerSide}
              </span>
              <h3>Round {selectedSummary.roundNumber}</h3>
            </div>
            <span className="round-duration">{selectedSummary.durationSeconds}s</span>
          </div>

          <div className="round-summary-grid">
            <SummaryMetric label="Start" value={selectedSummary.startTick} />
            <SummaryMetric label="Live" value={selectedSummary.freezeEndTick} />
            <SummaryMetric label="End" value={selectedSummary.endTick} />
            <SummaryMetric
              label="First kill"
              value={formatEventReference(selectedSummary.firstKill.tick, selectedSummary.firstKill.playerName)}
            />
            <SummaryMetric
              label="Plant"
              value={formatEventReference(selectedSummary.bombPlant.tick, selectedSummary.bombPlant.site)}
            />
            <SummaryMetric label="Kills" value={selectedSummary.killCount} />
            <SummaryMetric label="Utility" value={selectedSummary.utilityEventCount} />
            <SummaryMetric label="Coaching" value={selectedSummary.coachingEventCount} />
          </div>

          <div className="round-jump-actions" aria-label="Round quick jumps">
            {jumpTargets.map((target) => (
              <button
                key={target.id}
                className="secondary-button compact-button round-jump-button"
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

      <div className="round-review-list" aria-label="Round list">
        {model.rounds.map((round) => (
          <button
            key={round.roundNumber}
            className={`round-review-item ${round.isSelected ? "selected" : ""} ${
              round.isCurrent ? "current" : ""
            }`}
            type="button"
            onClick={() => onSelectRound(round.roundNumber)}
            aria-current={round.isSelected ? "true" : undefined}
          >
            <div className="round-review-item-top">
              <strong>R{round.roundNumber}</strong>
              <span className={`round-side-chip ${round.winnerSide.toLowerCase()}`}>
                {round.winnerSide}
              </span>
            </div>
            <div className="round-review-ticks">
              {round.startTick}-{round.endTick}
            </div>
            <div className="round-review-counts">
              <span>K {round.killCount}</span>
              <span>B {round.bombEventCount}</span>
              <span>U {round.utilityEventCount}</span>
              <span>Co {round.coachingEventCount}</span>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function SummaryMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="round-summary-metric">
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
