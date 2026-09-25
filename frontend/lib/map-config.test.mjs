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
  getTacticalMapLevel,
  mapDisplayName,
  resolveTacticalMapLevel,
  sanitizeRadarPercent,
  sanitizeRadarPoint,
  tacticalRadarImagePaths,
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

{
  const nuke = getTacticalMapPresentation({ mapName: "de_nuke" });
  assert.equal(getTacticalMapLevel(nuke, -496), "lower");
  assert.equal(getTacticalMapLevel(nuke, -495), "lower");
  assert.equal(getTacticalMapLevel(nuke, -494), "upper");
  for (const z of [undefined, null, NaN, Infinity]) assert.equal(getTacticalMapLevel(nuke, z), null);
  assert.equal(getTacticalMapLevel(getTacticalMapConfig("de_dust2"), -600), null);
  assert.equal(resolveTacticalMapLevel(nuke, "auto", -600).radarImagePath, "/maps/de_nuke_lower_radar.png");
  assert.equal(resolveTacticalMapLevel(nuke, "upper", -600).level, "upper");
  assert.equal(resolveTacticalMapLevel(nuke, "auto", undefined).followingPlayer, false);
  assert.deepEqual(normalize(worldToRadarPercent("de_nuke", -3453, 2887)), { x: 0, y: 0, confidence: "calibrated" });
  assert.deepEqual(normalize(worldToRadarPercent("de_nuke", 3715, -4281)), { x: 100, y: 100, confidence: "calibrated" });
}

{
  assert.equal(mapDisplayName("de_dust2"), "Dust II");
  assert.equal(mapDisplayName("de_nuke"), "Nuke");
  assert.equal(mapDisplayName("de_vertigo"), "Vertigo", "maps without a config still lose the de_ prefix");
  for (const pending of ["unknown", "", "  ", null, undefined]) {
    assert.equal(mapDisplayName(pending), "地图待识别", `upload placeholder ${JSON.stringify(pending)}`);
  }

  assert.deepEqual(normalize(tacticalRadarImagePaths("de_dust2")), ["/maps/de_dust2_radar.png"]);
  assert.deepEqual(normalize(tacticalRadarImagePaths("de_nuke")), ["/maps/de_nuke_radar.png", "/maps/de_nuke_lower_radar.png"],
    "Nuke preloads both levels");
  assert.deepEqual(normalize(tacticalRadarImagePaths("unknown")), []);
}
