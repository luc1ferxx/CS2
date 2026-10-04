"use client";

import { CircleDashed, CircleDot, Cloud, Flame, Zap, type LucideIcon } from "lucide-react";
import { memo } from "react";

import { getTacticalMapLevel, type TacticalMapLevel, type TacticalMapPresentation } from "@/lib/map-config";
import { FINDER_UTILITY_TYPES, utilityActiveAt, type ActiveUtility } from "@/lib/utility";
import type { ReplayData, ReplayUtility, UtilityType } from "@/types/replay";

// Icons encode the grenade kind only (lucide, ISC); no game artwork.
export const UTILITY_ICONS: Record<UtilityType, LucideIcon> = {
  smoke: Cloud,
  flash: Zap,
  molotov: Flame,
  he: CircleDot,
  decoy: CircleDashed
};

// On-screen pixel sizes (drawn in a group scaled back to radar percent, like the player dots).
const ICON_PX = 12;
const EFFECT_GLYPH_PX = 12;
// A grenade in flight that opens its analysis takes clicks in a 24 px circle around its icon.
const HIT_RADIUS_PX = 12;
const FALLBACK_UNITS_PER_PIXEL = 100 / 640;
// A smoke or fire fades in over this long and out over its last second.
const FADE_SECONDS = 0.4;

interface UtilityLayerProps {
  replay: Pick<ReplayData, "utility" | "rounds" | "tickRate" | "mapMetadata">;
  currentTick: number;
  map: TacticalMapPresentation;
  /** The floor on screen on a two-floor map; null on single-floor maps. */
  floor: TacticalMapLevel | null;
  roundNumber?: number | null;
  /** When set, other players' grenades are dimmed like their dots. */
  focusPlayerId?: string | null;
  /** Radar-percent units per screen pixel (the viewer's overlay context). */
  unitsPerPixel?: number;
  /**
   * When set, a smoke, flash, fire or HE still in the air can be clicked (道具投掷分析). Effects stay
   * click-through, so the player dots under a smoke keep their own clicks.
   */
  onSelect?: (utility: ReplayUtility) => void;
}

/**
 * Grenades during playback, drawn inside the tactical map's SVG (radar percent): in flight an icon
 * with a short trail in the thrower's side colour, then the effect (smoke cloud, fire, flash burst,
 * explosion ring) for as long as it lasts. Throws on the other floor of a two-floor map are dimmed.
 * The page draws it through the viewer's `overlayAbove` slot, over the players: a smoke is smaller
 * than a player dot on most maps and would vanish under a stack of them.
 */
export const UtilityLayer = memo(function UtilityLayer({
  replay,
  currentTick,
  map,
  floor,
  roundNumber,
  focusPlayerId = null,
  unitsPerPixel = FALLBACK_UNITS_PER_PIXEL,
  onSelect
}: UtilityLayerProps) {
  const active = utilityActiveAt(replay, currentTick, { map, roundNumber });
  if (active.length === 0) return null;
  const tickRate = replay.tickRate > 0 ? replay.tickRate : 64;
  // Effects first, so grenades still in the air are drawn over them.
  const ordered = [...active.filter((item) => item.phase === "effect"), ...active.filter((item) => item.phase === "flight")];

  function itemClass(item: ActiveUtility): string {
    const level = floor ? getTacticalMapLevel(map, item.z) : null;
    const otherFloor = floor !== null && level !== null && level !== floor;
    const unfocused = focusPlayerId !== null && item.utility.throwerId !== focusPlayerId;
    return `utility-item${otherFloor ? " other-floor" : ""}${unfocused ? " focus-dimmed" : ""}`;
  }

  return (
    <g className="utility-layer" data-testid="utility-layer" pointerEvents="none">
      {ordered.map((item) => (
        <g key={item.utility.id} className={itemClass(item)}>
          {item.phase === "effect"
            ? <UtilityEffect item={item} remainingSeconds={(item.endsAt - currentTick) / tickRate} unit={unitsPerPixel} />
            : <UtilityFlight item={item} unit={unitsPerPixel} onSelect={onSelect} />}
        </g>
      ))}
    </g>
  );
});

function UtilityEffect({ item, remainingSeconds, unit }: { item: ActiveUtility; remainingSeconds: number; unit: number }) {
  const { utility } = item;
  const radius = item.radius ?? 2;
  const common = {
    "data-utility-id": utility.id,
    "data-phase": "effect",
    className: `utility-effect utility-${utility.type}`,
    transform: `translate(${item.x} ${item.y})`
  };
  if (utility.type === "flash") {
    return (
      <g {...common} opacity={round2(1 - item.progress)}>
        <circle className="utility-flash-burst" r={round2(radius * (0.5 + item.progress * 0.5))} />
      </g>
    );
  }
  if (utility.type === "he") {
    return (
      <g {...common} opacity={round2(1 - item.progress)}>
        <circle className="utility-he-ring" r={round2(radius * (0.35 + item.progress * 0.65))} />
      </g>
    );
  }
  // Smoke and fire: a steady translucent area that fades in, and out over its last second. It is
  // drawn over the players (they show through it), without a ring, and with its kind's glyph in the
  // middle, so it never reads as a player dot.
  const opacity = Math.max(0, Math.min(1, item.age / FADE_SECONDS, remainingSeconds));
  const Icon = UTILITY_ICONS[utility.type];
  // The area keeps its true size; the glyph is a fixed pixel size, never wider than the area.
  const glyph = round2(Math.min(EFFECT_GLYPH_PX, (radius / unit) * 1.3));
  return (
    <g {...common} opacity={round2(opacity)}>
      <circle className={utility.type === "smoke" ? "utility-smoke-cloud" : "utility-fire-area"} r={radius} />
      <g transform={`scale(${round4(unit)})`}>
        <Icon className="utility-effect-glyph" x={-glyph / 2} y={-glyph / 2} width={glyph} height={glyph}
          strokeWidth={2} aria-hidden="true" />
      </g>
    </g>
  );
}

function UtilityFlight({
  item,
  unit,
  onSelect
}: {
  item: ActiveUtility;
  unit: number;
  onSelect?: (utility: ReplayUtility) => void;
}) {
  const { utility } = item;
  const Icon = UTILITY_ICONS[utility.type];
  // Pointer-only, like the player dots: 道具反查's list offers the same analysis to the keyboard.
  const select = onSelect && (FINDER_UTILITY_TYPES as readonly UtilityType[]).includes(utility.type) ? onSelect : null;
  const side = utility.throwerSide ? utility.throwerSide.toLowerCase() : "unknown";
  const segments = item.trail.slice(1).map((point, index) => ({ from: item.trail[index], to: point }));
  return (
    <g className={`utility-flight utility-${utility.type} side-${side}`} data-utility-id={utility.id} data-phase="flight">
      {segments.map((segment, index) => (
        <line key={index} className="utility-trail"
          x1={segment.from.x} y1={segment.from.y} x2={segment.to.x} y2={segment.to.y}
          opacity={round2(((index + 1) / segments.length) * 0.8)} />
      ))}
      <g transform={`translate(${item.x} ${item.y}) scale(${round4(unit)})`}
        className={select ? "utility-flight-select" : undefined}
        pointerEvents={select ? "auto" : undefined}
        style={select ? { cursor: "pointer" } : undefined}
        onClick={select ? () => select(utility) : undefined}>
        {select ? (
          <>
            <title>分析这颗道具</title>
            <circle className="utility-flight-hit" r={HIT_RADIUS_PX} fill="transparent" />
          </>
        ) : null}
        <circle className="utility-flight-body" r={ICON_PX / 2} />
        <Icon className="utility-flight-icon" x={-(ICON_PX - 4) / 2} y={-(ICON_PX - 4) / 2}
          width={ICON_PX - 4} height={ICON_PX - 4} strokeWidth={2.4} aria-hidden="true" />
      </g>
    </g>
  );
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}
