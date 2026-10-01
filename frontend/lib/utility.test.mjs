import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const directory = dirname(fileURLToPath(import.meta.url));
const loaded = new Map();

function load(relativePath) {
  const filename = resolve(directory, relativePath);
  if (loaded.has(filename)) return loaded.get(filename);
  const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename
  });
  const module = { exports: {} };
  loaded.set(filename, module.exports);
  vm.runInNewContext(outputText, {
    exports: module.exports, module,
    require(specifier) {
      if (specifier.startsWith("@/types/")) return {};
      if (specifier === "@/lib/map-config") return load("./map-config.ts");
      if (specifier === "@/lib/match-stats") return load("./match-stats.ts");
      if (specifier === "@/lib/replay-time") return load("./replay-time.ts");
      throw new Error(`Unexpected import ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const utility = load("./utility.ts");
const mapConfig = load("./map-config.ts");
const plain = (value) => JSON.parse(JSON.stringify(value));
// Results come from another realm (vm), so compare their JSON shape.
const same = (actual, expected) => assert.deepEqual(plain(actual), plain(expected));

const rounds = [
  { roundNumber: 1, startTick: 0, freezeEndTick: 1280, endTick: 6400, winnerSide: "T" },
  { roundNumber: 2, startTick: 7000, freezeEndTick: 8280, endTick: 14000, winnerSide: "CT" },
  { roundNumber: 13, startTick: 90000, freezeEndTick: 91280, endTick: 96000, winnerSide: "CT" }
];

function path(fromTick, toTick, from, to, step = 16) {
  const points = [];
  for (let tick = fromTick; tick <= toTick; tick += step) {
    const share = (tick - fromTick) / (toTick - fromTick);
    points.push({ tick, x: from[0] + (to[0] - from[0]) * share, y: from[1] + (to[1] - from[1]) * share });
  }
  return points;
}

const smoke = {
  id: "utility-smoke-11-1600", type: "smoke", throwerId: "p1", throwerName: "xelex", throwerSide: "T",
  roundNumber: 1, throwTick: 1600, detonateTick: 1728, endTick: 2880,
  points: path(1600, 1728, [20, 20], [40, 60])
};
const flash = {
  id: "utility-flash-12-2000", type: "flash", throwerId: "p2", throwerName: "other", throwerSide: "CT",
  roundNumber: 1, throwTick: 2000, detonateTick: 2064, endTick: 2064,
  points: path(2000, 2064, [80, 80], [70, 70])
};
const molotovNoExpire = {
  id: "utility-molotov-13-3000", type: "molotov", throwerId: "p1", throwerName: "xelex", throwerSide: "T",
  roundNumber: 1, throwTick: 3000, detonateTick: 3100, endTick: 3100,
  points: path(3000, 3096, [10, 10], [12, 14])
};
// Thrown after round 1 ended; the parser filed it under round 2.
const lateSmoke = {
  id: "utility-smoke-14-6600", type: "smoke", throwerId: "p2", throwerName: "other", throwerSide: "CT",
  roundNumber: 2, throwTick: 6600, detonateTick: 6700, endTick: 7900,
  points: path(6600, 6696, [50, 50], [55, 50])
};
const secondHalfHe = {
  id: "utility-he-15-92000", type: "he", throwerId: "p3", throwerName: "third", throwerSide: "CT",
  roundNumber: 13, throwTick: 92000, detonateTick: 92100, endTick: 92100,
  points: path(92000, 92096, [30, 30], [31, 31])
};
const replay = {
  contractVersion: "replay_contract_v2", tickRate: 64, rounds,
  utility: [secondHalfHe, lateSmoke, molotovNoExpire, flash, smoke]
};

const tests = [];
const test = (name, run) => tests.push([name, run]);

test("sanitizes throws, sorts them by throw tick and buckets them per round", () => {
  const dirty = {
    ...replay,
    utility: [
      ...replay.utility,
      { ...smoke, id: "bad-type", type: "grenade" },
      { ...smoke, id: "no-points", points: [{ tick: 1, x: Number.NaN, y: 3 }] },
      smoke, // duplicate id
      { ...flash, id: "clamped", points: [{ tick: 2000, x: 140, y: -3, z: 12 }], detonateTick: 1, endTick: -5 },
      null,
      "junk"
    ]
  };
  const throws = utility.replayUtility(dirty);
  same(throws.map((item) => item.id), [
    "utility-smoke-11-1600", "clamped", "utility-flash-12-2000", "utility-molotov-13-3000",
    "utility-smoke-14-6600", "utility-he-15-92000"
  ]);
  const clamped = throws.find((item) => item.id === "clamped");
  same(clamped.points, [{ tick: 2000, x: 100, y: 0, z: 12 }]);
  // A detonation before the throw falls back to the last point; the end is never before it.
  assert.equal(clamped.detonateTick, 2000);
  assert.equal(clamped.endTick, 2000);
  const byRound = utility.utilityByRound(replay);
  same([...byRound.keys()].sort((a, b) => a - b), [1, 2, 13]);
  same(byRound.get(1).map((item) => item.type), ["smoke", "flash", "molotov"]);
});

test("a v1 replay has no throws; the tab waits for a pending upgrade and hides otherwise", () => {
  const v1 = { contractVersion: "replay_contract_v1", tickRate: 64, rounds, utility: [] };
  same(utility.replayUtility({ rounds }), []);
  assert.equal(utility.utilityAvailability(v1, true), "pending");
  assert.equal(utility.utilityAvailability(v1, false), "hidden");
  assert.equal(utility.utilityAvailability({ ...v1, contractVersion: undefined }, true), "pending");
  assert.equal(utility.utilityAvailability(replay, false), "available");
  // A v2 replay without throws (a mock demo) has nothing to wait for.
  assert.equal(utility.utilityAvailability({ ...replay, utility: [] }, true), "hidden");
  assert.equal(utility.replayContractVersion({ contractVersion: "replay_contract_v2" }), 2);
  assert.equal(utility.replayContractVersion({ contractVersion: "something" }), 1);
});

test("radius constants convert through the map's world units per percent", () => {
  const mirage = mapConfig.getTacticalMapPresentation({ mapName: "de_mirage" });
  const dust2 = mapConfig.getTacticalMapPresentation({ mapName: "de_dust2" });
  assert.equal(utility.worldUnitsPerPercent(dust2), 4.4 * 1024 / 100);
  assert.equal(utility.worldUnitsPerPercent(mirage), (5120 + 5100) / 200);
  assert.equal(utility.utilityRadiusPercent("smoke", dust2), Math.round(144 / 45.056 * 100) / 100);
  assert.equal(utility.utilityRadiusPercent("molotov", mirage), Math.round(120 / 51.1 * 100) / 100);
  const fallback = mapConfig.getTacticalMapPresentation({ mapName: "de_unknown" });
  assert.equal(utility.worldUnitsPerPercent(fallback), null);
  assert.equal(utility.utilityRadiusPercent("smoke", fallback), 3);
  // The replay's stored scale wins over the map transform.
  assert.equal(utility.worldUnitsPerPercent({ transform: dust2.transform, worldUnitsPerPercent: { x: 50, y: 52 } }), 51);
  assert.equal(utility.worldUnitsPerPercent({ transform: dust2.transform, worldUnitsPerPercent: { x: 0, y: 52 } }), 4.4 * 1024 / 100);
  const stored = utility.utilityActiveAt({ ...replay, mapMetadata: { worldUnitsPerPercent: { x: 48, y: 48 } } }, 2000)[0];
  assert.equal(stored.radius, 3);
  assert.equal(utility.SMOKE_RADIUS_WORLD_UNITS, 144);
  assert.equal(utility.FIRE_RADIUS_WORLD_UNITS, 120);
});

test("in flight: interpolated position and a short trail ending at it", () => {
  const [active] = utility.utilityActiveAt(replay, 1608);
  assert.equal(active.phase, "flight");
  assert.equal(active.utility.id, smoke.id);
  assert.equal(active.x, 21.25);
  assert.equal(active.y, 22.5);
  same(active.trail, [{ x: 20, y: 20 }, { x: 21.25, y: 22.5 }]);
  assert.equal(active.radius, null);
  const later = utility.utilityActiveAt(replay, 1700)[0];
  assert.ok(later.trail.length > 2 && later.trail.length <= 12);
  same(later.trail.at(-1), { x: later.x, y: later.y });
});

test("effects: smoke until it expires, flash as a brief burst, fire falls back to 7 s", () => {
  const dust2 = mapConfig.getTacticalMapPresentation({ mapName: "de_dust2" });
  const atSmoke = utility.utilityActiveAt(replay, 2000, { map: dust2 });
  same(atSmoke.map((item) => [item.utility.type, item.phase]), [["smoke", "effect"], ["flash", "flight"]]);
  assert.equal(atSmoke[0].x, 40);
  assert.equal(atSmoke[0].y, 60);
  assert.equal(atSmoke[0].radius, utility.utilityRadiusPercent("smoke", dust2));
  assert.equal(utility.utilityActiveAt(replay, 2879).some((item) => item.utility.type === "smoke"), true);
  assert.equal(utility.utilityActiveAt(replay, 2880).some((item) => item.utility.type === "smoke"), false);
  // Flash: 0.3 s at 64 ticks = 19.2 ticks after detonation.
  assert.equal(utility.utilityActiveAt(replay, 2083).find((item) => item.utility.type === "flash")?.phase, "effect");
  assert.equal(utility.utilityActiveAt(replay, 2084).some((item) => item.utility.type === "flash"), false);
  const fire = (tick) => utility.utilityActiveAt(replay, tick).find((item) => item.utility.type === "molotov");
  assert.equal(fire(3100).phase, "effect");
  assert.equal(fire(3100 + 7 * 64 - 1).phase, "effect");
  assert.equal(fire(3100 + 7 * 64), undefined);
  assert.ok(fire(3100 + 3.5 * 64).progress > 0.49 && fire(3100 + 3.5 * 64).progress < 0.51);
});

test("a throw filed under the next round still shows after the round, and is gone once that round starts", () => {
  same(utility.utilityActiveAt(replay, 6800).map((item) => item.utility.id), [lateSmoke.id]);
  same(utility.utilityActiveAt(replay, 6999).map((item) => item.utility.id), [lateSmoke.id]);
  same(utility.utilityActiveAt(replay, 7000), []);
  same(utility.utilityActiveAt(replay, Number.NaN), []);
  same(utility.utilityActiveAt({ ...replay, utility: undefined }, 2000), []);
});

test("filters by type, team, player and rounds; scopes resolve to round numbers", () => {
  const throws = utility.replayUtility(replay);
  const ids = (list) => list.map((item) => item.id);
  same(ids(utility.filterThrows(throws, { types: ["smoke"] })), [smoke.id, lateSmoke.id]);
  same(ids(utility.filterThrows(throws, { team: { playerIds: ["p2", "p3"] } })), [flash.id, lateSmoke.id, secondHalfHe.id]);
  same(ids(utility.filterThrows(throws, { playerId: "p1" })), [smoke.id, molotovNoExpire.id]);
  same(ids(utility.filterThrows(throws, { types: ["smoke", "he"], rounds: [2, 13] })), [lateSmoke.id, secondHalfHe.id]);
  same(ids(utility.filterThrows(throws, {})), ids(throws));
  assert.equal(utility.roundsForScope(rounds, "all", 1), null);
  same(utility.roundsForScope(rounds, "current", 2), [2]);
  same(utility.roundsForScope(rounds, "first", 2), [1, 2]);
  same(utility.roundsForScope(rounds, "second", 2), [13]);
  same(utility.roundsForScope(rounds, "current", null), []);
});

test("rectangle selection keeps throws that landed inside it, edges included", () => {
  const throws = utility.replayUtility(replay);
  const rect = utility.normalizeRect({ x: 45, y: 65 }, { x: 35, y: 55 });
  same(rect, { x0: 35, y0: 55, x1: 45, y1: 65 });
  same(utility.throwsInRect(throws, rect).map((item) => item.id), [smoke.id]);
  same(utility.throwsInRect(throws, utility.normalizeRect({ x: 40, y: 60 }, { x: 55, y: 50 })).map((item) => item.id),
    [smoke.id, lateSmoke.id]);
  assert.equal(utility.throwsInRect(throws, null).length, throws.length);
  same((utility.normalizeRect({ x: -5, y: 120 }, { x: Number.NaN, y: 3 })), { x0: 0, y0: 3, x1: 0, y1: 100 });
  same(utility.landingPoint(utility.replayUtility(replay)[0]), { tick: 1728, x: 40, y: 60 });
});

test("看这颗 lands two seconds before the throw, never before the round's playable start", () => {
  const throws = utility.replayUtility(replay);
  assert.equal(utility.utilityJumpTick(throws.find((item) => item.id === smoke.id), rounds, 64), 1472);
  const early = { ...smoke, throwTick: 1300 };
  assert.equal(utility.utilityJumpTick(early, rounds, 64), 1280);
  assert.equal(utility.utilityJumpTick({ ...smoke, roundNumber: 99, throwTick: 5000 }, rounds, 64), 5000 - 128);
});

test("two-floor maps put a throw on the floor it landed on", () => {
  const nuke = mapConfig.getTacticalMapPresentation({ mapName: "de_nuke" });
  const lower = { ...smoke, points: [{ tick: 1600, x: 1, y: 1, z: -400 }, { tick: 1700, x: 2, y: 2, z: -600 }] };
  assert.equal(utility.utilityLevel(nuke, utility.replayUtility({ rounds, utility: [lower] })[0]), "lower");
  assert.equal(utility.utilityLevel(nuke, utility.replayUtility(replay)[0]), null);
  const halfway = utility.utilityPositionAt(lower, 1650);
  same(halfway, { x: 1.5, y: 1.5, z: -500 });
});

let failures = 0;
for (const [name, run] of tests) {
  try {
    run();
  } catch (error) {
    failures += 1;
    console.error(`not ok - ${name}`);
    console.error(error);
  }
}
if (failures > 0) {
  process.exitCode = 1;
} else {
  console.log(`utility tests passed (${tests.length})`);
}
