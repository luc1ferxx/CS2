"use client";

import { memo } from "react";

import { sanitizeRadarPoint } from "@/lib/map-config";
import { ROUND_END_REASON_LABELS, roundEndReason } from "@/lib/match-stats";
import { mapKillFacts, type MapKillFacts } from "@/lib/replay-events";
import { getFrameForTick } from "@/lib/replay-frames";
import type { BombStatus, PlayerSide, ReplayEvent, ReplayFrame, ReplayRound } from "@/types/replay";

// The radar as a live ops screen: a status bar over the map (alive, equipment, clock, bomb or the
// round's verdict), killer→victim traces at the kill moment and a corner kill feed. Every readout
// changes by a hard cut, like an instrument: no transitions here, nothing that tweens per frame.

/** A kill of the round on screen with where both players stood at the kill tick (radar percent). */
export interface MapKill extends MapKillFacts {
  type: "kill";
  roundNumber: number;
  killer: { x: number; y: number; z: number | null } | null;
  victim: { x: number; y: number; z: number | null } | null;
}

/**
 * The round's kills with both ends placed: the killer from the frame at the kill tick (the parser
 * samples every death tick), the victim from that frame or the event's own position. Names and
 * sides the event leaves out (mock kills) come from that frame too. Built once per round.
 */
export function mapKillsForRound(events: ReplayEvent[], frames: ReplayFrame[], tickRate: number): MapKill[] {
  const kills: MapKill[] = [];
  for (const event of events) {
    if (event.type !== "kill" || !Number.isFinite(event.tick)) continue;
    const facts = mapKillFacts(event);
    const frame = getFrameForTick(frames, event.tick, tickRate);
    const players = frame?.roundNumber === event.roundNumber ? frame.players : [];
    const killer = facts.attackerId ? players.find((player) => player.id === facts.attackerId) : undefined;
    const victim = facts.victimId ? players.find((player) => player.id === facts.victimId) : undefined;
    kills.push({
      ...facts,
      type: "kill",
      roundNumber: event.roundNumber,
      attackerName: facts.attackerName ?? killer?.name ?? null,
      attackerSide: facts.attackerSide ?? killer?.side ?? null,
      victimName: facts.victimName ?? victim?.name ?? null,
      victimSide: facts.victimSide ?? victim?.side ?? null,
      killer: placed(killer),
      victim: placed(victim) ?? placed(event)
    });
  }
  return kills;
}

function placed(point: { x?: number | null; y?: number | null; z?: number | null } | undefined) {
  const safe = point ? sanitizeRadarPoint(point) : null;
  if (!safe) return null;
  return { x: safe.x, y: safe.y, z: typeof safe.z === "number" && Number.isFinite(safe.z) ? safe.z : null };
}

/** "CT 胜：全歼" in the winner's colour once the round is over, through the next round's start. */
export function roundVerdictText(round: ReplayRound): string {
  const reason = roundEndReason(round);
  return reason === "other" ? `${round.winnerSide} 胜` : `${round.winnerSide} 胜：${ROUND_END_REASON_LABELS[reason]}`;
}

interface MapHudProps {
  // Team names from the match summary; the side letter stands alone without them.
  tName: string | null;
  ctName: string | null;
  tAlive: number;
  tTotal: number;
  ctAlive: number;
  ctTotal: number;
  // Live equipment value of the side's living players; null on v1 replays.
  tEquipment: number | null;
  ctEquipment: number | null;
  // Round clock, "m:ss", the transport's convention.
  clock: string;
  bombStatus: BombStatus | null;
  bombCarrier: string | null;
  bombSite: string | null;
  verdict: string | null;
  verdictSide: PlayerSide | null;
}

// Primitive props only: during playback it re-renders when a whole second ticks over, someone
// dies, the bomb changes hands or an equipment value moves, never per animation frame.
export const MapHud = memo(function MapHud(props: MapHudProps) {
  const { clock, verdict, verdictSide } = props;
  return (
    <div className="map-hud" role="group" aria-label="回合状态">
      <div className="map-hud-inner">
        <HudSide side="T" name={props.tName} alive={props.tAlive} total={props.tTotal} equipment={props.tEquipment} />
        <div className="map-hud-centre">
          <span className="map-hud-clock"><span className="visually-hidden">回合时间 </span>{clock}</span>
          {verdict ? (
            <strong className={`map-hud-status map-hud-verdict${verdictSide ? ` side-${verdictSide.toLowerCase()}` : ""}`}>{verdict}</strong>
          ) : (
            <BombReadout status={props.bombStatus} carrier={props.bombCarrier} site={props.bombSite} />
          )}
        </div>
        <HudSide side="CT" name={props.ctName} alive={props.ctAlive} total={props.ctTotal} equipment={props.ctEquipment} />
      </div>
    </div>
  );
});

function HudSide({ side, name, alive, total, equipment }: {
  side: PlayerSide;
  name: string | null;
  alive: number;
  total: number;
  equipment: number | null;
}) {
  const sideClass = `side-${side.toLowerCase()}`;
  return (
    <div className={`map-hud-team ${sideClass}`}>
      {name ? <span className="map-hud-name" title={name}>{name}</span> : null}
      <span className="map-hud-side">{side}</span>
      <span className="map-hud-pips" role="img" aria-label={`${side} 存活 ${alive}/${total}`}>
        {Array.from({ length: total }, (_, index) => <i key={index} className={index < alive ? "alive" : undefined} />)}
      </span>
      {equipment !== null ? (
        <span className="map-hud-equipment"><span className="map-hud-equipment-label">装备 </span>{formatMoney(equipment)}</span>
      ) : null}
    </div>
  );
}

function BombReadout({ status, carrier, site }: { status: BombStatus | null; carrier: string | null; site: string | null }) {
  // Nothing known about the bomb: no readout rather than "未知".
  if (!status || status === "unknown") return <span className="map-hud-status" data-testid="bomb-status" />;
  if (status === "carried") {
    return (
      <span className="map-hud-status" data-testid="bomb-status">
        炸弹：{carrier ? <><b className="map-hud-carrier">{carrier}</b> 携带</> : "携带中"}
      </span>
    );
  }
  const label = status === "planted" ? `已安装${site ? ` ${site} 点` : ""}` : BOMB_LABELS[status];
  return (
    <span className={`map-hud-status bomb-${status}`} data-testid="bomb-status">炸弹：<b>{label}</b></span>
  );
}

const BOMB_LABELS: Record<Exclude<BombStatus, "carried" | "planted" | "unknown">, string> = {
  dropped: "已掉落",
  defused: "已拆除",
  exploded: "已爆炸"
};

/** The kill feed's rows, newest first; the viewer hands over a new array only when the kill ids change. */
export const MapKillFeed = memo(function MapKillFeed({ kills }: { kills: MapKill[] }) {
  return (
    <ol className="map-kill-feed" aria-label="最近击杀">
      {kills.map((kill) => (
        <li key={kill.id} className="map-kill-row">
          <span className={`map-kill-name${sideClass(kill.attackerSide)}`}>{kill.attackerName ?? "未知"}</span>
          <span className="visually-hidden"> 击杀 </span>
          <span className="map-kill-cross" aria-hidden="true">✕</span>
          <span className={`map-kill-name${sideClass(kill.victimSide)}`}>{kill.victimName ?? "未知"}</span>
          {kill.weapon ? <span className="map-kill-weapon">{kill.weapon}</span> : null}
          {kill.headshot ? <span className="map-kill-headshot">爆头</span> : null}
        </li>
      ))}
    </ol>
  );
});

export interface MapKillTrace {
  id: string;
  side: PlayerSide | null;
  from: { x: number; y: number };
  to: { x: number; y: number };
  opacity: number;
  // One end on the other floor of a two-floor map.
  crossFloor: boolean;
}

/** Killer→victim lines in the killer's side colour; a pixel-constant stroke whatever the map's size. */
export function MapKillTraces({ traces }: { traces: MapKillTrace[] }) {
  if (traces.length === 0) return null;
  return (
    <g className="map-kill-traces" aria-hidden="true">
      {traces.map((trace) => (
        <line key={trace.id} className={`map-kill-trace${sideClass(trace.side)}${trace.crossFloor ? " cross-floor" : ""}`}
          x1={trace.from.x} y1={trace.from.y} x2={trace.to.x} y2={trace.to.y} opacity={trace.opacity} />
      ))}
    </g>
  );
}

function sideClass(side: PlayerSide | null): string {
  return side ? ` side-${side.toLowerCase()}` : "";
}

const MONEY = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function formatMoney(value: number): string {
  return `$${MONEY.format(Math.round(value))}`;
}
