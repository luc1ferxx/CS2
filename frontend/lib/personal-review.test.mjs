import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import vm from "node:vm";
import ts from "typescript";

const directory = dirname(fileURLToPath(import.meta.url));
const nodeRequire = createRequire(import.meta.url);

function load(relativePath) {
  const filename = resolve(directory, relativePath);
  const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
    fileName: filename
  });
  const module = { exports: {} };
  vm.runInNewContext(outputText, {
    exports: module.exports, module,
    require(specifier) {
      if (specifier === "react" || specifier === "react/jsx-runtime") return nodeRequire(specifier);
      if (specifier === "lucide-react") return new Proxy({}, { get() { return () => null; } });
      if (specifier === "@/lib/demo-library") return { isRenderActiveStatus: () => false };
      if (specifier === "@/lib/render-clips") return load("./render-clips.ts");
      if (specifier === "@/lib/coaching-copy") return load("./coaching-copy.ts");
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const review = load("./personal-review.ts");
const { PersonalReviewPanel } = load("../components/replay/PersonalReviewPanel.tsx");
const players = [
  { id: "76561198998266210", name: "xelex", side: "T", color: "#fff" },
  { id: "p2", name: "other", side: "CT", color: "#fff" }
];
const selectedId = players[0].id;
const ids = (values) => Array.from(values, (value) => value.id);

assert.equal(review.matchPreferredPlayer(players, "  XeLeX ").player.id, selectedId);
assert.equal(review.matchPreferredPlayer(players, selectedId).player.id, selectedId);
assert.equal(review.matchPreferredPlayer(players, "xele").status, "missing");
assert.equal(review.matchPreferredPlayer(players, "").player, null);
assert.equal(review.matchPreferredPlayer([], "xelex").player, null);
const duplicateNamePlayers = [...players, { ...players[1], id: "p3", name: "XELEX" }];
assert.equal(review.matchPreferredPlayer(duplicateNamePlayers, "xelex").status, "ambiguous");
assert.equal(review.matchPreferredPlayer(duplicateNamePlayers, "xelex").player, null);
assert.equal(review.matchPreferredPlayer(duplicateNamePlayers, selectedId).player.id, selectedId);

const events = [
  { id: "own", player_id: selectedId, severity: "high", round_number: 1, tick_start: 40, structured_context_json: { involvedPlayerIds: ["p2", selectedId] } },
  { id: "other-target", player_id: "p2", severity: "high", round_number: 2, tick_start: 20, structured_context_json: { involvedPlayerIds: [selectedId, "p2"], targetPlayerId: selectedId } },
  { id: "own-later", player_id: selectedId, severity: "low", round_number: 3, tick_start: 90, structured_context_json: {} },
  { id: "unknown", player_id: "", severity: "medium", round_number: 4, tick_start: 1, structured_context_json: { involvedPlayerIds: [selectedId] } }
];
assert.deepEqual(ids(review.coachingForPlayer(events, selectedId)), ["own", "own-later"]);
assert.deepEqual(ids(review.coachingForPlayer(events, null)), []);
assert.deepEqual({ ...review.personalReviewSummary(events, selectedId) }, {
  findingCount: 2, highPriorityCount: 1, roundCount: 2, firstFindingTick: 40
});
assert.equal(review.personalReviewSummary(events, null).firstFindingTick, null);

const parserEvents = [
  { id: "kill", type: "kill", playerId: "p2", playerIds: ["p2", selectedId] },
  { id: "own-damage", type: "damage", playerId: selectedId, playerIds: [] },
  { id: "other-smoke", type: "smoke", playerId: "p2", playerIds: ["p2"] },
  { id: "plant", type: "bomb_planted", playerId: "p2", playerIds: ["p2"] },
  { id: "round", type: "round_start", playerIds: [] }
];
assert.deepEqual(ids(review.parserEventsForPlayer(parserEvents, selectedId)), ["kill", "own-damage", "plant", "round"]);
assert.deepEqual(ids(review.parserEventsForPlayer(parserEvents, null)), ["plant", "round"]);

const data = new Map();
const storage = { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
assert.equal(review.readPreferredPlayer(() => storage), "xelex");
assert.equal(review.savePreferredPlayer(" other ", () => storage), true);
assert.equal(review.readPreferredPlayer(() => storage), "other");
assert.equal(review.savePreferredPlayer(" ", () => storage), false);
for (const bad of ["not json", "null", "[]", '{"version":2,"identity":"other"}', '{"version":1,"identity":22}', '{"version":1,"identity":""}']) {
  data.set(review.PLAYER_PREFERENCE_KEY, bad);
  assert.equal(review.readPreferredPlayer(() => storage), "xelex");
}
const blocked = () => { throw new Error("SecurityError"); };
assert.equal(review.readPreferredPlayer(blocked), "xelex");
assert.equal(review.savePreferredPlayer("other", blocked), false);
assert.equal(review.savePreferredPlayer("other", () => ({ getItem: storage.getItem, setItem: blocked })), false);
assert.equal(review.readPreferredPlayer(() => ({ getItem: blocked, setItem: storage.setItem })), "xelex");
assert.equal(review.savePreferredPlayer("other", () => null), false);

const panel = (identity, roster = players, player = null) => renderToStaticMarkup(React.createElement(PersonalReviewPanel, {
  preferredIdentity: identity,
  match: review.matchPreferredPlayer(roster, identity),
  players: roster, selectedPlayer: player,
  summary: review.personalReviewSummary(events, player?.id ?? null),
  preferenceSaved: true, onSaveIdentity() {}, onSelectPlayer() {}, onSeek() {}
}));
assert.match(panel("missing"), /这场比赛中未找到 missing/);
assert.match(panel("missing"), /选择复盘玩家/);
assert.doesNotMatch(panel("missing"), /正在复盘 <strong>xelex/);
assert.match(panel("xelex", duplicateNamePlayers), /有多位玩家使用 xelex/);
assert.match(panel("xelex", players, players[0]), /已匹配保存的身份 xelex/);
assert.match(panel("xelex", players, players[1]), /正在复盘 <strong>other/);
assert.match(panel("missing"), /<details[^>]*open=""/);
assert.doesNotMatch(panel("xelex", players, players[0]), /<details[^>]*open=/);
assert.match(panel("xelex", players, players[0]), /aria-label="First finding"/);

const { CoachingEventCard } = load("../components/coaching/CoachingEventCard.tsx");
const card = renderToStaticMarkup(React.createElement(CoachingEventCard, {
  reviewEvent: {
    event: { ...events[0], title: "Untraded death", message: "No trade was recorded.", player_name: "xelex", tick_end: 60,
      structured_context_json: { assessment: "review_candidate", action: "Review the teammate's angle before taking the duel.", limitation: "Positions cannot establish line of sight." } },
    ruleId: "untraded_death", ruleLabel: "Untraded death", involvedPlayers: ["xelex", "other"], evidence: []
  },
  active: false, inspected: true, clipRequesting: false, onToggleInspect() {}, onSeek() {}, onGenerateClip() {}
}));
assert.match(card, /查看这一刻/);
assert.match(card, /angle before taking the duel/);
assert.match(card, /Positions cannot establish line of sight/);
assert.match(card, /待复盘线索/);
assert.match(card, /选手 xelex/);
console.log("Personal review identity, target isolation, evidence scope, preference storage and UI states passed.");
