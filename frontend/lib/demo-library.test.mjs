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
  countParsingLibraryDemos,
  countVideoLibraryDemos,
  demoStatusDisplayLabel,
  formatLibraryDate,
  libraryDisplayTitle,
  libraryVideoLabel,
  demoFailureState,
  detailLoadState,
  detailSummaryItems,
  demoLibraryFilterOptions,
  filterAndSortDemos,
  friendlyErrorMessage,
  ingestionPhaseLabel,
  isRenderActiveStatus,
  libraryEmptyState,
  parseFailureAction,
  parseFailureCopy,
  parseFailureReason,
  playbackReadiness,
  processingElapsedLabel,
  processingHeadline,
  processingSteps,
  renderStatusLabel,
  shouldPollLibrary
} = loadTypeScriptModule("./demo-library.ts");

{
  // Parsed real .dem files initially share this mock video placeholder.
  for (const latest_render_status of [null, "completed", "failed"]) {
    assert.equal(playbackReadiness(demo({
      status: "completed",
      video_source: "mock",
      video_status: "ready",
      video_url: null,
      latest_render_status
    })), "none", `mock placeholder with ${latest_render_status} job is tactical-only`);
  }
  for (const latest_render_status of ["queued", "processing", "rendering"]) {
    assert.equal(playbackReadiness(demo({
      video_source: "mock",
      video_status: "ready",
      latest_render_status
    })), "rendering", "an active job remains visible over a mock placeholder");
  }
  for (const video_source of ["rendered", "manual_upload"]) {
    for (const latest_render_status of [null, "completed", "queued", "failed"]) {
      assert.equal(playbackReadiness(demo({
        video_source,
        video_status: "ready",
        latest_render_status
      })), "ready", "saved real video stays available during another job");
    }
  }
  assert.equal(playbackReadiness(demo({
    video_source: null,
    video_status: "ready"
  })), "ready", "legacy video status without a source remains compatible");
  assert.equal(playbackReadiness(demo({
    video_source: null,
    video_status: null,
    latest_render_status: "completed"
  })), "ready", "legacy completed jobs without video metadata remain compatible");
  assert.equal(playbackReadiness(demo({
    video_source: "rendered",
    video_status: "failed",
    latest_render_status: "completed"
  })), "none", "a completed job does not override an explicit video failure");
  assert.equal(playbackReadiness(demo({
    video_source: "rendered",
    video_status: "rendering"
  })), "rendering");
  assert.equal(playbackReadiness(demo({
    status: "parsing",
    video_source: "mock",
    video_status: "ready"
  })), "unavailable", "a mock video placeholder does not make parsing complete");
}

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
    search: "视频生成中",
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
    search: "dust 可以复盘",
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
      search: "意外 重新处理",
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
  // The API is unreachable, so an upload would fail too.
  assert.equal(state.showUploadAction, false);
  assert.equal(state.showMockAction, false);
  assert.match(state.message, /API is unreachable/);
}

{
  // A returning player's rows are on their way: no first-run CTAs meanwhile.
  const state = libraryEmptyState({
    loading: true,
    error: null,
    demos: [],
    visibleDemos: [],
    filters: defaultFilters
  });

  assert.equal(state.kind, "loading");
  for (const flag of ["showMockAction", "showUploadAction", "showRefreshAction", "showClearFiltersAction", "showArchivedAction"]) {
    assert.equal(state[flag], false, flag);
  }
}

{
  // Search reads the labels the rows show, not IDs, timestamps or English tokens.
  const library = [
    demo({ id: "0f3e9a1c-aaaa-4bbb-8ccc-000000000001", name: "Bravo Dust", map_name: "de_dust2", status: "completed" }),
    demo({
      id: "0f3e9a1c-aaaa-4bbb-8ccc-000000000002",
      name: "Broken Upload",
      map_name: "de_mirage",
      status: "failed",
      ingestion: ingestion({
        phase: "failed",
        retryable: false,
        failure: {
          errorCode: "INVALID_DEMO",
          message: "Invalid or unreadable demo file.",
          failedAt: "2026-05-08T00:03:00Z",
          updatedAt: "2026-05-08T00:04:00Z",
          retryable: false,
          attemptCount: 1
        }
      })
    })
  ];
  const search = (query, labels) =>
    normalize(filterAndSortDemos(library, { ...defaultFilters, search: query, sort: "name", order: "asc" }, labels).map((item) => item.name));

  assert.deepEqual(search("处理失败"), ["Broken Upload"]);
  assert.deepEqual(search("失败"), ["Broken Upload"]);
  assert.deepEqual(search("可以复盘"), ["Bravo Dust"]);
  assert.deepEqual(search("文件无法读取"), ["Broken Upload"]);
  assert.deepEqual(search("ready"), []);
  assert.deepEqual(search("2026"), []);
  // "f" is in both UUIDs but in nothing either row shows.
  assert.deepEqual(search("f"), []);
  assert.deepEqual(search("0f3e"), [], "a short ID fragment is not a search");
  assert.deepEqual(search("0f3e9a1c"), ["Bravo Dust", "Broken Upload"]);
  const mapLabel = (map) => ({ de_dust2: "Dust II", de_mirage: "Mirage" })[map] ?? map;
  assert.deepEqual(search("dust ii", { mapLabel }), ["Bravo Dust"]);
}

{
  assert.equal(demoStatusDisplayLabel("completed"), "可以复盘");
  assert.equal(demoStatusDisplayLabel("failed"), "处理失败");
  assert.equal(demoStatusDisplayLabel("queued"), "等待处理");
  assert.equal(libraryVideoLabel(demo({ status: "parsing", video_status: null, latest_render_status: null })), null);
  assert.equal(libraryVideoLabel(demo({ status: "completed", latest_render_status: "rendering" })), "视频生成中");
}

{
  // Only reads and analyses count as "正在处理"; a video job is reported on its own.
  const library = [
    demo({ id: "queued", status: "queued", latest_render_status: null, video_status: null }),
    demo({ id: "reparse", status: "failed", latest_render_status: null, video_status: null, ingestion: ingestion({ active: true }) }),
    demo({ id: "video", status: "completed", latest_render_status: "rendering" }),
    demo({ id: "done", status: "completed", latest_render_status: "completed" })
  ];
  assert.equal(countParsingLibraryDemos(library), 2);
  assert.equal(countVideoLibraryDemos(library), 1);
}

{
  const mapLabel = (map) => (map === "de_dust2" ? "Dust II" : map);
  const upload = { name: "match730_0037.dem", original_filename: "match730_0037.dem", map_name: "de_dust2", round_count: 24 };
  assert.deepEqual(
    { ...libraryDisplayTitle({ ...upload, status: "completed" }, mapLabel) },
    { title: "Dust II，24 回合", filename: "match730_0037.dem", composed: true }
  );
  // Still parsing: nothing better than the file name yet.
  assert.deepEqual(
    { ...libraryDisplayTitle({ ...upload, status: "parsing", round_count: 0 }, mapLabel) },
    { title: "match730_0037.dem", filename: null, composed: false }
  );
  // A name the player chose always wins.
  assert.deepEqual(
    { ...libraryDisplayTitle({ ...upload, name: "决赛 第二图", status: "completed" }, mapLabel) },
    { title: "决赛 第二图", filename: "match730_0037.dem", composed: false }
  );
}

{
  assert.equal(formatLibraryDate("not-a-date"), "—");
  assert.equal(formatLibraryDate(null), "—");
  assert.match(formatLibraryDate("2026-09-25T07:04:00Z"), /^9\/25/);
}

{
  assert.equal(shouldPollLibrary({ loading: false, creating: false, activeJobs: 0 }), false);
  // A slow initial response must finish before polling can supersede its request ID.
  assert.equal(shouldPollLibrary({ loading: true, creating: false, activeJobs: 0 }), false);
  assert.equal(shouldPollLibrary({ loading: true, creating: true, activeJobs: 2 }), false);
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

{
  // A completed demo whose replay artifact went missing must never be told it
  // is "still loading" -- the parse already ended, so nothing will arrive.
  const artifactGone = demoStatus({
    status: "completed",
    ingestion: ingestion({
      phase: "ready",
      retryable: true,
      attemptCount: 1,
      failure: {
        errorCode: "REPLAY_ARTIFACT_MISSING",
        message: "Replay data for this demo is no longer readable. Re-parse the demo to rebuild it.",
        failedAt: null,
        updatedAt: "2026-05-08T00:04:00Z",
        retryable: true,
        attemptCount: 1
      }
    })
  });
  const gone = detailLoadState({ status: artifactGone, statusFailure: null, replayLoadFailed: false });
  assert.equal(gone.kind, "replay_unavailable");
  assert.equal(gone.retryable, true, "the reported reason comes with the action that fixes it");
  assert.equal(gone.next.action, "retry");
  assert.equal(gone.failure.message, "回放数据已失效，重新处理即可恢复。");
  assert.equal(gone.errorCode, "REPLAY_ARTIFACT_MISSING", "the code stays available for the technical details");
  assert.doesNotMatch(JSON.stringify(gone.failure), /Replay data|Re-parse/, "backend English never reaches the copy");

  // Same state, but the source demo is gone too: say why, point at a new upload.
  const unrecoverable = detailLoadState({
    status: demoStatus({
      status: "completed",
      ingestion: ingestion({
        phase: "ready",
        retryable: false,
        failure: { ...artifactGone.ingestion.failure, retryable: false }
      })
    }),
    statusFailure: null,
    replayLoadFailed: false
  });
  assert.equal(unrecoverable.kind, "replay_unavailable");
  assert.equal(unrecoverable.retryable, false);
  assert.equal(unrecoverable.next.action, "reupload");

  const parsing = detailLoadState({
    status: demoStatus({ status: "parsing", ingestion: ingestion({ phase: "parsing", startedAt: "2026-05-08T00:01:00Z" }) }),
    statusFailure: null,
    replayLoadFailed: false
  });
  assert.deepEqual(normalize(parsing), { kind: "processing", step: "parsing", stale: false, startedAt: "2026-05-08T00:01:00Z" },
    "a demo still being ingested is genuinely still processing");
  assert.equal(
    detailLoadState({ status: demoStatus({ status: "queued", ingestion: ingestion({ phase: "uploaded" }) }), statusFailure: null, replayLoadFailed: false }).step,
    "uploaded"
  );
  assert.equal(
    detailLoadState({ status: demoStatus({ status: "analyzing", ingestion: ingestion({ phase: "analyzing", stale: true }) }), statusFailure: null, replayLoadFailed: false }).stale,
    true
  );
  // A re-parse queued on a failed row is processing again, not failed.
  assert.equal(detailLoadState({
    status: demoStatus({ status: "failed", ingestion: ingestion({ phase: "failed", active: true, jobStatus: "queued" }) }),
    statusFailure: null,
    replayLoadFailed: false
  }).step, "uploaded");

  assert.deepEqual(normalize(detailLoadState({ status: null, statusFailure: null, replayLoadFailed: false })), { kind: "connecting" },
    "before the first status lands there is nothing to report");

  const failed = detailLoadState({
    status: demoStatus({
      status: "failed",
      ingestion: ingestion({
        phase: "failed",
        retryable: true,
        failure: {
          errorCode: "PARSER_FAILED",
          message: "Parser timed out while reading demo",
          failedAt: "2026-05-08T00:03:00Z",
          updatedAt: "2026-05-08T00:04:00Z",
          retryable: true,
          attemptCount: 2
        }
      })
    }),
    statusFailure: null,
    replayLoadFailed: false
  });
  assert.equal(failed.kind, "failed");
  assert.equal(failed.retryable, true);
  assert.equal(failed.attemptCount, 2);
  assert.equal(failed.failure.message, "处理时出现意外错误。", "unknown codes fall back to the generic reason");
  assert.equal(failed.next.action, "retry");

  // A completed demo the backend reports as healthy is mid-fetch, not broken:
  // claiming otherwise flashed a false failure on every cold page load.
  assert.deepEqual(normalize(detailLoadState({ status: demoStatus({ status: "completed" }), statusFailure: null, replayLoadFailed: false })),
    { kind: "loading_replay" }, "the replay fetch has not settled yet, so nothing is known to be wrong");
  assert.deepEqual(normalize(detailLoadState({ status: demoStatus({ status: "completed" }), statusFailure: null, replayLoadFailed: true })),
    { kind: "replay_load_failed", retryable: false }, "once the caller's fetch has actually failed, say so");

  // The backend's verdict outranks the caller's: a missing artifact still names
  // the reason and offers the re-parse, not a pointless reload.
  const failedFetchWithReason = detailLoadState({ status: artifactGone, statusFailure: null, replayLoadFailed: true });
  assert.equal(failedFetchWithReason.kind, "replay_unavailable");
  assert.equal(failedFetchWithReason.retryable, true);

  // A status fetch that 404s (deleted, another account's demo, a mistyped link)
  // is final; a dropped connection only is before the first status arrives.
  assert.equal(detailLoadState({ status: null, statusFailure: "not_found", replayLoadFailed: false }).kind, "not_found");
  assert.equal(detailLoadState({ status: artifactGone, statusFailure: "not_found", replayLoadFailed: false }).kind, "not_found");
  assert.equal(detailLoadState({ status: null, statusFailure: "unreachable", replayLoadFailed: false }).kind, "unreachable");
  assert.equal(detailLoadState({ status: artifactGone, statusFailure: "unreachable", replayLoadFailed: false }).kind,
    "replay_unavailable", "a known status keeps its own state while the poll retries");
}

{
  // Files that cannot be fixed by another pass point at a new upload, even when
  // the backend would accept a retry.
  for (const code of ["INVALID_DEMO", "UNSUPPORTED_PARSER_FORMAT", "MISSING_MATCH_METADATA", "MISSING_FRAMES", "PARSE_ABANDONED"]) {
    const copy = parseFailureCopy(code);
    assert.equal(copy.suggestion, "reupload", code);
    assert.equal(parseFailureAction(copy, true).action, "reupload", code);
  }
  for (const code of ["PARSE_TIMED_OUT", "PARSE_OUT_OF_MEMORY", "PARSER_CRASHED", "STORAGE_READ_FAILED", "NORMALIZATION_FAILED", "PARSER_UNEXPECTED", "REPLAY_ARTIFACT_MISSING"]) {
    const copy = parseFailureCopy(code);
    assert.equal(copy.suggestion, "retry", code);
    assert.equal(parseFailureAction(copy, true).action, "retry", code);
    assert.equal(parseFailureAction(copy, false).action, "reupload", `${code} without a usable source file`);
  }
  for (const code of ["INVALID_DEMO", "PARSE_TIMED_OUT", "PARSER_UNEXPECTED", null, undefined, "SOMETHING_NEW"]) {
    assert.doesNotMatch(parseFailureCopy(code).message, /[A-Za-z]{4,}/, `${code} reads as Chinese copy, not a backend string`);
  }

  const invalid = demoFailureState(demoStatus({
    status: "failed",
    ingestion: ingestion({
      phase: "failed",
      retryable: true,
      attemptCount: 1,
      failure: {
        errorCode: "INVALID_DEMO",
        message: "Invalid or unreadable demo file.",
        failedAt: "2026-05-08T00:03:00Z",
        updatedAt: "2026-05-08T00:04:00Z",
        retryable: true,
        attemptCount: 1
      }
    })
  }));
  assert.equal(invalid.failure.message, "文件无法读取，可能不是完整的 CS2 .dem 比赛文件。");
  assert.equal(invalid.next.action, "reupload");
  assert.equal(invalid.retryable, true, "the retry stays available, just not as the suggested fix");
  assert.equal(demoFailureState(demoStatus({ status: "completed" })), null);
  // A legacy failed row without ingestion metadata still gets a reason.
  assert.equal(demoFailureState(demoStatus({ status: "failed", ingestion: null, error_message: "boom" })).failure.message,
    "处理时出现意外错误。");
}

{
  assert.deepEqual(normalize(processingSteps("uploaded")).map((step) => step.state), ["done", "current", "pending"]);
  assert.deepEqual(normalize(processingSteps("parsing")).map((step) => step.state), ["done", "current", "pending"]);
  assert.deepEqual(normalize(processingSteps("analyzing")).map((step) => step.state), ["done", "done", "current"]);
  assert.deepEqual(normalize(processingSteps("parsing")).map((step) => step.label), ["上传完成", "解析比赛", "分析建议"]);
  assert.match(processingHeadline("uploaded"), /排队/);
  assert.match(processingHeadline("parsing"), /^解析中/);
  assert.match(processingHeadline("analyzing"), /^分析中/);

  const started = Date.parse("2026-05-08T00:00:00Z");
  assert.equal(processingElapsedLabel("2026-05-08T00:00:00Z", started + 133_000), "已用时 2:13");
  assert.equal(processingElapsedLabel("2026-05-08T00:00:00Z", started + 5_000), "已用时 0:05");
  assert.equal(processingElapsedLabel(null, started), null, "a queued job has not started");
  assert.equal(processingElapsedLabel("2026-05-08T00:00:00Z", started - 5_000), null, "clock skew hides the timer");
  assert.equal(processingElapsedLabel("not a date", started), null);
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
