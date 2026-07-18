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
  buildRoundReviewModel,
  findRoundNumberForTick,
  jumpTargetsForRound
} = loadTypeScriptModule("./round-review.ts");

const rounds = [
  round({ roundNumber: 1, startTick: 100, freezeEndTick: 180, endTick: 500, winnerSide: "T" }),
  round({ roundNumber: 2, startTick: 600, freezeEndTick: 600, endTick: 1100, winnerSide: "CT" }),
  round({ roundNumber: 3, startTick: 1200, freezeEndTick: undefined, endTick: 1500, winnerSide: "T" })
];

const parserEvents = [
  replayEvent({ id: "kill-r1-a", type: "kill", tick: 220, roundNumber: 1, playerName: "entry.t" }),
  replayEvent({ id: "kill-r1-b", type: "kill", tick: 260, roundNumber: 1, playerName: "anchor.ct" }),
  replayEvent({ id: "plant-r1", type: "bomb_planted", tick: 310, roundNumber: 1, label: "Bomb planted A", metadata: { site: "A" } }),
  replayEvent({ id: "smoke-r1", type: "smoke", tick: 330, roundNumber: 1 }),
  replayEvent({ id: "flash-r1", type: "flash", tick: 340, roundNumber: 1 }),
  replayEvent({ id: "he-r1", type: "he", tick: 350, roundNumber: 1 }),
  replayEvent({ id: "molotov-r2", type: "molotov", tick: 720, roundNumber: 2 }),
  replayEvent({ id: "plant-r2", type: "bomb_planted", tick: 800, roundNumber: 2, metadata: { site: "B" } })
];

const coachingEvents = [
  coachingEvent({ id: "coach-r1-a", round_number: 1, tick_start: 230 }),
  coachingEvent({ id: "coach-r1-b", round_number: 1, tick_start: 360 }),
  coachingEvent({ id: "coach-r2", round_number: 2, tick_start: 820 })
];

{
  const model = buildRoundReviewModel({
    rounds,
    parserEvents,
    coachingEvents,
    currentTick: 240,
    selectedRoundNumber: 1,
    tickRate: 64
  });

  assert.equal(model.rounds.length, 3);
  assert.equal(model.selectedRound?.roundNumber, 1);
  assert.equal(model.currentRoundNumber, 1);

  const first = model.rounds[0];
  assert.equal(first.killCount, 2);
  assert.equal(first.bombEventCount, 1);
  assert.equal(first.utilityEventCount, 3);
  assert.equal(first.coachingEventCount, 2);
  assert.equal(first.durationSeconds, 6.25);
  assert.equal(first.isSelected, true);
  assert.equal(first.isCurrent, true);
}

{
  const model = buildRoundReviewModel({
    rounds,
    parserEvents,
    coachingEvents,
    currentTick: 845,
    selectedRoundNumber: 2,
    tickRate: 64
  });
  const second = model.selectedRound;

  assert.equal(second?.firstKill?.tick, null);
  assert.equal(second?.bombPlant?.tick, 800);
  assert.equal(second?.bombPlant?.site, "B");
  assert.equal(second?.killCount, 0);
  assert.equal(second?.utilityEventCount, 1);
  assert.equal(second?.coachingEventCount, 1);
}

{
  const targets = jumpTargetsForRound(
    buildRoundReviewModel({
      rounds,
      parserEvents,
      coachingEvents,
      currentTick: 240,
      selectedRoundNumber: 1,
      tickRate: 64
    }).rounds[0]
  );

  assert.deepEqual(normalize(targets), [
    { id: "round_start", label: "Round start", tick: 100, available: true },
    { id: "live_start", label: "Live start", tick: 180, available: true },
    { id: "first_kill", label: "First kill", tick: 220, available: true },
    { id: "bomb_plant", label: "Bomb plant", tick: 310, available: true }
  ]);
}

{
  const model = buildRoundReviewModel({
    rounds,
    parserEvents: undefined,
    coachingEvents,
    currentTick: 1210,
    selectedRoundNumber: 3,
    tickRate: 64
  });
  const third = model.selectedRound;
  const targets = jumpTargetsForRound(third);

  assert.equal(model.currentRoundNumber, 3);
  assert.equal(third?.freezeEndTick, 1200);
  assert.equal(third?.firstKill?.tick, null);
  assert.equal(third?.bombPlant?.tick, null);
  assert.equal(third?.killCount, 0);
  assert.equal(third?.bombEventCount, 0);
  assert.equal(third?.utilityEventCount, 0);
  assert.deepEqual(normalize(targets.map((target) => [target.id, target.tick, target.available])), [
    ["round_start", 1200, true],
    ["live_start", 1200, true],
    ["first_kill", null, false],
    ["bomb_plant", null, false]
  ]);
}

{
  assert.equal(findRoundNumberForTick(rounds, 99), 1);
  assert.equal(findRoundNumberForTick(rounds, 550), 1);
  assert.equal(findRoundNumberForTick(rounds, 600), 2);
  assert.equal(findRoundNumberForTick(rounds, 1150), 2);
  assert.equal(findRoundNumberForTick(rounds, 1501), 3);
}

{
  const model = buildRoundReviewModel({
    rounds,
    parserEvents,
    coachingEvents,
    currentTick: 845,
    selectedRoundNumber: 1,
    tickRate: 64
  });

  assert.equal(model.rounds[0].isSelected, true);
  assert.equal(model.rounds[0].isCurrent, false);
  assert.equal(model.rounds[1].isSelected, false);
  assert.equal(model.rounds[1].isCurrent, true);
}

function round(overrides) {
  return {
    roundNumber: overrides.roundNumber,
    startTick: overrides.startTick,
    freezeEndTick: overrides.freezeEndTick,
    endTick: overrides.endTick,
    winnerSide: overrides.winnerSide
  };
}

function replayEvent(overrides) {
  return {
    id: overrides.id,
    type: overrides.type,
    tick: overrides.tick,
    roundNumber: overrides.roundNumber,
    playerId: overrides.playerId ?? "p1",
    playerName: overrides.playerName ?? "player.one",
    side: overrides.side ?? "T",
    label: overrides.label ?? overrides.type,
    metadata: overrides.metadata ?? {}
  };
}

function coachingEvent(overrides) {
  return {
    id: overrides.id,
    demo_id: "demo-1",
    round_number: overrides.round_number,
    player_id: "p1",
    player_name: "player.one",
    tick_start: overrides.tick_start,
    tick_end: overrides.tick_start + 128,
    category: "positioning",
    severity: "medium",
    title: "Review event",
    message: "Review event message",
    structured_context_json: {},
    confidence: 0.7,
    created_at: "2026-05-08T00:00:00Z"
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
