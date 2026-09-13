import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("./replay-time.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
});
const module = { exports: {} };
vm.runInNewContext(outputText, { module, exports: module.exports });
const { advanceReplayTick, tickToVideoTime, videoTimeToTick, videoPlaybackState, videoMediaIdentity } = module.exports;
const xelexId = "76561198998266210";
const video = {
  status: "ready", source: "rendered", url: "/demos/demo-1/media/video",
  tickStart: 6363, tickEnd: 7643, tickRate: 64, durationSeconds: 20,
  timeOriginSeconds: 0, povSteamId: xelexId,
  renderJobId: "fb347c00-f7e2-415e-aef4-e1435c07a229"
};

assert.equal(tickToVideoTime(6363, video), 0);
assert.equal(tickToVideoTime(6683, video), 5);
assert.equal(tickToVideoTime(7643, video), 20);
assert.equal(videoTimeToTick(5, video), 6683);
assert.equal(videoTimeToTick(20, video), 7643);
assert.equal(tickToVideoTime(6683, { ...video, timeOriginSeconds: 12.5, durationSeconds: 32.5 }), 17.5);

assert.equal(videoPlaybackState(video, 6362, xelexId), "outside-clip");
assert.equal(videoPlaybackState(video, 6363, xelexId), "active");
assert.equal(videoPlaybackState(video, 7642, xelexId), "active");
assert.equal(videoPlaybackState(video, 7643, xelexId), "outside-clip");
assert.equal(videoPlaybackState(video, 6683, null), "different-player");
assert.equal(videoPlaybackState({ ...video, povSteamId: undefined }, 6683, null), "active");
assert.equal(videoPlaybackState(video, 6683, xelexId, true), "unavailable");
for (const override of [{ url: null }, { status: "queued" }, { tickRate: 0 }, { tickEnd: 6363 }, { timeOriginSeconds: NaN }]) {
  assert.equal(videoPlaybackState({ ...video, ...override }, 6683, xelexId), "unavailable");
}

// The 2D clock lands exactly on the clip start, then resumes from its half-open end.
assert.equal(advanceReplayTick(6360, 6.4, 12000, video, xelexId), 6363);
assert.equal(advanceReplayTick(7643, 6.4, 12000, video, xelexId), 7649.4);
assert.equal(advanceReplayTick(11000, 6.4, 12000, video, xelexId), 11006.4);
assert.equal(advanceReplayTick(11999, 6.4, 12000, video, xelexId), 12000);
assert.equal(advanceReplayTick(6360, 6.4, 12000, video, xelexId, true), 6366.4);
assert.notEqual(videoMediaIdentity(video), videoMediaIdentity({ ...video, renderJobId: "replacement-job" }));
console.log("Replay clip timing and clock handoff checks passed.");
