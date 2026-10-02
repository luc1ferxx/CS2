"use client";

import { memo, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { teamDisplayName } from "@/components/replay/MatchScoreBanner";
import type { MatchTeam, TeamKey } from "@/lib/match-stats";
import {
  ECONOMY_LABELS,
  economySummary,
  type EconomyKind,
  type RoundEconomy,
  type TeamRoundEconomy
} from "@/lib/round-economy";
import type { PlayerSide } from "@/types/replay";

interface EconomyPanelProps {
  // Each team's buy per round, computed once by the page (roundEconomies) and shared with the
  // round strip; [] (v1 replays) hides the panel.
  economies: readonly RoundEconomy[];
  // Team names (matchSummary) and keys; missing names fall back to 队伍 A / 队伍 B.
  teams: MatchTeam[];
  selectedRound: number;
  // The page's shared round change; the stage comes into view.
  onSelectRound: (roundNumber: number) => void;
}

// Table rows, and the strip's filter, in this order.
export const ECONOMY_KIND_ORDER: readonly EconomyKind[] = ["pistol", "full", "force", "half", "eco"];
// Both teams need this many full buys before their records are compared in a sentence.
const MIN_FULL_BUYS_TO_COMPARE = 3;

export type EconomyTeamNames = Record<TeamKey, string>;

export function economyTeamNames(teams: readonly MatchTeam[] | null | undefined): EconomyTeamNames {
  const name = (key: TeamKey) => {
    const team = teams?.find((item) => item.key === key);
    return teamDisplayName({ key, name: team?.name ?? null });
  };
  return { A: name("A"), B: name("B") };
}

// One formatter: toLocaleString builds a new one per call, and a round change formats ~100 values
// (the strip's cell labels and the chart's pair labels).
const MONEY = new Intl.NumberFormat("en-US");

export function formatMoney(value: number): string {
  return `$${MONEY.format(Math.round(value))}`;
}

/** "MOUZ 强起 $14,250" for each team with a known buy; shared by the round strip and the chart. */
export function economyTeamParts(round: RoundEconomy, names: EconomyTeamNames): string[] {
  return round.teams
    .filter((team) => team.kind !== null)
    .map((team) => `${names[team.teamKey]} ${ECONOMY_LABELS[team.kind as EconomyKind].name} ${formatMoney(team.equipValue)}`);
}

/**
 * 经济: each team's equipment value at the end of freeze time, round by round, in the colour of
 * the side it played, and how each buy type turned out. Hidden for replays without economy data.
 */
export const EconomyPanel = memo(function EconomyPanel({ economies: rounds, teams, selectedRound, onSelectRound }: EconomyPanelProps) {
  const summary = useMemo(() => economySummary(rounds), [rounds]);
  const names = useMemo(() => economyTeamNames(teams), [teams]);
  if (rounds.length === 0) return null;

  const fullA = summary.A.full;
  const fullB = summary.B.full;
  const compareFullBuys = fullA.rounds >= MIN_FULL_BUYS_TO_COMPARE && fullB.rounds >= MIN_FULL_BUYS_TO_COMPARE;

  return (
    <section className="panel economy-panel" aria-labelledby="economy-title">
      <div className="panel-bar">
        <h2 className="panel-bar-title" id="economy-title">经济</h2>
        <span className="panel-bar-meta">冻结时间结束时的装备价值，点柱子跳到那个回合</span>
      </div>
      <div className="economy-grid">
        <EconomyChart rounds={rounds} names={names} selectedRound={selectedRound} onSelectRound={onSelectRound} />
        <section className="economy-record" aria-labelledby="economy-record-title">
          <div className="match-analysis-cell-bar">
            <h3 id="economy-record-title">经济类型战绩</h3>
          </div>
          <table className="data-table economy-table" aria-labelledby="economy-record-title">
            {/* The win columns carry the full-buy rate, so they get more room than the round counts. */}
            <colgroup>
              <col className="economy-col-kind" />
              <col className="economy-col-rounds" />
              <col className="economy-col-wins" />
              <col className="economy-col-rounds" />
              <col className="economy-col-wins" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col" rowSpan={2}>经济类型</th>
                <th scope="colgroup" colSpan={2} className="economy-team-head" title={names.A}>{names.A}</th>
                <th scope="colgroup" colSpan={2} className="economy-team-head" title={names.B}>{names.B}</th>
              </tr>
              <tr>
                <th scope="col" className="num" aria-label={`${names.A} 回合`}>回合</th>
                <th scope="col" className="num" aria-label={`${names.A} 胜`}>胜</th>
                <th scope="col" className="num" aria-label={`${names.B} 回合`}>回合</th>
                <th scope="col" className="num" aria-label={`${names.B} 胜`}>胜</th>
              </tr>
            </thead>
            <tbody>
              {ECONOMY_KIND_ORDER.map((kind) => (
                <tr key={kind}>
                  <th scope="row">{ECONOMY_LABELS[kind].name}</th>
                  <RecordCells record={summary.A[kind]} withRate={kind === "full"} />
                  <RecordCells record={summary.B[kind]} withRate={kind === "full"} />
                </tr>
              ))}
            </tbody>
          </table>
          {compareFullBuys ? (
            // Each team's clause stays on one line when it fits, so a wrap falls after the comma.
            <p className="economy-sentence">
              全起对全起：<span className="economy-clause">{names.A} 全起 {fullA.rounds} 回合赢 {fullA.wins}，</span>
              <span className="economy-clause">{names.B} 全起 {fullB.rounds} 回合赢 {fullB.wins}。</span>
            </p>
          ) : null}
        </section>
      </div>
    </section>
  );
});

// "—" for a buy type the team never had; a full buy's wins carry the win rate, which may wrap
// under the number in a narrow column instead of running out of it.
function RecordCells({ record, withRate }: { record: { rounds: number; wins: number }; withRate: boolean }) {
  if (record.rounds === 0) {
    return <><td className="num economy-none">—</td><td className="num economy-none">—</td></>;
  }
  return (
    <>
      <td className="num">{record.rounds}</td>
      <td className="num economy-wins">
        {record.wins}
        {withRate ? <>{" "}<span className="economy-rate">({Math.round((record.wins / record.rounds) * 100)}%)</span></> : null}
      </td>
    </>
  );
}

// Drawn at the measured pixel width, so 11 px labels stay 11 px on a phone and on a wide screen.
const FALLBACK_WIDTH = 640;
const AXIS_WIDTH = 38;
const PLOT_TOP = 10;
const HALF_GAP = 12;
const MAX_BAR_WIDTH = 12;
const BAR_GAP = 1;
const WIN_MARK = 5;
// Round labels closer than this (centre to centre) would touch; the selected round's wins.
const MIN_LABEL_DISTANCE = 18;
const MIN_TOP_VALUE = 30000;
const VALUE_STEP = 5000;
// The 5-player thresholds: under $5k is an eco, $10k a half buy, $20k a full buy.
const THRESHOLDS = [5000, 10000, 20000];

interface SlotLayout {
  round: RoundEconomy;
  x: number;
  step: number;
  // A half-time (or overtime) switch of sides happens just before this round.
  switchBefore: boolean;
}

function EconomyChart({ rounds, names, selectedRound, onSelectRound }: {
  rounds: readonly RoundEconomy[];
  names: EconomyTeamNames;
  selectedRound: number;
  onSelectRound: (roundNumber: number) => void;
}) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const pairRefs = useRef<(SVGGElement | null)[]>([]);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  const [focusIndex, setFocusIndex] = useState<number | null>(null);

  useLayoutEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const measure = () => {
      const next = Math.round(frame.getBoundingClientRect().width);
      if (next > 0) setWidth(next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const plotHeight = width >= 560 ? 190 : 130;
  const baseline = PLOT_TOP + plotHeight;
  const winMarkY = baseline + 3;
  const labelY = baseline + 24;
  const chartHeight = baseline + 30;
  const maxValue = Math.max(0, ...rounds.flatMap((round) => round.teams.map((team) => team.equipValue)));
  const topValue = Math.max(MIN_TOP_VALUE, Math.ceil(maxValue / VALUE_STEP) * VALUE_STEP);
  const yFor = (value: number) => baseline - (Math.max(0, value) / topValue) * plotHeight;
  const slots = layoutSlots(rounds, width);
  const selectedIndex = rounds.findIndex((round) => round.roundNumber === selectedRound);
  const activeIndex = Math.min(focusIndex ?? Math.max(0, selectedIndex), Math.max(0, rounds.length - 1));
  // The chart redraws on every round change (the selected pair); its labels and the hidden table do not change.
  const pairLabels = useMemo(
    () => new Map(rounds.map((round) => [round.roundNumber, economyPairLabel(round, names)])),
    [names, rounds]
  );
  const pairLabel = (round: RoundEconomy) => pairLabels.get(round.roundNumber) ?? economyPairLabel(round, names);
  const dataTable = useMemo(() => (
    // Hidden through a wrapper: a table ignores width: 1px, and its full width would widen a phone page.
    <div className="visually-hidden">
      <table>
        <caption>每回合装备价值</caption>
        <thead>
          <tr>
            <th scope="col">回合</th>
            {(["A", "B"] as const).map((key) => (
              <th key={key} scope="col">{names[key]}</th>
            ))}
            <th scope="col">胜方</th>
          </tr>
        </thead>
        <tbody>
          {rounds.map((round) => (
            <tr key={round.roundNumber}>
              <th scope="row">第 {round.roundNumber} 回合</th>
              {round.teams.map((team) => <td key={team.teamKey}>{teamCellText(team)}</td>)}
              <td>{winnerText(round, names)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ), [names, rounds]);
  // Every 3rd round is numbered, and the selected one always (bold): hovering a pair only tints it,
  // so the number is what tells the selected round apart.
  const selectedSlot = slots.find((slot) => slot.round.roundNumber === selectedRound) ?? null;
  const centre = (slot: SlotLayout) => slot.x + slot.step / 2;
  const showRoundLabel = (slot: SlotLayout) => {
    if (slot === selectedSlot) return true;
    if (slot.round.roundNumber % 3 !== 0) return false;
    return !selectedSlot || Math.abs(centre(slot) - centre(selectedSlot)) >= MIN_LABEL_DISTANCE;
  };

  function handleKeyDown(event: KeyboardEvent<SVGGElement>, index: number) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectRound(rounds[index].roundNumber);
      return;
    }
    const next = event.key === "ArrowRight" ? index + 1
      : event.key === "ArrowLeft" ? index - 1
        : event.key === "Home" ? 0
          : event.key === "End" ? rounds.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    const target = Math.max(0, Math.min(rounds.length - 1, next));
    setFocusIndex(target);
    pairRefs.current[target]?.focus();
  }

  return (
    <section className="economy-chart" aria-labelledby="economy-chart-title">
      <div className="match-analysis-cell-bar">
        <h3 id="economy-chart-title">每回合装备价值</h3>
        <span className="economy-legend" aria-hidden="true">
          <span><i className="swatch side-t" />T 方</span>
          <span><i className="swatch side-ct" />CT 方</span>
          <span>左柱 {names.A}，右柱 {names.B}（深色）</span>
          <span><i className="swatch won" />赢下回合</span>
          <span><i className="swatch threshold" />5 人 ECO、半起、全起分界</span>
        </span>
      </div>
      <div className="economy-chart-frame" ref={frameRef}>
        <svg width={width} height={chartHeight} viewBox={`0 0 ${width} ${chartHeight}`}
          role="group" aria-label="每回合装备价值柱状图，点击柱子跳到那个回合">
          <g aria-hidden="true">
            <line className="economy-chart-grid" x1={AXIS_WIDTH} x2={width} y1={yFor(topValue)} y2={yFor(topValue)} />
            <text className="economy-chart-tick" x={AXIS_WIDTH - 6} y={yFor(topValue) + 4} textAnchor="end">{shortMoney(topValue)}</text>
            {THRESHOLDS.map((value) => (
              <g key={value}>
                <line className="economy-chart-threshold" x1={AXIS_WIDTH} x2={width} y1={yFor(value)} y2={yFor(value)} />
                <text className="economy-chart-tick" x={AXIS_WIDTH - 6} y={yFor(value) + 4} textAnchor="end">{shortMoney(value)}</text>
              </g>
            ))}
            <text className="economy-chart-tick" x={AXIS_WIDTH - 6} y={baseline + 4} textAnchor="end">$0</text>
            {slots.filter((slot) => slot.switchBefore).map((slot) => (
              <line key={slot.round.roundNumber} className="economy-chart-half" x1={slot.x - HALF_GAP / 2} x2={slot.x - HALF_GAP / 2}
                y1={PLOT_TOP} y2={baseline + 8} />
            ))}
          </g>
          {slots.map((slot, index) => {
            const { round, x, step } = slot;
            const pairWidth = Math.min(MAX_BAR_WIDTH * 2 + BAR_GAP, Math.max(5, step * 0.78));
            const barWidth = Math.max(2, (pairWidth - BAR_GAP) / 2);
            const left = x + (step - pairWidth) / 2;
            const selected = round.roundNumber === selectedRound;
            return (
              <g key={round.roundNumber} className={`economy-chart-pair${selected ? " selected" : ""}`} role="button"
                tabIndex={index === activeIndex ? 0 : -1}
                ref={(element) => { pairRefs.current[index] = element; }}
                aria-label={pairLabel(round)}
                aria-current={selected ? "step" : undefined}
                onClick={() => { setFocusIndex(index); onSelectRound(round.roundNumber); }}
                onKeyDown={(event) => handleKeyDown(event, index)}>
                <title>{pairLabel(round)}</title>
                <rect className="economy-chart-hit" x={x} y={PLOT_TOP - 4} width={step} height={labelY - PLOT_TOP + 8} />
                {round.teams.map((team, teamIndex) => {
                  const barX = left + teamIndex * (barWidth + BAR_GAP);
                  return (
                    <g key={team.teamKey}>
                      {team.kind !== null && team.equipValue > 0 ? (
                        <rect className={`economy-chart-bar team-${team.teamKey.toLowerCase()} ${sideClass(team.side)}`}
                          x={barX} y={yFor(team.equipValue)} width={barWidth} height={baseline - yFor(team.equipValue)} />
                      ) : null}
                      {team.won ? (
                        <rect className="economy-chart-win" x={barX + (barWidth - Math.min(WIN_MARK, barWidth)) / 2} y={winMarkY}
                          width={Math.min(WIN_MARK, barWidth)} height={WIN_MARK} />
                      ) : null}
                    </g>
                  );
                })}
                {showRoundLabel(slot) ? (
                  <text className={`economy-chart-round${selected ? " selected" : ""}`} x={centre(slot)} y={labelY} textAnchor="middle">
                    {round.roundNumber}
                  </text>
                ) : null}
              </g>
            );
          })}
          <line className="economy-chart-axis" x1={AXIS_WIDTH} x2={width} y1={baseline} y2={baseline} aria-hidden="true" />
        </svg>
      </div>
      {dataTable}
    </section>
  );
}

// One slot per round; a gap wherever team A's side changes (half time, overtime halves).
function layoutSlots(rounds: readonly RoundEconomy[], width: number): SlotLayout[] {
  const switches = rounds.map((round, index) => index > 0 && sideChanged(rounds[index - 1].teams[0].side, round.teams[0].side));
  const gaps = switches.filter(Boolean).length;
  const available = Math.max(rounds.length, width - AXIS_WIDTH - 2 - gaps * HALF_GAP);
  const step = rounds.length > 0 ? available / rounds.length : 0;
  let x = AXIS_WIDTH + 2;
  return rounds.map((round, index) => {
    if (switches[index]) x += HALF_GAP;
    const slot = { round, x, step, switchBefore: switches[index] };
    x += step;
    return slot;
  });
}

function sideChanged(previous: PlayerSide | null, next: PlayerSide | null): boolean {
  return previous !== null && next !== null && previous !== next;
}

function sideClass(side: PlayerSide | null): string {
  return side === "T" ? "side-t" : side === "CT" ? "side-ct" : "side-unknown";
}

function shortMoney(value: number): string {
  return value === 0 ? "$0" : `$${Math.round(value / 1000)}k`;
}

function winnerText(round: RoundEconomy, names: EconomyTeamNames): string {
  const winner = round.teams.find((team) => team.won === true);
  return winner ? `${names[winner.teamKey]} 胜` : "—";
}

function teamCellText(team: TeamRoundEconomy): string {
  if (team.kind === null) return "—";
  return `${team.side ?? "—"} 方，${ECONOMY_LABELS[team.kind].name}，${formatMoney(team.equipValue)}`;
}

// "第 2 回合，MOUZ 强起 $14,250，Spirit 全起 $22,150，MOUZ 胜".
function economyPairLabel(round: RoundEconomy, names: EconomyTeamNames): string {
  const winner = round.teams.find((team) => team.won === true);
  return [`第 ${round.roundNumber} 回合`, ...economyTeamParts(round, names), ...(winner ? [`${names[winner.teamKey]} 胜`] : [])].join("，");
}
