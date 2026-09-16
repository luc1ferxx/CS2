import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const nodeRequire = createRequire(import.meta.url);
function load(path, imports = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
    fileName: path
  });
  const module = { exports: {} };
  vm.runInNewContext(outputText, {
    module, exports: module.exports,
    require(name) {
      if (imports[name]) return imports[name];
      if (name === "react/jsx-runtime" || name === "react" || name === "lucide-react") return nodeRequire(name);
      throw new Error(`Unexpected import ${name}`);
    }
  });
  return module.exports;
}

const helpers = load("./render-worker.ts");
const { renderWorkerOffline, renderWorkerNotice, renderWorkerLastSeenText, renderWorkerOperatorHint } = helpers;

const connected = { mode: "external", required: true, connected: true, status: "connected", last_seen_at: "2026-09-16T12:00:00+00:00", age_seconds: 3, busy_rendering: false };
const rendering = { ...connected, status: "rendering", age_seconds: 420, busy_rendering: true };
const offline = { ...connected, connected: false, status: "offline", age_seconds: 900, busy_rendering: false };
const neverSeen = { mode: "external", required: true, connected: false, status: "never_seen", last_seen_at: null, age_seconds: null, busy_rendering: false };
const fallback = { mode: "fallback", required: false, connected: false, status: "never_seen", last_seen_at: null, age_seconds: null, busy_rendering: false };
const queued = { status: "queued" };

assert.equal(renderWorkerOffline(offline), true);
assert.equal(renderWorkerOffline(neverSeen), true);
assert.equal(renderWorkerOffline(connected), false);
assert.equal(renderWorkerOffline(rendering), false, "A worker busy inside a long render is still connected");
assert.equal(renderWorkerOffline(fallback), false,
  "The fallback mode has no external renderer to miss; it fails jobs itself with its own error");
assert.equal(renderWorkerOffline(null), false, "An unknown status must not accuse a healthy renderer");
assert.equal(renderWorkerOffline(undefined), false);

assert.match(renderWorkerNotice(offline, queued).label, /渲染器未连接/);
assert.match(renderWorkerNotice(offline, queued).detail, /15 分钟/, "The detail names how long it has been silent");
assert.match(renderWorkerNotice(offline, queued).detail, /任务已保留/,
  "The job stays durably queued, so the copy must not imply it was lost");
assert.match(renderWorkerNotice(neverSeen, queued).detail, /从未连接过/);
assert.equal(renderWorkerNotice(fallback, queued), null);
assert.equal(renderWorkerNotice(connected, queued), null);
assert.equal(renderWorkerNotice(rendering, queued), null);
assert.equal(renderWorkerNotice(offline, null), null, "No job means nothing is waiting on the renderer");
assert.equal(renderWorkerNotice(offline, { status: "rendering" }), null);
assert.equal(renderWorkerNotice(offline, { status: "completed" }), null);
assert.equal(renderWorkerNotice(offline, { status: "failed" }), null,
  "A failed job already carries its own error; do not overwrite it with a liveness notice");

assert.equal(renderWorkerLastSeenText(null), "");
assert.match(renderWorkerLastSeenText({ ...offline, age_seconds: 45 }), /45 秒/);
assert.match(renderWorkerLastSeenText({ ...offline, age_seconds: 3600 }), /1 小时/);
assert.match(renderWorkerLastSeenText({ ...offline, age_seconds: null }), /当前未连接/,
  "A timestamp that fails to parse still reads as offline, not as never connected");

assert.equal(renderWorkerOperatorHint(connected), null);
assert.equal(renderWorkerOperatorHint(fallback), null);
assert.match(renderWorkerOperatorHint(offline), /last polled 15m ago/);
assert.match(renderWorkerOperatorHint(neverSeen), /has never polled/);
assert.match(renderWorkerOperatorHint(offline), /stays queued/);

const { RenderOperatorPanel } = load("../components/replay/RenderOperatorPanel.tsx", {
  "@/lib/render-worker": helpers,
  "@/lib/demo-library": { friendlyErrorMessage: (message) => message }
});
const pendingVideo = { status: "pending", source: "mock", url: null, tickStart: 0, tickEnd: 0, tickRate: 64, durationSeconds: 0, timeOriginSeconds: 0, povSteamId: null, renderJobId: null };
const queuedJob = { job_id: "queued-job-1", demo_id: "demo-1", job_type: "render_clip", status: "queued", source: "rendered", metadata: {}, created_at: "2026-09-16T12:00:00Z" };
function panel(renderWorker) {
  return renderToStaticMarkup(React.createElement(RenderOperatorPanel, {
    video: pendingVideo, latestJob: queuedJob, renderWorker, jobCount: 1, refreshing: false, onRefresh() {}
  }));
}
assert.match(panel(offline), /Queued, no render worker/);
assert.match(panel(offline), /last polled 15m ago/);
assert.match(panel(connected), /<strong>Queued<\/strong>/);
assert.match(panel(connected), /starts on its own once one polls/);
assert.match(panel(null), /<strong>Queued<\/strong>/, "An unreachable status endpoint must not report the renderer as offline");
for (const markup of [panel(offline), panel(connected), panel(null)]) {
  assert.doesNotMatch(markup, /check diagnostics for worker heartbeat/,
    "The panel now reports the heartbeat itself instead of pointing elsewhere");
}

console.log("Render worker liveness copy, fallback-mode gating and operator panel checks passed.");
