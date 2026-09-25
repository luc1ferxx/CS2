"use client";

import { Users, X } from "lucide-react";
import { memo, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { getTacticalMapLevel, getTacticalMapPresentation, resolveTacticalMapLevel, sanitizeRadarPoint } from "@/lib/map-config";
import type { TacticalMapLevelMode, TacticalMapPresentation } from "@/lib/map-config";
import { getFrameForTick } from "@/lib/replay-frames";
import { describeParserEvent, killSide, parserEventPresentation, recentMapParserEvents } from "@/lib/replay-events";
import { formatRoundTime } from "@/lib/replay-time";
import type { ReplayData, ReplayEvent, ReplayFrame, ReplayFramePlayer } from "@/types/replay";

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
}

export const ReplayViewer = memo(function ReplayViewer({
  replay,
  currentTick,
  selectedPlayerId,
  onSelectPlayer,
  variant = "full",
  levelMode: controlledLevelMode,
  onLevelModeChange
}: ReplayViewerProps) {
  const [localLevelMode, setLocalLevelMode] = useState<TacticalMapLevelMode>("auto");
  const levelMode = controlledLevelMode ?? localLevelMode;
  const setLevelMode = onLevelModeChange ?? setLocalLevelMode;
  const [highlightedPlayerId, setHighlightedPlayerId] = useState<string | null>(null);
  const [rosterOpen, setRosterOpen] = useState(false);
  const rosterButtons = useRef(new Map<string, HTMLButtonElement>());
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
  const nearbyParserEvents = useMemo(
    () => recentMapParserEvents(
      hasFloors ? (replay.events ?? []).filter((event) => getTacticalMapLevel(mapPresentation, event.z) === floor.level) : replay.events ?? [],
      currentRoundNumber, currentTick, replay.tickRate
    ),
    [currentRoundNumber, currentTick, replay.events, replay.tickRate, hasFloors, mapPresentation, floor.level]
  );
  const rosterOrder = [...tPlayers, ...ctPlayers];
  const rovingId = [highlightedPlayerId, selectedPlayerId].find((id) => id && rosterOrder.some((player) => player.id === id))
    ?? rosterOrder[0]?.id ?? null;
  const tickRate = replay.tickRate > 0 ? replay.tickRate : 64;

  function toggleHighlight(playerId: string) {
    setHighlightedPlayerId((current) => (current === playerId ? null : playerId));
  }

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
    register: (playerId: string, element: HTMLButtonElement | null) => {
      if (element) rosterButtons.current.set(playerId, element);
      else rosterButtons.current.delete(playerId);
    }
  };

  return (
    <section
      className={`panel replay-panel ${layoutClass}`}
      aria-label="战术地图"
    >
      <div className={`map-frame ${hasRadarImage ? "radar-map-frame" : "fallback-map-frame"}`}>
        <svg
          viewBox="0 0 100 100"
          role="img"
          aria-label={`${mapPresentation.displayName} 战术地图${hasFloors ? `（${floor.level === "lower" ? "下层" : "上层"}）` : ""}`}
        >
          <defs>
            <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
              <path d="M 5 0 L 0 0 0 5" fill="none" stroke="#2a2e33" strokeWidth="0.25" />
            </pattern>
          </defs>
          {floor.radarImagePath ? (
            <RadarImageBackground radarUrl={floor.radarImagePath} />
          ) : (
            <GenericMapBackground label={`${mapPresentation.displayName}（坐标未校准）`} />
          )}

          {visiblePlayers.map((player) => (
            <PlayerDot
              key={player.id}
              player={player}
              index={(player.side === "T" ? tPlayers : ctPlayers).findIndex((item) => item.id === player.id) + 1}
              selected={selectedPlayerId === player.id}
              highlighted={highlightedPlayerId === player.id}
              onHighlight={toggleHighlight}
            />
          ))}

          {!hasFloors || getTacticalMapLevel(mapPresentation, frame?.bombState.z) === floor.level
            ? <BombMarker bombState={frame?.bombState} /> : null}

          {nearbyParserEvents.map((event) => (
            <ParserEventMapMarker key={event.id} event={event} playerId={selectedPlayerId}
              time={formatRoundTime((event.tick - (round?.startTick ?? event.tick)) / tickRate)} />
          ))}
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
          {framePlayers.length > 0 ? (
            <button type="button" className="map-roster-toggle" aria-expanded={rosterOpen} onClick={() => setRosterOpen((open) => !open)}>
              <Users size={14} aria-hidden="true" />玩家名单
            </button>
          ) : null}
        </div>

        {framePlayers.length > 0 ? (
          <div className={`player-list ${rosterOpen ? "roster-open" : "roster-collapsed"}`} role="group" aria-label="玩家名单"
            onKeyDown={handleRosterKeyDown}>
            <Roster side="T" title="进攻方" players={tPlayers} {...rosterProps} />
            <Roster side="CT" title="防守方" players={ctPlayers} {...rosterProps} />
          </div>
        ) : null}

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
    </section>
  );
});

function ParserEventMapMarker({ event, playerId, time }: { event: ReplayEvent; playerId: string | null; time: string }) {
  const point = sanitizeRadarPoint(event);
  if (!point) {
    return null;
  }
  const presentation = parserEventPresentation(event, playerId);
  const side = killSide(event);

  return (
    <g
      className={`parser-map-event ${presentation.tone}${side ? ` side-${side.toLowerCase()}` : ""}`}
      transform={`translate(${point.x} ${point.y})`}
    >
      <circle r="3.2" />
      <text y="1.1" textAnchor="middle" pointerEvents="none">
        {presentation.shortLabel}
      </text>
      <title>{`${describeParserEvent(event)} · ${time}`}</title>
    </g>
  );
}

function BombMarker({ bombState }: { bombState: ReplayFrame["bombState"] | undefined }) {
  if (bombState?.status !== "planted" && bombState?.status !== "dropped") {
    return null;
  }
  const point = sanitizeRadarPoint(bombState);
  if (!point) {
    return null;
  }

  return (
    <g className="map-bomb-marker" transform={`translate(${point.x} ${point.y})`}>
      <rect x="-2" y="-2" width="4" height="4" rx="0.5" />
      {bombState.status === "planted" ? <circle r="4" strokeDasharray="1 1" /> : null}
      <title>炸弹：{bombStatusLabel(bombState.status)}</title>
    </g>
  );
}

function GenericMapBackground({ label }: { label: string }) {
  return (
    <>
      <rect x="0" y="0" width="100" height="100" fill="#1b1d20" />
      <rect x="0" y="0" width="100" height="100" fill="url(#grid)" />
      <path
        d="M13 68 L28 68 L28 58 L40 58 L40 48 L53 48 L53 36 L66 36 L66 27 L82 27 L82 42 L72 42 L72 53 L84 53 L84 66 L66 66 L66 80 L50 80 L50 66 L35 66 L35 81 L18 81 L18 74 L13 74 Z"
        fill="#262a2f"
        stroke="#4b5158"
        strokeWidth="0.8"
      />
      <path
        d="M35 66 L50 66 L50 80 L66 80 L66 66 L84 66 L84 53 L72 53 L72 42 L66 42 L66 36 L53 36 L53 48 L40 48 L40 58 L35 58 Z"
        fill="#2f343a"
        opacity="0.88"
      />
      <rect x="70" y="33" width="12" height="12" fill="rgba(238,234,226,0.06)" stroke="#4b5158" />
      <text x="76" y="41" textAnchor="middle" fill="#a4a8ad" fontSize="7" fontWeight="700">
        A
      </text>
      <rect x="22" y="70" width="12" height="12" fill="rgba(238,234,226,0.06)" stroke="#4b5158" />
      <text x="28" y="78" textAnchor="middle" fill="#a4a8ad" fontSize="7" fontWeight="700">
        B
      </text>
      <text
        className="fallback-map-label"
        x="50"
        y="12"
        textAnchor="middle"
        fill="#a4a8ad"
        fontSize="4"
        fontWeight="700"
      >
        {label}
      </text>
    </>
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
  onHighlight
}: {
  player: ReplayFramePlayer;
  index: number;
  selected: boolean;
  highlighted: boolean;
  onHighlight: (playerId: string) => void;
}) {
  const opacity = player.alive ? 1 : 0.32;

  return (
    <g
      className={`map-player-dot side-${player.side.toLowerCase()}${highlighted ? " highlighted" : ""}${selected ? " reviewed" : ""}`}
      transform={`translate(${player.x} ${player.y})`}
      opacity={opacity}
      onClick={() => onHighlight(player.id)}
      style={{ cursor: "pointer" }}
    >
      <circle r="7" fill="transparent" />
      {highlighted ? <circle className="map-player-highlight" r="6" /> : null}
      {selected ? <circle className="map-player-reviewed" r="5" /> : null}
      <circle className="map-player-body" r="3.4" />
      <text className="map-player-index" y="1.3" textAnchor="middle" pointerEvents="none">
        {index}
      </text>
      {!player.alive ? (
        <path className="map-player-dead" d="M-2.1 -2.1 L2.1 2.1 M2.1 -2.1 L-2.1 2.1" />
      ) : null}
      {selected || highlighted ? (
        <text className="map-player-name" y="8.6" textAnchor="middle" pointerEvents="none">{player.name}</text>
      ) : null}
    </g>
  );
}

function Roster({
  side,
  title,
  players,
  map,
  selectedPlayerId,
  highlightedPlayerId,
  rovingId,
  onHighlight,
  register
}: {
  side: "T" | "CT";
  title: string;
  players: ReplayFramePlayer[];
  map: TacticalMapPresentation;
  selectedPlayerId: string | null;
  highlightedPlayerId: string | null;
  rovingId: string | null;
  onHighlight: (playerId: string) => void;
  register: (playerId: string, element: HTMLButtonElement | null) => void;
}) {
  const sideClass = `side-${side.toLowerCase()}`;
  return (
    <div className={`side-roster ${sideClass}`}>
      <h3><span className="roster-side">{side}</span> {title}</h3>
      {players.map((player, index) => (
        <div key={player.id} className={`roster-row ${player.alive ? "" : "dead"} ${player.id === selectedPlayerId ? "reviewed" : ""}`}>
          <span className={`roster-index ${sideClass}`}>{index + 1}</span>
          <button type="button" className="roster-name" ref={(element) => register(player.id, element)}
            tabIndex={player.id === rovingId ? 0 : -1}
            aria-pressed={highlightedPlayerId === player.id} onClick={() => onHighlight(player.id)}>
            {player.name}
            {map.secondaryRadarImagePath ? <small className="roster-floor"> {floorLabel(getTacticalMapLevel(map, player.z))}</small> : null}
            {player.id === selectedPlayerId ? <small className="roster-reviewed-tag"> 复盘中</small> : null}
          </button>
          <span className="roster-hp">{player.alive ? player.hp : 0}</span>
        </div>
      ))}
    </div>
  );
}

function floorLabel(level: string | null): string {
  return level === "upper" ? "上层" : level === "lower" ? "下层" : "高度未知";
}

function bombStatusLabel(status: string | undefined): string {
  return ({ carried: "携带中", planted: "已安装", dropped: "已掉落", defused: "已拆除", exploded: "已爆炸", unknown: "未知" } as Record<string, string>)[status ?? "unknown"] ?? "未知";
}
