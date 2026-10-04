"use client";

import { memo, useCallback, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { teamDisplayName } from "@/components/replay/MatchScoreBanner";
import { RadarGraticule } from "@/components/replay/RadarGraticule";
import { UTILITY_ICONS } from "@/components/replay/UtilityLayer";
import {
  getTacticalMapPresentation,
  resolveTacticalMapLevel,
  type TacticalMapLevel
} from "@/lib/map-config";
import type { MatchTeam, TeamKey } from "@/lib/match-stats";
import { liveRoundTimeAt } from "@/lib/replay-time";
import {
  FINDER_UTILITY_TYPES,
  UTILITY_LABELS,
  UTILITY_SHORT_LABELS,
  filterThrows,
  landingPoint,
  normalizeRect,
  replayUtility,
  roundsForScope,
  throwPoint,
  throwsInRect,
  utilityLevel,
  type FinderUtilityType,
  type UtilityRect,
  type UtilityRoundScope
} from "@/lib/utility";
import type { ReplayData, ReplayUtility } from "@/types/replay";

/** The finder's filters and selection; the page keeps it so it survives a trip to 战术回放 and back. */
export interface UtilityFinderState {
  type: FinderUtilityType;
  team: TeamKey | "all";
  playerId: string | null;
  roundScope: UtilityRoundScope;
  rect: UtilityRect | null;
  floor: TacticalMapLevel;
}

// Opens on the round being reviewed: a whole match of smokes piles up into one blot.
export const DEFAULT_UTILITY_FINDER_STATE: UtilityFinderState = {
  type: "smoke", team: "all", playerId: null, roundScope: "current", rect: null, floor: "upper"
};

const ROUND_SCOPES: { value: UtilityRoundScope; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "current", label: "本回合" },
  { value: "first", label: "上半场" },
  { value: "second", label: "下半场" }
];
// A drag shorter than this (radar percent) is a click, not a selection.
const MIN_SELECTION = 1;
const ICON_SIZE = 2.4;
const FLOOR_NAMES: Record<TacticalMapLevel, string> = { upper: "上层", lower: "下层" };

interface UtilityFinderProps {
  replay: ReplayData;
  teams: readonly MatchTeam[];
  currentRound: number | null;
  state: UtilityFinderState;
  onStateChange: (state: UtilityFinderState) => void;
  /** "看这颗": the page seeks to the throw and shows 战术回放. */
  onJump: (utility: ReplayUtility) => void;
}

/**
 * 道具反查: every throw of one kind on the map, filtered by thrower and rounds, with an optional
 * rectangle around a landing area. The list is the keyboard path; the rectangle is optional.
 * A press anywhere on the map starts a rectangle; a press that does not move on a trajectory is a
 * click on that throw. While the rectangle is drawn only its own <rect> changes (no re-render).
 */
export const UtilityFinder = memo(function UtilityFinder({
  replay,
  teams,
  currentRound,
  state,
  onStateChange,
  onJump
}: UtilityFinderProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const draftRef = useRef<SVGRectElement | null>(null);
  const dragRef = useRef<{ start: { x: number; y: number }; pointerId: number; throwId: string | null } | null>(null);
  // Where the rectangle being drawn started; null when none is being drawn.
  const [draftStart, setDraftStart] = useState<{ x: number; y: number } | null>(null);
  const drafting = draftStart !== null;
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const map = useMemo(() => getTacticalMapPresentation(replay), [replay]);
  const hasFloors = Boolean(map.secondaryRadarImagePath);
  const floor = resolveTacticalMapLevel(map, hasFloors ? state.floor : "upper", null);
  const tickRate = replay.tickRate > 0 ? replay.tickRate : 64;
  const roundsByNumber = useMemo(() => new Map(replay.rounds.map((round) => [round.roundNumber, round])), [replay.rounds]);
  const playerNames = useMemo(() => new Map(replay.players.map((player) => [player.id, player.name])), [replay.players]);

  const team = state.team === "all" ? null : teams.find((item) => item.key === state.team) ?? null;
  const allThrows = replayUtility(replay);
  const matching = useMemo(() => filterThrows(allThrows, {
    types: [state.type],
    team,
    playerId: state.playerId,
    rounds: roundsForScope(replay.rounds, state.roundScope, currentRound)
  }), [allThrows, currentRound, replay.rounds, state.playerId, state.roundScope, state.type, team]);
  const floorLevel = floor.level;
  // On a two-floor map a selection drawn on one floor's image only takes throws that landed there.
  const onFloor = useCallback((utility: ReplayUtility) => {
    if (!hasFloors) return true;
    const level = utilityLevel(map, utility);
    return level === null || level === floorLevel;
  }, [floorLevel, hasFloors, map]);
  // On a two-floor map the list and its count follow the floor on screen, like the map's dimming.
  const onScreen = useMemo(() => (hasFloors ? matching.filter(onFloor) : matching), [hasFloors, matching, onFloor]);
  const selected = useMemo(
    () => (state.rect ? throwsInRect(onScreen, state.rect) : onScreen),
    [onScreen, state.rect]
  );
  const selectedIds = useMemo(() => new Set(selected.map((utility) => utility.id)), [selected]);
  const playerOptions = useMemo(() => {
    const ids = team ? team.playerIds : teams.flatMap((item) => item.playerIds);
    const known = ids.length > 0 ? ids : replay.players.map((player) => player.id);
    return known.map((id) => ({ id, name: playerNames.get(id) ?? id }));
  }, [playerNames, replay.players, team, teams]);

  const label = UTILITY_LABELS[state.type];
  const update = (patch: Partial<UtilityFinderState>) => onStateChange({ ...state, ...patch });
  const nameOf = (utility: ReplayUtility) =>
    utility.throwerName ?? (utility.throwerId ? playerNames.get(utility.throwerId) : undefined) ?? "未知玩家";
  const timeOf = (utility: ReplayUtility) =>
    liveRoundTimeAt(utility.throwTick, roundsByNumber.get(utility.roundNumber), tickRate);

  // The square viewBox is centred in the element ("xMidYMid meet"), so measure against that square.
  function pointerPercent(event: ReactPointerEvent<SVGSVGElement>): { x: number; y: number } | null {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box || box.width <= 0 || box.height <= 0) return null;
    const size = Math.min(box.width, box.height);
    const left = box.left + (box.width - size) / 2;
    const top = box.top + (box.height - size) / 2;
    return { x: ((event.clientX - left) / size) * 100, y: ((event.clientY - top) / size) * 100 };
  }

  function drawDraft(rect: UtilityRect) {
    const element = draftRef.current;
    if (!element) return;
    element.setAttribute("x", String(rect.x0));
    element.setAttribute("y", String(rect.y0));
    element.setAttribute("width", String(round2(rect.x1 - rect.x0)));
    element.setAttribute("height", String(round2(rect.y1 - rect.y0)));
  }

  function handlePointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return;
    const point = pointerPercent(event);
    if (!point) return;
    event.preventDefault();
    const throwElement = event.target instanceof Element ? event.target.closest(".utility-finder-throw") : null;
    dragRef.current = { start: point, pointerId: event.pointerId, throwId: throwElement?.getAttribute("data-utility-id") ?? null };
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // A pointer that is already gone cannot be captured; the drag still works without it.
    }
    setDraftStart(point);
  }

  function handlePointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = pointerPercent(event);
    if (point) drawDraft(normalizeRect(drag.start, point));
  }

  function handlePointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDraftStart(null);
    const point = pointerPercent(event) ?? drag.start;
    const rect = normalizeRect(drag.start, point);
    if (rect.x1 - rect.x0 >= MIN_SELECTION || rect.y1 - rect.y0 >= MIN_SELECTION) {
      update({ rect });
      return;
    }
    // Pressed and released in place on a trajectory: that throw.
    const clicked = drag.throwId ? matching.find((utility) => utility.id === drag.throwId) : undefined;
    if (clicked) onJump(clicked);
  }

  function cancelDrag() {
    dragRef.current = null;
    setDraftStart(null);
  }

  const heading = state.rect
    ? `落在选区里的${label} ${selected.length} 颗`
    : hasFloors ? `${FLOOR_NAMES[floor.level]}的${label} ${selected.length} 颗` : `${label} ${selected.length} 颗`;

  return (
    <div className="utility-finder">
      <div className="utility-finder-map">
        <svg
          ref={svgRef}
          viewBox="0 0 100 100"
          role="img"
          aria-label={`${map.displayName} ${label}落点图${hasFloors ? `（${FLOOR_NAMES[floor.level]}）` : ""}，拖动可框出落点区域`}
          className={drafting ? "drafting" : undefined}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={cancelDrag}
        >
          {floor.radarImagePath ? (
            <>
              <image className="map-radar-image" href={floor.radarImagePath} x="0" y="0" width="100" height="100"
                preserveAspectRatio="none" />
              <RadarGraticule />
            </>
          ) : (
            <rect className="utility-finder-fallback" x="0" y="0" width="100" height="100" />
          )}
          {matching.map((utility) => (
            <FinderThrow
              key={utility.id}
              utility={utility}
              dimmed={(state.rect !== null && !selectedIds.has(utility.id)) || !onFloor(utility)}
              hovered={hoveredId === utility.id}
              title={`第 ${utility.roundNumber} 回合 ${timeOf(utility)} ${nameOf(utility)}的${label}`}
            />
          ))}
          {drafting ? (
            <rect ref={draftRef} className="utility-finder-selection drafting" data-testid="utility-selection"
              x={draftStart?.x ?? 0} y={draftStart?.y ?? 0} width={0} height={0} />
          ) : state.rect ? (
            <rect className="utility-finder-selection" data-testid="utility-selection"
              x={state.rect.x0} y={state.rect.y0} width={state.rect.x1 - state.rect.x0} height={state.rect.y1 - state.rect.y0} />
          ) : null}
        </svg>
      </div>

      <div className="utility-finder-side">
        <div className="utility-finder-filters">
          <div className="utility-finder-filter" role="group" aria-label="道具类型">
            <span className="utility-finder-filter-label" aria-hidden="true">道具</span>
            <div className="utility-finder-segments">
              {FINDER_UTILITY_TYPES.map((type) => (
                <button key={type} type="button" className="utility-finder-segment" aria-pressed={state.type === type}
                  onClick={() => update({ type })}>
                  {UTILITY_SHORT_LABELS[type]}
                </button>
              ))}
            </div>
          </div>
          <div className="utility-finder-filter" role="group" aria-label="投掷者">
            <span className="utility-finder-filter-label" aria-hidden="true">投掷者</span>
            <div className="utility-finder-segments">
              <button type="button" className="utility-finder-segment" aria-pressed={state.team === "all"}
                onClick={() => update({ team: "all" })}>全部</button>
              {teams.map((item) => (
                <button key={item.key} type="button" className="utility-finder-segment" aria-pressed={state.team === item.key}
                  onClick={() => update({
                    team: item.key,
                    playerId: state.playerId && item.playerIds.includes(state.playerId) ? state.playerId : null
                  })}>
                  {teamDisplayName(item)}
                </button>
              ))}
            </div>
            <select aria-label="投掷玩家" className="utility-finder-player" value={state.playerId ?? ""}
              onChange={(event) => update({ playerId: event.target.value || null })}>
              <option value="">全部玩家</option>
              {playerOptions.map((player) => <option key={player.id} value={player.id}>{player.name}</option>)}
            </select>
          </div>
          <div className="utility-finder-filter" role="group" aria-label="回合范围">
            <span className="utility-finder-filter-label" aria-hidden="true">回合</span>
            <div className="utility-finder-segments">
              {ROUND_SCOPES.map((scope) => (
                <button key={scope.value} type="button" className="utility-finder-segment"
                  aria-pressed={state.roundScope === scope.value} onClick={() => update({ roundScope: scope.value })}>
                  {scope.value === "current" && currentRound !== null ? `本回合（${currentRound}）` : scope.label}
                </button>
              ))}
            </div>
          </div>
          {hasFloors ? (
            <label className="utility-finder-filter">
              <span className="utility-finder-filter-label">楼层</span>
              <select aria-label="落点图楼层" value={floor.level}
                onChange={(event) => update({ floor: event.target.value as TacticalMapLevel, rect: null })}>
                <option value="upper">上层</option>
                <option value="lower">下层</option>
              </select>
            </label>
          ) : null}
        </div>

        <div className="utility-finder-results">
          <div className="utility-finder-results-head">
            <h3 className="utility-finder-count" aria-live="polite">{heading}</h3>
            {state.rect ? (
              <button type="button" className="text-button compact-button" onClick={() => update({ rect: null })}>清除选区</button>
            ) : null}
          </div>
          {selected.length > 0 ? (
            <ol className="data-rows utility-finder-list">
              {selected.map((utility) => (
                <FinderRow key={utility.id} utility={utility} time={timeOf(utility)} name={nameOf(utility)}
                  onJump={onJump} onHover={setHoveredId} />
              ))}
            </ol>
          ) : (
            <p className="utility-finder-empty">
              {state.rect ? `选区里没有符合条件的${label}。` : `没有符合条件的${label}。`}
            </p>
          )}
        </div>
      </div>
    </div>
  );
});

// One list row; memoised so hovering one row re-renders only the two throws it highlights.
const FinderRow = memo(function FinderRow({
  utility,
  time,
  name,
  onJump,
  onHover
}: {
  utility: ReplayUtility;
  time: string;
  name: string;
  onJump: (utility: ReplayUtility) => void;
  onHover: (id: string | null) => void;
}) {
  const side = utility.throwerSide ? utility.throwerSide.toLowerCase() : "unknown";
  return (
    <li>
      <button type="button" className="utility-finder-row"
        onClick={() => onJump(utility)}
        onMouseEnter={() => onHover(utility.id)} onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(utility.id)} onBlur={() => onHover(null)}>
        <span className="utility-finder-round">第 {utility.roundNumber} 回合</span>
        <span className="utility-finder-time">{time}</span>
        <span className={`utility-finder-thrower side-${side}`}>{name}</span>
        <span className="utility-finder-go">看这颗</span>
      </button>
    </li>
  );
});

// Pointer-only, like the player dots: the list below offers the same jump to the keyboard. The map's
// pointer handlers turn a press that does not move on it into the jump (see handlePointerUp).
const FinderThrow = memo(function FinderThrow({
  utility,
  dimmed,
  hovered,
  title
}: {
  utility: ReplayUtility;
  dimmed: boolean;
  hovered: boolean;
  title: string;
}) {
  const start = throwPoint(utility);
  const landing = landingPoint(utility);
  if (!start || !landing) return null;
  const Icon = UTILITY_ICONS[utility.type];
  const side = utility.throwerSide ? utility.throwerSide.toLowerCase() : "unknown";
  const points = utility.points.map((point) => `${point.x},${point.y}`).join(" ");
  return (
    <g className={`utility-finder-throw utility-${utility.type} side-${side}${dimmed ? " dimmed" : ""}${hovered ? " hovered" : ""}`}
      data-utility-id={utility.id}>
      <title>{title}</title>
      <polyline className="utility-finder-hit" points={points} />
      <polyline className="utility-finder-path" points={points} />
      <circle className="utility-finder-origin" cx={start.x} cy={start.y} r="0.7" />
      <circle className="utility-finder-landing" cx={landing.x} cy={landing.y} r={ICON_SIZE / 2 + 0.2} />
      <Icon className="utility-finder-landing-icon" x={landing.x - ICON_SIZE / 2 + 0.3} y={landing.y - ICON_SIZE / 2 + 0.3}
        width={ICON_SIZE - 0.6} height={ICON_SIZE - 0.6} strokeWidth={2.4} aria-hidden="true" />
    </g>
  );
});

/** The tab's body while the background upgrade has not reached this match yet. */
export function UtilityFinderPending() {
  return (
    <div className="utility-finder-pending">
      <p className="utility-finder-pending-notice" role="status">这场比赛还在补充道具数据，稍后刷新</p>
    </div>
  );
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
