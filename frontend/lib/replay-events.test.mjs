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
  parserEventPresentationForType,
  recentMapParserEvents,
  timelineParserEventMarkersForRound
} = loadTypeScriptModule("./replay-events.ts");

const parserEvents = [
  replayEvent({ id: "kill-1", type: "kill", tick: 150, label: "T One killed CT One" }),
  replayEvent({ id: "plant-1", type: "bomb_planted", tick: 200, label: "Bomb planted A" }),
  replayEvent({ id: "smoke-1", type: "smoke", tick: 300, label: "Smoke", x: 45, y: 55 }),
  replayEvent({ id: "flash-2", type: "flash", tick: 420, roundNumber: 2, label: "Flash" })
];

{
  assert.deepEqual(normalize(parserEventPresentationForType("bomb_planted")), {
    label: "Plant",
    tone: "objective",
    shortLabel: "P"
  });
  assert.deepEqual(normalize(parserEventPresentationForType("he")), {
    label: "HE",
    tone: "damage",
    shortLabel: "H"
  });
}

{
  const markers = timelineParserEventMarkersForRound(parserEvents, 1, 100, 500);
  assert.deepEqual(
    normalize(markers.map((marker) => ({
      id: marker.event.id,
      leftPercent: marker.leftPercent,
      seekTick: marker.seekTick,
      label: marker.presentation.label,
      tone: marker.presentation.tone,
      shortLabel: marker.presentation.shortLabel
    }))),
    [
      { id: "kill-1", leftPercent: 12.5, seekTick: 150, label: "Kill", tone: "combat", shortLabel: "K" },
      { id: "plant-1", leftPercent: 25, seekTick: 200, label: "Plant", tone: "objective", shortLabel: "P" },
      { id: "smoke-1", leftPercent: 50, seekTick: 300, label: "Smoke", tone: "utility", shortLabel: "S" }
    ]
  );
}

{
  const nearby = recentMapParserEvents(parserEvents, 1, 290, 64);
  assert.deepEqual(normalize(nearby.map((event) => event.id)), ["smoke-1"]);
}

function replayEvent(overrides) {
  return {
    id: overrides.id,
    type: overrides.type,
    tick: overrides.tick,
    roundNumber: overrides.roundNumber ?? 1,
    playerId: "t-1",
    playerName: "T One",
    side: "T",
    x: overrides.x,
    y: overrides.y,
    label: overrides.label,
    metadata: {}
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
