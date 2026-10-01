import type { PlayerSide, ReplayData, ReplayFrame, ReplayPlayerState, ReplayRound } from "@/types/replay";

/*
 * Compact matches for lib/round-economy.ts. Team A (a1..a5) starts T, team B (b1..b5) CT; each
 * round's sides are written into its frames only, so the side rule has to find them there.
 * Round n: startTick = n x 10000, freezeEndTick = start + 1000, endTick = start + 8000.
 * Frames at start, start + 1000, start + 1500 (the first one at or after start + 20 s at 64 tick)
 * and start + 6000. Every player's playerStates track per round:
 *   start         $800, equip 200 (before the buy)
 *   start + 1000  the round's buy: { equip, money } per player
 *   start + 1500  equip + 100 (a late grenade), so a test can tell which tick was read
 * Imports types only, so node tests can load it with the libs' transpile-and-run loader.
 */

export const ECON_TICK_RATE = 64;

/** Per-player buy: equipValue and money left at freeze end. */
export type EconomyBuy = [equip: number, money: number];

export interface EconomyRoundSpec {
  sideA: PlayerSide;
  winner: PlayerSide;
  a: EconomyBuy;
  b: EconomyBuy;
}

export interface EconomyMatchOptions {
  playersA?: number;
  playersB?: number;
}

export const FULL_BUY: EconomyBuy = [4500, 1500];

/** Rounds from a string of team A's sides ("T" or "C" per round); B plays the other side. */
export function roundsFromSides(sidesOfA: string, buy: { a?: EconomyBuy; b?: EconomyBuy } = {}): EconomyRoundSpec[] {
  return [...sidesOfA].map((code) => {
    const sideA: PlayerSide = code === "T" ? "T" : "CT";
    return { sideA, winner: sideA, a: buy.a ?? FULL_BUY, b: buy.b ?? FULL_BUY };
  });
}

export function economyReplay(specs: EconomyRoundSpec[], options: EconomyMatchOptions = {}): ReplayData {
  const teamA = playerIds("a", options.playersA ?? 5);
  const teamB = playerIds("b", options.playersB ?? 5);
  const rounds: ReplayRound[] = [];
  const frames: ReplayFrame[] = [];
  const playerStates: Record<string, ReplayPlayerState[]> = {};
  specs.forEach((spec, position) => {
    const roundNumber = position + 1;
    const start = roundNumber * 10000;
    rounds.push({ roundNumber, startTick: start, freezeEndTick: start + 1000, endTick: start + 8000, winnerSide: spec.winner });
    const sideB: PlayerSide = spec.sideA === "T" ? "CT" : "T";
    for (const tick of [start, start + 1000, start + 1500, start + 6000]) {
      frames.push({
        tick,
        timeSeconds: tick / ECON_TICK_RATE,
        roundNumber,
        bombState: { status: "unknown" },
        players: [...teamA.map((id) => [id, spec.sideA] as const), ...teamB.map((id) => [id, sideB] as const)]
          .map(([id, side], index) => ({ id, name: id.toUpperCase(), side, x: index * 10, y: 50, z: 0, alive: true, hp: 100, hasBomb: false }))
      });
    }
    for (const [ids, [equip, money]] of [[teamA, spec.a], [teamB, spec.b]] as const) {
      for (const id of ids) {
        (playerStates[id] ??= []).push(
          { tick: start, money: 800, equipValue: 200 },
          { tick: start + 1000, money, equipValue: equip },
          { tick: start + 1500, money, equipValue: equip + 100 }
        );
      }
    }
  });
  return {
    demoId: "demo-economy",
    contractVersion: "replay_contract_v2",
    mapName: "de_mirage",
    tickRate: ECON_TICK_RATE,
    video: {
      status: "pending", url: null, durationSeconds: 0, tickStart: 0, tickEnd: 0, tickRate: ECON_TICK_RATE, source: "mock",
      errorCode: null, errorMessage: null, timeOriginSeconds: 0, povSteamId: null, renderJobId: null
    },
    rounds,
    players: [...teamA, ...teamB].map((id) => ({
      id, name: id.toUpperCase(), side: id.startsWith("a") ? "T" : "CT", color: id.startsWith("a") ? "#f5b542" : "#2ed3d0"
    })),
    frames,
    events: [],
    playerStates,
    utility: [],
    generatedAt: "2026-09-30T12:00:00Z"
  };
}

function playerIds(prefix: string, count: number): string[] {
  return Array.from({ length: count }, (_, index) => `${prefix}${index + 1}`);
}
