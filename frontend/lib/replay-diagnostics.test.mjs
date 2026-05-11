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
  buildReplayDiagnostics,
  replayHasUsableFrames,
  renderStateSummary
} = loadTypeScriptModule("./replay-diagnostics.ts");

{
  const diagnostics = buildReplayDiagnostics(
    replay({
      diagnostics: {
        contractVersion: "legacy",
        normalizedLegacy: true,
        parserEventCount: 0,
        roundCount: 0,
        playerCount: 0,
        frameCount: 0,
        missingFields: ["events", "rounds"],
        degradedFields: ["video"],
        eventFamilyCounts: { combat: 0, objective: 0, utility: 0 },
        missingEventFamilies: ["combat", "objective", "utility"]
      },
      rounds: [],
      players: [],
      frames: [],
      events: []
    }),
    [],
    []
  );

  assert.equal(diagnostics.contractVersion, "legacy");
  assert.equal(diagnostics.normalizedLegacy, true);
  assert.equal(diagnostics.counts.parserEvents, 0);
  assert.equal(diagnostics.counts.coachingEvents, 0);
  assert.equal(diagnostics.counts.rounds, 0);
  assert.equal(diagnostics.counts.players, 0);
  assert.equal(diagnostics.counts.frames, 0);
  assert.equal(diagnostics.usableReplay, false);
  assert.deepEqual(normalize(diagnostics.emptyStates), ["no-rounds", "no-parser-events", "no-coaching-events", "no-frames"]);
  assert.ok(diagnostics.warnings.some((warning) => warning.includes("legacy")));
  assert.ok(diagnostics.warnings.some((warning) => warning.includes("No rounds")));
}

{
  const diagnostics = buildReplayDiagnostics(
    replay({
      diagnostics: null,
      rounds: [round()],
      players: [{ id: "p1", name: "Player One", side: "T", color: "#fff" }],
      frames: [frame()],
      events: undefined
    }),
    [coachingEvent()],
    [{ status: "queued", job_type: "render_clip", error_message: null }]
  );

  assert.equal(diagnostics.contractVersion, "runtime");
  assert.equal(diagnostics.counts.parserEvents, 0);
  assert.equal(diagnostics.counts.coachingEvents, 1);
  assert.equal(diagnostics.counts.rounds, 1);
  assert.equal(diagnostics.counts.players, 1);
  assert.equal(diagnostics.counts.frames, 1);
  assert.equal(diagnostics.usableReplay, true);
  assert.deepEqual(normalize(diagnostics.emptyStates), ["no-parser-events"]);
  assert.equal(diagnostics.renderState.label, "Render queued");
  assert.equal(diagnostics.renderState.active, true);
}

{
  assert.equal(replayHasUsableFrames(replay({ frames: [frame()] })), true);
  assert.equal(replayHasUsableFrames(replay({ frames: [] })), false);
  assert.equal(renderStateSummary("queued", null).active, true);
  assert.equal(renderStateSummary("processing", null).active, true);
  assert.equal(renderStateSummary("rendering", null).active, true);
  assert.equal(renderStateSummary("failed", "Worker unavailable").tone, "failed");
  assert.equal(renderStateSummary("ready", null).active, false);
}

function replay(overrides) {
  return {
    demoId: "demo-1",
    mapName: "de_inferno",
    tickRate: 64,
    video: {
      status: "ready",
      url: null,
      durationSeconds: 10,
      tickStart: 0,
      tickEnd: 640,
      tickRate: 64,
      source: "mock",
      errorMessage: null,
      timeOriginSeconds: 0
    },
    rounds: [round()],
    players: [],
    frames: [frame()],
    events: [],
    generatedAt: "2026-05-08T00:00:00Z",
    diagnostics: {
      contractVersion: "replay_contract_v1",
      normalizedLegacy: false,
      parserEventCount: 0,
      roundCount: 1,
      playerCount: 0,
      frameCount: 1,
      missingFields: [],
      degradedFields: [],
      eventFamilyCounts: { combat: 0, objective: 0, utility: 0 },
      missingEventFamilies: ["combat", "objective", "utility"]
    },
    ...overrides
  };
}

function round() {
  return {
    roundNumber: 1,
    startTick: 0,
    freezeEndTick: 0,
    endTick: 640,
    winnerSide: "CT"
  };
}

function frame() {
  return {
    tick: 0,
    timeSeconds: 0,
    roundNumber: 1,
    players: [],
    bombState: { status: "carried" }
  };
}

function coachingEvent() {
  return {
    id: "coach-1",
    demo_id: "demo-1",
    round_number: 1,
    player_id: "p1",
    player_name: "Player One",
    tick_start: 128,
    tick_end: 256,
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
