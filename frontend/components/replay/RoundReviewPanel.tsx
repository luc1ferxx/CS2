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
  selectedPlayerName?: string | null;
  onSelectRound: (roundNumber: number) => void;
  onSeek: (tick: number) => void;
}

export function RoundReviewPanel({
  replay,
  coachingEvents,
  currentTick,
  selectedRound,
  selectedPlayerName,
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
          <h2>回合</h2>
        </div>
        {selectedSummary ? (
          <div className="round-ribbon-state" aria-label={`Selected round ${selectedSummary.roundNumber} state`}>
            <span className="round-ribbon-selected">第 {selectedSummary.roundNumber} 回合</span>
            <span className={`round-outcome-pill ${selectedSummary.winnerSide.toLowerCase()}`}>
              {selectedSummary.winnerSide} 获胜
            </span>
            <span className="round-ribbon-state-detail">
              {selectedSummary.coachingEventCount} 条建议
            </span>
          </div>
        ) : null}
      </div>

      {!selectedSummary ? (
        <div className="round-review-empty">
          <strong>暂无回合数据</strong>
          <span>这场比赛暂时无法按回合跳转。</span>
        </div>
      ) : null}

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
            title={`第 ${round.roundNumber} 回合：${round.killCount} 次击杀，${round.coachingEventCount} 条建议`}
          >
            <div className="round-ribbon-item-top">
              <strong>{round.roundNumber}</strong>
            </div>
            <div className="round-ribbon-review-count">
              <span aria-hidden="true" />
              <strong>{round.coachingEventCount}</strong>
              <small>条</small>
            </div>
          </button>
        ))}
      </div>

      {selectedSummary ? (
        <details className="round-review-details">
          <summary>本回合详情与快速跳转</summary>
          {selectedPlayerName !== undefined ? (
            <p className="round-review-scope">{selectedPlayerName ? `建议针对 ${selectedPlayerName}` : "选择玩家后查看建议"}；击杀、安装炸弹等为全场事件。</p>
          ) : null}
          <div className="round-ribbon-detail">
            <div className="round-ribbon-actions" aria-label="Round quick jumps">
              {jumpTargets.map((target) => (
                <button key={target.id} className="round-ribbon-jump" type="button"
                  disabled={!target.available || target.tick === null}
                  onClick={() => { if (target.tick !== null) onSeek(target.tick); }}
                  aria-label={target.label}
                  title={target.tick === null ? `${jumpLabel(target)}暂无数据` : `跳转至 Tick ${target.tick}`}>
                  <JumpTargetIcon target={target} />
                  <span>{jumpLabel(target)}</span>
                </button>
              ))}
            </div>
            <div className="round-ribbon-metrics" aria-label={`Round ${selectedSummary.roundNumber} evidence summary`}>
              <RoundRibbonMetric label="回合开始 · Tick" value={selectedSummary.startTick} />
              <RoundRibbonMetric label="冻结结束 · Tick" value={selectedSummary.freezeEndTick} />
              <RoundRibbonMetric label="首次击杀 · Tick" value={formatEventReference(selectedSummary.firstKill.tick, selectedSummary.firstKill.playerName)} />
              <RoundRibbonMetric label="安装炸弹 · Tick" value={formatEventReference(selectedSummary.bombPlant.tick, selectedSummary.bombPlant.site)} />
              <RoundRibbonMetric label="回合结束 · Tick" value={selectedSummary.endTick} />
            </div>
          </div>
        </details>
      ) : null}
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

function jumpLabel(target: RoundJumpTarget): string {
  return { round_start: "回合开始", live_start: "冻结结束", first_kill: "首次击杀", bomb_plant: "安装炸弹" }[target.id];
}

function formatEventReference(tick: number | null, detail?: string | null) {
  if (tick === null) {
    return "-";
  }
  return detail ? `${tick} / ${detail}` : String(tick);
}
