import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadTypeScriptModule(relativePath) {
  const filename = resolve(__dirname, relativePath);
  const source = readFileSync(filename, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: filename
  });
  const module = { exports: {} };
  const context = {
    console,
    exports: module.exports,
    module,
    require(specifier) {
      if (specifier === "@/lib/bomb-site") return loadTypeScriptModule("./bomb-site.ts");
      if (specifier.startsWith("@/types/")) {
        return {};
      }
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

const {
  buildCoachingReviewModel,
  evidenceSummaryForEvent,
  feedbackProgress,
  ruleIdForEvent,
  timelineMarkersForRound,
  withFeedback
} = loadTypeScriptModule("./coaching-review.ts");

const players = [
  { id: "p1", name: "entry.one", side: "T", color: "#f4b740" },
  { id: "p2", name: "trade.two", side: "T", color: "#f4b740" },
  { id: "ct1", name: "anchor.ct", side: "CT", color: "#28c7c1" }
];

const events = [
  coachingEvent({
    id: "isolated",
    round_number: 2,
    player_id: "p1",
    player_name: "entry.one",
    tick_start: 1100,
    severity: "high",
    title: "Entry died isolated from trade support",
    message: "The nearest teammate was too far away to trade.",
    structured_context_json: {
      ruleId: "isolated_entry",
      involvedPlayerIds: ["p1", "p2"],
      evidenceTicks: [1100, 1120],
      distance: 26.42,
      windowSeconds: 5
    }
  }),
  coachingEvent({
    id: "spread",
    round_number: 3,
    player_id: "p2",
    player_name: "trade.two",
    tick_start: 2200,
    severity: "medium",
    title: "Post-plant spacing stayed too clustered",
    message: "T players stayed clustered after the plant.",
    structured_context_json: {
      ruleId: "post_plant_spread_issue",
      involvedPlayerIds: ["p1", "p2"],
      evidenceTicks: [2200, 2300],
      site: "A",
      nearbyCount: 3,
      windowSeconds: 4.5
    }
  }),
  coachingEvent({
    id: "spacing",
    round_number: 2,
    player_id: "ct1",
    player_name: "anchor.ct",
    tick_start: 1600,
    severity: "low",
    title: "Spacing too stacked",
    message: "Closest teammates were stacked.",
    structured_context_json: {
      ruleId: "poor_spacing",
      involvedPlayerIds: ["ct1"],
      evidenceTicks: [1600],
      distance: 2.1,
      spacingType: "stacked"
    }
  }),
  coachingEvent({
    id: "weak-utility",
    round_number: 4,
    player_id: "p1",
    player_name: "entry.one",
    tick_start: 3100,
    severity: "medium",
    title: "Execute lacked utility before the plant",
    message: "The plant happened with too little utility support.",
    structured_context_json: {
      ruleId: "weak_utility_before_execute",
      involvedPlayerIds: ["p1"],
      evidenceTicks: [3100],
      relatedEventIds: ["plant-a", "smoke-early"],
      utilityType: "smoke",
      utilityLabel: "Smoke",
      bombEventType: "bomb_planted",
      bombEventLabel: "Bomb planted A",
      windowSeconds: 12
    }
  })
];

{
  const model = buildCoachingReviewModel(events, players, {
    severity: "high",
    rule: "isolated_entry",
    search: "trade.two"
  });

  assert.equal(model.totalCount, 4);
  assert.equal(model.filteredCount, 1);
  assert.equal(model.roundGroups.length, 1);
  assert.equal(model.roundGroups[0].roundNumber, 2);
  assert.equal(model.roundGroups[0].events[0].event.id, "isolated");
  assert.deepEqual(normalize(model.availableRules.map((item) => item.id)), [
    "isolated_entry",
    "poor_spacing",
    "post_plant_spread",
    "weak_utility_before_execute"
  ]);
}

{
  const model = buildCoachingReviewModel(events, players, {
    severity: "all",
    rule: "post_plant_spread",
    search: "clustered"
  });

  assert.equal(model.filteredCount, 1);
  assert.equal(model.roundGroups[0].events[0].event.id, "spread");
  assert.equal(ruleIdForEvent(model.roundGroups[0].events[0].event), "post_plant_spread_issue");
}

{
  const model = buildCoachingReviewModel(events, players, {
    severity: "all",
    rule: "weak_utility_before_execute",
    search: "plant-a"
  });

  assert.equal(model.filteredCount, 1);
  assert.equal(model.roundGroups[0].events[0].event.id, "weak-utility");
}

{
  const reviewEvent = buildCoachingReviewModel(events, players, {
    severity: "all",
    rule: "weak_utility_before_execute",
    search: ""
  }).roundGroups[0].events[0];

  assert.equal(reviewEvent.ruleId, "weak_utility_before_execute");
  assert.deepEqual(normalize(reviewEvent.involvedPlayers), ["entry.one"]);
  assert.deepEqual(normalize(reviewEvent.evidence.slice(0, 3)), [
    { label: "relatedEventIds", value: "plant-a, smoke-early" },
    { label: "windowSeconds", value: "12" },
    { label: "evidenceTicks", value: "3100" }
  ]);
}

{
  const summary = evidenceSummaryForEvent(events[0]);
  assert.deepEqual(normalize(summary), [
    { label: "distance", value: "26.42" },
    { label: "windowSeconds", value: "5" },
    { label: "evidenceTicks", value: "1100, 1120" }
  ]);
}

{
  const summary = evidenceSummaryForEvent(events[3]);
  assert.deepEqual(normalize(summary), [
    { label: "relatedEventIds", value: "plant-a, smoke-early" },
    { label: "windowSeconds", value: "12" },
    { label: "evidenceTicks", value: "3100" },
    { label: "utilityType", value: "smoke" },
    { label: "utilityLabel", value: "Smoke" }
  ]);
}

{
  const summary = evidenceSummaryForEvent({
    ...events[2],
    structured_context_json: {
      ...events[2].structured_context_json,
      verticalDistanceWorldUnits: 0,
      maxStackedVerticalDistanceWorldUnits: 96
    }
  });
  assert.deepEqual(normalize(summary.slice(0, 3)), [
    { label: "distance", value: "2.1" },
    { label: "verticalDistanceWorldUnits", value: "0" },
    { label: "maxStackedVerticalDistanceWorldUnits", value: "96" }
  ]);
  assert.deepEqual(normalize(evidenceSummaryForEvent(events[2])), [
    { label: "distance", value: "2.1" },
    { label: "evidenceTicks", value: "1600" },
    { label: "spacingType", value: "stacked" }
  ], "Legacy findings without vertical evidence keep their existing evidence");
}

{
  const summary = evidenceSummaryForEvent(
    coachingEvent({
      id: "unknown-metadata",
      round_number: 5,
      player_id: "p1",
      player_name: "entry.one",
      tick_start: 4100,
      severity: "low",
      title: "Unknown metadata shape",
      message: "Unknown metadata should not crash summary generation.",
      structured_context_json: {
        ruleId: "experimental_rule",
        relatedEventIds: ["event-a"],
        utilityTypes: [],
        customNested: { unsupported: true }
      }
    })
  );

  assert.deepEqual(normalize(summary), [{ label: "relatedEventIds", value: "event-a" }]);
}

{
  const markers = timelineMarkersForRound(events, 2, 1000, 1800);
  assert.deepEqual(
    normalize(markers.map((marker) => ({
      id: marker.event.id,
      leftPercent: marker.leftPercent,
      severity: marker.event.severity
    }))),
    [
      { id: "isolated", leftPercent: 12.5, severity: "high" },
      { id: "spacing", leftPercent: 75, severity: "low" }
    ]
  );
}

{
  for (const site of [313, "313", 0, 1, "0", "1", true, {}, []]) {
    const event = coachingEvent({ id: "legacy-nuke-plant", tick_start: 90539,
      structured_context_json: { bombEventType: "bomb_planted", bombEventLabel: "Bomb planted 313", site } });
    const summary = evidenceSummaryForEvent(event);
    assert.equal(summary.find((item) => item.label === "bombEventLabel").value, "炸弹已安放（包点未知）");
    // Empty malformed arrays are intentionally omitted by the general evidence guard.
    if (!Array.isArray(site)) assert.equal(summary.find((item) => item.label === "site").value, "未知");
    assert.equal(event.structured_context_json.bombEventLabel, "Bomb planted 313");
  }
  for (const site of ["A", " b "]) {
    const summary = evidenceSummaryForEvent(coachingEvent({ tick_start: 1,
      structured_context_json: { bombEventType: "bomb_planted", bombEventLabel: "Bomb planted 313", site } }));
    assert.equal(summary.find((item) => item.label === "site").value, site.trim().toUpperCase());
    assert.match(summary.find((item) => item.label === "bombEventLabel").value, /[AB] 点/);
  }
  const legacyLabelOnly = evidenceSummaryForEvent(coachingEvent({ tick_start: 1,
    structured_context_json: { bombEventLabel: "Bomb planted A" } }));
  assert.equal(legacyLabelOnly[0].value, "炸弹已安放（A 点）");
}

{
  const stamp = { note: null, updated_at: "2026-09-18T00:00:00Z" };
  const unrated = ["r1", "r2", "r3"].map((id, index) =>
    coachingEvent({
      id, round_number: 1, player_id: "p1", player_name: "entry.one", tick_start: 100 + index * 50,
      severity: "medium", title: id, message: id, structured_context_json: { ruleId: "isolated_entry" }
    })
  );
  const rated = withFeedback(withFeedback(unrated, "r1", { verdict: "helpful", ...stamp }), "r2", { verdict: "unsure", ...stamp });
  assert.deepEqual(normalize(feedbackProgress(rated)), { total: 3, rated: 2, helpful: 1, irrelevant: 0, unsure: 1 });
  assert.equal(unrated[0].feedback, undefined, "withFeedback must not mutate its input");
  assert.deepEqual(normalize(feedbackProgress(withFeedback(rated, "r1", null))), { total: 3, rated: 1, helpful: 0, irrelevant: 0, unsure: 1 });
  assert.deepEqual(normalize(feedbackProgress([])), { total: 0, rated: 0, helpful: 0, irrelevant: 0, unsure: 0 });
}

function coachingEvent(overrides) {
  return {
    id: overrides.id,
    demo_id: "demo-1",
    round_number: overrides.round_number,
    player_id: overrides.player_id,
    player_name: overrides.player_name,
    tick_start: overrides.tick_start,
    tick_end: overrides.tick_start + 192,
    category: "positioning",
    severity: overrides.severity,
    title: overrides.title,
    message: overrides.message,
    structured_context_json: overrides.structured_context_json,
    confidence: 0.7,
    created_at: "2026-05-08T00:00:00Z"
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
