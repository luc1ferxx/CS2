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

const helpers = load("./render-clips.ts");
const { playableClipVideo, matchingClipJob, clipRequestAction, clipsForPlayer, buildEventClipRequest, buildTickClipRequest, retainSelectedClip, reviewVideo } = helpers;
const xelex = "76561198998266210";
const request = { playerId: xelex, tickStart: 5000, tickEnd: 7560, tickRate: 64, renderPreset: "event_clip_v1" };
const ready = {
  job_id: "saved-1", demo_id: "demo-1", job_type: "render_clip", status: "completed", source: "rendered",
  tick_start: 5000, tick_end: 7560, tick_rate: 64, pov_steam_id: xelex, round_number: 1, render_preset: "event_clip_v1",
  metadata: { eventId: "old-event" }, created_at: "2026-09-07T12:00:00Z",
  video: {
    status: "ready", source: "rendered", url: "/demos/demo-1/render/jobs/saved-1/media/video",
    tickStart: 5000, tickEnd: 7560, tickRate: 64, durationSeconds: 40, timeOriginSeconds: 0,
    povSteamId: xelex, renderJobId: "saved-1"
  }
};
const queued = { ...ready, job_id: "queued-2", status: "queued", video: null };
const failed = { ...ready, job_id: "failed-3", status: "failed", video: null, error_message: "GPU worker not connected" };
const other = { ...ready, job_id: "other-player", pov_steam_id: "another-player" };
const legacy = { ...ready, job_id: "old-job", video: undefined };

assert.equal(playableClipVideo(ready), ready.video);
assert.equal(playableClipVideo(queued), null);
assert.equal(playableClipVideo(legacy), null);
assert.equal(playableClipVideo(other), null, "A mismatched POV must never become a playable clip");
assert.equal(playableClipVideo({ ...ready, video: { ...ready.video, renderJobId: "someone-else" } }), null);
assert.equal(playableClipVideo({ ...ready, video: { ...ready.video, tickRate: 0 } }), null);
assert.equal(playableClipVideo({ ...ready, video: { ...ready.video, url: "/demos/demo-1/media/video" } }), null,
  "A completed job must provide its own stable media reference");
assert.equal(matchingClipJob([failed, queued, ready], { ...request, eventId: "new-event" }), ready,
  "A saved clip is reusable across event IDs and wins over newer failures/queued duplicates");
assert.equal(matchingClipJob([failed, queued], request), queued);
assert.equal(matchingClipJob([failed], request), failed, "Failed-only history keeps the generate action available");
for (const change of [{ playerId: "another-player" }, { tickStart: 4999 }, { tickEnd: 7561 }, { tickRate: 128 }, { renderPreset: "selected_tick_v1" }]) {
  assert.equal(matchingClipJob([ready], { ...request, ...change }), null);
}
assert.equal(matchingClipJob([ready], { ...request, playerId: undefined }), null);
assert.deepEqual(Array.from(clipsForPlayer([ready, queued, failed, other], xelex), (job) => job.job_id), ["saved-1", "queued-2", "failed-3"]);
assert.equal(clipsForPlayer([ready], null).length, 0);
assert.equal(clipsForPlayer([{ ...ready, job_type: "mock_render" }], xelex).length, 0);
assert.equal(matchingClipJob([{
  ...ready, tick_start: null, tick_end: null, tick_rate: null, pov_steam_id: null, render_preset: null,
  metadata: { povSteamId: xelex, tickStart: 5000, tickEnd: 7560, tickRate: 64, renderPreset: "event_clip_v1" }
}], request)?.job_id, ready.job_id, "Older jobs can use safe metadata fallback");

// Which call the 生成这一刻的视频 button makes, given whatever job the request
// already matched. A failed clip is requeued, so the history never grows a
// second row for footage the user asked for once.
assert.equal(clipRequestAction(failed), "retry");
assert.equal(clipRequestAction(ready), "play");
assert.equal(clipRequestAction(null), "create");
for (const status of ["queued", "rendering", "processing"]) {
  assert.equal(clipRequestAction({ ...queued, status }), "wait");
}
assert.equal(clipRequestAction(legacy), "create",
  "A completed job with no usable artifact has nothing to requeue, so it renders afresh");
assert.equal(clipRequestAction(matchingClipJob([failed], request)), "retry");
assert.equal(clipRequestAction(matchingClipJob([failed, ready], request)), "play",
  "A playable clip outranks an older failure for the same footage");

const rounds = [{ roundNumber: 1, startTick: 4800, freezeEndTick: 4900, endTick: 10000 }];
const replay = { tickRate: 64, video: ready.video, rounds };
const eventRequest = buildEventClipRequest(replay, { id: "finding", player_id: xelex, tick_start: 6280, round_number: 1 });
assert.equal(eventRequest.tickStart, 5000);
assert.equal(eventRequest.tickEnd, 7560);
assert.equal(matchingClipJob([ready], eventRequest), ready);
const roundStartRequest = buildTickClipRequest(replay, 4800, 1, xelex);
assert.equal(roundStartRequest.tickStart, 4800);
assert.equal(roundStartRequest.tickEnd, 6080);
assert.equal(roundStartRequest.renderPreset, "selected_tick_v1");

// Open without a video, complete A, then generate/complete B while reviewing A.
const emptyVideo = { ...ready.video, status: "pending", url: null, renderJobId: null, povSteamId: null };
const defaultA = { ...ready.video, url: "/demos/demo-1/media/video" };
let selection = retainSelectedClip(null, [], emptyVideo, "demo-1");
assert.equal(selection, null);
selection = retainSelectedClip(selection, [{ ...ready, status: "rendering", video: null }], defaultA, "demo-1");
assert.equal(selection, null, "A status response arriving before bound media must not pin the mutable default URL");
assert.equal(reviewVideo(defaultA, [], selection, "demo-1").url, null);
selection = retainSelectedClip(selection, [ready], defaultA, "demo-1");
assert.equal(selection.video.url, ready.video.url, "The first completed clip is pinned to its job URL");
const second = { ...ready, job_id: "saved-2", tick_start: 8000, tick_end: 10560,
  video: { ...ready.video, renderJobId: "saved-2", tickStart: 8000, tickEnd: 10560,
    url: "/demos/demo-1/render/jobs/saved-2/media/video" } };
const defaultB = { ...second.video, url: "/demos/demo-1/media/video" };
const firstSelection = selection;
selection = retainSelectedClip(selection, [ready], reviewVideo(defaultA, [ready], selection, "demo-1"), "demo-1");
assert.equal(selection, firstSelection, "Starting B preserves A's selected artifact");
selection = retainSelectedClip(selection, [{ ...second, status: "queued", video: null }, ready], defaultA, "demo-1");
assert.equal(selection, firstSelection);
selection = retainSelectedClip(selection, [second, ready], defaultB, "demo-1");
assert.equal(selection, firstSelection, "B completing cannot replace A's media or time coordinates");
assert.equal(reviewVideo(defaultB, [second, ready], selection, "demo-1"), ready.video);
assert.equal(retainSelectedClip(selection, [second], defaultB, "demo-1"), firstSelection,
  "A later or stale jobs response cannot dislodge a previously trusted immutable selection");
assert.equal(retainSelectedClip({ demoId: "demo-1", video: defaultA }, [second, ready], defaultB, "demo-1").video, ready.video,
  "Legacy mutable snapshots are upgraded using the matching job, never the latest job");
assert.equal(retainSelectedClip(null, [ready], { ...defaultA, povSteamId: "another-player" }, "demo-1"), null);
assert.equal(reviewVideo(defaultB, [second, ready], { demoId: "other-demo", video: ready.video }, "demo-1"), second.video);
const manual = { ...defaultA, source: "manual_upload", renderJobId: null, povSteamId: null };
assert.equal(retainSelectedClip(null, [], manual, "demo-1"), null, "Manual default URLs stay derived instead of becoming immutable selections");
assert.equal(reviewVideo(manual, [], null, "demo-1"), manual);
assert.equal(retainSelectedClip({ demoId: "demo-1", video: manual }, [ready], defaultA, "demo-1").video, ready.video);

const { ClipLibrary } = load("../components/replay/ClipLibrary.tsx", {
  "@/lib/render-clips": helpers, "@/lib/demo-library": { friendlyErrorMessage: (message) => message }
});
const markup = renderToStaticMarkup(React.createElement(ClipLibrary, {
  jobs: [ready, queued, failed, legacy], playerName: "xelex", rounds, selectedJobId: ready.job_id, onPlay() {}
}));
assert.match(markup, /1 段可观看/);
assert.match(markup, /aria-label="Play xelex clip at tick 5000"/);
assert.match(markup, /aria-pressed="true"/);
assert.match(markup, /视频不可用/);
assert.match(markup, /<details class="clip-library-history">/);
assert.doesNotMatch(markup, /<details[^>]*open=/);
assert.match(markup, /GPU worker not connected/);
assert.equal((markup.match(/<button/g) ?? []).length, 1, "Only validated ready artifacts get a play action");
const { CoachingEventCard } = load("../components/coaching/CoachingEventCard.tsx", {
  "@/lib/render-clips": helpers,
  "@/lib/coaching-copy": load("./coaching-copy.ts"),
  "@/lib/demo-library": { isRenderActiveStatus: (status) => ["queued", "rendering", "processing"].includes(status) }
});
function card(job) {
  return renderToStaticMarkup(React.createElement(CoachingEventCard, {
    reviewEvent: { event: { id: "finding", title: "Test", severity: "high", structured_context_json: {}, tick_start: 6280 }, evidence: [], involvedPlayers: [] },
    active: false, inspected: true, renderJob: job, clipRequesting: false,
    onToggleInspect() {}, onSeek() {}, onGenerateClip() {}
  }));
}
assert.match(card(ready), /观看视频/);
assert.match(card(queued), /disabled=""[^>]*>.*等待生成/);
assert.match(card(failed), /重试/);
console.log("Saved clip POV isolation, reuse, range, legacy and UI state checks passed.");
