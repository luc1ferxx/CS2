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
  SUPPORTED_TACTICAL_MAP_NAMES,
  getTacticalMapConfig,
  getTacticalMapPresentation,
  worldToRadarPercent
} = loadTypeScriptModule("./map-config.ts");

assert.deepEqual(normalize(SUPPORTED_TACTICAL_MAP_NAMES), [
  "de_dust2",
  "de_mirage",
  "de_inferno",
  "de_ancient",
  "de_nuke",
  "de_anubis"
]);

for (const mapName of SUPPORTED_TACTICAL_MAP_NAMES) {
  const config = getTacticalMapConfig(mapName);
  assert.ok(config, `${mapName} should have a tactical map config`);
  assert.equal(config.mapName, mapName);
  assert.ok(config.radarImagePath.startsWith(`/maps/${mapName}`));
  assert.ok(["calibrated", "approximate"].includes(config.confidence));
}

{
  const presentation = getTacticalMapPresentation({
    demoId: "old-replay",
    mapName: "de_dust2"
  });

  assert.equal(presentation.mapName, "de_dust2");
  assert.equal(presentation.radarImagePath, "/maps/de_dust2_radar.png");
  assert.equal(presentation.calibrated, true);
  assert.equal(presentation.confidence, "calibrated");
}

{
  const presentation = getTacticalMapPresentation({
    demoId: "unknown-replay",
    mapName: "de_cache"
  });

  assert.equal(presentation.mapName, "de_cache");
  assert.equal(presentation.radarImagePath, null);
  assert.equal(presentation.calibrated, false);
  assert.equal(presentation.confidence, "fallback");
  assert.notEqual(presentation.radarImagePath, "/maps/de_dust2_radar.png");
}

{
  const point = worldToRadarPercent("de_anubis", -999999, 999999);
  assert.ok(point.x >= 0 && point.x <= 100);
  assert.ok(point.y >= 0 && point.y <= 100);
  assert.equal(point.confidence, "approximate");
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
