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
  sanitizeRadarPercent,
  sanitizeRadarPoint,
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

{
  assert.equal(worldToRadarPercent("de_dust2", Number.NaN, 0), null);
  assert.equal(worldToRadarPercent("de_mirage", 0, Number.POSITIVE_INFINITY), null);
}

{
  const dust2Point = worldToRadarPercent("de_dust2", 0, 0);
  const miragePoint = worldToRadarPercent("de_mirage", 0, 0);
  const infernoPoint = worldToRadarPercent("de_inferno", 0, 0);

  assert.notDeepEqual(
    [dust2Point.x, dust2Point.y],
    [miragePoint.x, miragePoint.y]
  );
  assert.notDeepEqual(
    [miragePoint.x, miragePoint.y],
    [infernoPoint.x, infernoPoint.y]
  );
}

{
  assert.equal(sanitizeRadarPercent(Number.NaN), null);
  assert.equal(sanitizeRadarPercent(Number.POSITIVE_INFINITY), null);
  assert.equal(sanitizeRadarPercent(-12), 0);
  assert.equal(sanitizeRadarPercent(112), 100);
  assert.deepEqual(normalize(sanitizeRadarPoint({ x: 150, y: -25, id: "p1" })), { x: 100, y: 0, id: "p1" });
  assert.equal(sanitizeRadarPoint({ x: 50, y: Number.NaN }), null);
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
