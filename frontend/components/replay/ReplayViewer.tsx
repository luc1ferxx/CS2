"use client";

import type { ReplayData, ReplayFrame, ReplayFramePlayer } from "@/types/replay";

interface ReplayViewerProps {
  replay: ReplayData;
  currentTick: number;
  selectedPlayerId: string | null;
  onSelectPlayer: (playerId: string) => void;
  variant?: "full" | "companion";
}

export function ReplayViewer({
  replay,
  currentTick,
  selectedPlayerId,
  onSelectPlayer,
  variant = "full"
}: ReplayViewerProps) {
  const frame = getFrameForTick(replay.frames, currentTick);
  const tPlayers = frame.players.filter((player) => player.side === "T");
  const ctPlayers = frame.players.filter((player) => player.side === "CT");
  const round = replay.rounds.find((item) => item.roundNumber === frame.roundNumber);

  return (
    <section
      className={`panel replay-panel ${variant === "companion" ? "tactical-panel" : ""}`}
      aria-label={variant === "companion" ? "Tactical map companion" : "2D replay viewer"}
    >
      <div className="viewer-header">
        <span>
          Tactical Map / Round {frame.roundNumber} / Tick {Math.round(currentTick)}
        </span>
        <span>{round ? `${round.winnerSide} won round ${round.roundNumber}` : "Mock replay"}</span>
      </div>

      <div className="map-frame">
        <svg viewBox="0 0 100 100" role="img" aria-label="Abstract tactical minimap">
          <defs>
            <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
              <path d="M 5 0 L 0 0 0 5" fill="none" stroke="#1c2a32" strokeWidth="0.25" />
            </pattern>
          </defs>
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
        </svg>

        <div className="player-list">
          <Roster title="T Side" players={tPlayers} />
          <Roster title="CT Side" players={ctPlayers} />
        </div>
      </div>
    </section>
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

function getFrameForTick(frames: ReplayFrame[], tick: number): ReplayFrame {
  let selected = frames[0];
  for (const frame of frames) {
    if (frame.tick > tick) {
      break;
    }
    selected = frame;
  }
  return selected;
}
