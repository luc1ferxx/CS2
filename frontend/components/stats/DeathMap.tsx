"use client";

import { useMemo, useState } from "react";

import {
  getTacticalMapLevel,
  getTacticalMapPresentation,
  sanitizeRadarPercent,
  type TacticalMapLevel
} from "@/lib/map-config";
import type { PlayerDeath } from "@/lib/match-stats";
import { weaponName } from "@/lib/replay-events";
import { liveRoundTimeAt } from "@/lib/replay-time";
import type { ReplayData } from "@/types/replay";

// Two deaths closer than this (radar percent) count as the same spot for the caption.
const CLUSTER_RADIUS = 6;
// The radar is framed on the deaths (both floors), never tighter than half the map.
const FRAME_PADDING = 10;
const MIN_FRAME = 50;
// Dot buttons are 20 px on a canvas of about 340 px: centres closer than this (percent of the
// canvas) would cover each other, so such dots fan out around their shared spot and each stays
// clickable. The halos keep the true positions.
const DOT_SPACING = 6;

interface DeathMapProps {
  replay: Pick<ReplayData, "mapName" | "mapMetadata" | "rounds" | "tickRate">;
  // The reviewed player: a death by their own hand reads "自杀".
  playerId: string;
  deaths: PlayerDeath[];
  onSelectDeath: (death: PlayerDeath) => void;
}

interface PlacedDeath {
  death: PlayerDeath;
  x: number;
  y: number;
  // null on single-floor maps and when the height is missing (shown on the upper floor).
  level: TacticalMapLevel | null;
}

export function DeathMap({ replay, playerId, deaths, onSelectDeath }: DeathMapProps) {
  const map = useMemo(() => getTacticalMapPresentation(replay), [replay]);
  const hasFloors = Boolean(map.radarImagePath && map.secondaryRadarImagePath);
  const placed = useMemo(() => deaths.flatMap((death): PlacedDeath[] => {
    const x = sanitizeRadarPercent(death.x);
    const y = sanitizeRadarPercent(death.y);
    if (x === null || y === null) return [];
    return [{ death, x, y, level: hasFloors ? getTacticalMapLevel(map, death.z) : null }];
  }), [deaths, hasFloors, map]);
  const lowerCount = placed.filter((item) => item.level === "lower").length;
  const upperCount = placed.length - lowerCount;
  const unknownHeight = hasFloors ? placed.filter((item) => item.level === null).length : 0;
  // Open on the floor where most of the deaths happened; the viewer can switch.
  const [chosenLevel, setChosenLevel] = useState<TacticalMapLevel | null>(null);
  const level: TacticalMapLevel = chosenLevel ?? (lowerCount > upperCount ? "lower" : "upper");
  const visible = hasFloors
    ? placed.filter((item) => (item.level === "lower") === (level === "lower"))
    : placed;
  const radarImagePath = hasFloors && level === "lower" ? map.secondaryRadarImagePath ?? null : map.radarImagePath;
  const cluster = useMemo(() => densestSpot(placed), [placed]);
  const frame = useMemo(() => framing(placed), [placed]);
  const dots = useMemo(() => spreadDots(visible.map((item) => ({
    item,
    left: ((item.x - frame.x) / frame.size) * 100,
    top: ((item.y - frame.y) / frame.size) * 100
  }))), [frame, visible]);
  const headshots = deaths.filter((death) => death.headshot).length;
  const topKiller = mostFrequent(deaths.map((death) => (death.killerId === playerId ? null : death.killerName)));
  const topWeapon = mostFrequent(deaths.map((death) => weaponName(death.weapon)));
  const floorName = level === "lower" ? "下层" : "上层";

  return (
    <section className="match-analysis-cell death-map" aria-labelledby="death-map-title">
      <div className="match-analysis-cell-bar">
        <h3 id="death-map-title">阵亡位置</h3>
        {hasFloors ? (
          <div className="death-map-floors" role="group" aria-label="地图楼层">
            <button type="button" className="death-map-floor" aria-pressed={level === "upper"} onClick={() => setChosenLevel("upper")}>
              上层 {upperCount}
            </button>
            <button type="button" className="death-map-floor" aria-pressed={level === "lower"} onClick={() => setChosenLevel("lower")}>
              下层 {lowerCount}
            </button>
          </div>
        ) : null}
      </div>
      <div className="death-map-body">
        <div className="death-map-canvas" role="group"
          aria-label={`${map.displayName}${hasFloors ? floorName : ""}阵亡位置，${visible.length} 个点`}>
          <svg viewBox={`${frame.x} ${frame.y} ${frame.size} ${frame.size}`} aria-hidden="true" focusable="false">
            {radarImagePath ? (
              <image className="death-map-radar" href={radarImagePath} x="0" y="0" width="100" height="100" preserveAspectRatio="none" />
            ) : (
              <>
                <defs>
                  <pattern id="death-map-grid" width="10" height="10" patternUnits="userSpaceOnUse">
                    <path d="M 10 0 L 0 0 0 10" fill="none" className="death-map-grid-line" strokeWidth="0.3" />
                  </pattern>
                </defs>
                <rect x="0" y="0" width="100" height="100" fill="url(#death-map-grid)" />
              </>
            )}
            {/* Overlapping translucent discs darken where deaths repeat. */}
            {visible.map((item) => (
              <circle key={`halo-${item.death.tick}`} className="death-map-halo" cx={item.x} cy={item.y} r={frame.size / 20} />
            ))}
          </svg>
          {dots.map(({ item, left, top }) => {
            const label = deathLabel(item.death, playerId, replay);
            return (
              <button key={item.death.tick} type="button" className="death-map-dot"
                style={{ left: `${left}%`, top: `${top}%` }}
                aria-label={label} title={label} onClick={() => onSelectDeath(item.death)} />
            );
          })}
          {!radarImagePath ? <span className="death-map-uncalibrated">这张地图没有雷达图，位置仅供参考</span> : null}
        </div>
        <div className="death-map-notes">
          {deaths.length === 0 ? (
            <p className="death-map-empty">这场比赛没有阵亡记录。</p>
          ) : (
            <dl className="death-map-facts">
              <div><dt>阵亡</dt><dd>{deaths.length} 次</dd></div>
              <div><dt>被爆头</dt><dd>{headshots} 次</dd></div>
              {topKiller ? <div><dt>被谁击杀最多</dt><dd>{topKiller.value}（{topKiller.count} 次）</dd></div> : null}
              {topWeapon ? <div><dt>最常见的武器</dt><dd>{topWeapon.value}（{topWeapon.count} 次）</dd></div> : null}
            </dl>
          )}
          {cluster ? (
            <p className="death-map-cluster">
              最集中的一片：{cluster.length} 次，第 {cluster.map((item) => item.death.roundNumber).join("、")} 回合
              {hasFloors ? `（${cluster[0].level === "lower" ? "下层" : "上层"}）` : ""}
            </p>
          ) : null}
          {unknownHeight > 0 ? <p className="death-map-note">{unknownHeight} 次没有高度数据，画在上层。</p> : null}
        </div>
      </div>
    </section>
  );
}

function deathLabel(death: PlayerDeath, playerId: string, replay: Pick<ReplayData, "rounds" | "tickRate">): string {
  const round = replay.rounds.find((item) => item.roundNumber === death.roundNumber);
  const moment = `第 ${death.roundNumber} 回合 ${liveRoundTimeAt(death.tick, round, replay.tickRate)}`;
  const weapon = weaponName(death.weapon);
  const headshot = death.headshot ? "（爆头）" : "";
  if (death.killerId === playerId) return `${moment} 自杀${weapon ? `（${weapon}）` : ""}`;
  if (!death.killerName) return `${moment} 阵亡${headshot}`;
  return `${moment} 被 ${death.killerName}${weapon ? ` 用 ${weapon}` : ""} 击杀${headshot}`;
}

// Groups dots whose centres (canvas percent) are closer than DOT_SPACING and spreads each group
// on a small ring around its centre, wide enough that neighbours on the ring do not overlap.
function spreadDots<T extends { left: number; top: number }>(dots: T[]): T[] {
  const placed = dots.map((dot) => ({ ...dot }));
  const grouped = new Set<number>();
  for (let first = 0; first < dots.length; first += 1) {
    if (grouped.has(first)) continue;
    const group = [first];
    for (let other = first + 1; other < dots.length; other += 1) {
      if (grouped.has(other)) continue;
      if (group.some((member) => Math.hypot(dots[member].left - dots[other].left, dots[member].top - dots[other].top) < DOT_SPACING)) {
        group.push(other);
      }
    }
    if (group.length < 2) continue;
    for (const member of group) grouped.add(member);
    const centreLeft = group.reduce((sum, member) => sum + dots[member].left, 0) / group.length;
    const centreTop = group.reduce((sum, member) => sum + dots[member].top, 0) / group.length;
    const radius = DOT_SPACING / (2 * Math.sin(Math.PI / group.length));
    group.forEach((member, position) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * position) / group.length;
      placed[member].left = Math.min(100, Math.max(0, centreLeft + radius * Math.cos(angle)));
      placed[member].top = Math.min(100, Math.max(0, centreTop + radius * Math.sin(angle)));
    });
  }
  return placed;
}

// A square window (radar percent) around every death, padded and clamped to the radar.
function framing(placed: PlacedDeath[]): { x: number; y: number; size: number } {
  if (placed.length === 0) return { x: 0, y: 0, size: 100 };
  const xs = placed.map((item) => item.x);
  const ys = placed.map((item) => item.y);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const size = Math.min(100, Math.max(MIN_FRAME, Math.max(maxX - minX, maxY - minY) + FRAME_PADDING * 2));
  const origin = (low: number, high: number) => Math.min(100 - size, Math.max(0, (low + high) / 2 - size / 2));
  return { x: origin(minX, maxX), y: origin(minY, maxY), size };
}

// The death with the most others within CLUSTER_RADIUS on the same floor, and those others
// (in round order); null when no two deaths share a spot. At most a few dozen deaths, so O(n²).
function densestSpot(placed: PlacedDeath[]): PlacedDeath[] | null {
  let best: PlacedDeath[] = [];
  for (const center of placed) {
    const near = placed.filter((item) => (item.level === "lower") === (center.level === "lower") &&
      Math.hypot(item.x - center.x, item.y - center.y) <= CLUSTER_RADIUS);
    if (near.length > best.length) best = near;
  }
  return best.length >= 2 ? [...best].sort((left, right) => left.death.tick - right.death.tick) : null;
}

function mostFrequent(values: (string | null)[]): { value: string; count: number } | null {
  const counts = new Map<string, number>();
  for (const value of values) {
    if (value) counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  let best: { value: string; count: number } | null = null;
  for (const [value, count] of counts) {
    if (!best || count > best.count) best = { value, count };
  }
  // Once is not a pattern.
  return best && best.count >= 2 ? best : null;
}
