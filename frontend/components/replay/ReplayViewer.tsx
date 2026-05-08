"use client";

import { useMemo } from "react";

import { getTacticalMapPresentation } from "@/lib/map-config";
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
  const frame = useMemo(
    () => getInterpolatedFrameForTick(replay.frames, currentTick),
    [currentTick, replay.frames]
  );
  const tPlayers = frame.players.filter((player) => player.side === "T");
  const ctPlayers = frame.players.filter((player) => player.side === "CT");
  const round = replay.rounds.find((item) => item.roundNumber === frame.roundNumber);
  const mapPresentation = useMemo(
    () => getTacticalMapPresentation(replay),
    [replay]
  );
  const hasRadarImage = Boolean(mapPresentation.radarImagePath);
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
    () => recentMapParserEvents(replay.events ?? [], frame.roundNumber, currentTick, replay.tickRate),
    [currentTick, frame.roundNumber, replay.events, replay.tickRate]
  );

  return (
    <section
      className={`panel replay-panel ${layoutClass}`}
      aria-label={ariaLabel}
    >
      <div className="viewer-header">
        <div className="viewer-header-main">
          <span>
            Tactical Map / {mapPresentation.displayName} / Round {frame.roundNumber} / Tick{" "}
            {Math.round(currentTick)}
          </span>
          <span className={`map-calibration-pill ${mapPresentation.confidence}`}>
            {mapPresentation.confidence}
          </span>
        </div>
        <span>{round ? `${round.winnerSide} won round ${round.roundNumber}` : "Mock replay"}</span>
      </div>

      <div className={`map-frame ${hasRadarImage ? "radar-map-frame" : "fallback-map-frame"}`}>
        <svg
          viewBox="0 0 100 100"
          role="img"
          aria-label={`${mapPresentation.displayName} tactical minimap ${mapPresentation.confidence}`}
        >
          <defs>
            <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
              <path d="M 5 0 L 0 0 0 5" fill="none" stroke="#1c2a32" strokeWidth="0.25" />
            </pattern>
          </defs>
          {mapPresentation.radarImagePath ? (
            <RadarImageBackground radarUrl={mapPresentation.radarImagePath} />
          ) : (
            <GenericMapBackground label={`${mapPresentation.displayName} uncalibrated`} />
          )}

          {frame.players.map((player, index) => (
            <PlayerDot
              key={player.id}
              player={player}
              index={index + 1}
              selected={selectedPlayerId === player.id}
              onSelectPlayer={onSelectPlayer}
            />
          ))}

          {frame.bombState.status === "planted" && frame.bombState.x && frame.bombState.y ? (
            <g transform={`translate(${frame.bombState.x} ${frame.bombState.y})`}>
              <rect x="-2" y="-2" width="4" height="4" rx="0.6" fill="#f4b740" />
              <circle r="4" fill="none" stroke="#f4b740" strokeDasharray="1 1" />
            </g>
          ) : null}

          {nearbyParserEvents.map((event) => (
            <ParserEventMapMarker key={event.id} event={event} />
          ))}
        </svg>

        <div className="player-list">
          <Roster title="T Side" players={tPlayers} />
          <Roster title="CT Side" players={ctPlayers} />
        </div>
      </div>
    </section>
  );
}

function ParserEventMapMarker({ event }: { event: ReplayEvent }) {
  if (typeof event.x !== "number" || typeof event.y !== "number") {
    return null;
  }
  const presentation = parserEventPresentationForType(event.type);

  return (
    <g
      className={`parser-map-event ${presentation.tone}`}
      transform={`translate(${event.x} ${event.y})`}
    >
      <circle r="3.2" />
      <text y="1.3" textAnchor="middle" pointerEvents="none">
        {presentation.shortLabel}
      </text>
      <title>{`${event.label} at tick ${event.tick}`}</title>
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
  players
}: {
  title: string;
  players: ReplayFramePlayer[];
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
          <span>{player.name}</span>
          <span>{player.alive ? player.hp : 0}</span>
        </div>
      ))}
    </div>
  );
}

function getInterpolatedFrameForTick(frames: ReplayFrame[], tick: number): ReplayFrame {
  const firstFrame = frames[0];
  if (tick <= firstFrame.tick) {
    return firstFrame;
  }

  for (let index = 1; index < frames.length; index += 1) {
    const nextFrame = frames[index];
    if (nextFrame.tick < tick) {
      continue;
    }

    const previousFrame = frames[index - 1];
    if (nextFrame.tick === previousFrame.tick) {
      return previousFrame;
    }

    const progress = Math.min(
      1,
      Math.max(0, (tick - previousFrame.tick) / (nextFrame.tick - previousFrame.tick))
    );

    return {
      ...previousFrame,
      tick,
      timeSeconds: interpolate(previousFrame.timeSeconds, nextFrame.timeSeconds, progress),
      roundNumber: progress < 0.5 ? previousFrame.roundNumber : nextFrame.roundNumber,
      players: interpolatePlayers(previousFrame.players, nextFrame.players, progress),
      bombState: progress < 0.5 ? previousFrame.bombState : nextFrame.bombState
    };
  }

  return frames[frames.length - 1];
}

function interpolatePlayers(
  previousPlayers: ReplayFramePlayer[],
  nextPlayers: ReplayFramePlayer[],
  progress: number
): ReplayFramePlayer[] {
  const nextById = new Map(nextPlayers.map((player) => [player.id, player]));
  return previousPlayers.map((player) => {
    const nextPlayer = nextById.get(player.id);
    if (!nextPlayer) {
      return player;
    }

    return {
      ...player,
      x: interpolate(player.x, nextPlayer.x, progress),
      y: interpolate(player.y, nextPlayer.y, progress),
      alive: progress < 0.85 ? player.alive : nextPlayer.alive,
      hp: Math.round(interpolate(player.hp, nextPlayer.hp, progress)),
      hasBomb: progress < 0.5 ? player.hasBomb : nextPlayer.hasBomb
    };
  });
}

function interpolate(start: number, end: number, progress: number): number {
  return start + (end - start) * progress;
}
