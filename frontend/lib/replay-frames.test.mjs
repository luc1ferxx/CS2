import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./replay-frames.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText, { module, exports: module.exports });
const { getFrameForTick } = module.exports;
const player = { id: "xelex", name: "xelex", side: "T", x: 20, y: 30, z: -400, hp: 100, alive: true, hasBomb: true };
const frame = (tick, overrides = {}) => ({ tick, timeSeconds: tick / 64, roundNumber: 1, players: [player], bombState: { status: "carried" }, ...overrides });
const a = frame(100);
const b = frame(116, { players: [{ ...player, x: 40, y: 50, z: -600, hp: 40, hasBomb: false }], bombState: { status: "planted" } });
const halfway = getFrameForTick([a, b], 108);
assert.equal(halfway.players[0].x, 30);
assert.equal(halfway.players[0].z, -500);
assert.equal(halfway.players[0].hp, 100, "damage cannot appear before the event tick");
assert.equal(halfway.players[0].hasBomb, true);
assert.equal(halfway.bombState.status, "carried");
assert.equal(getFrameForTick([a, b], 116), b, "exact tick must use new state");
const dead = frame(116, { players: [{ ...player, alive: false, hp: 0 }] });
assert.equal(getFrameForTick([a, dead], 115).players[0].alive, true);
assert.equal(getFrameForTick([a, dead], 116).players[0].alive, false);
assert.equal(getFrameForTick([a, frame(120, { roundNumber: 2 })], 119), a, "do not interpolate across rounds");
assert.equal(getFrameForTick([a, frame(500)], 250), a, "old sparse samples must not imply detailed motion");
assert.equal(getFrameForTick([], 0), null);
assert.equal(getFrameForTick([a], 10000), a);
const dense = Array.from({ length: 30000 }, (_, i) => frame(i * 16));
assert.equal(getFrameForTick(dense, 479984).tick, 479984);
assert.equal(getFrameForTick(dense, 399999).tick, 399999);
console.log("Replay frame interpolation checks passed");
