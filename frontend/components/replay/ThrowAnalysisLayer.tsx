"use client";

import { memo } from "react";

import { UTILITY_ICONS } from "@/components/replay/UtilityLayer";
import { throwerPositionAt, type ThrowAnalysis } from "@/lib/throw-analysis";
import { utilityPositionAt, utilityRadiusPercent, type MapScaleSource } from "@/lib/utility";
import type { ReplayUtility } from "@/types/replay";

export interface ThrowAnalysisLayerProps {
  utility: ReplayUtility;
  analysis: ThrowAnalysis;
  /** The local playback tick. */
  tick: number;
  /** For the effect's true size (utilityRadiusPercent). */
  map: MapScaleSource | null;
  /** The current viewBox size / 100 (1 = the whole map): every marker size and stroke is multiplied by it. */
  scale: number;
}

// Marker sizes in radar percent at scale 1 (the mockup's values), so they keep one on-screen size.
const TRAJECTORY_STROKE = 0.8;
const PATH_STROKE = 0.45;
const PATH_DASH = [1, 0.7] as const;
const RELEASE_R = 2.2;
const RELEASE_STROKE = 0.4;
const RELEASE_RING_R = 3.8;
const RELEASE_RING_STROKE = 0.5;
const SMALL_RING_R = 2.6;
const SMALL_RING_STROKE = 0.5;
const EFFECT_ICON = 3.2;
const THROWER_R = 1.6;
const THROWER_STROKE = 0.4;
const GRENADE_R = 1.8;
const GRENADE_STROKE = 0.35;
const GRENADE_ICON = 2.4;
const LABEL_SIZE = 3.4;
const LABEL_STROKE = 0.5;
const LABEL_GAP = 1.4;

/**
 * One analysed throw on the finder's map (SVG, radar percent): the whole flight in the thrower's
 * side colour, the landing effect with its icon, the thrower's ±1 s path dotted, the release point
 * (side-colour dot in a red ring), the 出手 / 落点 labels, and at the playback tick the thrower and
 * the grenade while it is in the air. Draws nothing clickable.
 */
export const ThrowAnalysisLayer = memo(function ThrowAnalysisLayer({ utility, analysis, tick, map, scale }: ThrowAnalysisLayerProps) {
  const unit = Number.isFinite(scale) && scale > 0 ? scale : 1;
  const points = utility.points;
  if (points.length === 0) return null;
  const release = points[0];
  const landing = points[points.length - 1];
  const side = utility.throwerSide === "T" ? "t" : utility.throwerSide === "CT" ? "ct" : "unknown";
  const Icon = UTILITY_ICONS[utility.type];
  const area = utility.type === "smoke" || utility.type === "molotov";
  const effectRadius = area ? utilityRadiusPercent(utility.type, map) : SMALL_RING_R * unit;
  const effectIcon = round3(area ? Math.min(EFFECT_ICON * unit, effectRadius * 1.3) : EFFECT_ICON * unit * 0.8);
  const thrower = throwerPositionAt(analysis, tick);
  const flying = tick >= utility.throwTick && tick < utility.detonateTick ? utilityPositionAt(utility, tick) : null;
  const label = (x: number, y: number, text: string, className: string) => (
    <text className={`throw-analysis-label ${className}`} x={round3(x)} y={round3(y)} textAnchor="middle"
      fontSize={round3(LABEL_SIZE * unit)} strokeWidth={round3(LABEL_STROKE * unit)}>
      {text}
    </text>
  );

  return (
    <g className={`throw-analysis-layer side-${side}`} pointerEvents="none" data-testid="throw-analysis-layer">
      <g className={`throw-analysis-effect utility-${utility.type}`} transform={`translate(${landing.x} ${landing.y})`}>
        <circle className="throw-analysis-effect-area" r={round3(effectRadius)}
          strokeWidth={area ? undefined : round3(SMALL_RING_STROKE * unit)} />
        <Icon className="throw-analysis-effect-icon" x={-effectIcon / 2} y={-effectIcon / 2} width={effectIcon} height={effectIcon}
          strokeWidth={2} aria-hidden="true" />
      </g>
      <polyline className="throw-analysis-trajectory" points={pointList(points)} fill="none"
        strokeWidth={round3(TRAJECTORY_STROKE * unit)} strokeLinejoin="round" strokeLinecap="round" />
      <circle className="throw-analysis-release-dot" cx={release.x} cy={release.y} r={round3(RELEASE_R * unit)}
        strokeWidth={round3(RELEASE_STROKE * unit)} />
      <circle className="throw-analysis-release-ring" cx={release.x} cy={release.y} r={round3(RELEASE_RING_R * unit)}
        fill="none" strokeWidth={round3(RELEASE_RING_STROKE * unit)} />
      {/* Over the release marker: a second of running is only a few percent of the map. */}
      {analysis.throwerPath.length > 1 ? (
        <polyline className="throw-analysis-thrower-path" points={pointList(analysis.throwerPath)} fill="none"
          strokeWidth={round3(PATH_STROKE * unit)} strokeDasharray={PATH_DASH.map((value) => round3(value * unit)).join(" ")}
          strokeLinecap="round" />
      ) : null}
      {label(release.x, release.y - (RELEASE_RING_R + LABEL_GAP) * unit, "出手", "release")}
      {label(landing.x, landing.y - Math.max(effectRadius, SMALL_RING_R * unit) - LABEL_GAP * unit, "落点", "landing")}
      {thrower ? (
        <circle className="throw-analysis-thrower" data-testid="throw-analysis-thrower" cx={thrower.x} cy={thrower.y}
          r={round3(THROWER_R * unit)} strokeWidth={round3(THROWER_STROKE * unit)} />
      ) : null}
      {flying ? (
        <g className="throw-analysis-grenade" data-testid="throw-analysis-grenade" transform={`translate(${flying.x} ${flying.y})`}>
          <circle r={round3(GRENADE_R * unit)} strokeWidth={round3(GRENADE_STROKE * unit)} />
          <Icon className="throw-analysis-grenade-icon" x={round3(-GRENADE_ICON * unit / 2)} y={round3(-GRENADE_ICON * unit / 2)}
            width={round3(GRENADE_ICON * unit)} height={round3(GRENADE_ICON * unit)} strokeWidth={2.4} aria-hidden="true" />
        </g>
      ) : null}
    </g>
  );
});

function pointList(points: readonly { x: number; y: number }[]): string {
  return points.map((point) => `${point.x},${point.y}`).join(" ");
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}
