import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const nodeRequire = createRequire(import.meta.url);
function load(path) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
    fileName: path
  });
  const module = { exports: {} };
  vm.runInNewContext(outputText, {
    module, exports: module.exports,
    require(name) {
      if (["react/jsx-runtime", "react", "lucide-react"].includes(name)) return nodeRequire(name);
      if (name.startsWith("@/lib/")) return load(`./${name.slice("@/lib/".length)}.ts`);
      if (name === "./CoachingEventCard") return load("../components/coaching/CoachingEventCard.tsx");
      throw new Error(`Unexpected import ${name}`);
    }
  });
  return module.exports;
}

const copy = load("./coaching-copy.ts");
const { coachingMomentLabel, reviewEventForEvent } = load("./coaching-review.ts");
const { CoachingEventCard } = load("../components/coaching/CoachingEventCard.tsx");
const { CoachingPanel } = load("../components/coaching/CoachingPanel.tsx");
const event = {
  id: "spacing-1", demo_id: "demo-1", round_number: 2, tick_start: 5504, tick_end: 5696,
  player_id: "xelex-id", player_name: "xelex", category: "positioning", severity: "low",
  title: "Review close teammate spacing", message: "In this sample, the closest teammates are 2.1 radar percentage points apart.",
  structured_context_json: {
    ruleId: "poor_spacing", spacingType: "stacked", distance: 2.1, assessment: "review_candidate",
    action: "Review the next contact.", limitation: "A sample does not establish visibility. Map calibration is approximate."
  }
};
const rounds = [{ roundNumber: 2, startTick: 4800, freezeEndTick: 5000, endTick: 9000 }];
const reviewEvent = reviewEventForEvent(event, new Map());
assert.match(copy.coachingCopy(event).title, /过近/);
assert.match(copy.coachingCopy(event).guidance, /留出避免被一起扫射的空间/);
const tooFarCopy = copy.coachingCopy({ ...event, tick_start: 12675,
  structured_context_json: { ...event.structured_context_json, spacingType: "too_far", distance: 28.48 } });
assert.match(tooFarCopy.guidance, /先确认队友能否及时支援/);
assert.match(tooFarCopy.guidance, /等队友靠近/);
assert.doesNotMatch(tooFarCopy.guidance, /留出|扫射/);
assert.match(tooFarCopy.limitation, /分散控图都可能合理/);
assert.match(tooFarCopy.limitation, /不等于实际移动距离/);
assert.match(tooFarCopy.limitation, /近似值/);
assert.match(copy.coachingCopy({ ...event, structured_context_json: { ...event.structured_context_json, spacingType: "too_far" } }).title, /支援距离/);
assert.match(copy.coachingCopy(event).limitation, /不等于实际移动距离/);
assert.match(copy.coachingCopy(event).limitation, /近似值/);
const unknown = { ...event, title: "Experimental finding", structured_context_json: { ruleId: "future_rule", action: "Keep original guidance.", limitation: "Unknown sensor." } };
assert.equal(copy.coachingCopy(unknown).title, unknown.title);
assert.equal(copy.coachingCopy(unknown).guidance, "Keep original guidance.");
assert.equal(copy.coachingCopy(unknown).limitation, "Unknown sensor.");
for (const id of ["__proto__", "toString", "constructor"]) {
  assert.equal(copy.coachingCopy({ ...unknown, structured_context_json: { ...unknown.structured_context_json, ruleId: id } }).title, unknown.title);
  assert.equal(copy.coachingEvidenceLabel(id), id);
}
// The shooting rules have their own Chinese copy (the analyzer adds no distance or calibration note to them).
for (const [ruleId, title, guidance] of [
  ["moving_shots", "边移动边开枪", /先停下再开枪：反向点一下移动键/],
  ["no_counter_strafe", "第一枪没有急停", /练习急停：松开移动键并反向点一下/]
]) {
  const shot = copy.coachingCopy({ ...event, category: "mechanics", structured_context_json: {
    ruleId, limitation: "Speed comes from the velocity recorded with each shot; a miss can have other causes." } });
  assert.equal(shot.title, title);
  assert.match(shot.guidance, guidance);
  assert.equal(shot.limitation, "速度取自每一枪记录的移动速度；没有计算弹道恢复、蹲下、开镜和对手的移动，没打中也可能有别的原因。");
}
for (const [key, label] of [["weaponLabel", "武器"], ["accurateSpeed", "稳定线（单位/秒）"], ["keysAtShot", "开枪时按着的移动键"],
  ["counterStrafe", "有反向急停"], ["occurrencesInRound", "本回合出现次数"]]) assert.equal(copy.coachingEvidenceLabel(key), label);
for (const search of ["过近", "补枪", "xelex", "poor_spacing", "2.1", "直线距离"]) assert.equal(copy.coachingMatchesSearch(reviewEvent, search), true, search);
assert.equal(copy.coachingMatchesSearch(reviewEvent, "不存在的内容"), false);

const props = {
  reviewEvent, active: false, inspected: false, locationLabel: coachingMomentLabel(event, rounds, 64),
  clipRequesting: false, onToggleInspect() {}, onSeek() {}, onGenerateClip() {}
};
const closedCard = renderToStaticMarkup(React.createElement(CoachingEventCard, props));
assert.match(closedCard, /查看这一刻/);
assert.match(closedCard, /生成视频/);
assert.match(closedCard, /第 2 回合 0:11/);
assert.match(closedCard, /补枪/);
assert.doesNotMatch(closedCard, /poor_spacing|证据 tick|2\.1 radar/,
  "Primary actions and guidance must be visible without opening raw evidence");
assert.match(closedCard, /aria-expanded="false"/);
const openCard = renderToStaticMarkup(React.createElement(CoachingEventCard, { ...props, inspected: true }));
assert.match(openCard, /判断边界/);
assert.match(openCard, /直线距离（世界坐标单位）/);
assert.match(openCard, /A sample does not establish visibility/,
  "Original limitations remain available alongside the translated rule guidance");
const oldPlantEvent = { ...event, id: "legacy-nuke-plant",
  structured_context_json: { ruleId: "post_plant_spacing_with_bomb_event",
    bombEventType: "bomb_planted", bombEventLabel: "Bomb planted 313", site: "313" } };
const oldPlantCard = renderToStaticMarkup(React.createElement(CoachingEventCard, {
  ...props, reviewEvent: reviewEventForEvent(oldPlantEvent, new Map()), inspected: true
}));
assert.match(oldPlantCard, /炸弹已安放（包点未知）/);
assert.match(oldPlantCard, /<dt>包点<\/dt><dd>未知<\/dd>/);
assert.doesNotMatch(oldPlantCard, /313/);
const farCard = renderToStaticMarkup(React.createElement(CoachingEventCard, {
  ...props, reviewEvent: reviewEventForEvent({ ...event,
    structured_context_json: { ...event.structured_context_json, spacingType: "too_far" } }, new Map())
}));
assert.match(farCard, /先确认队友能否及时支援/);
assert.doesNotMatch(farCard, /留出避免被一起扫射的空间/);
const queuedCard = renderToStaticMarkup(React.createElement(CoachingEventCard, { ...props, renderJob: { status: "queued" } }));
assert.match(queuedCard, /disabled=""[^>]*>.*等待生成/);
assert.equal((queuedCard.match(/disabled=""/g) ?? []).length, 1,
  "Rendering video must not disable seeking to the finding");

const panelProps = {
  events: [event, { ...event, id: "other-round", round_number: 3, title: "Other round finding" }],
  players: [], currentTick: 5504, selectedRound: 2, selectedPlayerName: "xelex", rounds, tickRate: 64,
  renderJobByEventId: new Map(), requestingEventId: null, onSeek() {}, onGenerateClip() {}
};
const panel = renderToStaticMarkup(React.createElement(CoachingPanel, panelProps));
assert.match(panel, /重点建议/);
assert.match(panel, /当前回合<span class="coaching-count"> 1 条<\/span>/);
assert.match(panel, /全部回合<span class="coaching-count"> 2 条<\/span>/);
assert.equal((panel.match(/<article/g) ?? []).length, 1, "Initial rail shows the selected round only");
assert.match(panel, /查看这一刻/);
assert.match(panel, /<span class="coaching-feed-clock" title="第 2 回合 0:11">0:11<\/span>/,
  "cards lead with the same m:ss round clock as the replay");
assert.match(panel, /<details class="coaching-filter-toggle">/,
  "Detailed filters start collapsed so a finding is visible first");
const emptyRound = renderToStaticMarkup(React.createElement(CoachingPanel, { ...panelProps, selectedRound: 4 }));
assert.match(emptyRound, /这一回合暂无建议/);
assert.match(emptyRound, /查看其他回合的 2 条建议/);
assert.doesNotMatch(emptyRound, /没有建议不代表每次选择都正确/,
  "A quiet round must not be confused with an entirely empty review");
console.log("Chinese coaching guidance, honest evidence, timing, localized search and accessible primary actions passed.");
