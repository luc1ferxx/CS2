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
      if (specifier === "@/lib/replay-frames") return loadTypeScriptModule("./replay-frames.ts");
      throw new Error(`Unexpected import ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const { activeWeaponLabel, deathInfoAt, hasPlayerStates, killsDeathsAt, playerStateTrack, stateAt, teamEquipmentAt } =
  loadTypeScriptModule("./player-state.ts");
const { V2_ALPHA, V2_BRAVO, V2_CHARLIE, V2_DELTA, replayV1, replayV2 } = loadTypeScriptModule("./test-fixtures/replay-v2.ts");

// Values built inside the vm context have that realm's prototypes.
const plain = (value) => JSON.parse(JSON.stringify(value));
const tests = [];
const test = (name, run) => tests.push({ name, run });

test("stateAt returns the last change point at or before the tick", () => {
  const replay = replayV2();
  assert.equal(stateAt(replay, V2_ALPHA, 99), null, "before the first entry there is no state");
  assert.equal(stateAt(replay, V2_ALPHA, 100).money, 800);
  assert.equal(stateAt(replay, V2_ALPHA, 163).money, 800);
  assert.deepEqual(plain(stateAt(replay, V2_ALPHA, 164).grenades), ["flash", "flash", "smoke"]);
  assert.equal(stateAt(replay, V2_ALPHA, 310).weapon, "Smoke Grenade");
  assert.deepEqual(plain(stateAt(replay, V2_ALPHA, 999).grenades), ["flash", "flash"]);
  assert.equal(stateAt(replay, V2_ALPHA, 1300).weapon, null);
  assert.equal(stateAt(replay, V2_ALPHA, 99999).tick, 1300);
  assert.equal(stateAt(replay, "nobody", 500), null);
  assert.equal(stateAt(replay, V2_ALPHA, Number.NaN), null);
});

test("v1 replays and missing tracks have no state", () => {
  const replay = replayV1();
  assert.equal(hasPlayerStates(replay), false);
  assert.equal(hasPlayerStates({ ...replay, playerStates: undefined }), false);
  assert.equal(stateAt(replay, V2_ALPHA, 500), null);
  assert.equal(teamEquipmentAt(replay, "T", 500), null);
  assert.equal(hasPlayerStates(replayV2()), true);
});

test("malformed and unsorted tracks are cleaned once", () => {
  const raw = [{ tick: 300, money: 3 }, null, { tick: "x", money: 9 }, { tick: 100, money: 1 }, { tick: 200, money: 2 }];
  const replay = { ...replayV2(), playerStates: { p: raw } };
  assert.deepEqual(plain(playerStateTrack(replay, "p").map((entry) => entry.money)), [1, 2, 3]);
  assert.equal(playerStateTrack(replay, "p"), playerStateTrack(replay, "p"), "cached per track array");
  assert.equal(stateAt(replay, "p", 250).money, 2);
  assert.equal(stateAt({ ...replay, playerStates: { p: "bad" } }, "p", 250), null);
});

test("killsDeathsAt counts match-cumulative K/D with the scoreboard's rules", () => {
  const replay = replayV2();
  const before = killsDeathsAt(replay, 499);
  assert.equal(before.get(V2_ALPHA), undefined);
  const afterFirst = killsDeathsAt(replay, 500);
  assert.deepEqual(plain(afterFirst.get(V2_ALPHA)), { kills: 1, deaths: 0 });
  assert.deepEqual(plain(afterFirst.get(V2_DELTA)), { kills: 0, deaths: 1 });
  const end = killsDeathsAt(replay, 1800);
  assert.deepEqual(plain(end.get(V2_ALPHA)), { kills: 1, deaths: 1 });
  assert.deepEqual(plain(end.get(V2_DELTA)), { kills: 1, deaths: 1 });
  assert.deepEqual(plain(end.get(V2_CHARLIE)), { kills: 1, deaths: 0 });
  assert.deepEqual(plain(end.get(V2_BRAVO)), { kills: 0, deaths: 1 });
});

test("a team kill is a death for the victim and no kill for anyone", () => {
  const base = replayV2();
  const teamKill = {
    ...base.events[0], id: "tk", tick: 800,
    metadata: { attackerId: V2_ALPHA, attackerSide: "T", victimId: V2_BRAVO, victimSide: "T", weapon: "ak47" }
  };
  const replay = { ...base, events: [base.events[0], teamKill] };
  const counts = killsDeathsAt(replay, 900);
  assert.deepEqual(plain(counts.get(V2_ALPHA)), { kills: 1, deaths: 0 });
  assert.deepEqual(plain(counts.get(V2_BRAVO)), { kills: 0, deaths: 1 });
});

test("deathInfoAt names the killer and weapon of this round's death only", () => {
  const replay = replayV2();
  assert.equal(deathInfoAt(replay, V2_DELTA, 499), null);
  assert.deepEqual(plain(deathInfoAt(replay, V2_DELTA, 600)),
    { tick: 500, killerId: V2_ALPHA, killerName: "Alpha", weapon: "ak47", headshot: true });
  // Still shown after the round's end until the next round starts.
  assert.equal(deathInfoAt(replay, V2_DELTA, 950)?.killerName, "Alpha");
  assert.equal(deathInfoAt(replay, V2_DELTA, 1000), null, "a new round starts with nobody dead");
  assert.equal(deathInfoAt(replay, V2_ALPHA, 1400)?.weapon, "awp");
  assert.equal(deathInfoAt(replay, V2_CHARLIE, 1800), null);
});

test("deathInfoAt falls back to the player list for a killer without a name", () => {
  const base = replayV2();
  const nameless = { ...base.events[0], metadata: { ...base.events[0].metadata, attackerName: undefined } };
  const replay = { ...base, events: [nameless] };
  assert.equal(deathInfoAt(replay, V2_DELTA, 600).killerName, "Alpha");
});

test("teamEquipmentAt sums equipValue by side or by team", () => {
  const replay = replayV2();
  assert.equal(teamEquipmentAt(replay, "T", 200), 950 + 1150);
  assert.equal(teamEquipmentAt(replay, "CT", 200), 800 + 850);
  assert.equal(teamEquipmentAt(replay, "A", 200), 950 + 1150, "team A started T");
  assert.equal(teamEquipmentAt(replay, "B", 1100), 4600 + 5950);
  assert.equal(teamEquipmentAt(replay, "T", 600), 650 + 1150);
  // A player without an equipment value is skipped, not counted as zero.
  const partial = { ...replay, playerStates: { [V2_ALPHA]: [{ tick: 100, money: 1 }] } };
  assert.equal(teamEquipmentAt(partial, "T", 200), null);
});

test("teamEquipmentAt leaves out dead players, whose state keeps the value they died with", () => {
  // Real demos: current_equip_value is not reset on death (Delta dies at 500 carrying $850).
  const base = replayV2();
  const states = { ...base.playerStates, [V2_DELTA]: base.playerStates[V2_DELTA].map((entry) =>
    entry.tick === 500 ? { ...entry, equipValue: 850 } : entry) };
  const replay = { ...base, playerStates: states };
  assert.equal(teamEquipmentAt(replay, "CT", 400), 800 + 850, "both alive");
  assert.equal(teamEquipmentAt(replay, "CT", 600), 800, "only the living Charlie still carries his kit");
  // A side with everyone dead carries $0, not its last values (and not "unknown").
  const allDead = { ...replay, frames: replay.frames.map((frame) => ({
    ...frame, players: frame.players.map((player) => player.side === "CT" ? { ...player, alive: false, hp: 0 } : player)
  })) };
  assert.equal(teamEquipmentAt(allDead, "CT", 600), 0);
});

test("killsDeathsAt hands back the same table until the next kill", () => {
  const replay = replayV2();
  const first = killsDeathsAt(replay, 600);
  assert.equal(killsDeathsAt(replay, 650), first, "no kill between 600 and 650");
  assert.equal(killsDeathsAt(replay, 600).get(V2_ALPHA), first.get(V2_ALPHA));
  const later = killsDeathsAt(replay, 1800);
  assert.notEqual(later, first);
  assert.deepEqual(plain(killsDeathsAt(replay, 600).get(V2_ALPHA)), { kills: 1, deaths: 0 });
  assert.equal(killsDeathsAt(replay, Number.NaN).size, 0);
});

test("activeWeaponLabel shortens grenades, knives and the bomb", () => {
  assert.equal(activeWeaponLabel("High Explosive Grenade"), "手雷");
  assert.equal(activeWeaponLabel("Smoke Grenade"), "烟雾弹");
  assert.equal(activeWeaponLabel("Flashbang"), "闪光弹");
  assert.equal(activeWeaponLabel("Incendiary Grenade"), "燃烧弹");
  assert.equal(activeWeaponLabel("Molotov"), "燃烧弹");
  assert.equal(activeWeaponLabel("Decoy Grenade"), "诱饵弹");
  assert.equal(activeWeaponLabel("Butterfly Knife"), "刀");
  assert.equal(activeWeaponLabel("Karambit"), "刀");
  assert.equal(activeWeaponLabel("C4 Explosive"), "C4");
  assert.equal(activeWeaponLabel(" AK-47 "), "AK-47");
  assert.equal(activeWeaponLabel(null), null);
  assert.equal(activeWeaponLabel("  "), null);
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
  console.log(`${tests.length} player-state tests passed`);
}
