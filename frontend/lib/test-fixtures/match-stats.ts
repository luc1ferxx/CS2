import type { PlayerSide, ReplayData, ReplayEvent, ReplayFrame, ReplayRound } from "@/types/replay";

/*
 * A 2v2 match for the match-stats helpers: 28 rounds (12 + 12 + 4 overtime), A = Ace + Ash
 * (starts T), B = Bolt + Blaze (starts CT). Bolt drops out in round 28 and Cinder takes his
 * place on B. Final score A 15 : B 13.
 *   Round  1-12  A=T   winners T C T C T(no reason) C C C C C C C  -> A 3,  B 9
 *   Round 13-24  A=CT  13-21 CT, 22-24 T                           -> A 9,  B 3
 *   Round 25-27  A=T   T T C, round 28 A=CT: CT                    -> A 3,  B 1
 * Kills (tickRate 64, trade window 320 ticks):
 *   r1  Ace kills Bolt (hs, assist Ash) 1200; Blaze kills Ace 1300; Ash kills Blaze 1556 (trade)
 *   r2  Bolt kills Ash 2200 (no x/y, assist Blaze); Ace kills Bolt 2584 (6 s later: no trade)
 *   r3  Ash team-kills Ace 3200; Blaze kills Ash 3300 (hs, "assist" by Ace ignored)
 *   r4  two malformed kills; Blaze dies to the world 4300
 *   r13 Ace kills Bolt 13200 (Ace now CT)
 *   r25 Ace kills himself 25200; Ash kills Blaze 25300
 */

const TICK_RATE = 64;

const PLAYERS = {
  a1: { name: "Ace", x: 20, y: 30 },
  a2: { name: "Ash", x: 25, y: 35 },
  b1: { name: "Bolt", x: 70, y: 60 },
  b2: { name: "Blaze", x: 75, y: 65 },
  c1: { name: "Cinder", x: 80, y: 70 }
} as const;
type FixturePlayerId = keyof typeof PLAYERS;

// Side of team A per round; B plays the other one.
function sideOfA(roundNumber: number): PlayerSide {
  if (roundNumber <= 12) return "T";
  if (roundNumber <= 24) return "CT";
  return roundNumber <= 27 ? "T" : "CT";
}

const WINNERS: Record<number, [PlayerSide, string | undefined]> = {
  1: ["T", "bomb_exploded"],
  2: ["CT", "bomb_defused"],
  3: ["T", "ct_killed"],
  4: ["CT", "time_ran_out"],
  5: ["T", undefined],
  22: ["T", "ct_killed"],
  23: ["T", "ct_killed"],
  24: ["T", "ct_killed"],
  25: ["T", "ct_killed"],
  26: ["T", "bomb_exploded"],
  27: ["CT", "t_killed"],
  28: ["CT", "t_killed"]
};

function round(roundNumber: number): ReplayRound {
  // Every other round is a CT elimination: B in the first half, A in the second.
  const [winnerSide, winnerReason] = WINNERS[roundNumber] ?? ["CT", "t_killed"];
  const startTick = roundNumber * 1000;
  const result: ReplayRound = { roundNumber, startTick, freezeEndTick: startTick + 100, endTick: startTick + 900, winnerSide };
  if (winnerReason) result.winnerReason = winnerReason;
  return result;
}

function roster(roundNumber: number): FixturePlayerId[] {
  return roundNumber === 28 ? ["a1", "a2", "c1", "b2"] : ["a1", "a2", "b1", "b2"];
}

function frame(roundNumber: number, tick: number, shift: number): ReplayFrame {
  const sideA = sideOfA(roundNumber);
  return {
    tick,
    timeSeconds: tick / TICK_RATE,
    roundNumber,
    bombState: { status: "unknown" },
    players: roster(roundNumber).map((id) => ({
      id,
      name: PLAYERS[id].name,
      side: id.startsWith("a") ? sideA : sideA === "T" ? "CT" : "T",
      x: PLAYERS[id].x + shift,
      y: PLAYERS[id].y,
      z: 0,
      alive: true,
      hp: 100,
      hasBomb: false
    }))
  };
}

interface KillSpec {
  tick: number;
  attacker?: FixturePlayerId;
  victim: FixturePlayerId;
  assister?: FixturePlayerId;
  weapon?: string;
  headshot?: boolean;
  at?: [number, number, number];
}

function kill(roundNumber: number, spec: KillSpec): ReplayEvent {
  const sideA = sideOfA(roundNumber);
  const sideOf = (id: FixturePlayerId): PlayerSide => (id.startsWith("a") ? sideA : sideA === "T" ? "CT" : "T");
  const metadata: Record<string, unknown> = {
    victimId: spec.victim,
    victimName: PLAYERS[spec.victim].name,
    victimSide: sideOf(spec.victim),
    headshot: spec.headshot ?? false
  };
  if (spec.attacker) {
    metadata.attackerId = spec.attacker;
    metadata.attackerName = PLAYERS[spec.attacker].name;
    metadata.attackerSide = sideOf(spec.attacker);
  }
  if (spec.assister) metadata.assisterId = spec.assister;
  if (spec.weapon) metadata.weapon = spec.weapon;
  const event: ReplayEvent = {
    id: `kill-${spec.tick}-${spec.victim}`,
    type: "kill",
    tick: spec.tick,
    roundNumber,
    source: "parser",
    playerIds: [spec.attacker, spec.victim].filter((id): id is FixturePlayerId => Boolean(id)),
    playerId: spec.attacker ?? null,
    label: "kill",
    metadata
  };
  if (spec.at) [event.x, event.y, event.z] = spec.at;
  return event;
}

function damage(roundNumber: number, tick: number, attacker: FixturePlayerId, victim: FixturePlayerId,
  damageHealth: unknown, health?: number): ReplayEvent {
  return {
    id: `damage-${tick}-${attacker}-${victim}`,
    type: "damage",
    tick,
    roundNumber,
    source: "parser",
    playerIds: [attacker, victim],
    playerId: attacker,
    label: "Damage",
    metadata: { attackerId: attacker, victimId: victim, weapon: "ak47", damageHealth, ...(health === undefined ? {} : { health }) }
  };
}

function utility(type: "smoke" | "flash" | "molotov" | "he", roundNumber: number, tick: number,
  playerId: FixturePlayerId | null): ReplayEvent {
  return { id: `${type}-${tick}`, type, tick, roundNumber, source: "parser", playerIds: playerId ? [playerId] : [], playerId, label: type };
}

const ROUND_NUMBERS = Array.from({ length: 28 }, (_, index) => index + 1);

export const matchStatsReplay: ReplayData = {
  demoId: "match-stats-fixture",
  mapName: "de_mirage",
  tickRate: TICK_RATE,
  video: {
    status: "pending", url: null, durationSeconds: 0, tickStart: 0, tickEnd: 0, tickRate: TICK_RATE,
    source: "mock", timeOriginSeconds: 0
  },
  rounds: ROUND_NUMBERS.map(round),
  // Like real replays, players[].side is the final side, not the starting one.
  players: (["a1", "a2", "b1", "b2"] as const).map((id) => ({
    id, name: PLAYERS[id].name, side: id.startsWith("a") ? "CT" : "T", color: "#888888"
  })),
  frames: ROUND_NUMBERS.flatMap((roundNumber) => [
    frame(roundNumber, roundNumber * 1000 + 100, 0),
    frame(roundNumber, roundNumber * 1000 + 500, 5)
  ]),
  events: [
    kill(1, { tick: 1200, attacker: "a1", victim: "b1", assister: "a2", weapon: "ak47", headshot: true, at: [40, 40, 0] }),
    kill(1, { tick: 1300, attacker: "b2", victim: "a1", weapon: "m4a1", at: [30, 30, 0] }),
    kill(1, { tick: 1556, attacker: "a2", victim: "b2", weapon: "ak47", headshot: true, at: [50, 50, 0] }),
    kill(2, { tick: 2200, attacker: "b1", victim: "a2", assister: "b2", weapon: "awp" }),
    kill(2, { tick: 2584, attacker: "a1", victim: "b1", weapon: "ak47", at: [60, 60, 0] }),
    kill(3, { tick: 3200, attacker: "a2", victim: "a1", weapon: "ak47", at: [22, 32, 0] }),
    kill(3, { tick: 3300, attacker: "b2", victim: "a2", assister: "a1", weapon: "m4a1", headshot: true, at: [45, 50, -10] }),
    { id: "kill-empty", type: "kill", tick: 4100, roundNumber: 4, source: "parser", playerIds: [], label: "kill", metadata: {} },
    { id: "kill-no-metadata", type: "kill", tick: 4200, roundNumber: 4, source: "parser", playerIds: [], label: "kill" },
    kill(4, { tick: 4300, victim: "b2", at: [77, 66, 0] }),
    { ...kill(4, { tick: 4400, attacker: "a1", victim: "b2" }), tick: Number.NaN },
    kill(13, { tick: 13200, attacker: "a1", victim: "b1", weapon: "m4a1", at: [65, 55, 0] }),
    kill(25, { tick: 25200, attacker: "a1", victim: "a1", weapon: "hegrenade", at: [21, 31, 0] }),
    kill(25, { tick: 25300, attacker: "a2", victim: "b2", weapon: "ak47", at: [70, 70, 0] }),
    damage(1, 1100, "b1", "a1", 30, 70),
    damage(1, 1150, "a2", "a1", 20, 50),
    damage(1, 1190, "a1", "b1", 100, 0),
    damage(1, 1300, "b2", "a1", 123, 0),
    damage(1, 1556, "a2", "b2", 100, 0),
    damage(3, 3100, "a1", "a1", 10, 90),
    damage(3, 3150, "b1", "a2", "x"),
    utility("smoke", 1, 1050, "a1"),
    utility("flash", 1, 1060, "a1"),
    utility("smoke", 13, 13050, "a1"),
    utility("molotov", 13, 13060, "a1"),
    utility("he", 13, 13070, "a1"),
    utility("flash", 2, 2050, "b1"),
    utility("smoke", 2, 2060, null)
  ],
  generatedAt: "2026-09-27T00:00:00Z"
};
