"use client";

import { Bomb, Crosshair, Flag, Skull, Target, TimerReset, X } from "lucide-react";
import { Fragment, memo, useEffect, useMemo, useRef } from "react";

import {
  buildRoundReviewModel,
  jumpTargetsForRound,
  type RoundJumpTarget,
  type RoundReviewSummary
} from "@/lib/round-review";
import { formatRoundTime } from "@/lib/replay-time";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayData } from "@/types/replay";

interface RoundModelProps {
  replay: ReplayData;
  coachingEvents: CoachingEvent[];
  // The round the playhead is in; a number rather than the tick so playback does not rebuild the strip.
  currentRoundNumber: number | null;
  selectedRound: number;
  selectedPlayerId?: string | null;
}

interface RoundStripProps extends RoundModelProps {
  onSelectRound: (roundNumber: number) => void;
}

// Rounds are chosen on the strip; this panel only jumps within the selected one.
interface RoundReviewPanelProps extends RoundModelProps {
  selectedPlayerName?: string | null;
  onSeek: (tick: number) => void;
}

// Both views read one model built from the page's round state; neither keeps its own.
function useRoundModel({ replay, coachingEvents, currentRoundNumber, selectedRound, selectedPlayerId = null }: RoundModelProps) {
  return useMemo(
    () =>
      buildRoundReviewModel({
        rounds: replay.rounds,
        parserEvents: replay.events ?? [],
        coachingEvents,
        currentTick: 0,
        currentRoundNumber,
        selectedRoundNumber: selectedRound,
        tickRate: replay.tickRate,
        selectedPlayerId,
        frames: replay.frames
      }),
    [coachingEvents, currentRoundNumber, replay.events, replay.frames, replay.rounds, replay.tickRate, selectedPlayerId, selectedRound]
  );
}

/**
 * The match at a glance, and the way between rounds: one cell per round in the
 * winning side's colour, a gap where the teams swap sides, the reviewed
 * player's deaths and suggestion counts underneath.
 */
export const RoundStrip = memo(function RoundStrip(props: RoundStripProps) {
  const { selectedRound, onSelectRound } = props;
  const model = useRoundModel(props);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const roundButtonRefs = useRef(new Map<number, HTMLButtonElement>());

  // The selected round stays in view when the strip scrolls (phones, long matches).
  useEffect(() => {
    const track = trackRef.current;
    const selectedButton = roundButtonRefs.current.get(selectedRound);
    if (!track || !selectedButton) {
      return;
    }

    const trackRect = track.getBoundingClientRect();
    const itemRect = selectedButton.getBoundingClientRect();
    const edgePadding = 24;
    let nextScrollLeft: number | null = null;

    if (itemRect.left < trackRect.left + edgePadding) {
      nextScrollLeft = Math.max(0, track.scrollLeft + itemRect.left - trackRect.left - edgePadding);
    } else if (itemRect.right > trackRect.right - edgePadding) {
      nextScrollLeft = Math.max(0, track.scrollLeft + itemRect.right - trackRect.right + edgePadding);
    }

    if (nextScrollLeft !== null) {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      track.scrollTo({ left: nextScrollLeft, behavior: reducedMotion ? "auto" : "smooth" });
    }
  }, [selectedRound]);

  function handleRoundKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, roundIndex: number) {
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

  if (model.rounds.length === 0) {
    return (
      <section className="round-strip empty" aria-label="回合">
        <p className="round-review-empty"><strong>暂无回合数据</strong><span>这场比赛暂时无法按回合跳转。</span></p>
      </section>
    );
  }

  return (
    <section className="round-strip" aria-label="回合">
      <div ref={trackRef} className="round-strip-track" role="group" aria-label="回合列表">
        {model.rounds.map((round, roundIndex) => (
          <Fragment key={round.roundNumber}>
            {round.startsNewHalf && roundIndex > 0 ? (
              <span className="round-strip-half" aria-hidden="true" title="交换攻守" />
            ) : null}
            <button
              ref={(element) => {
                if (element) {
                  roundButtonRefs.current.set(round.roundNumber, element);
                } else {
                  roundButtonRefs.current.delete(round.roundNumber);
                }
              }}
              className={roundCellClass(round)}
              type="button"
              onClick={() => onSelectRound(round.roundNumber)}
              onKeyDown={(event) => handleRoundKeyDown(event, roundIndex)}
              aria-current={round.isCurrent ? "step" : undefined}
              aria-pressed={round.isSelected}
              tabIndex={round.isSelected ? 0 : -1}
              aria-label={roundAriaLabel(round)}
              title={`第 ${round.roundNumber} 回合：${outcomeText(round)}，${round.killCount} 次击杀，${round.coachingEventCount} 条建议${round.playerDeath.tick !== null ? "，阵亡" : ""}`}
            >
              <span className="round-strip-number">{round.roundNumber}</span>
              <span className="round-strip-marks" aria-hidden="true">
                {round.playerDeath.tick !== null ? <X className="round-strip-death" size={13} strokeWidth={3} /> : null}
                <SuggestionMarks count={round.coachingEventCount} />
              </span>
            </button>
          </Fragment>
        ))}
      </div>
    </section>
  );
});

/** The selected round: who won it, and quick jumps to its key moments. */
export const RoundReviewPanel = memo(function RoundReviewPanel(props: RoundReviewPanelProps) {
  const { replay, selectedPlayerId = null, selectedPlayerName, onSeek } = props;
  const model = useRoundModel(props);
  const selectedSummary = model.selectedRound;
  const jumpTargets = jumpTargetsForRound(selectedSummary, Boolean(selectedPlayerId));
  const tickRate = replay.tickRate > 0 ? replay.tickRate : 64;
  const clock = (tick: number) => formatRoundTime((tick - (selectedSummary?.startTick ?? tick)) / tickRate);

  if (!selectedSummary) {
    return null;
  }

  return (
    <section className="round-detail-panel" aria-label="本回合">
      <div className="round-detail-head">
        <h2 className="round-detail-title">第 {selectedSummary.roundNumber} 回合</h2>
        <span className={`round-detail-winner side-${selectedSummary.winnerSide.toLowerCase()}`}>
          {selectedSummary.winnerSide} 获胜
        </span>
        {selectedSummary.playerOutcome ? (
          <span className={`round-detail-outcome ${selectedSummary.playerOutcome}`}>
            {selectedSummary.playerOutcome === "won" ? "赢" : "输"}
          </span>
        ) : null}
        <span className="round-detail-count">{selectedSummary.coachingEventCount} 条建议</span>
      </div>

      <div className="round-quick-jumps" role="group" aria-label="快速跳转">
        {jumpTargets.map((target) => (
          <button key={target.id} className="round-ribbon-jump" type="button"
            disabled={!target.available || target.tick === null}
            onClick={() => { if (target.tick !== null) onSeek(target.tick); }}
            title={target.tick === null ? `本回合没有${target.label}记录` : `跳到${target.label}（${clock(target.tick)}）`}>
            <JumpTargetIcon target={target} />
            <span>{target.label}</span>
            {target.tick !== null ? <small>{clock(target.tick)}</small> : null}
          </button>
        ))}
      </div>

      <details className="round-review-details">
        <summary>本回合详情</summary>
        {selectedPlayerName !== undefined ? (
          <p className="round-review-scope">{selectedPlayerName ? `建议与输赢针对 ${selectedPlayerName}` : "选择玩家后查看建议"}；击杀、安装炸弹等为全场事件。</p>
        ) : null}
        <dl className="round-ribbon-metrics">
          <RoundRibbonMetric label="回合开始" value="0:00" tick={selectedSummary.startTick} />
          <RoundRibbonMetric label="冻结时间结束" value={clock(selectedSummary.freezeEndTick)} tick={selectedSummary.freezeEndTick} />
          <RoundRibbonMetric label="全场首杀" value={eventText(selectedSummary.firstKill.tick, selectedSummary.firstKill.playerName, clock)} tick={selectedSummary.firstKill.tick} />
          <RoundRibbonMetric label="安装炸弹" value={eventText(selectedSummary.bombPlant.tick, selectedSummary.bombPlant.site ? `${selectedSummary.bombPlant.site} 点` : null, clock)} tick={selectedSummary.bombPlant.tick} />
          <RoundRibbonMetric label="回合结束" value={clock(selectedSummary.endTick)} tick={selectedSummary.endTick} />
        </dl>
      </details>
    </section>
  );
});

function roundCellClass(round: RoundReviewSummary): string {
  return [
    "round-strip-cell",
    `winner-${round.winnerSide.toLowerCase()}`,
    round.playerOutcome ?? "",
    round.playerDeath.tick !== null ? "player-died" : "",
    round.coachingEventCount > 0 ? "has-coaching" : "",
    round.isSelected ? "selected" : "",
    round.isCurrent ? "current" : ""
  ].filter(Boolean).join(" ");
}

// One dot per suggestion up to three; past that a dot and the count, so the cell never grows.
function SuggestionMarks({ count }: { count: number }) {
  if (count <= 0) return null;
  if (count <= 3) {
    return (
      <span className="round-strip-dots">
        {Array.from({ length: count }, (_, index) => <i key={index} />)}
      </span>
    );
  }
  return <span className="round-strip-dots"><i /><b>{count}</b></span>;
}

function outcomeText(round: RoundReviewSummary): string {
  if (round.playerOutcome === "won") return `赢 · ${round.winnerSide} 获胜`;
  if (round.playerOutcome === "lost") return `输 · ${round.winnerSide} 获胜`;
  return `${round.winnerSide} 获胜`;
}

function roundAriaLabel(round: RoundReviewSummary): string {
  const parts = [`第 ${round.roundNumber} 回合`, outcomeText(round), `${round.coachingEventCount} 条建议`];
  if (round.playerDeath.tick !== null) parts.push("阵亡");
  if (round.isCurrent) parts.push("正在播放");
  return parts.join("，");
}

function RoundRibbonMetric({ label, value, tick }: { label: string; value: string; tick: number | null }) {
  return (
    <div className="round-ribbon-metric" title={tick === null ? undefined : `Tick ${tick}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
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
  if (target.id === "player_first_kill") {
    return <Target size={14} aria-hidden="true" />;
  }
  if (target.id === "player_death") {
    return <Skull size={14} aria-hidden="true" />;
  }
  return <Bomb size={14} aria-hidden="true" />;
}

function eventText(tick: number | null, detail: string | null | undefined, clock: (tick: number) => string): string {
  if (tick === null) {
    return "-";
  }
  return detail ? `${clock(tick)}，${detail}` : clock(tick);
}
