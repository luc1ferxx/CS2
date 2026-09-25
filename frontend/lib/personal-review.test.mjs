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
      if (specifier === "@/lib/coaching-review") return load("./coaching-review.ts");
      if (specifier === "@/lib/bomb-site") return load("./bomb-site.ts");
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
{
  const summary = review.personalReviewSummary(events, selectedId);
  assert.deepEqual([summary.findingCount, summary.priorityCount, summary.roundCount], [2, 1, 2]);
  assert.equal(summary.topFinding.id, "own");
  assert.equal(review.personalReviewSummary(events, null).topFinding, null);
  assert.equal(review.personalReviewSummary(events, null).priorityCount, 0);
}
{
  // Real rules emit only medium and low: medium counts as worth reviewing first,
  // and the top finding is the most severe one, not the earliest.
  const realShaped = [
    { id: "spacing-1", player_id: selectedId, severity: "low", round_number: 1, tick_start: 10 },
    { id: "spacing-2", player_id: selectedId, severity: "low", round_number: 1, tick_start: 20 },
    { id: "untraded-late", player_id: selectedId, severity: "medium", round_number: 3, tick_start: 90 },
    { id: "untraded-early", player_id: selectedId, severity: "medium", round_number: 2, tick_start: 50 },
    { id: "info", player_id: selectedId, severity: "info", round_number: 1, tick_start: 1 }
  ];
  const summary = review.personalReviewSummary(realShaped, selectedId);
  assert.equal(summary.priorityCount, 2);
  assert.equal(summary.topFinding.id, "untraded-early");
}

const parserEvents = [
  { id: "kill", type: "kill", playerId: "p2", playerIds: ["p2", selectedId] },
  { id: "own-damage", type: "damage", playerId: selectedId, playerIds: [] },
  { id: "other-smoke", type: "smoke", playerId: "p2", playerIds: ["p2"] },
  { id: "plant", type: "bomb_planted", playerId: "p2", playerIds: ["p2"] },
  { id: "round", type: "round_start", playerIds: [] }
];
assert.deepEqual(ids(review.parserEventsForPlayer(parserEvents, selectedId)), ["kill", "own-damage", "plant", "round"]);
assert.deepEqual(ids(review.parserEventsForPlayer(parserEvents, null)), ["plant", "round"]);

const KEY = review.PLAYER_PREFERENCE_KEY;
const devAccount = { displayName: "Local development", avatarUrl: null, provider: "development" };
const steamAccount = { displayName: "Tactical Reviewer", avatarUrl: null, provider: "steam", steamId: selectedId };
const otherSteamAccount = { ...steamAccount, displayName: "Someone else", steamId: "76561198000000009" };
const oidcAccount = { displayName: "other", avatarUrl: null, provider: "oidc" };

// Preferences are keyed per signed-in account; development keeps its original key.
assert.equal(review.playerPreferenceKey(devAccount), KEY);
assert.equal(review.playerPreferenceKey(steamAccount), `${KEY}:steam:${selectedId}`);
assert.equal(review.playerPreferenceKey({ ...steamAccount, steamId: "not-a-steam-id" }), `${KEY}:steam:Tactical Reviewer`);
assert.equal(review.playerPreferenceKey(oidcAccount), `${KEY}:oidc:other`);
assert.equal(review.playerPreferenceKey(undefined), null);

// Only local development defaults to the developer's player.
const candidateIds = (values) => Array.from(values, (value) => `${value.source}:${value.identity}`);
assert.deepEqual(candidateIds(review.accountIdentityCandidates(devAccount)), ["development:xelex"]);
assert.deepEqual(candidateIds(review.accountIdentityCandidates(steamAccount)), [`steamId:${selectedId}`]);
assert.deepEqual(candidateIds(review.accountIdentityCandidates({ ...steamAccount, steamId: null })), ["displayName:Tactical Reviewer"]);
assert.deepEqual(candidateIds(review.accountIdentityCandidates({ ...steamAccount, steamId: " 123 " })), ["displayName:Tactical Reviewer"]);
assert.deepEqual(candidateIds(review.accountIdentityCandidates(oidcAccount)), ["displayName:other"]);
assert.deepEqual(candidateIds(review.accountIdentityCandidates(undefined)), []);
assert.deepEqual(candidateIds(review.reviewIdentityCandidates(" p2 ", steamAccount)), ["saved:p2", `steamId:${selectedId}`]);
assert.deepEqual(candidateIds(review.reviewIdentityCandidates(selectedId, steamAccount)), [`saved:${selectedId}`]);
assert.deepEqual(candidateIds(review.reviewIdentityCandidates("", undefined)), []);

{
  const resolve = (saved, account, roster = players) =>
    review.resolveReviewIdentity(roster, review.reviewIdentityCandidates(saved, account));
  // The signed-in Steam account finds itself with nothing saved.
  const own = resolve("", steamAccount);
  assert.deepEqual([own.status, own.player.id, own.source], ["matched", selectedId, "steamId"]);
  // A saved identity wins when it is in this match...
  const saved = resolve("other", steamAccount);
  assert.deepEqual([saved.player.id, saved.source], ["p2", "saved"]);
  // ...and falls through to the account's own when it is not.
  const fallThrough = resolve("somebody-from-another-demo", steamAccount);
  assert.deepEqual([fallThrough.player.id, fallThrough.source], [selectedId, "steamId"]);
  // Another account never lands on this player, and the report names the saved identity first.
  const stranger = resolve("somebody", otherSteamAccount);
  assert.deepEqual([stranger.status, stranger.player, stranger.identity, stranger.source], ["missing", null, "somebody", "saved"]);
  const ambiguous = resolve("", { ...oidcAccount, displayName: "xelex" }, duplicateNamePlayers);
  assert.deepEqual([ambiguous.status, ambiguous.candidates.length, ambiguous.source], ["ambiguous", 2, "displayName"]);
  assert.deepEqual({ ...review.resolveReviewIdentity(players, []), candidates: [] },
    { status: "missing", player: null, candidates: [], identity: "", source: null });
}

const data = new Map();
const storage = { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
const steamKey = review.playerPreferenceKey(steamAccount);
const otherKey = review.playerPreferenceKey(otherSteamAccount);
assert.equal(review.readPreferredPlayer(() => storage, steamKey), "");
assert.equal(review.savePreferredPlayer(" other ", () => storage, steamKey), true);
assert.equal(review.readPreferredPlayer(() => storage, steamKey), "other");
// A second account on the same browser does not inherit the first one's choice.
assert.equal(review.readPreferredPlayer(() => storage, otherKey), "");
assert.equal(review.readPreferredPlayer(() => storage, KEY), "");
assert.equal(review.savePreferredPlayer(" ", () => storage, steamKey), false);
assert.equal(review.savePreferredPlayer("other", () => storage, null), false);
assert.equal(review.readPreferredPlayer(() => storage, null), "");
for (const bad of ["not json", "null", "[]", '{"version":2,"identity":"other"}', '{"version":1,"identity":22}', '{"version":1,"identity":""}']) {
  data.set(steamKey, bad);
  assert.equal(review.readPreferredPlayer(() => storage, steamKey), "");
}
const blocked = () => { throw new Error("SecurityError"); };
assert.equal(review.readPreferredPlayer(blocked, steamKey), "");
assert.equal(review.savePreferredPlayer("other", blocked, steamKey), false);
assert.equal(review.savePreferredPlayer("other", () => ({ getItem: storage.getItem, setItem: blocked }), steamKey), false);
assert.equal(review.readPreferredPlayer(() => ({ getItem: blocked, setItem: storage.setItem }), steamKey), "");
assert.equal(review.savePreferredPlayer("other", () => null, steamKey), false);

const panel = (saved, account, roster = players, player = null) => renderToStaticMarkup(React.createElement(PersonalReviewPanel, {
  savedIdentity: saved,
  match: review.resolveReviewIdentity(roster, review.reviewIdentityCandidates(saved, account)),
  players: roster, selectedPlayer: player,
  summary: review.personalReviewSummary(events, player?.id ?? null),
  preferenceSaved: true, onSaveIdentity() {}, onSelectPlayer() {}, onSeek() {}
}));
{
  // Nobody matched: ask the viewer directly and put the players right there.
  const unmatched = panel("", otherSteamAccount);
  assert.match(unmatched, /<h2>选择你在这场比赛中的玩家<\/h2>/);
  assert.match(unmatched, /id="review-player-picker"/);
  assert.match(unmatched, /<button[^>]*title="xelex · 76561198998266210"[^>]*>xelex<\/button>/);
  assert.match(unmatched, /<button[^>]*title="other · p2"[^>]*>other<\/button>/);
  assert.doesNotMatch(unmatched, /未找到|没有你保存的身份|查看最值得回看的一条/);
  assert.doesNotMatch(unmatched, /76561198000000009/, "the viewer's own id is never echoed as a stranger");
  assert.doesNotMatch(unmatched, /<details[^>]*open=/);
}
assert.match(panel("missing", otherSteamAccount), /这场比赛中没有你保存的身份 missing/);
assert.doesNotMatch(panel("", devAccount, [players[1]]), /xelex/, "the development default is never named to the viewer");
{
  const ambiguous = panel("xelex", otherSteamAccount, duplicateNamePlayers);
  assert.match(ambiguous, /有多位玩家叫 xelex/);
  assert.match(ambiguous, /personal-player-chip active[^>]*>xelex<small>…6210<\/small>/);
  assert.match(ambiguous, /personal-player-chip active[^>]*>XELEX<small>…p3<\/small>/);
}
{
  const own = panel("", steamAccount, players, players[0]);
  // A long name is cut with an ellipsis, so the full name rides along in its title.
  assert.match(own, /正在复盘 <strong class="personal-review-name" title="xelex">xelex<\/strong>/);
  assert.match(own, /已按你的 Steam 账号匹配到 xelex/);
  assert.match(own, /<strong>1<\/strong> 条值得优先回看/);
  assert.match(own, />查看最值得回看的一条<\/button>/);
  assert.doesNotMatch(own, /id="review-player-picker"|设为我的玩家/);
  assert.doesNotMatch(own, /First finding/);
}
assert.match(panel("xelex", devAccount, players, players[0]), /已按你保存的身份匹配到 xelex/);
assert.match(panel("", devAccount, players, players[0]), /已按本地开发身份匹配到 xelex/);
{
  const someoneElse = panel("", steamAccount, players, players[1]);
  assert.match(someoneElse, /正在复盘 <strong class="personal-review-name" title="other">other/);
  assert.match(someoneElse, /已按你的 Steam 账号匹配到 xelex。当前在看其他玩家。/);
  assert.match(someoneElse, />设为我的玩家<\/button>/);
  // A pick that is not the viewer's player does not re-raise the "who are you" prompt.
  const unconfirmed = panel("", otherSteamAccount, players, players[1]);
  assert.match(unconfirmed, /还没有确认你在这场比赛中的玩家/);
  assert.doesNotMatch(unconfirmed, /role="status">[^<]*选择你/);
}

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
assert.match(card, /<dt>玩家<\/dt><dd>xelex<\/dd>/);
assert.match(card, /<dt>相关玩家<\/dt><dd>xelex、other<\/dd>/);
assert.doesNotMatch(card, /选手|待复盘线索/, "one word for a person: 玩家");
assert.match(card, /<article id="coaching-event-own" tabindex="-1"/, "the card id contract the review page scrolls back to");
assert.match(card, /<details class="coaching-technical-details"><summary>技术详情<\/summary><p>No trade was recorded\.<\/p>/,
  "the English analyzer message is kept, but only inside 技术详情");
{
  const own = panel("", steamAccount, players, players[0]);
  assert.match(own, /aria-label="复盘玩家"/);
  assert.match(own, /<label for="review-player-select">当前复盘玩家<\/label>/);
  assert.match(own, /<select id="review-player-select"/);
  assert.doesNotMatch(own, /Player to review|Personal review|Selected player review summary/,
    "accessible names are the Chinese labels the player sees");
}
console.log("Personal review identity defaults, per-account preferences, priority summary, target isolation and UI states passed.");
