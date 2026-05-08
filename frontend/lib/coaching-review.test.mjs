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
  ruleIdForEvent,
  timelineMarkersForRound
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
  })
];

{
  const model = buildCoachingReviewModel(events, players, {
    severity: "high",
    rule: "isolated_entry",
    search: "trade.two"
  });

  assert.equal(model.totalCount, 3);
  assert.equal(model.filteredCount, 1);
  assert.equal(model.roundGroups.length, 1);
  assert.equal(model.roundGroups[0].roundNumber, 2);
  assert.equal(model.roundGroups[0].events[0].event.id, "isolated");
  assert.deepEqual(normalize(model.availableRules.map((item) => item.id)), [
    "isolated_entry",
    "poor_spacing",
    "post_plant_spread"
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
  const summary = evidenceSummaryForEvent(events[0]);
  assert.deepEqual(normalize(summary), [
    { label: "distance", value: "26.42" },
    { label: "windowSeconds", value: "5" },
    { label: "evidenceTicks", value: "1100, 1120" }
  ]);
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
