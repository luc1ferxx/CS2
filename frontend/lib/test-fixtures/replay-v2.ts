// Hand-written replay contract v2 fixture (live roster: playerStates change points, utility) until
// real v2 replays exist. Two rounds on Mirage, two players a side, three kills. Imports types only,
// so node tests can load it with the same transpile-and-run loader as the libs.
import type {
  ReplayData,
  ReplayEvent,
  ReplayFrame,
  ReplayFramePlayer,
  ReplayPlayerState,
  ReplayRound
} from "@/types/replay";

export const V2_ALPHA = "76561198000000011"; // T in both rounds, carries the bomb in round 1
export const V2_BRAVO = "76561198000000012"; // T, dies at tick 700
export const V2_CHARLIE = "76561198000000013"; // CT, has a defuse kit
export const V2_DELTA = "76561198000000014"; // CT, dies at tick 500

const NAMES: Record<string, string> = {
  [V2_ALPHA]: "Alpha",
  [V2_BRAVO]: "Bravo",
  [V2_CHARLIE]: "Charlie",
  [V2_DELTA]: "Delta"
};
const SIDES: Record<string, "T" | "CT"> = { [V2_ALPHA]: "T", [V2_BRAVO]: "T", [V2_CHARLIE]: "CT", [V2_DELTA]: "CT" };
const ORDER = [V2_ALPHA, V2_BRAVO, V2_CHARLIE, V2_DELTA];

export function v2Rounds(): ReplayRound[] {
  return [
    { roundNumber: 1, startTick: 100, freezeEndTick: 164, endTick: 900, winnerSide: "T", winnerReason: "ct_killed" },
    { roundNumber: 2, startTick: 1000, freezeEndTick: 1064, endTick: 1800, winnerSide: "CT", winnerReason: "time_ran_out" }
  ];
}

// Who is dead (and at what hp everyone else is) per frame tick.
const HP: Record<number, Partial<Record<string, number>>> = {
  100: {}, 164: {}, 300: { [V2_BRAVO]: 76 }, 500: { [V2_BRAVO]: 76, [V2_DELTA]: 0 },
  700: { [V2_BRAVO]: 0, [V2_DELTA]: 0, [V2_CHARLIE]: 23 }, 900: { [V2_BRAVO]: 0, [V2_DELTA]: 0, [V2_CHARLIE]: 23 },
  1000: {}, 1064: {}, 1300: { [V2_ALPHA]: 0 }, 1600: { [V2_ALPHA]: 0 }
};

function frame(tick: number, roundNumber: number): ReplayFrame {
  const hp = HP[tick] ?? {};
  const players: ReplayFramePlayer[] = ORDER.map((id, index) => {
    const health = hp[id] ?? 100;
    return {
      id,
      name: NAMES[id],
      side: SIDES[id],
      x: 20 + index * 15 + (tick % 1000) / 100,
      y: 30 + index * 10,
      z: -160,
      alive: health > 0,
      hp: health,
      hasBomb: id === V2_ALPHA && roundNumber === 1
    };
  });
  return {
    tick,
    timeSeconds: tick / 64,
    roundNumber,
    players,
    bombState: roundNumber === 1 ? { status: "carried", carrierPlayerId: V2_ALPHA } : { status: "unknown" }
  };
}

function kill(id: string, tick: number, roundNumber: number, attackerId: string, victimId: string, weapon: string, headshot = false): ReplayEvent {
  return {
    id,
    type: "kill",
    tick,
    roundNumber,
    source: "parser",
    playerIds: [attackerId, victimId],
    playerId: attackerId,
    playerName: NAMES[attackerId],
    side: SIDES[attackerId],
    x: 50,
    y: 50,
    label: `${NAMES[attackerId]} killed ${NAMES[victimId]}`,
    metadata: {
      attackerId, attackerName: NAMES[attackerId], attackerSide: SIDES[attackerId],
      victimId, victimName: NAMES[victimId], victimSide: SIDES[victimId], weapon, headshot
    }
  };
}

export function v2PlayerStates(): Record<string, ReplayPlayerState[]> {
  return {
    [V2_ALPHA]: [
      { tick: 100, money: 800, armor: 0, helmet: false, weapon: "Glock-18", grenades: [], equipValue: 200 },
      { tick: 164, money: 50, armor: 100, helmet: false, weapon: "Glock-18", grenades: ["flash", "flash", "smoke"], equipValue: 950 },
      { tick: 300, money: 50, armor: 100, helmet: false, weapon: "Smoke Grenade", grenades: ["flash", "flash", "smoke"], equipValue: 950 },
      { tick: 316, money: 50, armor: 100, helmet: false, weapon: "Glock-18", grenades: ["flash", "flash"], equipValue: 650 },
      { tick: 1000, money: 3350, armor: 100, helmet: false, weapon: "Glock-18", grenades: [], equipValue: 500 },
      { tick: 1064, money: 250, armor: 100, helmet: true, weapon: "AK-47", grenades: ["molotov", "he"], equipValue: 4700 },
      { tick: 1300, money: 250, armor: 0, helmet: false, weapon: null, grenades: [], equipValue: 0 }
    ],
    [V2_BRAVO]: [
      { tick: 100, money: 800, armor: 0, helmet: false, weapon: "Glock-18", grenades: [], equipValue: 200 },
      { tick: 164, money: 0, armor: 100, helmet: true, weapon: "Glock-18", grenades: ["he"], equipValue: 1150 },
      { tick: 700, money: 0, armor: 0, helmet: false, weapon: null, grenades: [], equipValue: 0 },
      { tick: 1000, money: 2400, armor: 0, helmet: false, weapon: "Glock-18", grenades: [], equipValue: 200 },
      { tick: 1064, money: 100, armor: 100, helmet: true, weapon: "Galil AR", grenades: ["smoke", "decoy"], equipValue: 3100 }
    ],
    [V2_CHARLIE]: [
      { tick: 100, money: 800, armor: 0, helmet: false, defuser: false, weapon: "USP-S", grenades: [], equipValue: 200 },
      { tick: 164, money: 0, armor: 0, helmet: false, defuser: true, weapon: "USP-S", grenades: ["smoke"], equipValue: 800 },
      { tick: 700, money: 300, armor: 0, helmet: false, defuser: true, weapon: "USP-S", grenades: ["smoke"], equipValue: 800 },
      { tick: 1000, money: 1700, armor: 0, helmet: false, defuser: true, weapon: "USP-S", grenades: ["smoke"], equipValue: 800 },
      { tick: 1064, money: 0, armor: 100, helmet: true, defuser: true, weapon: "M4A1-S", grenades: ["smoke", "flash"], equipValue: 4600 }
    ],
    [V2_DELTA]: [
      { tick: 100, money: 800, armor: 0, helmet: false, defuser: false, weapon: "USP-S", grenades: [], equipValue: 200 },
      { tick: 164, money: 150, armor: 100, helmet: false, defuser: false, weapon: "USP-S", grenades: ["flash"], equipValue: 850 },
      { tick: 500, money: 150, armor: 0, helmet: false, defuser: false, weapon: null, grenades: [], equipValue: 0 },
      { tick: 1000, money: 1950, armor: 0, helmet: false, defuser: false, weapon: "USP-S", grenades: [], equipValue: 200 },
      { tick: 1064, money: 50, armor: 100, helmet: true, defuser: false, weapon: "AWP", grenades: ["flash"], equipValue: 5950 }
    ]
  };
}

export function replayV2(overrides: Partial<ReplayData> = {}): ReplayData {
  const frames = [
    ...[100, 164, 300, 500, 700, 900].map((tick) => frame(tick, 1)),
    ...[1000, 1064, 1300, 1600].map((tick) => frame(tick, 2))
  ];
  return {
    demoId: "demo-v2",
    contractVersion: "replay_contract_v2",
    mapName: "de_mirage",
    tickRate: 64,
    video: {
      status: "pending", url: null, durationSeconds: 0, tickStart: 0, tickEnd: 0, tickRate: 64, source: "mock",
      errorCode: null, errorMessage: null, timeOriginSeconds: 0, povSteamId: null, renderJobId: null
    },
    rounds: v2Rounds(),
    players: ORDER.map((id) => ({ id, name: NAMES[id], side: SIDES[id], color: SIDES[id] === "T" ? "#f5b542" : "#2ed3d0" })),
    frames,
    events: [
      kill("kill-1", 500, 1, V2_ALPHA, V2_DELTA, "ak47", true),
      kill("kill-2", 700, 1, V2_CHARLIE, V2_BRAVO, "m4a1_silencer"),
      kill("kill-3", 1300, 2, V2_DELTA, V2_ALPHA, "awp")
    ],
    playerStates: v2PlayerStates(),
    utility: [
      {
        id: "utility-smoke-301-300",
        type: "smoke",
        throwerId: V2_ALPHA,
        throwerName: "Alpha",
        throwerSide: "T",
        roundNumber: 1,
        throwTick: 300,
        detonateTick: 380,
        endTick: 1532,
        points: [
          { tick: 300, x: 21, y: 30, z: -160 },
          { tick: 340, x: 30, y: 35, z: -100 },
          { tick: 380, x: 40, y: 42, z: -160 }
        ]
      }
    ],
    generatedAt: "2026-09-30T12:00:00Z",
    ...overrides
  };
}

/** The same match as a v1 replay: no playerStates, no utility. */
export function replayV1(): ReplayData {
  return replayV2({ contractVersion: "replay_contract_v1", playerStates: {}, utility: [] });
}
