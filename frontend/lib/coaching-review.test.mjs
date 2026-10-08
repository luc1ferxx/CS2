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
      if (specifier === "@/lib/replay-events") return loadTypeScriptModule("./replay-events.ts");
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
  activeCoachingEventIds,
  buildCoachingReviewModel,
  byImportance,
  coachingEventSide,
  coachingExtraReasons,
  coachingImpact,
  coachingWeapon,
  compareImportance,
  deathChips,
  extraReasonLines,
  coachingFacts,
  coachingFeed,
  coachingMomentLabel,
  coachingRoundClock,
  coachingSidesByRound,
  compareFindingPriority,
  evidenceSummaryForEvent,
  feedbackProgress,
  playerSidesByRound,
  isPriorityFinding,
  playerEvidenceForEvent,
  reviewEventForEvent,
  ruleIdForEvent,
  ruleLabelForRuleId,
  sameIdSet,
  severityFilterOptions,
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
  // Inside a round the most severe card leads, then the earliest, like the analyzer.
  const round = [
    coachingEvent({ id: "low-early", round_number: 7, tick_start: 100, severity: "low", structured_context_json: { ruleId: "poor_spacing" } }),
    coachingEvent({ id: "medium-late", round_number: 7, tick_start: 900, severity: "medium", structured_context_json: { ruleId: "untraded_death" } }),
    coachingEvent({ id: "medium-early", round_number: 7, tick_start: 500, severity: "medium", structured_context_json: { ruleId: "untraded_death" } }),
    coachingEvent({ id: "critical", round_number: 7, tick_start: 950, severity: "critical", structured_context_json: { ruleId: "isolated_entry" } }),
    coachingEvent({ id: "info", round_number: 7, tick_start: 50, severity: "info", structured_context_json: { ruleId: "poor_spacing" } }),
    coachingEvent({ id: "unknown", round_number: 7, tick_start: 10, severity: "bogus", structured_context_json: {} })
  ];
  const model = buildCoachingReviewModel(round, players, { severity: "all", rule: "all", search: "" });
  assert.deepEqual(normalize(model.roundGroups[0].events.map((item) => item.event.id)),
    ["critical", "medium-early", "medium-late", "low-early", "info", "unknown"]);
  assert.deepEqual([...round].sort(compareFindingPriority).map((event) => event.id),
    ["critical", "medium-early", "medium-late", "low-early", "info", "unknown"]);
  assert.deepEqual(round.filter(isPriorityFinding).map((event) => event.id), ["medium-late", "medium-early", "critical"]);
  // Only levels that are present become filters, with their counts.
  assert.deepEqual(normalize(severityFilterOptions(round)), [
    { value: "high", count: 1 }, { value: "medium", count: 2 }, { value: "low", count: 2 }
  ]);
  assert.deepEqual(normalize(severityFilterOptions(round.filter((event) => event.severity !== "critical"))), [
    { value: "medium", count: 2 }, { value: "low", count: 2 }
  ]);
  assert.deepEqual(normalize(severityFilterOptions([])), []);
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

{
  // Rule names are Chinese and keyed by ruleId; nothing inherits from Object.prototype.
  assert.equal(ruleLabelForRuleId("untraded_death"), "无人补枪的阵亡");
  assert.equal(ruleLabelForRuleId("post_plant_spread_issue"), ruleLabelForRuleId("post_plant_spread"));
  for (const id of ["future_rule", "toString", "__proto__"]) assert.equal(ruleLabelForRuleId(id), "其他建议");
}

{
  // Each card carries the facts that tell it apart from others of its rule.
  const fact = (ruleId, context) => coachingFacts(coachingEvent({ id: ruleId, round_number: 1, tick_start: 10, severity: "low",
    structured_context_json: { ruleId, ...context } }));
  assert.equal(fact("untraded_death", { attackerName: "donk", windowSeconds: 5 }), "被 donk 击杀，5 秒内没有队友补枪");
  assert.equal(fact("untraded_death", {}), "没有记录到队友补枪");
  assert.equal(fact("isolated_entry", { distance: 1075.93, isolatedTeammateDistance: 900 }), "T 方首个阵亡，最近的队友约 1076 单位外");
  assert.equal(fact("isolated_entry", { distance: 26.42 }), "T 方首个阵亡", "legacy radar-percent distances are not called units");
  assert.equal(fact("poor_spacing", { spacingType: "too_far", maxNearestDistance: 1403.2, distance: 28 }), "最近的队友约 1403 单位外");
  assert.equal(fact("poor_spacing", { spacingType: "stacked", minPairDistance: 88.4 }), "两名队友相距约 88 单位");
  assert.equal(fact("poor_spacing", { spacingType: "stacked", distance: 2.1 }), "队友站位较近");
  assert.equal(fact("post_plant_spread_issue", { site: "B", nearbyCount: 3, windowSeconds: 4.25 }), "B 点下包后 3 名 T 站位集中，持续约 4.3 秒");
  assert.equal(fact("post_plant_spacing_with_bomb_event", { site: 313 }), "下包后站位集中");
  assert.equal(fact("retake_desync", { nearbyCount: 2, windowSeconds: 3.5 }), "2 名 CT 先后到达包点，前后相差约 3.5 秒");
  assert.equal(fact("weak_utility_before_execute", { utilityCount: 1, requiredUtilityCount: 2, windowSeconds: 12 }), "下包前 12 秒内记录到 1/2 个道具");
  assert.equal(fact("late_post_plant_utility", { utilityType: "smoke", windowSeconds: 18 }), "下包约 18 秒后投出第一个烟雾弹");
  assert.equal(fact("late_post_plant_utility", { utilityLabel: "Molotov" }), "", "no timing, no fact line");
  assert.equal(fact("future_rule", { attackerName: "donk" }), "");
  for (const text of [fact("untraded_death", { attackerName: "donk", windowSeconds: 5 }), fact("weak_utility_before_execute", { utilityCount: 1 })]) {
    assert.doesNotMatch(text, /tick|evt-|[a-z]+_[a-z]+/, "no raw ids or rule slugs in the facts line");
  }
}

{
  // Player evidence drops ids, ticks and parser enums and translates what it keeps;
  // the raw evidence list (search, legacy callers) is unchanged.
  const event = events[3];
  assert.deepEqual(normalize(playerEvidenceForEvent(event).slice(0, 3)), [
    { label: "windowSeconds", value: "12" },
    { label: "utilityType", value: "烟雾弹" },
    { label: "utilityLabel", value: "烟雾弹" }
  ]);
  for (const item of playerEvidenceForEvent(event)) {
    assert.ok(!["relatedEventIds", "evidenceTicks", "bombTick", "bombEventType"].includes(item.label), item.label);
  }
  assert.equal(playerEvidenceForEvent(events[2]).find((item) => item.label === "spacingType").value, "过近");
  assert.equal(evidenceSummaryForEvent(event)[0].label, "relatedEventIds");
  const reviewEvent = reviewEventForEvent(events[0], new Map());
  assert.equal(reviewEvent.facts, coachingFacts(events[0]));
  assert.deepEqual(normalize(reviewEvent.playerEvidence), normalize(playerEvidenceForEvent(events[0])));
}

{
  const rounds = [{ roundNumber: 2, startTick: 1000, freezeEndTick: 1200, endTick: 9000 }];
  const event = coachingEvent({ id: "moment", round_number: 2, tick_start: 1000 + 64 * 71, severity: "low", structured_context_json: {} });
  assert.equal(coachingMomentLabel(event, rounds, 64), "第 2 回合 1:11");
  assert.equal(coachingMomentLabel({ ...event, tick_start: 1000 + 64 * 9 }, rounds, 64), "第 2 回合 0:09");
  for (const rate of [undefined, 0, -1, NaN, Infinity]) assert.equal(coachingMomentLabel(event, rounds, rate), "第 2 回合");
  assert.equal(coachingMomentLabel(event, [], 64), "第 2 回合");
  // The kill-feed time column: the m:ss clock alone, or nothing when it cannot be told.
  assert.equal(coachingRoundClock(event, rounds, 64), "1:11");
  assert.equal(coachingRoundClock({ ...event, tick_start: 900 }, rounds, 64), null);
  assert.equal(coachingRoundClock(event, [], 64), null);
}

{
  // A card as a kill-feed row: killer ✕ victim for a death, and a finding that does not repeat them.
  const feed = (ruleId, context) => normalize(coachingFeed(coachingEvent({ id: ruleId, round_number: 3, tick_start: 10, severity: "low",
    structured_context_json: { ruleId, ...context } })));
  assert.deepEqual(feed("untraded_death", { attackerName: "donk", windowSeconds: 5 }), { died: true, killer: "donk", finding: "5 秒内没有队友补枪" });
  assert.deepEqual(feed("untraded_death", {}), { died: true, killer: null, finding: "没有记录到队友补枪" });
  assert.deepEqual(feed("isolated_entry", {}), { died: true, killer: null, finding: "T 方首个阵亡" });
  assert.deepEqual(feed("poor_spacing", { spacingType: "too_far", maxNearestDistance: 1403.2 }),
    { died: false, killer: null, finding: "最近的队友约 1403 单位外" });
  assert.deepEqual(feed("future_rule", {}), { died: false, killer: null, finding: "" });

  // Sides swap at half, so the side comes from the round, not the roster.
  const side = (ruleId, context = {}, round = 3) => coachingEvent({ id: `${ruleId}-${round}`, round_number: round, tick_start: 10,
    severity: "low", structured_context_json: { ruleId, ...context } });
  assert.equal(coachingEventSide(side("poor_spacing", { side: "CT" })), "CT");
  assert.equal(coachingEventSide(side("poor_spacing", { side: "spectator" })), null);
  assert.equal(coachingEventSide(side("isolated_entry")), "T");
  assert.equal(coachingEventSide(side("late_post_plant_utility")), "T");
  assert.equal(coachingEventSide(side("retake_desync")), "CT");
  assert.equal(coachingEventSide(side("untraded_death")), null);
  assert.deepEqual(normalize([...coachingSidesByRound([side("untraded_death"), side("poor_spacing", { side: "CT" }), side("retake_desync", {}, 14),
    side("isolated_entry", {}, 14)])]), [[3, "CT"], [14, "CT"]]);

  const frameAt = (tick, playerSide) => ({ tick, timeSeconds: 0, roundNumber: 0, bombState: { status: "unknown" },
    players: [{ id: "me", name: "me", side: playerSide, x: 0, y: 0, alive: true, hp: 100, hasBomb: false }] });
  const sideRounds = [
    { roundNumber: 1, startTick: 0, freezeEndTick: 100, endTick: 900, winnerSide: "T" },
    { roundNumber: 13, startTick: 1000, freezeEndTick: 1100, endTick: 1900, winnerSide: "CT" },
    { roundNumber: 14, startTick: 2000, freezeEndTick: 2100, endTick: 2900, winnerSide: "CT" }
  ];
  const frames = [frameAt(0, "CT"), frameAt(120, "T"), frameAt(1120, "CT")];
  assert.deepEqual(normalize([...playerSidesByRound(frames, sideRounds, "me")]), [[1, "T"], [13, "CT"]]);
  assert.deepEqual(normalize([...playerSidesByRound(frames, sideRounds, null)]), []);
}

{
  const a = coachingEvent({ id: "a", round_number: 1, tick_start: 1000, severity: "low", structured_context_json: {} });
  const b = coachingEvent({ id: "b", round_number: 1, tick_start: 2000, severity: "low", structured_context_json: {} });
  assert.deepEqual([...activeCoachingEventIds([a, b], 1000 - 128)], ["a"]);
  assert.deepEqual([...activeCoachingEventIds([a, b], a.tick_end + 129)], []);
  assert.deepEqual([...activeCoachingEventIds([a, b], 1900)].sort(), ["b"]);
  assert.equal(sameIdSet(new Set(["a", "b"]), new Set(["b", "a"])), true);
  assert.equal(sameIdSet(new Set(["a"]), new Set(["b"])), false);
  assert.equal(sameIdSet(new Set(), new Set(["a"])), false);
}

{
  // 本场最值得回看: round lost > first death > man disadvantage > more reasons > severity > round > tick;
  // events without the death impact follow, in their existing (severity, round, tick) order.
  const death = (id, round, tick, impact, extra = 0, severity = "medium") => coachingEvent({
    id, round_number: round, tick_start: tick, severity, structured_context_json: {
      ruleId: "untraded_death", impact,
      extraReasons: Array.from({ length: extra }, () => ({ ruleId: "poor_spacing", spacingType: "too_far", distance: 1200 }))
    }
  });
  const plain = (id, round, tick, severity, ruleId = "poor_spacing") =>
    coachingEvent({ id, round_number: round, tick_start: tick, severity, structured_context_json: { ruleId } });
  const events = [
    plain("stacked-high", 1, 50, "high"),
    death("nothing", 2, 2000, { roundLost: false, firstDeath: false, manDisadvantage: false }),
    plain("retake-medium", 1, 60, "medium", "retake_desync"),
    death("disadvantage", 3, 3000, { roundLost: false, firstDeath: false, manDisadvantage: true }),
    death("first-late", 5, 5000, { roundLost: false, firstDeath: true }),
    death("first-early", 4, 4000, { roundLost: false, firstDeath: true }),
    death("lost-unknown-first", 6, 6000, { roundLost: null, firstDeath: true }),
    death("lost-reasons", 8, 8000, { roundLost: true }, 1),
    death("lost", 7, 7000, { roundLost: true }),
    death("nothing-reasons", 9, 9000, {}, 2, "low"),
    plain("legacy-low", 1, 10, "low", "untraded_death")
  ];
  const order = [...events].sort(compareImportance).map((event) => event.id);
  assert.deepEqual(order, [
    "lost-reasons", "lost",
    "first-early", "first-late", "lost-unknown-first",
    "disadvantage",
    "nothing-reasons", "nothing",
    "stacked-high", "retake-medium", "legacy-low"
  ]);
  assert.deepEqual(normalize(byImportance(events.map((event) => ({ event }))).map((item) => item.event.id)), order);
  assert.equal(events[0].id, "stacked-high", "byImportance sorts a copy");
  // Without any impact the order is exactly the existing priority order.
  const legacy = events.filter((event) => !event.structured_context_json.impact);
  assert.deepEqual([...legacy].sort(compareImportance).map((event) => event.id), [...legacy].sort(compareFindingPriority).map((event) => event.id));
}

{
  // Readers keep only well-formed fields and never guess.
  const withContext = (context) => coachingEvent({ id: "reader", round_number: 1, tick_start: 1, severity: "medium", structured_context_json: context });
  assert.equal(coachingImpact(withContext({ ruleId: "untraded_death" })), null);
  assert.equal(coachingImpact(withContext({ impact: [] })), null);
  assert.equal(coachingImpact({ ...withContext({}), structured_context_json: undefined }), null);
  assert.deepEqual(normalize(coachingImpact(withContext({ impact: {
    roundLost: null, firstDeath: "yes", manDisadvantage: true, aliveBefore: { own: 4, enemy: 4 }, aliveAfter: { own: 3.5, enemy: 4 }
  } }))), { roundLost: null, manDisadvantage: true, aliveBefore: { own: 4, enemy: 4 } });
  assert.deepEqual(normalize(coachingExtraReasons(withContext({ extraReasons: [
    { ruleId: "isolated_entry", distance: 980, tick: 400 }, { distance: 5 }, null, "x",
    { ruleId: "poor_spacing", spacingType: "too_far", distance: "far", durationSeconds: 3 }
  ] }))), [
    { ruleId: "isolated_entry", distance: 980, tick: 400 },
    { ruleId: "poor_spacing", spacingType: "too_far", durationSeconds: 3 }
  ]);
  assert.deepEqual(normalize(coachingExtraReasons(withContext({ extraReasons: "nope" }))), []);
  assert.equal(coachingWeapon(withContext({ weapon: " awp " })), "awp");
  assert.equal(coachingWeapon(withContext({ weapon: 7 })), null);
}

{
  // Death cards: chips after the finding and one "另外" line per folded reason.
  const card = (context, ruleId = "untraded_death") => coachingEvent({ id: "chips", round_number: 1, tick_start: 1, severity: "medium",
    structured_context_json: { ruleId, ...context } });
  const full = card({
    weapon: "ak47",
    impact: { roundLost: true, firstDeath: true, aliveBefore: { own: 4, enemy: 4 }, aliveAfter: { own: 3, enemy: 4 }, manDisadvantage: true },
    extraReasons: [
      { ruleId: "poor_spacing", spacingType: "too_far", distance: 1240, durationSeconds: 4.5, tick: 300 },
      { ruleId: "isolated_entry", distance: 980.4, tick: 400 },
      { ruleId: "future_rule" }
    ]
  });
  assert.deepEqual(normalize(deathChips(full)), ["ak47", "4v4→3v4", "回合输了"], "the stored weapon string without a label function");
  assert.deepEqual(normalize(deathChips(full, (weapon) => weapon === "ak47" ? "AK-47" : null)), ["AK-47", "4v4→3v4", "回合输了"]);
  assert.equal(deathChips(full, () => null)[0], "ak47", "an unmapped weapon falls back to the stored string");
  assert.deepEqual(normalize(extraReasonLines(full)), [
    "另外：阵亡前已经离最近的队友 1240 单位，持续 4.5 秒",
    "另外：这是本回合 T 方第一个阵亡，最近的队友约 980 单位外"
  ]);
  assert.deepEqual(normalize(extraReasonLines(card({ extraReasons: [{ ruleId: "poor_spacing", spacingType: "too_far" }, { ruleId: "isolated_entry" }] }))),
    ["另外：阵亡前已经离最近的队友较远", "另外：这是本回合 T 方第一个阵亡"]);
  // Missing data leaves the chip out rather than guessing it.
  assert.deepEqual(normalize(deathChips(card({ impact: { roundLost: false, aliveBefore: { own: 2, enemy: 1 } } }))), []);
  assert.deepEqual(normalize(deathChips(card({ impact: { roundLost: null, aliveBefore: { own: 2, enemy: 1 }, aliveAfter: { own: 1, enemy: 1 } } }, "isolated_entry"))),
    ["2v1→1v1"]);
  assert.deepEqual(normalize(deathChips(card({ weapon: "awp", impact: { roundLost: true } }, "poor_spacing"))), [], "only death cards carry chips");
  assert.deepEqual(normalize(deathChips(card({}))), []);
  assert.deepEqual(normalize(extraReasonLines(card({}))), []);
  const reviewEvent = reviewEventForEvent(full, new Map(), { weaponLabel: (weapon) => weapon.toUpperCase() });
  assert.deepEqual(normalize(reviewEvent.chips), ["AK47", "4v4→3v4", "回合输了"]);
  assert.equal(reviewEvent.extraReasonLines.length, 2);
  assert.deepEqual(normalize(reviewEventForEvent(card({}), new Map()).chips), []);

  // Stacked cards say how long the pair stayed together; isolated entries name the killer when recorded.
  const fact = (context) => coachingFacts(card({ spacingType: "stacked", ...context }, "poor_spacing"));
  assert.equal(fact({ minPairDistance: 88.4, durationSeconds: 3.5 }), "两名队友相距约 88 单位，持续 3.5 秒");
  assert.equal(fact({ minPairDistance: 88.4, durationSeconds: 4 }), "两名队友相距约 88 单位，持续 4 秒");
  assert.equal(fact({ durationSeconds: 3.25 }), "队友站位较近，持续 3.3 秒");
  assert.equal(fact({ minPairDistance: 88.4 }), "两名队友相距约 88 单位", "older stacked cards keep their line");
  assert.deepEqual(normalize(coachingFeed(card({ attackerName: "donk", distance: 980, isolatedTeammateDistance: 900 }, "isolated_entry"))),
    { died: true, killer: "donk", finding: "T 方首个阵亡，最近的队友约 980 单位外" });
}

{
  // Shooting rules: the facts line says how fast the shot was against the weapon's stable speed.
  const shooting = (ruleId, context) => coachingEvent({ id: ruleId, round_number: 4, tick_start: 10, severity: "low",
    structured_context_json: { ruleId, weapon: "m4a1_silencer", weaponLabel: "M4A1-S", accurateSpeed: 76, ...context } });
  const facts = (ruleId, context) => coachingFacts(shooting(ruleId, context));
  assert.equal(facts("no_counter_strafe", { speed: 168, hit: false, died: false, keysAtShot: ["A"], counterStrafe: false }),
    "第一枪时速度约 168（M4A1-S 稳定线 76），按着 A 没有反向急停，没打中");
  assert.equal(facts("no_counter_strafe", { speed: 140, hit: true, died: true, attackerName: "donk", keysAtShot: ["W", "D"], counterStrafe: false,
    occurrencesInRound: 2 }), "第一枪时速度约 140（M4A1-S 稳定线 76），按着 W+D 没有反向急停，被 donk 击杀，这回合共 2 次");
  assert.equal(facts("no_counter_strafe", { speed: 0, airborne: true, hit: false, died: true, keysAtShot: ["A"], counterStrafe: false }),
    "第一枪时速度约 0（M4A1-S 稳定线 76），在空中开枪，没打中，2 秒内阵亡", "in the air, the keys are not the point");
  assert.equal(facts("no_counter_strafe", { speed: 150, hit: false, keysAtShot: ["D"], counterStrafe: true }),
    "第一枪时速度约 150（M4A1-S 稳定线 76），反向点了但开枪太早，没打中", "a counter-strafe that came too late");
  assert.equal(facts("no_counter_strafe", { speed: 150, hit: false, keysAtShot: [], counterStrafe: false }), "第一枪时速度约 150（M4A1-S 稳定线 76），没打中");
  assert.equal(facts("no_counter_strafe", { speed: 150, hit: false }), "第一枪时速度约 150（M4A1-S 稳定线 76），没打中", "no inputs, no key part");
  assert.equal(facts("moving_shots", { speed: 201, movingShotCount: 4, hit: false, died: false, occurrencesInRound: 1 }),
    "边移动边开了 4 枪（最高速度约 201，M4A1-S 稳定线 76），一枪没中");
  assert.equal(facts("moving_shots", { speed: 201, movingShotCount: 3, hit: true, died: true, attackerName: "donk", occurrencesInRound: 3 }),
    "边移动边开了 3 枪（最高速度约 201，M4A1-S 稳定线 76），一枪没中，被 donk 击杀，这回合共 3 次");
  for (const text of [facts("moving_shots", { speed: 201, movingShotCount: 3 }), facts("no_counter_strafe", { speed: 150, hit: false })]) {
    assert.doesNotMatch(text, /tick|m4a1_silencer|[a-z]+_[a-z]+/, "the weapon label, never the weapon key");
  }

  // Rule names, the filter list and the side come from the card.
  assert.equal(ruleLabelForRuleId("moving_shots"), "移动射击");
  assert.equal(ruleLabelForRuleId("no_counter_strafe"), "第一枪没急停");
  const model = buildCoachingReviewModel(
    [shooting("moving_shots", { speed: 201, movingShotCount: 3 }), shooting("no_counter_strafe", { speed: 150 }),
      { ...shooting("no_counter_strafe", { speed: 160 }), id: "ncs-2" }],
    players, { severity: "all", rule: "no_counter_strafe", search: "" });
  assert.deepEqual(normalize(model.availableRules), [
    { id: "moving_shots", label: "移动射击", count: 1 },
    { id: "no_counter_strafe", label: "第一枪没急停", count: 2 }
  ]);
  assert.equal(model.filteredCount, 2);
  assert.equal(coachingEventSide(shooting("moving_shots", { side: "CT" })), "CT");
  assert.equal(coachingEventSide(shooting("moving_shots", {})), null);

  // As a kill-feed row: a death after the burst puts the killer in front and leaves it out of the finding.
  assert.deepEqual(normalize(coachingFeed(shooting("no_counter_strafe", { speed: 150, hit: false, died: true, attackerName: "donk" }))),
    { died: true, killer: "donk", finding: "第一枪时速度约 150（M4A1-S 稳定线 76），没打中" });
  assert.deepEqual(normalize(coachingFeed(shooting("moving_shots", { speed: 201, movingShotCount: 3, died: true }))),
    { died: true, killer: null, finding: "边移动边开了 3 枪（最高速度约 201，M4A1-S 稳定线 76），一枪没中" });
  assert.deepEqual(normalize(coachingFeed(shooting("moving_shots", { speed: 201, movingShotCount: 3, died: false, attackerName: "donk" }))),
    { died: false, killer: null, finding: "边移动边开了 3 枪（最高速度约 201，M4A1-S 稳定线 76），一枪没中" });

  // Player evidence: side first, then the weapon and the speeds; held keys read W+A.
  const evidence = playerEvidenceForEvent(shooting("no_counter_strafe", { side: "T", speed: 150, movingShotCount: 1, shotCount: 2,
    keysAtShot: ["W", "A"] }));
  assert.deepEqual(normalize(evidence.map((item) => item.label)), ["side", "weaponLabel", "speed", "accurateSpeed", "movingShotCount"]);
  assert.equal(evidence.find((item) => item.label === "weaponLabel").value, "M4A1-S");
  const keys = playerEvidenceForEvent(shooting("no_counter_strafe", { keysAtShot: ["W", "A"] })).find((item) => item.label === "keysAtShot");
  assert.equal(keys.value, "W+A");

  // The weapon reads from the same Chinese table as the death cards, not the analyzer's English label.
  for (const [weapon, weaponLabel, name] of [
    ["deagle", "Desert Eagle", "沙漠之鹰"], ["famas", "FAMAS", "法玛斯"], ["galilar", "Galil AR", "加利尔"],
    ["revolver", "R8 Revolver", "R8 左轮"], ["ak47", "AK-47", "AK-47"]
  ]) {
    const card = shooting("no_counter_strafe", { weapon, weaponLabel, speed: 150, hit: false });
    assert.equal(coachingFacts(card), `第一枪时速度约 150（${name} 稳定线 76），没打中`, weapon);
    assert.equal(coachingFeed(shooting("moving_shots", { weapon, weaponLabel, speed: 201, movingShotCount: 2 })).finding,
      `边移动边开了 2 枪（最高速度约 201，${name} 稳定线 76），一枪没中`, weapon);
    assert.equal(playerEvidenceForEvent(card).find((item) => item.label === "weaponLabel").value, name, weapon);
  }
  // Cards without the weapon key keep the analyzer's label.
  const labelOnly = shooting("no_counter_strafe", { weapon: undefined, weaponLabel: "Desert Eagle", speed: 150, hit: false });
  assert.equal(coachingFacts(labelOnly), "第一枪时速度约 150（Desert Eagle 稳定线 76），没打中");
  assert.equal(playerEvidenceForEvent(labelOnly).find((item) => item.label === "weaponLabel").value, "Desert Eagle");
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
