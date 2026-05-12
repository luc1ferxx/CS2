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
  canRetryParse,
  countActiveLibraryDemos,
  detailSummaryItems,
  demoLibraryFilterOptions,
  filterAndSortDemos,
  friendlyErrorMessage,
  ingestionPhaseLabel,
  isRenderActiveStatus,
  libraryEmptyState,
  parseFailureReason,
  renderStatusLabel,
  shouldPollLibrary
} = loadTypeScriptModule("./demo-library.ts");

const defaultFilters = {
  search: "",
  status: "all",
  map: "all",
  sort: "recent",
  order: "desc",
  includeArchived: false
};

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
  }),
  demo({
    id: "render-processing",
    name: "Nuke Render",
    original_filename: "nuke.dem",
    map_name: "de_nuke",
    status: "completed",
    created_at: "2026-05-04T00:00:00Z",
    latest_render_status: "processing"
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

  assert.deepEqual(normalize(result.map((item) => item.id)), ["render-processing", "dust-new", "failed", "dust-old"]);
}

{
  const result = filterAndSortDemos(demos, {
    search: "render processing",
    status: "all",
    map: "all",
    sort: "recent",
    order: "desc",
    includeArchived: true
  });

  assert.deepEqual(normalize(result.map((item) => item.id)), ["render-processing"]);
}

{
  const result = filterAndSortDemos(demos, {
    search: "dust ready",
    status: "all",
    map: "all",
    sort: "name",
    order: "asc",
    includeArchived: false
  });

  assert.deepEqual(normalize(result.map((item) => item.id)), ["dust-old", "dust-new"]);
}

{
  const options = demoLibraryFilterOptions(demos);

  assert.deepEqual(normalize(options.maps), ["de_dust2", "de_inferno", "de_mirage", "de_nuke"]);
  assert.deepEqual(normalize(options.statuses), ["queued", "parsing", "analyzing", "completed", "failed"]);
}

{
  assert.equal(renderStatusLabel(demo({ video_status: "ready", video_source: "manual_upload" })), "manual ready");
  assert.equal(renderStatusLabel(demo({ latest_render_status: "failed", video_status: "failed" })), "render failed");
  assert.equal(renderStatusLabel(demo({ video_status: null, latest_render_status: null })), "not requested");
}

{
  const unstable = [
    demo({ id: "tie-b", name: "Same Name", original_filename: "b.dem", created_at: "not-a-date" }),
    demo({ id: "tie-a", name: "Same Name", original_filename: "a.dem", created_at: "not-a-date" }),
    demo({ id: "valid", name: "Valid", created_at: "2026-05-05T00:00:00Z" })
  ];

  const result = filterAndSortDemos(unstable, {
    search: "",
    status: "all",
    map: "all",
    sort: "recent",
    order: "desc",
    includeArchived: true
  });

  assert.deepEqual(normalize(result.map((item) => item.id)), ["valid", "tie-a", "tie-b"]);
}

{
  const failedParse = demo({
    id: "failed-parse",
    name: "Failed Parse",
    status: "failed",
    latest_render_status: null,
    video_status: null,
    ingestion: ingestion({
      phase: "failed",
      retryable: true,
      attemptCount: 2,
      failure: {
        errorCode: "PARSER_FAILED",
        message: "Parser timed out while reading demo",
        failedAt: "2026-05-08T00:03:00Z",
        updatedAt: "2026-05-08T00:04:00Z",
        retryable: true,
        attemptCount: 2
      }
    })
  });

  assert.equal(ingestionPhaseLabel(failedParse), "failed");
  assert.equal(parseFailureReason(failedParse), "PARSER_FAILED: Parser timed out while reading demo / retry available / attempt 2");
  assert.equal(canRetryParse(failedParse), true);
}

{
  const result = filterAndSortDemos(
    [
      demo({
        id: "failed-parse",
        name: "Failed Parse",
        status: "failed",
        latest_render_status: null,
        video_status: null,
        ingestion: ingestion({
          phase: "failed",
          retryable: true,
          failure: {
            errorCode: "PARSER_FAILED",
            message: "Parser timed out while reading demo",
            failedAt: "2026-05-08T00:03:00Z",
            updatedAt: "2026-05-08T00:04:00Z",
            retryable: true,
            attemptCount: 1
          }
        })
      })
    ],
    {
      search: "timed retry",
      status: "all",
      map: "all",
      sort: "recent",
      order: "desc",
      includeArchived: false
    }
  );

  assert.deepEqual(normalize(result.map((item) => item.id)), ["failed-parse"]);
}

{
  assert.equal(isRenderActiveStatus("queued"), true);
  assert.equal(isRenderActiveStatus("processing"), true);
  assert.equal(isRenderActiveStatus("rendering"), true);
  assert.equal(isRenderActiveStatus("failed"), false);
  assert.equal(
    countActiveLibraryDemos([
      demo({ id: "uploaded", status: "queued", latest_render_status: null }),
      demo({ id: "rendering", status: "completed", latest_render_status: "processing" }),
      demo({ id: "video-rendering", status: "completed", latest_render_status: null, video_status: "rendering" }),
      demo({ id: "ingestion-active", status: "completed", latest_render_status: null, video_status: null, ingestion: ingestion({ phase: "parsing", active: true }) }),
      demo({ id: "done", status: "completed", latest_render_status: "completed" })
    ]),
    4
  );
}

{
  const state = libraryEmptyState({
    loading: false,
    error: null,
    demos: [],
    visibleDemos: [],
    filters: defaultFilters
  });

  assert.equal(state.kind, "empty");
  assert.equal(state.showMockAction, true);
  assert.equal(state.showUploadAction, true);
  assert.equal(state.showRefreshAction, true);
}

{
  const state = libraryEmptyState({
    loading: false,
    error: null,
    demos: [demo({ id: "archived-only", archived: true })],
    visibleDemos: [],
    filters: defaultFilters
  });

  assert.equal(state.kind, "archived");
  assert.equal(state.showArchivedAction, true);
  assert.equal(state.showClearFiltersAction, false);
}

{
  const state = libraryEmptyState({
    loading: false,
    error: null,
    demos: [demo({ id: "mirage", map_name: "de_mirage" })],
    visibleDemos: [],
    filters: { ...defaultFilters, search: "dust failure", status: "failed" }
  });

  assert.equal(state.kind, "search");
  assert.match(state.message, /dust failure/);
  assert.equal(state.showClearFiltersAction, true);
}

{
  const state = libraryEmptyState({
    loading: false,
    error: "Failed to fetch",
    demos: [],
    visibleDemos: [],
    filters: defaultFilters
  });

  assert.equal(state.kind, "error");
  assert.equal(state.showRefreshAction, true);
  assert.match(state.message, /API is unreachable/);
}

{
  assert.equal(shouldPollLibrary({ loading: false, creating: false, activeJobs: 0 }), false);
  assert.equal(shouldPollLibrary({ loading: true, creating: false, activeJobs: 0 }), true);
  assert.equal(shouldPollLibrary({ loading: false, creating: true, activeJobs: 0 }), true);
  assert.equal(shouldPollLibrary({ loading: false, creating: false, activeJobs: 2 }), true);
}

{
  assert.equal(
    friendlyErrorMessage("Failed to fetch"),
    "API is unreachable. Check the backend, Redis worker, and /diagnostics."
  );
  assert.equal(
    friendlyErrorMessage("GPU worker not connected for render_clip."),
    "GPU worker not connected for render_clip."
  );
}

{
  const items = detailSummaryItems({
    status: demoStatus({
      name: "Dust sample",
      original_filename: "falcons-vs-furia.dem",
      status: "completed",
      map_name: "de_dust2",
      round_count: 24,
      coaching_event_count: 18,
      ingestion: ingestion({ phase: "ready", attemptCount: 1, jobStatus: "completed" })
    }),
    replay: replay({
      mapName: "de_dust2",
      mapMetadata: {
        displayName: "Dust II",
        confidence: "calibrated",
        calibrated: true
      },
      video: {
        status: "failed",
        source: "rendered",
        url: null,
        errorMessage: "GPU worker not connected for render_clip."
      }
    }),
    latestRenderJob: {
      job_type: "render_clip",
      status: "failed",
      error_message: "GPU worker not connected for render_clip."
    }
  });
  const labels = Object.fromEntries(items.map((item) => [item.label, item.value]));

  assert.equal(labels.File, "falcons-vs-furia.dem");
  assert.equal(labels.Map, "Dust II");
  assert.equal(labels.Calibration, "calibrated");
  assert.equal(labels.Rounds, "24");
  assert.equal(labels.Coaching, "18 events");
  assert.equal(labels.Parser, "ready");
  assert.equal(labels.Media, "rendered failed");
  assert.equal(labels.Render, "render_clip failed");
}

{
  const items = detailSummaryItems({
    status: demoStatus({
      status: "failed",
      ingestion: ingestion({
        phase: "failed",
        failure: {
          errorCode: "INVALID_DEMO",
          message: "Invalid or unreadable demo file.",
          failedAt: "2026-05-08T00:03:00Z",
          updatedAt: "2026-05-08T00:04:00Z",
          retryable: true,
          attemptCount: 1
        }
      })
    }),
    replay: null,
    latestRenderJob: null
  });
  const labels = Object.fromEntries(items.map((item) => [item.label, item.value]));

  assert.equal(labels.Parser, "INVALID_DEMO");
  assert.equal(labels.Media, "replay unavailable");
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
    latest_render_status: valueOrDefault(overrides, "latest_render_status", null),
    ingestion: valueOrDefault(overrides, "ingestion", ingestion({ phase: overrides.status === "failed" ? "failed" : "ready" }))
  };
}

function demoStatus(overrides) {
  return {
    id: overrides.id ?? "demo-status",
    owner_id: overrides.owner_id ?? "dev-user",
    name: overrides.name ?? "Demo Status",
    original_filename: overrides.original_filename ?? "demo.dem",
    status: overrides.status ?? "completed",
    archived: overrides.archived ?? false,
    map_name: overrides.map_name ?? "de_dust2",
    round_count: overrides.round_count ?? 12,
    coaching_event_count: overrides.coaching_event_count ?? 3,
    error_message: overrides.error_message ?? null,
    updated_at: overrides.updated_at ?? "2026-05-08T00:00:00Z",
    completed_at: overrides.completed_at ?? null,
    ingestion: valueOrDefault(overrides, "ingestion", ingestion({ phase: overrides.status === "failed" ? "failed" : "ready" }))
  };
}

function replay(overrides) {
  return {
    demoId: overrides.demoId ?? "demo",
    mapName: overrides.mapName ?? "de_dust2",
    mapMetadata: overrides.mapMetadata ?? null,
    tickRate: 64,
    video: {
      status: "pending",
      source: "mock",
      url: null,
      errorMessage: null,
      durationSeconds: 0,
      tickStart: 0,
      tickEnd: 0,
      tickRate: 64,
      timeOriginSeconds: 0,
      ...(overrides.video ?? {})
    },
    rounds: [],
    players: [],
    frames: [],
    events: [],
    generatedAt: "2026-05-08T00:00:00Z"
  };
}

function ingestion(overrides) {
  return {
    phase: overrides.phase ?? "ready",
    active: overrides.active ?? ["uploaded", "parsing", "analyzing"].includes(overrides.phase),
    stale: overrides.stale ?? false,
    retryable: overrides.retryable ?? false,
    attemptCount: overrides.attemptCount ?? 0,
    jobId: overrides.jobId ?? null,
    jobType: overrides.jobType ?? null,
    jobStatus: overrides.jobStatus ?? null,
    hasSourceDemo: overrides.hasSourceDemo ?? false,
    updatedAt: overrides.updatedAt ?? "2026-05-08T00:00:00Z",
    startedAt: overrides.startedAt ?? null,
    finishedAt: overrides.finishedAt ?? null,
    failure: overrides.failure ?? null
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}

function valueOrDefault(object, key, fallback) {
  return Object.prototype.hasOwnProperty.call(object, key) ? object[key] : fallback;
}
