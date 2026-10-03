"use client";

import { Bomb, Clock, Scissors, Skull, X, type LucideIcon } from "lucide-react";
import { Fragment, memo, useEffect, useMemo, useRef, useState } from "react";

import {
  ECONOMY_KIND_ORDER,
  economyTeamNames,
  economyTeamParts,
  type EconomyTeamNames
} from "@/components/stats/EconomyPanel";
import {
  buildRoundReviewModel,
  jumpTargetsForRound,
  type RoundReviewModel,
  type RoundReviewSummary
} from "@/lib/round-review";
import { ROUND_END_REASON_LABELS, roundEndReason, type MatchTeam, type RoundEndReason, type TeamKey } from "@/lib/match-stats";
import { ECONOMY_LABELS, type EconomyKind, type RoundEconomy } from "@/lib/round-economy";
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
  // Each team's buy per round, computed once by the page (roundEconomies) and shared with 经济;
  // [] (v1 replays) leaves out the economy rows, filter and key.
  economies: readonly RoundEconomy[];
  // Team names for the economy rows (matchSummary); 队伍 A / 队伍 B without them.
  teams?: MatchTeam[];
  onSelectRound: (roundNumber: number) => void;
}

type EconomyFilterKind = EconomyKind | "all";
// Team A's buy row, then team B's (RoundEconomy.teams has the same order).
const TEAM_ROWS = ["A", "B"] as const;

// Rounds are chosen on the strip; this panel only jumps within the selected one.
interface RoundReviewPanelProps extends RoundModelProps {
  selectedPlayerName?: string | null;
  onSeek: (tick: number) => void;
}

// Both views read one model built from the page's round state; neither keeps its own.
function useRoundModel({ replay, coachingEvents, currentRoundNumber, selectedRound, selectedPlayerId = null }: RoundModelProps): RoundReviewModel {
  // The summaries depend on the replay, the suggestions and the player. The selected and the
  // playing round only set two flags, so a round change (or playback crossing into the next
  // round) reuses them, and every summary whose flags did not change keeps its identity.
  const unflagged = useMemo(
    () =>
      buildRoundReviewModel({
        rounds: replay.rounds,
        parserEvents: replay.events ?? [],
        coachingEvents,
        currentTick: 0,
        currentRoundNumber: null,
        selectedRoundNumber: Number.NaN,
        tickRate: replay.tickRate,
        selectedPlayerId,
        frames: replay.frames
      }).rounds,
    [coachingEvents, replay.events, replay.frames, replay.rounds, replay.tickRate, selectedPlayerId]
  );
  return useMemo(() => {
    const rounds = unflagged.map((summary) => {
      const isSelected = summary.roundNumber === selectedRound;
      const isCurrent = summary.roundNumber === currentRoundNumber;
      return isSelected || isCurrent ? { ...summary, isSelected, isCurrent } : summary;
    });
    return {
      rounds,
      selectedRound: rounds.find((summary) => summary.roundNumber === selectedRound) ?? rounds[0] ?? null,
      currentRoundNumber
    };
  }, [currentRoundNumber, selectedRound, unflagged]);
}

// How a round ended, drawn in its cell; "other" (unknown or missing) gets no icon.
const ROUND_END_ICONS: Record<Exclude<RoundEndReason, "other">, LucideIcon> = {
  bomb_exploded: Bomb,
  bomb_defused: Scissors,
  elimination: Skull,
  time: Clock
};

/**
 * The match at a glance, and the way between rounds: one cell per round in the
 * winning side's colour with an icon for how it ended, a gap where the teams swap
 * sides, the reviewed player's deaths and suggestion counts underneath, then each
 * team's buy type (v2 replays). The economy filter only fades rounds; it never
 * changes the shared round or tick.
 */
export const RoundStrip = memo(function RoundStrip(props: RoundStripProps) {
  const { replay, selectedRound, economies, teams, onSelectRound } = props;
  const model = useRoundModel(props);
  const endReasons = useMemo(
    () => new Map(replay.rounds.map((round) => [round.roundNumber, roundEndReason(round)])),
    [replay.rounds]
  );
  const economyByRound = useMemo(() => new Map(economies.map((round) => [round.roundNumber, round])), [economies]);
  const showEconomy = economies.length > 0;
  const teamNames = useMemo(() => economyTeamNames(teams), [teams]);
  const [filterKind, setFilterKind] = useState<EconomyFilterKind>("all");
  const [filterTeam, setFilterTeam] = useState<TeamKey>("A");
  const filtering = showEconomy && filterKind !== "all";
  const matchesFilter = (roundNumber: number) =>
    !filtering || economyByRound.get(roundNumber)?.teams[filterTeam === "A" ? 0 : 1].kind === filterKind;
  const trackRef = useRef<HTMLDivElement | null>(null);
  const teamsColumnRef = useRef<HTMLDivElement | null>(null);
  const roundButtonRefs = useRef(new Map<number, HTMLButtonElement>());

  // The selected round stays in view when the strip scrolls (phones, long matches), clear of the sticky team names.
  useEffect(() => {
    const track = trackRef.current;
    const selectedButton = roundButtonRefs.current.get(selectedRound);
    if (!track || !selectedButton) {
      return;
    }

    const trackRect = track.getBoundingClientRect();
    const itemRect = selectedButton.getBoundingClientRect();
    const edgePadding = 24;
    const leftEdge = trackRect.left + (teamsColumnRef.current?.getBoundingClientRect().width ?? 0) + edgePadding;
    let nextScrollLeft: number | null = null;

    if (itemRect.left < leftEdge) {
      nextScrollLeft = Math.max(0, track.scrollLeft + itemRect.left - leftEdge);
    } else if (itemRect.right > trackRect.right - edgePadding) {
      nextScrollLeft = Math.max(0, track.scrollLeft + itemRect.right - trackRect.right + edgePadding);
    }

    if (nextScrollLeft !== null) {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      track.scrollTo({ left: nextScrollLeft, behavior: reducedMotion ? "auto" : "smooth" });
    }
  }, [selectedRound]);

  // A new filter brings its first match into view when none is in view (phones show about 9 of 24
  // rounds). It scrolls the track only: the shared round and tick stay where they are.
  useEffect(() => {
    const track = trackRef.current;
    if (filterKind === "all" || !track) {
      return;
    }
    const matches = [...track.querySelectorAll<HTMLElement>(".round-strip-cell:not(.econ-faded)")];
    if (matches.length === 0) {
      return;
    }
    const trackRect = track.getBoundingClientRect();
    const leftEdge = trackRect.left + (teamsColumnRef.current?.getBoundingClientRect().width ?? 0);
    const inView = matches.some((cell) => {
      const rect = cell.getBoundingClientRect();
      return rect.left >= leftEdge && rect.right <= trackRect.right;
    });
    if (inView) {
      return;
    }
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const left = Math.max(0, track.scrollLeft + matches[0].getBoundingClientRect().left - leftEdge - 24);
    track.scrollTo({ left, behavior: reducedMotion ? "auto" : "smooth" });
  }, [filterKind, filterTeam]);

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
      <section className="panel round-strip empty" aria-labelledby="round-strip-title">
        <div className="panel-bar round-strip-head"><h2 className="panel-bar-title" id="round-strip-title">回合记录</h2></div>
        <p className="round-review-empty"><strong>暂无回合数据</strong><span>这场比赛暂时无法按回合跳转。</span></p>
      </section>
    );
  }

  const matchingCount = filtering ? model.rounds.filter((round) => matchesFilter(round.roundNumber)).length : 0;

  return (
    <section className="panel round-strip" aria-labelledby="round-strip-title">
      <div className="panel-bar round-strip-head">
        <h2 className="panel-bar-title" id="round-strip-title">回合记录</h2>
        {showEconomy ? (
          <EconomyFilter kind={filterKind} team={filterTeam} names={teamNames} matchingCount={matchingCount}
            onKindChange={setFilterKind} onTeamChange={setFilterTeam} />
        ) : null}
      </div>
      <div ref={trackRef} role="group" aria-label="回合列表"
        className={`round-strip-track${showEconomy ? " has-economy" : ""}${filtering ? ` filter-team-${filterTeam.toLowerCase()}` : ""}`}>
        {showEconomy ? (
          // Sticky while the rounds scroll sideways: whose buy each of the two rows under the cells is.
          <div ref={teamsColumnRef} className="round-strip-teams" aria-hidden="true">
            <span className="round-strip-team team-a" title={teamNames.A}>{teamNames.A}</span>
            <span className="round-strip-team team-b" title={teamNames.B}>{teamNames.B}</span>
          </div>
        ) : null}
        {model.rounds.map((round, roundIndex) => {
          const reason = endReasons.get(round.roundNumber) ?? "other";
          const ReasonIcon = reason === "other" ? null : ROUND_END_ICONS[reason];
          const economy = economyByRound.get(round.roundNumber) ?? null;
          const faded = !matchesFilter(round.roundNumber);
          return (
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
              className={`${roundCellClass(round)}${faded ? " econ-faded" : ""}`}
              type="button"
              onClick={() => onSelectRound(round.roundNumber)}
              onKeyDown={(event) => handleRoundKeyDown(event, roundIndex)}
              aria-current={round.isCurrent ? "step" : undefined}
              aria-pressed={round.isSelected}
              tabIndex={round.isSelected ? 0 : -1}
              aria-label={roundAriaLabel(round, reason, economy, teamNames, faded)}
              title={roundTitle(round, reason, economy, teamNames)}
            >
              <span className="round-strip-fill" aria-hidden="true">
                {ReasonIcon ? <ReasonIcon className="round-strip-reason" size={14} strokeWidth={2.25} /> : null}
              </span>
              <span className="round-strip-number">{round.roundNumber}</span>
              <span className="round-strip-marks" aria-hidden="true">
                {round.playerDeath.tick !== null ? <X className="round-strip-death" size={13} strokeWidth={3} /> : null}
                <SuggestionMarks count={round.coachingEventCount} />
              </span>
              {showEconomy ? TEAM_ROWS.map((key, teamIndex) => {
                const kind = economy?.teams[teamIndex]?.kind ?? null;
                return (
                  <span key={key} className={`round-strip-econ team-${key.toLowerCase()}`} aria-hidden="true">
                    {kind ? ECONOMY_LABELS[kind].short : ""}
                  </span>
                );
              }) : null}
            </button>
          </Fragment>
          );
        })}
      </div>
      {/* Under the track so the bar keeps room for the filter; on phones it wraps. */}
      <RoundStripLegend economy={showEconomy} />
    </section>
  );
});

// 筛选 [全部 | 手枪局 | 全起 | 强起 | 半起 | ECO] [team A | team B] N 个回合: only fades the strip's
// other rounds. The team choice is unavailable under 全部, where it would filter nothing.
function EconomyFilter({ kind, team, names, matchingCount, onKindChange, onTeamChange }: {
  kind: EconomyFilterKind;
  team: TeamKey;
  names: EconomyTeamNames;
  matchingCount: number;
  onKindChange: (kind: EconomyFilterKind) => void;
  onTeamChange: (team: TeamKey) => void;
}) {
  const kinds: { value: EconomyFilterKind; label: string }[] = [
    { value: "all", label: "全部" },
    ...ECONOMY_KIND_ORDER.map((value) => ({ value, label: ECONOMY_LABELS[value].name }))
  ];
  const idle = kind === "all";
  return (
    <div className="round-strip-filter">
      <div className="round-strip-filter-controls">
        <div className="econ-segments" role="group" aria-label="按经济类型筛选回合">
          {kinds.map((option) => (
            <button key={option.value} type="button" className="econ-segment" aria-pressed={kind === option.value}
              onClick={() => onKindChange(option.value)}>
              {option.label}
            </button>
          ))}
        </div>
        <div className="econ-segments" role="group" aria-label="筛选哪支队伍">
          {TEAM_ROWS.map((key) => (
            <button key={key} type="button" className="econ-segment econ-segment-team" aria-pressed={team === key}
              aria-disabled={idle || undefined} title={idle ? `${names[key]}（先选经济类型）` : names[key]}
              onClick={() => { if (!idle) onTeamChange(key); }}>
              {names[key]}
            </button>
          ))}
        </div>
        {/* Mounted with the filter so the first change is announced too; empty under 全部. */}
        <span className="round-strip-filter-count" aria-live="polite" aria-atomic="true">
          {idle ? null : (
            <>
              <span className="visually-hidden">{names[team]} {ECONOMY_LABELS[kind].name}：</span>
              {matchingCount} 个回合
            </>
          )}
        </span>
      </div>
    </div>
  );
}

// Two groups, the round key and the economy key: a line only ever breaks between them or inside one.
function RoundStripLegend({ economy }: { economy: boolean }) {
  return (
    <p className="round-strip-legend" aria-hidden="true">
      <span className="round-strip-legend-group">
        <span><i className="side-t" />T 胜</span>
        <span><i className="side-ct" />CT 胜</span>
        {(Object.keys(ROUND_END_ICONS) as (keyof typeof ROUND_END_ICONS)[]).map((reason) => {
          const Icon = ROUND_END_ICONS[reason];
          return <span key={reason}><Icon size={12} strokeWidth={2.25} className="round-strip-legend-reason" />{ROUND_END_REASON_LABELS[reason]}</span>;
        })}
        <span><X size={12} strokeWidth={3} className="round-strip-death" />阵亡</span>
        <span><b className="round-strip-legend-dot" />建议</span>
      </span>
      {economy ? (
        <span className="round-strip-legend-group econ">
          {ECONOMY_KIND_ORDER.map((kind) => (
            <span key={kind} className="round-strip-legend-econ">
              <b>{ECONOMY_LABELS[kind].short}</b> = {ECONOMY_LEGEND_NAMES[kind]}
            </span>
          ))}
        </span>
      ) : null}
    </p>
  );
}

const ECONOMY_LEGEND_NAMES: Record<EconomyKind, string> = {
  pistol: "手枪局",
  full: "全起",
  force: "强起",
  half: "半起",
  eco: "ECO 经济局"
};

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
    <section className="panel round-detail-panel" aria-label="本回合">
      <div className="panel-bar round-detail-head">
        <h2 className="panel-bar-title round-detail-title">第 {selectedSummary.roundNumber} 回合</h2>
        <span className={`round-detail-winner side-${selectedSummary.winnerSide.toLowerCase()}`}>
          {selectedSummary.winnerSide} 获胜
        </span>
        <span className="round-detail-count">{selectedSummary.coachingEventCount} 条建议</span>
      </div>

      <div className="round-quick-jumps" role="group" aria-label="快速跳转">
        {/* A round without a plant is common: its empty 安装炸弹 row says nothing. The player's own
            missing first kill or death stays, dimmed: that absence is about them. */}
        {jumpTargets.filter((target) => target.id !== "bomb_plant" || target.tick !== null).map((target) => (
          <button key={target.id} className="round-ribbon-jump" type="button"
            disabled={!target.available || target.tick === null}
            onClick={() => { if (target.tick !== null) onSeek(target.tick); }}
            title={target.tick === null ? `本回合没有${target.label}记录` : `跳到${target.label}（${clock(target.tick)}）`}>
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

// "第 5 回合 T 胜，炸弹爆炸，赢下本回合": the winner, how the round ended, then the reviewed player's result.
function outcomeParts(round: RoundReviewSummary, reason: RoundEndReason): string[] {
  const parts = [`第 ${round.roundNumber} 回合 ${round.winnerSide} 胜`];
  if (reason !== "other") parts.push(ROUND_END_REASON_LABELS[reason]);
  if (round.playerOutcome === "won") parts.push("赢下本回合");
  if (round.playerOutcome === "lost") parts.push("输掉本回合");
  return parts;
}

// Each team's buy follows the outcome: "…，MOUZ 强起 $14,250，Spirit 全起 $22,150，…".
function roundAriaLabel(round: RoundReviewSummary, reason: RoundEndReason, economy: RoundEconomy | null,
  names: EconomyTeamNames, faded: boolean): string {
  const parts = [...outcomeParts(round, reason), ...(economy ? economyTeamParts(economy, names) : []), `${round.coachingEventCount} 条建议`];
  if (round.playerDeath.tick !== null) parts.push("阵亡");
  if (round.isCurrent) parts.push("正在播放");
  if (faded) parts.push("不符合筛选");
  return parts.join("，");
}

function roundTitle(round: RoundReviewSummary, reason: RoundEndReason, economy: RoundEconomy | null,
  names: EconomyTeamNames): string {
  const parts = [...outcomeParts(round, reason), ...(economy ? economyTeamParts(economy, names) : []),
    `${round.killCount} 次击杀`, `${round.coachingEventCount} 条建议`];
  if (round.playerDeath.tick !== null) parts.push("阵亡");
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

function eventText(tick: number | null, detail: string | null | undefined, clock: (tick: number) => string): string {
  if (tick === null) {
    return "-";
  }
  return detail ? `${clock(tick)}，${detail}` : clock(tick);
}
