import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("./replay-place.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
});
const module = { exports: {} };
vm.runInNewContext(outputText, { module, exports: module.exports, URLSearchParams });
const { parseReviewPlace, reviewPlaceSearch } = module.exports;

const replay = {
  rounds: [
    { roundNumber: 1, startTick: 100, freezeEndTick: 164, endTick: 900, winnerSide: "CT" },
    { roundNumber: 2, startTick: 1000, freezeEndTick: 1064, endTick: 1800, winnerSide: "T" }
  ],
  players: [{ id: "76561198000000001", name: "T Entry", side: "T", color: "#fff" }]
};
const plain = (value) => JSON.parse(JSON.stringify(value));

// A full, valid place comes back as written.
assert.deepEqual(plain(parseReviewPlace("?r=2&t=1200&p=76561198000000001", replay)),
  { roundNumber: 2, tick: 1200, playerId: "76561198000000001" });
// A tick outside the named round keeps the round but drops the tick.
assert.deepEqual(plain(parseReviewPlace("?r=2&t=500", replay)), { roundNumber: 2, tick: null, playerId: null });
// A tick alone finds its round.
assert.deepEqual(plain(parseReviewPlace("?t=450", replay)), { roundNumber: 1, tick: 450, playerId: null });
// Unknown rounds, malformed numbers and players from another match are ignored.
for (const search of ["?r=9&t=99999", "?r=abc&t=1e9", "?r=-1&t=-5", "?t=Infinity", "?r=1.5", ""]) {
  assert.deepEqual(plain(parseReviewPlace(search, replay)), { roundNumber: null, tick: null, playerId: null }, search);
}
assert.equal(parseReviewPlace("?p=someone-else", replay).playerId, null);

assert.equal(reviewPlaceSearch("", { roundNumber: 2, tick: 1200.6, playerId: "p1" }), "?r=2&t=1201&p=p1");
assert.equal(reviewPlaceSearch("?p=old&x=1", { roundNumber: 1, tick: 164, playerId: null }), "?x=1&r=1&t=164");
console.log("Review place query checks passed.");
