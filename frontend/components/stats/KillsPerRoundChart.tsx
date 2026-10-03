"use client";

import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

import type { PlayerRoundKills } from "@/lib/match-stats";
import type { PlayerSide } from "@/types/replay";

interface KillsPerRoundChartProps {
  rows: PlayerRoundKills[];
  onSelectRound: (roundNumber: number) => void;
}

// Drawn at the measured pixel width, so 11 px labels stay 11 px on a phone and on a wide screen.
const FALLBACK_WIDTH = 360;
const AXIS_WIDTH = 22;
const PLOT_TOP = 8;
const HALF_GAP = 10;
const MAX_BAR_WIDTH = 22;

interface BarLayout {
  row: PlayerRoundKills;
  x: number;
  step: number;
}

export function KillsPerRoundChart({ rows, onSelectRound }: KillsPerRoundChartProps) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const barRefs = useRef<(SVGGElement | null)[]>([]);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  const [focusIndex, setFocusIndex] = useState(0);

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

  // Taller where the cell beside the radar has the room; short on a phone.
  const plotHeight = width >= 480 ? 200 : 120;
  const baseline = PLOT_TOP + plotHeight;
  const deathMarkY = baseline + 3;
  const labelY = baseline + 22;
  const chartHeight = baseline + 28;
  const maxKills = Math.max(1, ...rows.map((row) => row.kills));
  const bars = layoutBars(rows, width);
  const yFor = (kills: number) => baseline - (kills / maxKills) * plotHeight;
  const totalKills = rows.reduce((sum, row) => sum + row.kills, 0);
  const roundsWithKill = rows.filter((row) => row.kills > 0).length;
  const activeIndex = Math.min(focusIndex, Math.max(0, rows.length - 1));

  function handleKeyDown(event: KeyboardEvent<SVGGElement>, index: number) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectRound(rows[index].roundNumber);
      return;
    }
    const next = event.key === "ArrowRight" ? index + 1
      : event.key === "ArrowLeft" ? index - 1
        : event.key === "Home" ? 0
          : event.key === "End" ? rows.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    const target = Math.max(0, Math.min(rows.length - 1, next));
    setFocusIndex(target);
    barRefs.current[target]?.focus();
  }

  return (
    <section className="match-analysis-cell kills-chart" aria-labelledby="kills-chart-title">
      <div className="match-analysis-cell-bar">
        <h3 id="kills-chart-title">每回合击杀</h3>
        <span className="kills-chart-legend" aria-hidden="true">
          <span><i className="swatch side-t" />T 方</span>
          <span><i className="swatch side-ct" />CT 方</span>
          <span><i className="swatch died" />阵亡</span>
        </span>
      </div>
      <p className="match-analysis-caption">
        {/* The death count is 阵亡位置's first fact; the red marks under the bars show which rounds. */}
        共 {totalKills} 杀，{roundsWithKill} 个回合有击杀
      </p>
      <div className="kills-chart-frame" ref={frameRef}>
        {rows.length === 0 ? <p className="match-analysis-caption">没有回合数据。</p> : (
          <svg width={width} height={chartHeight} viewBox={`0 0 ${width} ${chartHeight}`}
            role="group" aria-label="每回合击杀柱状图，点击柱子跳到那个回合">
            {yTicks(maxKills).map((value) => (
              <g key={value} aria-hidden="true">
                <line className="kills-chart-grid" x1={AXIS_WIDTH} x2={width} y1={yFor(value)} y2={yFor(value)} />
                <text className="kills-chart-tick" x={AXIS_WIDTH - 6} y={yFor(value) + 4} textAnchor="end">{value}</text>
              </g>
            ))}
            {bars.map(({ row, x, step }, index) => {
              const barWidth = Math.min(MAX_BAR_WIDTH, Math.max(3, step * 0.68));
              const barX = x + (step - barWidth) / 2;
              return (
                <g key={row.roundNumber} className="kills-chart-bar" role="button"
                  tabIndex={index === activeIndex ? 0 : -1}
                  ref={(element) => { barRefs.current[index] = element; }}
                  aria-label={barLabel(row)}
                  onClick={() => { setFocusIndex(index); onSelectRound(row.roundNumber); }}
                  onKeyDown={(event) => handleKeyDown(event, index)}>
                  <title>{barLabel(row)}</title>
                  <rect className="kills-chart-hit" x={x} y={PLOT_TOP - 4} width={step} height={labelY - PLOT_TOP + 8} />
                  {row.kills > 0 ? (
                    <rect className={`kills-chart-fill ${sideClass(row.side)}`} x={barX} y={yFor(row.kills)}
                      width={barWidth} height={baseline - yFor(row.kills)} />
                  ) : null}
                  {row.died ? <rect className="kills-chart-death" x={barX} y={deathMarkY} width={barWidth} height={3} /> : null}
                  {row.roundNumber % 3 === 0 ? (
                    <text className="kills-chart-round" x={x + step / 2} y={labelY} textAnchor="middle">{row.roundNumber}</text>
                  ) : null}
                </g>
              );
            })}
            <line className="kills-chart-axis" x1={AXIS_WIDTH} x2={width} y1={baseline} y2={baseline} aria-hidden="true" />
          </svg>
        )}
      </div>
      <table className="visually-hidden">
        <caption>每回合击杀</caption>
        <thead><tr><th scope="col">回合</th><th scope="col">阵营</th><th scope="col">击杀</th><th scope="col">阵亡</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.roundNumber}>
              <th scope="row">第 {row.roundNumber} 回合</th>
              <td>{row.side ?? "—"}</td>
              <td>{row.kills}</td>
              <td>{row.died ? "是" : "否"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// One slot per round; a gap wherever the player's side changes (half time, overtime halves).
function layoutBars(rows: PlayerRoundKills[], width: number): BarLayout[] {
  const switches = rows.filter((row, index) => index > 0 && sideChanged(rows[index - 1].side, row.side)).length;
  const available = Math.max(rows.length, width - AXIS_WIDTH - 2 - switches * HALF_GAP);
  const step = rows.length > 0 ? available / rows.length : 0;
  let x = AXIS_WIDTH + 2;
  return rows.map((row, index) => {
    if (index > 0 && sideChanged(rows[index - 1].side, row.side)) x += HALF_GAP;
    const bar = { row, x, step };
    x += step;
    return bar;
  });
}

function sideChanged(previous: PlayerSide | null, next: PlayerSide | null): boolean {
  return previous !== null && next !== null && previous !== next;
}

function yTicks(maxKills: number): number[] {
  if (maxKills <= 5) return Array.from({ length: maxKills + 1 }, (_, index) => index);
  return [0, Math.round(maxKills / 2), maxKills];
}

function sideClass(side: PlayerSide | null): string {
  return side === "T" ? "side-t" : side === "CT" ? "side-ct" : "side-unknown";
}

function barLabel(row: PlayerRoundKills): string {
  const side = row.side ? `，${row.side} 方` : "";
  return `第 ${row.roundNumber} 回合${side}，${row.kills} 杀${row.died ? "，阵亡" : ""}`;
}
