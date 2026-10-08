import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

import {
  coachingEvidenceEvents,
  degradedReplay,
  parserEventReplay,
  replayPlayers
} from "./test-fixtures/replay-quality.mjs";

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

const { buildCoachingReviewModel } = loadTypeScriptModule("./coaching-review.ts");
const { buildReplayDiagnostics } = loadTypeScriptModule("./replay-diagnostics.ts");
const { parserEventPresentationForType, timelineParserEventMarkersForRound } =
  loadTypeScriptModule("./replay-events.ts");

{
  const diagnostics = buildReplayDiagnostics(degradedReplay(), [], []);

  assert.equal(diagnostics.contractVersion, "legacy");
  assert.equal(diagnostics.normalizedLegacy, true);
  assert.deepEqual(normalize(diagnostics.emptyStates), [
    "no-rounds",
    "no-parser-events",
    "no-coaching-events",
    "no-frames"
  ]);
  assert.equal(diagnostics.usableReplay, false);
}

{
  const replay = parserEventReplay();
  const markers = timelineParserEventMarkersForRound(replay.events, 1, 100, 500);

  assert.deepEqual(normalize(markers.map((marker) => marker.presentation.shortLabel)), [
    "击",
    "包",
    "烟",
    "闪"
  ]);
  assert.deepEqual(normalize(parserEventPresentationForType("future_parser_event")), {
    label: "事件",
    tone: "objective",
    shortLabel: "事"
  });
}

{
  const model = buildCoachingReviewModel(coachingEvidenceEvents(), replayPlayers(), {
    severity: "all",
    rule: "weak_utility_before_execute",
    search: "smoke-execute"
  });
  const reviewEvent = model.roundGroups[0].events[0];

  assert.equal(model.filteredCount, 1);
  assert.equal(reviewEvent.ruleId, "weak_utility_before_execute");
  assert.deepEqual(normalize(reviewEvent.involvedPlayers), ["T Entry", "T Support"]);
  assert.deepEqual(normalize(reviewEvent.evidence.slice(0, 3)), [
    { label: "relatedEventIds", value: "plant-a, smoke-execute" },
    { label: "windowSeconds", value: "12" },
    { label: "evidenceTicks", value: "900, 1200" }
  ]);
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
