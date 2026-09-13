"use client";

import { useMemo, useState } from "react";

import { getTacticalMapLevel, getTacticalMapPresentation, resolveTacticalMapLevel, sanitizeRadarPoint } from "@/lib/map-config";
import type { TacticalMapLevelMode, TacticalMapPresentation } from "@/lib/map-config";
import { getFrameForTick } from "@/lib/replay-frames";
import { parserEventPresentationForType, recentMapParserEvents } from "@/lib/replay-events";
import type { ReplayData, ReplayEvent, ReplayFrame, ReplayFramePlayer } from "@/types/replay";

interface ReplayViewerProps {
  replay: ReplayData;
  currentTick: number;
  selectedPlayerId: string | null;
  onSelectPlayer: (playerId: string) => void;
  variant?: "full" | "featured" | "companion";
}

export function ReplayViewer({
  replay,
  currentTick,
  selectedPlayerId,
  onSelectPlayer,
  variant = "full"
}: ReplayViewerProps) {
  const [levelMode, setLevelMode] = useState<TacticalMapLevelMode>("auto");
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
  const ariaLabel =
    variant === "featured" || variant === "companion"
      ? "Tactical map companion"
      : "2D replay viewer";
  const nearbyParserEvents = useMemo(
    () => recentMapParserEvents(
      hasFloors ? (replay.events ?? []).filter((event) => getTacticalMapLevel(mapPresentation, event.z) === floor.level) : replay.events ?? [],
      currentRoundNumber, currentTick, replay.tickRate
    ),
    [currentRoundNumber, currentTick, replay.events, replay.tickRate, hasFloors, mapPresentation, floor.level]
  );

  return (
    <section
      className={`panel replay-panel ${layoutClass}`}
      aria-label={ariaLabel}
    >
      <div className="viewer-header">
        <div className="viewer-header-main">
          <span className="viewer-eyebrow">战术回放</span>
          <strong className="viewer-map-name">{mapPresentation.displayName}</strong>
          <span className={`map-calibration-pill ${mapPresentation.confidence}`}>
            {mapPresentation.confidence === "calibrated" ? "地图已校准" : "参考坐标"}
          </span>
        </div>
        <span className="viewer-outcome">
          {round ? `第 ${round.roundNumber} 回合 · ${round.winnerSide} 获胜` : "暂无回合数据"}
        </span>
      </div>

      <div className="viewer-header" style={{ flexWrap: "wrap", gap: 8 }}>
        {hasFloors ? (
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            楼层
            <select className="speed-select" aria-label="Tactical map floor" value={levelMode}
              onChange={(event) => setLevelMode(event.target.value as TacticalMapLevelMode)}>
              <option value="auto">跟随玩家</option>
              <option value="upper">上层</option>
              <option value="lower">下层</option>
            </select>
            <span data-testid="map-floor-label">{floor.level === "lower" ? "下层" : "上层"}
              {floor.followingPlayer ? ` · ${selectedPlayer?.name}` : ""}</span>
          </label>
        ) : null}
        <span data-testid="bomb-status">炸弹：{bombStatusLabel(frame?.bombState.status)}</span>
        {hasFloors ? <small>本层 {visiblePlayers.length} 人 · 另一层 {framePlayers.length - visiblePlayers.length - unknownHeights} 人</small> : null}
        {hasFloors && levelMode === "auto" && !floor.followingPlayer ? <small>选择有高度数据的玩家后可自动切换楼层。</small> : null}
        {unknownHeights > 0 ? <small>{unknownHeights} 人的高度数据缺失，请查看名单。</small> : null}
      </div>

      <div className={`map-frame ${hasRadarImage ? "radar-map-frame" : "fallback-map-frame"}`}>
        <svg
          viewBox="0 0 100 100"
          role="img"
          aria-label={`${mapPresentation.displayName} tactical minimap ${mapPresentation.confidence}${hasFloors ? ` ${floor.level}` : ""}`}
        >
          <defs>
            <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
              <path d="M 5 0 L 0 0 0 5" fill="none" stroke="#1c2a32" strokeWidth="0.25" />
            </pattern>
          </defs>
          {floor.radarImagePath ? (
            <RadarImageBackground radarUrl={floor.radarImagePath} />
          ) : (
            <GenericMapBackground label={`${mapPresentation.displayName} · 坐标未校准`} />
          )}

          {visiblePlayers.map((player) => (
            <PlayerDot
              key={player.id}
              player={player}
              index={(player.side === "T" ? tPlayers : ctPlayers).findIndex((item) => item.id === player.id) + 1}
              selected={selectedPlayerId === player.id}
              onSelectPlayer={onSelectPlayer}
            />
          ))}

          {!hasFloors || getTacticalMapLevel(mapPresentation, frame?.bombState.z) === floor.level
            ? <BombMarker bombState={frame?.bombState} /> : null}

          {nearbyParserEvents.map((event) => (
            <ParserEventMapMarker key={event.id} event={event} />
          ))}
        </svg>

        <div className="player-list">
          <Roster title="T · 进攻方" players={tPlayers} map={mapPresentation} selectedPlayerId={selectedPlayerId} onSelectPlayer={onSelectPlayer} />
          <Roster title="CT · 防守方" players={ctPlayers} map={mapPresentation} selectedPlayerId={selectedPlayerId} onSelectPlayer={onSelectPlayer} />
        </div>
        {!frame ? (
          <div className="map-empty-state">
            <strong>暂无位置数据</strong>
            <span>这场比赛暂时无法显示玩家位置。</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ParserEventMapMarker({ event }: { event: ReplayEvent }) {
  const point = sanitizeRadarPoint(event);
  if (!point) {
    return null;
  }
  const presentation = parserEventPresentationForType(event.type);

  return (
    <g
      className={`parser-map-event ${presentation.tone}`}
      transform={`translate(${point.x} ${point.y})`}
    >
      <circle r="3.2" />
      <text y="1.3" textAnchor="middle" pointerEvents="none">
        {presentation.shortLabel}
      </text>
      <title>{`${event.label} at tick ${event.tick}`}</title>
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
    <g transform={`translate(${point.x} ${point.y})`}>
      <rect x="-2" y="-2" width="4" height="4" rx="0.6" fill="#f4b740" />
      {bombState.status === "planted" ? <circle r="4" fill="none" stroke="#f4b740" strokeDasharray="1 1" /> : null}
      <title>炸弹：{bombStatusLabel(bombState.status)}</title>
    </g>
  );
}

function GenericMapBackground({ label }: { label: string }) {
  return (
    <>
      <rect x="0" y="0" width="100" height="100" fill="#0c1318" />
      <rect x="0" y="0" width="100" height="100" fill="url(#grid)" />
      <path
        d="M13 68 L28 68 L28 58 L40 58 L40 48 L53 48 L53 36 L66 36 L66 27 L82 27 L82 42 L72 42 L72 53 L84 53 L84 66 L66 66 L66 80 L50 80 L50 66 L35 66 L35 81 L18 81 L18 74 L13 74 Z"
        fill="#17232c"
        stroke="#435865"
        strokeWidth="0.8"
      />
      <path
        d="M35 66 L50 66 L50 80 L66 80 L66 66 L84 66 L84 53 L72 53 L72 42 L66 42 L66 36 L53 36 L53 48 L40 48 L40 58 L35 58 Z"
        fill="#1f2f3a"
        opacity="0.88"
      />
      <rect x="70" y="33" width="12" height="12" fill="rgba(244,183,64,0.13)" stroke="#7b6650" />
      <text x="76" y="41" textAnchor="middle" fill="#d6b777" fontSize="7" fontWeight="700">
        A
      </text>
      <rect x="22" y="70" width="12" height="12" fill="rgba(244,183,64,0.13)" stroke="#7b6650" />
      <text x="28" y="78" textAnchor="middle" fill="#d6b777" fontSize="7" fontWeight="700">
        B
      </text>
      <text
        className="fallback-map-label"
        x="50"
        y="12"
        textAnchor="middle"
        fill="#9eabb4"
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

function PlayerDot({
  player,
  index,
  selected,
  onSelectPlayer
}: {
  player: ReplayFramePlayer;
  index: number;
  selected: boolean;
  onSelectPlayer: (playerId: string) => void;
}) {
  const fill = player.side === "T" ? "#f4b740" : "#28c7c1";
  const opacity = player.alive ? 1 : 0.28;

  return (
    <g
      transform={`translate(${player.x} ${player.y})`}
      opacity={opacity}
      onClick={() => onSelectPlayer(player.id)}
      role="button"
      tabIndex={0}
      aria-label={`Review ${player.name}`}
      aria-pressed={selected}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelectPlayer(player.id);
        }
      }}
      style={{ cursor: "pointer" }}
    >
      {selected ? <circle r="4.9" fill="none" stroke="#eef4f6" strokeWidth="0.8" /> : null}
      <circle r="3.4" fill={fill} stroke="#061014" strokeWidth="0.8" />
      <text
        y="1.4"
        textAnchor="middle"
        fill="#061014"
        fontSize="3.4"
        fontWeight="900"
        pointerEvents="none"
      >
        {index}
      </text>
      {!player.alive ? (
        <path d="M-2.1 -2.1 L2.1 2.1 M2.1 -2.1 L-2.1 2.1" stroke="#eef4f6" strokeWidth="0.8" />
      ) : null}
    </g>
  );
}

function Roster({
  title,
  players,
  map,
  selectedPlayerId,
  onSelectPlayer
}: {
  title: string;
  players: ReplayFramePlayer[];
  map: TacticalMapPresentation;
  selectedPlayerId: string | null;
  onSelectPlayer: (playerId: string) => void;
}) {
  return (
    <div className="side-roster">
      <h3>{title}</h3>
      {players.map((player, index) => (
        <div key={player.id} className={`roster-row ${player.alive ? "" : "dead"}`}>
          <span
            className="roster-index"
            style={{
              background: player.side === "T" ? "var(--amber)" : "var(--teal)",
              color: "#061014"
            }}
          >
            {index + 1}
          </span>
          <button type="button" aria-pressed={selectedPlayerId === player.id} onClick={() => onSelectPlayer(player.id)} style={{ textAlign: "left", color: "inherit", background: "none", border: 0, cursor: "pointer" }}>
            {player.name}{map.secondaryRadarImagePath ? ` · ${floorLabel(getTacticalMapLevel(map, player.z))}` : ""}
          </button>
          <span>{player.alive ? player.hp : 0}</span>
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
