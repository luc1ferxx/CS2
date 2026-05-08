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
  demoLibraryFilterOptions,
  filterAndSortDemos,
  renderStatusLabel
} = loadTypeScriptModule("./demo-library.ts");

const demos = [
  demo({ id: "archived", name: "Archived Inferno", map_name: "de_inferno", archived: true }),
  demo({
    id: "dust-new",
    name: "Bravo Dust",
    original_filename: "furia-dust2.dem",
    map_name: "de_dust2",
    status: "completed",
    created_at: "2026-05-03T00:00:00Z"
  }),
  demo({
    id: "dust-old",
    name: "Alpha Dust",
    original_filename: "ancient.dem",
    map_name: "de_dust2",
    status: "completed",
    created_at: "2026-05-01T00:00:00Z"
  }),
  demo({
    id: "failed",
    name: "Mirage Failure",
    map_name: "de_mirage",
    status: "failed",
    created_at: "2026-05-02T00:00:00Z"
  })
];

{
  const result = filterAndSortDemos(demos, {
    search: "dust",
    status: "completed",
    map: "de_dust2",
    sort: "name",
    order: "asc",
    includeArchived: false
  });

  assert.deepEqual(normalize(result.map((item) => item.id)), ["dust-old", "dust-new"]);
}

{
  const result = filterAndSortDemos(demos, {
    search: "",
    status: "all",
    map: "all",
    sort: "recent",
    order: "desc",
    includeArchived: false
  });

  assert.deepEqual(normalize(result.map((item) => item.id)), ["dust-new", "failed", "dust-old"]);
}

{
  const result = filterAndSortDemos(demos, {
    search: "inferno",
    status: "all",
    map: "all",
    sort: "recent",
    order: "desc",
    includeArchived: true
  });

  assert.deepEqual(normalize(result.map((item) => item.id)), ["archived"]);
}

{
  const options = demoLibraryFilterOptions(demos);

  assert.deepEqual(normalize(options.maps), ["de_dust2", "de_inferno", "de_mirage"]);
  assert.deepEqual(normalize(options.statuses), ["completed", "failed"]);
}

{
  assert.equal(renderStatusLabel(demo({ video_status: "ready", video_source: "manual_upload" })), "manual ready");
  assert.equal(renderStatusLabel(demo({ latest_render_status: "failed", video_status: "failed" })), "render failed");
  assert.equal(renderStatusLabel(demo({ video_status: null, latest_render_status: null })), "not requested");
}

function demo(overrides) {
  return {
    id: overrides.id ?? "demo",
    name: overrides.name ?? "Demo",
    original_filename: overrides.original_filename ?? "demo.dem",
    map_name: overrides.map_name ?? "de_dust2",
    tick_rate: 64,
    round_count: 12,
    coaching_event_count: 3,
    status: overrides.status ?? "completed",
    error_message: overrides.error_message ?? null,
    created_at: overrides.created_at ?? "2026-05-08T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-08T00:00:00Z",
    completed_at: overrides.completed_at ?? null,
    archived: overrides.archived ?? false,
    video_status: valueOrDefault(overrides, "video_status", "ready"),
    video_source: valueOrDefault(overrides, "video_source", "mock"),
    video_url: valueOrDefault(overrides, "video_url", null),
    latest_render_status: valueOrDefault(overrides, "latest_render_status", null)
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}

function valueOrDefault(object, key, fallback) {
  return Object.prototype.hasOwnProperty.call(object, key) ? object[key] : fallback;
}
