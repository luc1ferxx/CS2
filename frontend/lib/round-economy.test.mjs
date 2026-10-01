import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const loaded = new Map();

function loadTypeScriptModule(relativePath) {
  const filename = resolve(__dirname, relativePath);
  if (loaded.has(filename)) return loaded.get(filename);
  const source = readFileSync(filename, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { esModuleInterop: true, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename
  });
  const module = { exports: {} };
  loaded.set(filename, module.exports);
  vm.runInNewContext(outputText, {
    console,
    exports: module.exports,
    module,
    require(specifier) {
      if (specifier.startsWith("@/types/")) return {};
      if (specifier === "@/lib/match-stats") return loadTypeScriptModule("./match-stats.ts");
      if (specifier === "@/lib/player-state") return loadTypeScriptModule("./player-state.ts");
      if (specifier === "@/lib/replay-frames") return loadTypeScriptModule("./replay-frames.ts");
      throw new Error(`Unexpected import ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const { ECONOMY_LABELS, economySummary, hasEconomy, roundEconomies } = loadTypeScriptModule("./round-economy.ts");
const { FULL_BUY, economyReplay, roundsFromSides } = loadTypeScriptModule("./test-fixtures/round-economy.ts");

// Values built inside the vm context have that realm's prototypes.
const plain = (value) => JSON.parse(JSON.stringify(value));
const tests = [];
const test = (name, run) => tests.push({ name, run });

const team = (round, key) => round.teams.find((item) => item.teamKey === key);
// One short label per round ("-" for an unknown kind), as the round strip shows them.
const strip = (rounds, key) => rounds.map((round) => {
  const kind = team(round, key).kind;
  return kind ? ECONOMY_LABELS[kind].short : "-";
}).join("");
const pistolRounds = (rounds, key = "A") => rounds.filter((round) => team(round, key).kind === "pistol").map((round) => round.roundNumber);
const full = (count) => "全".repeat(count);

test("labels are the approved short and long names", () => {
  assert.deepEqual(plain(ECONOMY_LABELS), {
    pistol: { short: "枪", name: "手枪局" },
    full: { short: "全", name: "全起" },
    force: { short: "强", name: "强起" },
    half: { short: "半", name: "半起" },
    eco: { short: "经", name: "ECO" }
  });
});

test("MR12: pistol rounds are round 1 and the half-time switch at 13", () => {
  const rounds = roundEconomies(economyReplay(roundsFromSides("T".repeat(12) + "C".repeat(12))));
  assert.equal(rounds.length, 24);
  assert.deepEqual(plain(pistolRounds(rounds, "A")), [1, 13]);
  assert.deepEqual(plain(pistolRounds(rounds, "B")), [1, 13]);
  assert.equal(strip(rounds, "A"), `枪${full(11)}枪${full(11)}`);
  assert.equal(strip(rounds, "B"), `枪${full(11)}枪${full(11)}`);
  assert.deepEqual(plain(rounds[0].teams.map((item) => [item.teamKey, item.side])), [["A", "T"], ["B", "CT"]]);
  assert.deepEqual(plain(rounds[12].teams.map((item) => [item.teamKey, item.side])), [["A", "CT"], ["B", "T"]]);
  assert.deepEqual(plain(rounds[1]), {
    roundNumber: 2,
    freezeEndTick: 21000,
    teams: [
      { teamKey: "A", side: "T", equipValue: 22500, money: 7500, players: 5, kind: "full", won: true },
      { teamKey: "B", side: "CT", equipValue: 22500, money: 7500, players: 5, kind: "full", won: false }
    ]
  });
});

test("team A is whoever started T in the frames, not the fixture's a-players", () => {
  // The b-players start T here, so they are team A and their eco is A's.
  const rounds = roundEconomies(economyReplay(roundsFromSides("CCCC", { a: FULL_BUY, b: [500, 4000] })));
  assert.equal(team(rounds[1], "A").side, "T");
  assert.equal(team(rounds[1], "A").kind, "eco");
  assert.equal(team(rounds[1], "B").kind, "full");
});

test("overtime: switches after the first one are never pistol rounds", () => {
  const rounds = roundEconomies(economyReplay(roundsFromSides(`${"T".repeat(12)}${"C".repeat(12)}TTTCCC`)));
  assert.equal(rounds.length, 30);
  assert.deepEqual(plain(pistolRounds(rounds)), [1, 13]);
  assert.equal(team(rounds[24], "A").kind, "full", "round 25");
  assert.equal(team(rounds[27], "B").kind, "full", "round 28");
  assert.equal(team(rounds[24], "A").side, "T");
});

test("MR15: the switch at 16 is the second pistol round, not round 13", () => {
  const rounds = roundEconomies(economyReplay(roundsFromSides("T".repeat(15) + "C".repeat(15))));
  assert.deepEqual(plain(pistolRounds(rounds, "A")), [1, 16]);
  assert.deepEqual(plain(pistolRounds(rounds, "B")), [1, 16]);
  assert.equal(team(rounds[12], "A").kind, "full");
});

// Keeps only rounds from `first` on, as in a demo recorded part-way through the match.
const fromRound = (replay, first) => ({
  ...replay,
  rounds: replay.rounds.filter((round) => round.roundNumber >= first),
  frames: replay.frames.filter((frame) => frame.roundNumber >= first)
});

test("a demo recorded from a later round: only a switch by round 16 is a pistol round", () => {
  // MR12 from round 13, with overtime: the switch at 25 is overtime, not a half-time pistol round.
  const mr12 = roundEconomies(fromRound(economyReplay(roundsFromSides(`${"T".repeat(12)}${"C".repeat(12)}TTTCCC`)), 13));
  assert.equal(mr12[0].roundNumber, 13);
  assert.equal(mr12.length, 18);
  assert.deepEqual(plain(pistolRounds(mr12, "A")), []);
  assert.deepEqual(plain(pistolRounds(mr12, "B")), []);
  assert.equal(strip(mr12, "A"), full(18));
  assert.deepEqual(plain(economySummary(mr12).B.pistol), { rounds: 0, wins: 0 });
  // Overtime only (from 25): the switch at 28 is not a pistol round either.
  const overtime = roundEconomies(fromRound(economyReplay(roundsFromSides(`${"T".repeat(12)}${"C".repeat(12)}TTTCCC`)), 25));
  assert.deepEqual(plain(pistolRounds(overtime, "A")), []);
  // MR12 from round 3: the half-time switch at 13 still is.
  const late = roundEconomies(fromRound(economyReplay(roundsFromSides("T".repeat(12) + "C".repeat(12))), 3));
  assert.deepEqual(plain(pistolRounds(late, "A")), [13]);
  // MR15 from round 13 (still the first half): the switch at 16 is the second pistol round.
  const mr15 = roundEconomies(fromRound(economyReplay(roundsFromSides("T".repeat(15) + "C".repeat(15))), 13));
  assert.deepEqual(plain(pistolRounds(mr15, "A")), [16]);
});

test("thresholds scale with the players present: a 4-player team", () => {
  const rounds = roundEconomies(economyReplay(roundsFromSides("TT", { a: [3200, 0], b: [4000, 0] }), { playersB: 4 }));
  // A: 5 x 3200 = 16000 < 20000 with nothing left: force. B: 4 x 4000 = 16000 >= 16000: full.
  assert.deepEqual(plain(team(rounds[1], "A")), { teamKey: "A", side: "T", equipValue: 16000, money: 0, players: 5, kind: "force", won: true });
  assert.deepEqual(plain(team(rounds[1], "B")), { teamKey: "B", side: "CT", equipValue: 16000, money: 0, players: 4, kind: "full", won: false });
});

test("only players with a frame in the round and a known equipValue count", () => {
  const replay = economyReplay(roundsFromSides("TTT", { a: [3000, 500] }));
  // a5 left before round 2 (no frame there) but still has a state track.
  replay.frames = replay.frames.map((frame) => frame.roundNumber === 2
    ? { ...frame, players: frame.players.filter((player) => player.id !== "a5") }
    : frame);
  // a4 has no equipValue at all.
  replay.playerStates.a4 = replay.playerStates.a4.map(({ tick, money }) => ({ tick, money }));
  const rounds = roundEconomies(replay);
  assert.equal(team(rounds[1], "A").players, 3);
  assert.equal(team(rounds[1], "A").equipValue, 9000);
  assert.equal(team(rounds[1], "A").money, 1500);
  assert.equal(team(rounds[2], "A").players, 4);
  assert.equal(team(rounds[2], "B").players, 5);
});

test("exact thresholds: full at 4000n, eco below 1000n or below 2000n with 1000n kept, force spent, half kept", () => {
  const buys = [
    [4000, 0], // full: equip = 4000n
    [3999, 0], // force
    [1000, 0], // force: equip = 1000n is not eco, nothing kept
    [999, 5000], // eco: equip < 1000n
    [1999, 1000], // eco: equip < 2000n with money = 1000n
    [1999, 999], // force
    [2000, 1000], // half: equip = 2000n, money = 1000n kept
    [2000, 999], // force
    [3999, 1000] // half
  ];
  const specs = [{ sideA: "T", winner: "T", a: FULL_BUY, b: FULL_BUY },
    ...buys.map((buy) => ({ sideA: "T", winner: "CT", a: buy, b: FULL_BUY }))];
  const rounds = roundEconomies(economyReplay(specs));
  assert.equal(strip(rounds, "A"), "枪全强强经经强半强半");
  assert.equal(strip(rounds, "B"), `枪${full(9)}`);
});

test("a missing or out-of-bounds freeze end falls back to the first frame 20 s in, capped at endTick", () => {
  const replay = economyReplay(roundsFromSides("TTTTT", { a: [3000, 500] }));
  replay.rounds[1].freezeEndTick = Number.NaN;
  replay.rounds[2].freezeEndTick = replay.rounds[2].endTick + 1;
  replay.rounds[3].freezeEndTick = replay.rounds[3].startTick - 1;
  replay.rounds[3].endTick = replay.rounds[3].startTick + 1200; // before start + 20 s
  replay.rounds[4].freezeEndTick = replay.rounds[4].endTick; // the bounds are inclusive
  const rounds = roundEconomies(replay);
  assert.deepEqual(plain(rounds.map((round) => round.freezeEndTick)), [11000, 21500, 31500, 41200, 58000]);
  // At start + 1500 the fixture's late grenade (+100 a player) is already in.
  assert.equal(team(rounds[1], "A").equipValue, 15500);
  assert.equal(team(rounds[3], "A").equipValue, 15000);
  // 128 tick: start + 2560 has no frame until start + 6000.
  const fast = roundEconomies({ ...replay, tickRate: 128 });
  assert.deepEqual(plain(fast.map((round) => round.freezeEndTick)), [11000, 26000, 36000, 41200, 58000]);
});

test("v1 replays and replays without an equipValue have no economy", () => {
  const replay = economyReplay(roundsFromSides("TTCC"));
  assert.equal(hasEconomy(replay), true);
  for (const playerStates of [{}, undefined, null, "bad"]) {
    assert.deepEqual(plain(roundEconomies({ ...replay, playerStates })), []);
    assert.equal(hasEconomy({ ...replay, playerStates }), false);
  }
  const moneyOnly = Object.fromEntries(Object.entries(replay.playerStates)
    .map(([id, track]) => [id, track.map(({ tick, money }) => ({ tick, money }))]));
  assert.deepEqual(plain(roundEconomies({ ...replay, playerStates: moneyOnly })), []);
  assert.deepEqual(plain(roundEconomies({ ...replay, frames: [] })), [], "no frames: no teams");
  assert.deepEqual(plain(roundEconomies(null)), []);
});

test("a round without frames has unknown kinds; no known kind anywhere is no economy", () => {
  const replay = economyReplay(roundsFromSides("TTT"));
  const rounds = roundEconomies({ ...replay, frames: replay.frames.filter((frame) => frame.roundNumber !== 2) });
  assert.deepEqual(plain(team(rounds[1], "A")), { teamKey: "A", side: "T", equipValue: 0, money: 0, players: 0, kind: null, won: true });
  assert.equal(strip(rounds, "B"), "枪-全");
  // Every state starts after the last round: nothing is known at any buy tick.
  const late = Object.fromEntries(Object.keys(replay.playerStates).map((id) => [id, [{ tick: 999999, money: 0, equipValue: 0 }]]));
  assert.deepEqual(plain(roundEconomies({ ...replay, playerStates: late })), []);
});

test("won follows the team's side and the winner; the summary counts rounds and wins per kind", () => {
  const specs = [
    { sideA: "T", winner: "T", a: FULL_BUY, b: FULL_BUY }, // pistol: A wins
    { sideA: "T", winner: "CT", a: [3000, 0], b: FULL_BUY }, // A force, B full: B wins
    { sideA: "T", winner: "T", a: FULL_BUY, b: [2500, 2000] }, // A full, B half: A wins
    { sideA: "CT", winner: "CT", a: FULL_BUY, b: FULL_BUY }, // pistol after the switch: A wins
    { sideA: "CT", winner: "T", a: [500, 3000], b: FULL_BUY }, // A eco, B full: B wins
    { sideA: "CT", winner: "CT", a: FULL_BUY, b: [1500, 1500] } // A full, B eco: A wins
  ];
  const rounds = roundEconomies(economyReplay(specs));
  assert.equal(strip(rounds, "A"), "枪强全枪经全");
  assert.equal(strip(rounds, "B"), "枪全半枪全经");
  assert.deepEqual(plain(rounds.map((round) => [team(round, "A").won, team(round, "B").won])),
    [[true, false], [false, true], [true, false], [true, false], [false, true], [true, false]]);
  assert.deepEqual(plain(economySummary(rounds)), {
    A: { pistol: { rounds: 2, wins: 2 }, full: { rounds: 2, wins: 2 }, force: { rounds: 1, wins: 0 }, half: { rounds: 0, wins: 0 }, eco: { rounds: 1, wins: 0 } },
    B: { pistol: { rounds: 2, wins: 0 }, full: { rounds: 2, wins: 2 }, force: { rounds: 0, wins: 0 }, half: { rounds: 1, wins: 0 }, eco: { rounds: 1, wins: 0 } }
  });
});

test("an unknown winner leaves won null and counts the round without a win", () => {
  const replay = economyReplay(roundsFromSides("TT"));
  replay.rounds[1].winnerSide = "draw";
  const rounds = roundEconomies(replay);
  assert.deepEqual(plain(rounds[1].teams.map((item) => item.won)), [null, null]);
  assert.deepEqual(plain(economySummary(rounds).A.full), { rounds: 1, wins: 0 });
  assert.deepEqual(plain(economySummary([]).B.eco), { rounds: 0, wins: 0 });
  assert.deepEqual(plain(economySummary([{ roundNumber: 1, teams: [null, { teamKey: "C", kind: "full", won: true }] }]).A.full),
    { rounds: 0, wins: 0 }, "malformed entries are skipped");
});

test("a rebuilt replay wrapper over the same arrays returns the same result", () => {
  const replay = economyReplay(roundsFromSides("TTCC"));
  const first = roundEconomies(replay);
  assert.equal(roundEconomies({ ...replay }), first);
  assert.notEqual(roundEconomies({ ...replay, playerStates: { ...replay.playerStates } }), first);
});

let failed = 0;
for (const { name, run } of tests) {
  try {
    run();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}`);
    console.error(error);
  }
}
if (failed > 0) {
  process.exitCode = 1;
} else {
  console.log(`${tests.length} round-economy tests passed`);
}
