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

assert.equal(renderWorkerNotice(offline, queued).label, "视频服务暂时离线");
assert.equal(renderWorkerNotice(offline, queued).detail, "已排队，服务恢复后会自动开始生成。",
  "The job stays durably queued, so the player copy must not imply it was lost");
assert.doesNotMatch(renderWorkerNotice(offline, queued).detail, /渲染器|轮询|分钟/, "Players get no renderer or polling jargon");
assert.match(renderWorkerNotice(offline, queued).operatorDetail, /15 分钟/, "The operator detail names how long it has been silent");
assert.match(renderWorkerNotice(offline, queued).operatorDetail, /任务已保留/);
assert.match(renderWorkerNotice(neverSeen, queued).operatorDetail, /从未连接过/);
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
function panel(renderWorker, extra = {}) {
  return renderToStaticMarkup(React.createElement(RenderOperatorPanel, {
    video: pendingVideo, latestJob: queuedJob, renderWorker, jobCount: 1, refreshing: false, onRefresh() {}, ...extra
  }));
}
assert.match(panel(offline), /<strong[^>]*>排队中，视频服务暂时离线<\/strong>/);
assert.match(panel(offline), /已排队，服务恢复后会自动开始生成。/);
assert.doesNotMatch(panel(offline), /渲染器|15 分钟/, "the renderer's heartbeat stays in the operator view");
assert.match(panel(offline, { devTools: true }), /15 分钟/);
assert.match(panel(offline, { devTools: true }), /任务已保留，渲染器启动后会自动开始/);
assert.match(panel(connected), /<strong[^>]*>排队中<\/strong>/);
assert.match(panel(connected), /等待开始生成/);
assert.match(panel(connected, { devTools: true }), /领取后会自动开始/);
assert.match(panel(null), /<strong[^>]*>排队中<\/strong>/, "An unreachable status endpoint must not report the renderer as offline");
for (const markup of [panel(offline), panel(connected), panel(null)]) {
  assert.doesNotMatch(markup, /check diagnostics for worker heartbeat/,
    "The panel now reports the heartbeat itself instead of pointing elsewhere");
  assert.doesNotMatch(markup, /Queued|render_clip|queued-job/, "players get Chinese state, not job internals");
}
{
  const rendering = { ...queuedJob, status: "rendering", tick_start: 400, tick_end: 1040, tick_rate: 64 };
  const player = panel(connected, { latestJob: rendering });
  assert.doesNotMatch(player, /prepare-job|Tick 范围|400 - 1040/, "operator CLI steps and ticks stay behind dev tools");
  const operator = panel(connected, { latestJob: rendering, devTools: true });
  assert.match(operator, /prepare-job/);
  assert.match(operator, /400 - 1040/);
}

console.log("Render worker liveness copy, fallback-mode gating and operator panel checks passed.");
