"use client";

import { Bomb, CircleDot, Cloud, Disc, Flame, Scissors, Shield, ShieldHalf, X, Zap, type LucideIcon } from "lucide-react";
import { memo, useCallback, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";

import { RadarGraticule } from "@/components/replay/RadarGraticule";
import { getTacticalMapLevel, getTacticalMapPresentation, resolveTacticalMapLevel, sanitizeRadarPoint } from "@/lib/map-config";
import type { TacticalMapLevel, TacticalMapLevelMode, TacticalMapPresentation } from "@/lib/map-config";
import { teamKeyOfPlayer } from "@/lib/match-stats";
import {
  activeWeaponLabel,
  deathInfoAt,
  hasPlayerStates,
  killsDeathsAt,
  stateAt,
  teamEquipmentAt,
  type DeathInfo,
  type KillsDeaths
} from "@/lib/player-state";
import { getFrameForTick } from "@/lib/replay-frames";
import { describeParserEvent, killSide, parserEventPresentation, recentMapParserEvents, weaponName } from "@/lib/replay-events";
import { formatRoundTime } from "@/lib/replay-time";
import type {
  PlayerSide,
  ReplayData,
  ReplayEvent,
  ReplayFrame,
  ReplayFramePlayer,
  ReplayPlayerState,
  UtilityType
} from "@/types/replay";

/** What an overlay drawn inside the map's SVG (viewBox 0 0 100 100, radar-percent) gets to know. */
export interface ReplayMapOverlayContext {
  map: TacticalMapPresentation;
  hasFloors: boolean;
  // The floor on screen; null on a single-floor map. Compare with getTacticalMapLevel(map, z).
  floor: TacticalMapLevel | null;
  currentTick: number;
  roundNumber: number;
  focusPlayerId: string | null;
  // Radar-percent units per screen pixel, so markers can keep a fixed on-screen size.
  unitsPerPixel: number;
}

export type ReplayMapOverlay = ReactNode | ((context: ReplayMapOverlayContext) => ReactNode);

interface ReplayViewerProps {
  replay: ReplayData;
  currentTick: number;
  selectedPlayerId: string | null;
  // Changes whose review this is; the map itself only highlights, see "切换为他的视角".
  onSelectPlayer: (playerId: string) => void;
  variant?: "full" | "featured" | "companion";
  // Controlled floor choice, so it survives the viewer being swapped out for a video.
  levelMode?: TacticalMapLevelMode;
  onLevelModeChange?: (mode: TacticalMapLevelMode) => void;
  // Drawn inside the map SVG in radar-percent coordinates: `overlay` under the players and
  // markers, `overlayAbove` over everything (grenade effects that must not hide under the dots).
  overlay?: ReplayMapOverlay;
  overlayAbove?: ReplayMapOverlay;
  // Dims every other player on the map; the roster stays as it is.
  focusPlayerId?: string | null;
  // status.matchSummary.teams, for the roster headers.
  teamNames?: readonly { key: string; name?: string | null }[] | null;
  // The unfiltered replay for the roster's match-wide counters (K/D, deaths, teams, kit) when
  // `replay` carries a per-player subset of events. Pass the arrays the page gives
  // lib/match-stats elsewhere, so its per-replay index is shared, not rebuilt every tick.
  matchReplay?: ReplayData;
}

export const ReplayViewer = memo(function ReplayViewer({
  replay,
  currentTick,
  selectedPlayerId,
  onSelectPlayer,
  variant = "full",
  levelMode: controlledLevelMode,
  onLevelModeChange,
  overlay,
  overlayAbove,
  focusPlayerId = null,
  teamNames,
  matchReplay
}: ReplayViewerProps) {
  const [localLevelMode, setLocalLevelMode] = useState<TacticalMapLevelMode>("auto");
  const levelMode = controlledLevelMode ?? localLevelMode;
  const setLevelMode = onLevelModeChange ?? setLocalLevelMode;
  const [highlightedPlayerId, setHighlightedPlayerId] = useState<string | null>(null);
  const rosterButtons = useRef(new Map<string, HTMLButtonElement>());
  const mapSvgRef = useRef<SVGSVGElement>(null);
  const frame = useMemo(
    () => getFrameForTick(replay.frames, currentTick, replay.tickRate),
    [currentTick, replay.frames, replay.tickRate]
  );
  const framePlayers = useMemo(
    () =>
      (frame?.players ?? [])
        .map((player) => sanitizeRadarPoint(player))
        .filter((player): player is ReplayFramePlayer => player !== null),
    [frame]
  );
  const currentRoundNumber = frame?.roundNumber ?? replay.rounds[0]?.roundNumber ?? 1;
  const tPlayers = framePlayers.filter((player) => player.side === "T");
  const ctPlayers = framePlayers.filter((player) => player.side === "CT");
  const round = replay.rounds.find((item) => item.roundNumber === currentRoundNumber);
  const mapPresentation = useMemo(
    () => getTacticalMapPresentation(replay),
    [replay]
  );
  const selectedPlayer = framePlayers.find((player) => player.id === selectedPlayerId);
  const highlightedPlayer = highlightedPlayerId
    ? framePlayers.find((player) => player.id === highlightedPlayerId)
      ?? replay.players.find((player) => player.id === highlightedPlayerId)
    : undefined;
  const floor = resolveTacticalMapLevel(mapPresentation, levelMode, selectedPlayer?.z);
  const hasFloors = Boolean(mapPresentation.secondaryRadarImagePath);
  const visiblePlayers = hasFloors
    ? framePlayers.filter((player) => getTacticalMapLevel(mapPresentation, player.z) === floor.level)
    : framePlayers;
  const unknownHeights = hasFloors ? framePlayers.filter((player) => getTacticalMapLevel(mapPresentation, player.z) === null).length : 0;
  const hasRadarImage = Boolean(floor.radarImagePath);
  const layoutClass =
    variant === "featured"
      ? "featured-tactical-panel"
      : variant === "companion"
        ? "tactical-panel"
        : "";
  // Markers only ever come from the round on screen: index the events by round once, so a playback
  // frame looks at that round's few dozen instead of filtering the whole match.
  const eventsByRound = useMemo(() => {
    const byRound = new Map<number, ReplayEvent[]>();
    for (const event of replay.events ?? []) {
      const roundEvents = byRound.get(event.roundNumber);
      if (roundEvents) roundEvents.push(event);
      else byRound.set(event.roundNumber, [event]);
    }
    return byRound;
  }, [replay.events]);
  const roundEvents = (Number.isFinite(currentRoundNumber) ? eventsByRound.get(currentRoundNumber) : undefined) ?? NO_EVENTS;
  const nearbyParserEvents = useMemo(
    () => recentMapParserEvents(
      hasFloors ? roundEvents.filter((event) => getTacticalMapLevel(mapPresentation, event.z) === floor.level) : roundEvents,
      currentRoundNumber, currentTick, replay.tickRate
    ),
    [currentRoundNumber, currentTick, roundEvents, replay.tickRate, hasFloors, mapPresentation, floor.level]
  );
  const rosterOrder = [...tPlayers, ...ctPlayers];
  const rovingId = [highlightedPlayerId, selectedPlayerId].find((id) => id && rosterOrder.some((player) => player.id === id))
    ?? rosterOrder[0]?.id ?? null;
  const tickRate = replay.tickRate > 0 ? replay.tickRate : 64;
  const statsReplay = matchReplay ?? replay;
  const killsDeaths = useMemo(() => killsDeathsAt(statsReplay, currentTick), [statsReplay, currentTick]);
  // v1 replays have no equipment states: one line per player is enough.
  const compactRoster = useMemo(() => !hasPlayerStates(statsReplay), [statsReplay]);
  const unitsPerPixel = useUnitsPerPixel(mapSvgRef);
  const overlayContext: ReplayMapOverlayContext = {
    map: mapPresentation,
    hasFloors,
    floor: hasFloors ? floor.level : null,
    currentTick,
    roundNumber: currentRoundNumber,
    focusPlayerId,
    unitsPerPixel
  };
  const renderOverlay = (slot: ReplayMapOverlay | undefined) =>
    typeof slot === "function" ? slot(overlayContext) : slot ?? null;

  // Stable, so the memoised roster rows only re-render when their own facts change.
  const toggleHighlight = useCallback((playerId: string) => {
    setHighlightedPlayerId((current) => (current === playerId ? null : playerId));
  }, []);
  const registerRosterButton = useCallback((playerId: string, element: HTMLButtonElement | null) => {
    if (element) rosterButtons.current.set(playerId, element);
    else rosterButtons.current.delete(playerId);
  }, []);

  function handleRosterKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "Home" && event.key !== "End") return;
    const index = rosterOrder.findIndex((player) => rosterButtons.current.get(player.id) === event.target);
    if (index < 0) return;
    event.preventDefault();
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? rosterOrder.length - 1
      : Math.max(0, Math.min(rosterOrder.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
    rosterButtons.current.get(rosterOrder[nextIndex].id)?.focus();
  }

  const rosterProps = {
    map: mapPresentation,
    selectedPlayerId,
    highlightedPlayerId,
    rovingId,
    onHighlight: toggleHighlight,
    register: registerRosterButton,
    replay: statsReplay,
    currentTick,
    frame,
    killsDeaths,
    teamNames,
    compact: compactRoster
  };

  return (
    <section
      className={`panel replay-panel ${layoutClass}`}
      aria-label="战术地图"
    >
      <div className={`map-frame ${hasRadarImage ? "radar-map-frame" : "fallback-map-frame"}`}>
        <svg
          ref={mapSvgRef}
          viewBox="0 0 100 100"
          role="img"
          aria-label={`${mapPresentation.displayName} 战术地图${hasFloors ? `（${floor.level === "lower" ? "下层" : "上层"}）` : ""}`}
        >
          <defs>
            <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
              <path className="fallback-map-grid" d="M 5 0 L 0 0 0 5" fill="none" strokeWidth="0.25" />
            </pattern>
          </defs>
          {floor.radarImagePath ? (
            <>
              <RadarImageBackground radarUrl={floor.radarImagePath} />
              <RadarGraticule />
            </>
          ) : (
            <GenericMapBackground label={`${mapPresentation.displayName}（坐标未校准）`} />
          )}

          {overlay !== undefined ? <g className="map-overlay-slot">{renderOverlay(overlay)}</g> : null}

          {visiblePlayers.map((player) => (
            <PlayerDot
              key={player.id}
              player={player}
              index={(player.side === "T" ? tPlayers : ctPlayers).findIndex((item) => item.id === player.id) + 1}
              selected={selectedPlayerId === player.id}
              highlighted={highlightedPlayerId === player.id}
              focus={focusPlayerId === null ? "none" : focusPlayerId === player.id ? "focused" : "dimmed"}
              unit={unitsPerPixel}
              onHighlight={toggleHighlight}
            />
          ))}

          {!hasFloors || getTacticalMapLevel(mapPresentation, frame?.bombState.z) === floor.level
            ? <BombMarker bombState={frame?.bombState} unit={unitsPerPixel} /> : null}

          {nearbyParserEvents.map((event) => (
            <ParserEventMapMarker key={event.id} event={event} playerId={selectedPlayerId} unit={unitsPerPixel}
              time={formatRoundTime((event.tick - (round?.startTick ?? event.tick)) / tickRate)} />
          ))}

          {overlayAbove !== undefined ? <g className="map-overlay-slot above">{renderOverlay(overlayAbove)}</g> : null}
        </svg>

        <div className="map-overlay-bar">
          <strong className="viewer-map-name">{mapPresentation.displayName}</strong>
          {mapPresentation.confidence !== "calibrated" ? (
            <span className={`map-calibration-pill ${mapPresentation.confidence}`}>参考坐标</span>
          ) : null}
          <span data-testid="bomb-status">炸弹：{bombStatusLabel(frame?.bombState.status)}</span>
          {hasFloors ? (
            <label className="map-floor-control">
              楼层
              <select className="speed-select" aria-label="地图楼层" value={levelMode}
                onChange={(event) => setLevelMode(event.target.value as TacticalMapLevelMode)}>
                <option value="auto">跟随玩家</option>
                <option value="upper">上层</option>
                <option value="lower">下层</option>
              </select>
              <span data-testid="map-floor-label">{floor.level === "lower" ? "下层" : "上层"}
                {floor.followingPlayer ? `（跟随 ${selectedPlayer?.name}）` : ""}</span>
            </label>
          ) : null}
          {hasFloors ? <small>本层 {visiblePlayers.length} 人，另一层 {framePlayers.length - visiblePlayers.length - unknownHeights} 人</small> : null}
          {hasFloors && levelMode === "auto" && !floor.followingPlayer ? <small>选择有高度数据的玩家后可自动切换楼层。</small> : null}
          {unknownHeights > 0 ? <small>{unknownHeights} 人的高度数据缺失，请查看名单。</small> : null}
        </div>

        {highlightedPlayer ? (
          <div className="map-highlight-card" role="status">
            <span className={`map-highlight-side ${highlightedPlayer.side === "T" ? "side-t" : "side-ct"}`}>{highlightedPlayer.side}</span>
            <strong>{highlightedPlayer.name}</strong>
            {"hp" in highlightedPlayer ? <small>{highlightedPlayer.alive ? `${highlightedPlayer.hp} 血量` : "已阵亡"}</small> : null}
            {highlightedPlayer.id === selectedPlayerId ? (
              <small>正在复盘</small>
            ) : (
              <button type="button" className="secondary-button compact-button"
                onClick={() => {
                  onSelectPlayer(highlightedPlayer.id);
                  setHighlightedPlayerId(null);
                }}>
                切换为他的视角
              </button>
            )}
            <button type="button" className="map-highlight-close" aria-label="取消高亮" onClick={() => setHighlightedPlayerId(null)}>
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        ) : null}

        {!frame ? (
          <div className="map-empty-state">
            <strong>暂无位置数据</strong>
            <span>这场比赛暂时无法显示玩家位置。</span>
          </div>
        ) : null}
      </div>

      {framePlayers.length > 0 ? (
        <div className="player-list" role="group" aria-label="玩家名单" onKeyDown={handleRosterKeyDown}>
          <Roster side="T" title="进攻方" players={tPlayers} {...rosterProps} />
          <Roster side="CT" title="防守方" players={ctPlayers} {...rosterProps} />
        </div>
      ) : null}
    </section>
  );
});

// Map markers are drawn in screen pixels and scaled back to radar percent, so a player dot is
// the same size on a phone and a wide screen instead of growing with the map (at 3.4 % of the
// map a dot was ~50 px across on desktop and hid smokes and neighbours).
const FALLBACK_MAP_PX = 640;

function useUnitsPerPixel(svgRef: RefObject<SVGSVGElement | null>): number {
  const [unitsPerPixel, setUnitsPerPixel] = useState(100 / FALLBACK_MAP_PX);
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const update = () => {
      const width = svg.getBoundingClientRect().width;
      if (width > 0) setUnitsPerPixel((current) => {
        const next = 100 / width;
        return Math.abs(next - current) > 1e-4 ? next : current;
      });
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(svg);
    return () => observer.disconnect();
  }, [svgRef]);
  return unitsPerPixel;
}

function pixelTransform(x: number, y: number, unit: number): string {
  return `translate(${x} ${y}) scale(${Math.round(unit * 10000) / 10000})`;
}

function ParserEventMapMarker({ event, playerId, time, unit }: { event: ReplayEvent; playerId: string | null; time: string; unit: number }) {
  const point = sanitizeRadarPoint(event);
  if (!point) {
    return null;
  }
  const presentation = parserEventPresentation(event, playerId);
  const side = killSide(event);

  return (
    <g
      className={`parser-map-event ${presentation.tone}${side ? ` side-${side.toLowerCase()}` : ""}`}
      transform={pixelTransform(point.x, point.y, unit)}
    >
      <circle r="6" />
      <text y="2.8" textAnchor="middle" pointerEvents="none">
        {presentation.shortLabel}
      </text>
      <title>{`${describeParserEvent(event)} · ${time}`}</title>
    </g>
  );
}

function BombMarker({ bombState, unit }: { bombState: ReplayFrame["bombState"] | undefined; unit: number }) {
  if (bombState?.status !== "planted" && bombState?.status !== "dropped") {
    return null;
  }
  const point = sanitizeRadarPoint(bombState);
  if (!point) {
    return null;
  }

  return (
    <g className="map-bomb-marker" transform={pixelTransform(point.x, point.y, unit)}>
      <rect x="-4" y="-4" width="8" height="8" rx="1" />
      {bombState.status === "planted" ? <circle r="8" strokeDasharray="2 2" /> : null}
      <title>炸弹：{bombStatusLabel(bombState.status)}</title>
    </g>
  );
}

function GenericMapBackground({ label }: { label: string }) {
  return (
    <g className="fallback-map">
      <rect className="fallback-map-ground" x="0" y="0" width="100" height="100" />
      <rect x="0" y="0" width="100" height="100" fill="url(#grid)" />
      <path
        className="fallback-map-floor"
        d="M13 68 L28 68 L28 58 L40 58 L40 48 L53 48 L53 36 L66 36 L66 27 L82 27 L82 42 L72 42 L72 53 L84 53 L84 66 L66 66 L66 80 L50 80 L50 66 L35 66 L35 81 L18 81 L18 74 L13 74 Z"
        strokeWidth="0.8"
      />
      <path
        className="fallback-map-lane"
        d="M35 66 L50 66 L50 80 L66 80 L66 66 L84 66 L84 53 L72 53 L72 42 L66 42 L66 36 L53 36 L53 48 L40 48 L40 58 L35 58 Z"
      />
      <rect className="fallback-map-site" x="70" y="33" width="12" height="12" />
      <text className="fallback-map-text" x="76" y="41" textAnchor="middle" fontSize="7" fontWeight="700">
        A
      </text>
      <rect className="fallback-map-site" x="22" y="70" width="12" height="12" />
      <text className="fallback-map-text" x="28" y="78" textAnchor="middle" fontSize="7" fontWeight="700">
        B
      </text>
      <text
        className="fallback-map-text fallback-map-label"
        x="50"
        y="12"
        textAnchor="middle"
        fontSize="4"
        fontWeight="700"
      >
        {label}
      </text>
    </g>
  );
}

function RadarImageBackground({ radarUrl }: { radarUrl: string }) {
  return (
    <image
      className="map-radar-image"
      href={radarUrl}
      x="0"
      y="0"
      width="100"
      height="100"
      preserveAspectRatio="none"
    />
  );
}

// Pointer-only: a click highlights the player. Keyboard users get the same from the roster.
function PlayerDot({
  player,
  index,
  selected,
  highlighted,
  focus = "none",
  unit,
  onHighlight
}: {
  player: ReplayFramePlayer;
  index: number;
  selected: boolean;
  highlighted: boolean;
  // With a focused player (the utility finder's "只看"), everyone else is dimmed.
  focus?: "none" | "focused" | "dimmed";
  // Radar-percent units per screen pixel: the dot is drawn in pixels.
  unit: number;
  onHighlight: (playerId: string) => void;
}) {
  const opacity = (player.alive ? 1 : 0.32) * (focus === "dimmed" ? 0.25 : 1);

  return (
    <g
      className={`map-player-dot side-${player.side.toLowerCase()}${highlighted ? " highlighted" : ""}${selected ? " reviewed" : ""}${focus === "none" ? "" : ` focus-${focus}`}`}
      transform={pixelTransform(player.x, player.y, unit)}
      opacity={opacity}
      onClick={() => onHighlight(player.id)}
      style={{ cursor: "pointer" }}
    >
      <circle r="12" fill="transparent" />
      {highlighted ? <circle className="map-player-highlight" r="12" /> : null}
      {selected ? <circle className="map-player-reviewed" r="10" /> : null}
      <circle className="map-player-body" r="7" />
      <text className="map-player-index" y="3.2" textAnchor="middle" pointerEvents="none">
        {index}
      </text>
      {!player.alive ? (
        <path className="map-player-dead" d="M-4.5 -4.5 L4.5 4.5 M4.5 -4.5 L-4.5 4.5" />
      ) : null}
      {selected || highlighted || focus === "focused" ? (
        <text className="map-player-name" y="21" textAnchor="middle" pointerEvents="none">{player.name}</text>
      ) : null}
    </g>
  );
}

const GRENADES: Record<UtilityType, { icon: LucideIcon; label: string }> = {
  smoke: { icon: Cloud, label: "烟雾弹" },
  flash: { icon: Zap, label: "闪光弹" },
  molotov: { icon: Flame, label: "燃烧弹" },
  he: { icon: CircleDot, label: "手雷" },
  decoy: { icon: Disc, label: "诱饵弹" }
};
const LOW_HP = 30;
const NO_EVENTS: ReplayEvent[] = [];

type TeamNames = readonly { key: string; name?: string | null }[] | null | undefined;

// One panel per side: "队名 阵营" with the side's live equipment value, then two lines per player.
// Parts without data (v1 replays: money, armour, weapon, kit, grenades) are left out, and a v1
// row is a single line.
function Roster({
  side,
  title,
  players,
  map,
  selectedPlayerId,
  highlightedPlayerId,
  rovingId,
  onHighlight,
  register,
  replay,
  currentTick,
  frame,
  killsDeaths,
  teamNames,
  compact
}: {
  side: PlayerSide;
  title: string;
  players: ReplayFramePlayer[];
  map: TacticalMapPresentation;
  selectedPlayerId: string | null;
  highlightedPlayerId: string | null;
  rovingId: string | null;
  onHighlight: (playerId: string) => void;
  register: (playerId: string, element: HTMLButtonElement | null) => void;
  replay: ReplayData;
  currentTick: number;
  // The frame the map is drawing (the viewer's own), so the equipment total does not interpolate again.
  frame: ReplayFrame | null;
  killsDeaths: Map<string, KillsDeaths>;
  teamNames: TeamNames;
  compact: boolean;
}) {
  const sideClass = `side-${side.toLowerCase()}`;
  const teamName = sideTeamName(replay, players, teamNames);
  const equipment = teamEquipmentAt(replay, side, currentTick, frame);
  const floors = Boolean(map.secondaryRadarImagePath);
  return (
    <div className={`side-roster live-roster ${sideClass}`}>
      <div className={`panel-bar roster-head panel-bar-${side.toLowerCase()}`}>
        <h3 className="panel-bar-title">
          <span className="roster-team-name">{teamName ?? title}</span> <span className="roster-side">{side}</span>
        </h3>
        {equipment !== null ? <span className="roster-equipment">装备 {formatMoney(equipment)}</span> : null}
      </div>
      {players.map((player, index) => {
        const counts = killsDeaths.get(player.id);
        return (
          <RosterRow key={player.id} playerId={player.id} name={player.name} side={player.side}
            alive={player.alive} hp={player.alive ? Math.max(0, Math.min(100, Math.round(finite(player.hp) ?? 0))) : 0}
            hasBomb={Boolean(player.hasBomb)} floor={floors ? floorLabel(getTacticalMapLevel(map, player.z)) : null}
            index={index + 1} sideClass={sideClass}
            reviewed={player.id === selectedPlayerId} highlighted={highlightedPlayerId === player.id}
            tabbable={player.id === rovingId} onHighlight={onHighlight} register={register}
            state={stateAt(replay, player.id, currentTick)}
            deathText={player.alive ? null : describeDeath(player.id, deathInfoAt(replay, player.id, currentTick))}
            kills={counts?.kills ?? 0} deaths={counts?.deaths ?? 0} compact={compact} />
        );
      })}
    </div>
  );
}

// Line 1: number, name, bomb, money, K/D. Line 2: HP, armour, weapon, kit, grenades; or how they
// died. The icons are shorthand; the row's hidden text (the name button's description) says it all.
// Memoised on primitives (and the stable state entry): during playback most frames only move the
// dots, and then no row re-renders.
const RosterRow = memo(function RosterRow({
  playerId,
  name,
  side,
  alive,
  hp,
  hasBomb,
  floor,
  index,
  sideClass,
  reviewed,
  highlighted,
  tabbable,
  onHighlight,
  register,
  state,
  deathText,
  kills,
  deaths,
  compact
}: {
  playerId: string;
  name: string;
  side: PlayerSide;
  alive: boolean;
  hp: number;
  hasBomb: boolean;
  floor: string | null;
  index: number;
  sideClass: string;
  reviewed: boolean;
  highlighted: boolean;
  tabbable: boolean;
  onHighlight: (playerId: string) => void;
  register: (playerId: string, element: HTMLButtonElement | null) => void;
  state: ReplayPlayerState | null;
  deathText: string | null;
  kills: number;
  deaths: number;
  compact: boolean;
}) {
  const factsId = useId();
  const money = finite(state?.money);
  const armor = finite(state?.armor);
  const helmet = state?.helmet === true;
  const rawWeapon = typeof state?.weapon === "string" && state.weapon.trim() ? state.weapon.trim() : null;
  const weapon = activeWeaponLabel(rawWeapon);
  const grenades = (Array.isArray(state?.grenades) ? state.grenades : []).filter((key) => Object.hasOwn(GRENADES, key));
  const defuser = side === "CT" && state?.defuser === true;
  const facts = [
    name,
    reviewed ? "复盘中" : null,
    alive ? `存活，血量 ${hp}` : deathText?.replace("　", "，"),
    alive && armor !== null && armor > 0 ? `护甲 ${Math.round(armor)}${helmet ? "（含头盔）" : ""}` : null,
    alive && weapon ? `手持 ${weapon}` : null,
    alive && grenades.length > 0 ? `道具 ${grenadeSummary(grenades)}` : null,
    alive && defuser ? "有拆弹器" : null,
    hasBomb ? "携带炸弹" : null,
    money !== null ? `金钱 ${formatMoney(money)}` : null,
    `击杀 ${kills} 死亡 ${deaths}`
  ].filter(Boolean).join("，");
  const hpBar = (
    <>
      <span className="roster-hp-bar seg-meter"><span className={hp < LOW_HP ? "low" : undefined} style={{ width: `${hp}%` }} /></span>
      <span className="roster-hp">{hp}</span>
    </>
  );
  const singleLine = compact && alive;

  return (
    <div className={`roster-row live-roster-row${alive ? "" : " dead"}${reviewed ? " reviewed" : ""}${compact ? " compact" : ""}`}>
      <div className="roster-line roster-line-main">
        <span className={`roster-index ${sideClass}`} aria-hidden="true">{index}</span>
        <button type="button" className="roster-name" ref={(element) => register(playerId, element)}
          tabIndex={tabbable ? 0 : -1} aria-describedby={factsId}
          aria-pressed={highlighted} onClick={() => onHighlight(playerId)}>
          {name}
          {floor !== null ? <small className="roster-floor"> {floor}</small> : null}
          {reviewed ? <small className="roster-reviewed-tag"> 复盘中</small> : null}
        </button>
        <span className="roster-stats" aria-hidden="true">
          {hasBomb ? <Bomb className="roster-icon roster-bomb" size={13} aria-hidden="true" /> : null}
          {singleLine ? hpBar : null}
          {money !== null ? <span className="roster-money">{formatMoney(money)}</span> : null}
          <span className="roster-kd">{kills}/{deaths}</span>
        </span>
      </div>
      {singleLine ? null : (
        <div className="roster-line roster-line-detail" aria-hidden="true">
          {alive ? (
            <>
              {hpBar}
              {armor !== null && armor > 0 ? (
                helmet
                  ? <Shield className="roster-icon roster-armor" size={13} aria-hidden="true" />
                  : <ShieldHalf className="roster-icon roster-armor" size={13} aria-hidden="true" />
              ) : null}
              <span className="roster-weapon" title={rawWeapon ?? undefined}>{weapon}</span>
              {defuser ? <Scissors className="roster-icon roster-defuser" size={13} aria-hidden="true" /> : null}
              {grenades.length > 0 ? (
                <span className="roster-grenades">
                  {grenades.map((key, position) => {
                    const Icon = GRENADES[key].icon;
                    return <Icon key={`${key}-${position}`} className={`roster-icon grenade-${key}`} size={12} aria-hidden="true" />;
                  })}
                </span>
              ) : null}
            </>
          ) : (
            <span className="roster-death">{deathText}</span>
          )}
        </div>
      )}
      <span id={factsId} className="visually-hidden">{facts}</span>
    </div>
  );
});

// The team playing this side: its players' majority match-stats team, named by the match summary.
function sideTeamName(replay: ReplayData, players: ReplayFramePlayer[], teamNames: TeamNames): string | null {
  if (!teamNames?.length || players.length === 0) return null;
  let votes = 0;
  for (const player of players) {
    const key = teamKeyOfPlayer(replay, player.id);
    votes += key === "A" ? 1 : key === "B" ? -1 : 0;
  }
  if (votes === 0) return null;
  const name = teamNames.find((team) => team?.key === (votes > 0 ? "A" : "B"))?.name;
  return typeof name === "string" && name.trim() ? name.trim() : null;
}

function describeDeath(playerId: string, death: DeathInfo | null): string {
  if (!death) return "阵亡";
  const weapon = weaponName(death.weapon);
  if (death.killerId === playerId) return `阵亡　自杀${weapon ? `（${weapon}）` : ""}`;
  if (!death.killerName) return "阵亡";
  return `阵亡　被 ${death.killerName}${weapon ? ` 用 ${weapon}` : ""} 击杀`;
}

function grenadeSummary(grenades: UtilityType[]): string {
  const counts = new Map<UtilityType, number>();
  for (const key of grenades) counts.set(key, (counts.get(key) ?? 0) + 1);
  return [...counts].map(([key, count]) => `${GRENADES[key].label}${count > 1 ? ` ×${count}` : ""}`).join("、");
}

// One formatter for the whole roster: toLocaleString builds a new one on every call, every frame.
const MONEY = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function formatMoney(value: number): string {
  return `$${MONEY.format(Math.round(value))}`;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function floorLabel(level: string | null): string {
  return level === "upper" ? "上层" : level === "lower" ? "下层" : "高度未知";
}

function bombStatusLabel(status: string | undefined): string {
  return ({ carried: "携带中", planted: "已安装", dropped: "已掉落", defused: "已拆除", exploded: "已爆炸", unknown: "未知" } as Record<string, string>)[status ?? "unknown"] ?? "未知";
}
