import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function loadHelper(path) {
  const module = { exports: {} };
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
  });
  vm.runInNewContext(outputText, { module, exports: module.exports });
  return module.exports;
}

// Exercise the page's actual event handler without replacing the browser-owned player lifecycle.
const source = ts.createSourceFile("page.tsx", readFileSync(
  new URL("../app/demos/[demoId]/page.tsx", import.meta.url), "utf8"
), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
function findCallback(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === "playSavedClip") {
    callback = node.initializer.arguments[0].getText(source);
  }
  ts.forEachChild(node, findCallback);
}
findCallback(source);
assert.ok(callback, "Saved clip playback handler must remain wired in Demo Detail");
const handlerCode = ts.transpileModule(`const playSavedClip = ${callback}; playSavedClip;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020 }
}).outputText;
const { playableClipVideo } = loadHelper("./render-clips.ts");
const { videoMediaIdentity, tickToVideoTime } = loadHelper("./replay-time.ts");
const player = "76561198998266210";
const video = {
  status: "ready", source: "rendered", url: "/demos/demo/render/jobs/saved/media/video",
  renderJobId: "saved", povSteamId: player, tickStart: 5403, tickEnd: 7963, tickRate: 64,
  durationSeconds: 40, timeOriginSeconds: 0
};
const job = { demo_id: "demo", job_id: "saved", job_type: "render_clip", status: "completed",
  pov_steam_id: player, video, metadata: {} };

function playbackHarness(currentVideo = video, mounted = true) {
  const state = { videoTime: 20, tick: 6683, playing: false, seeks: [] };
  const context = {
    demoId: "demo", replay: { video: currentVideo, players: [{ id: player }] },
    playableClipVideo, videoMediaIdentity,
    setSelectedClip(value) { state.selection = value; },
    setViewMode() {}, selectPlayer() {}, setUnavailableVideoIdentity() {},
    setDetectedVideoDuration() {}, revealStage() {},
    updateCoordinateFromTick(tick) { state.tick = tick; },
    seek(tick) {
      state.seeks.push(tick);
      if (mounted) state.videoTime = tickToVideoTime(tick, currentVideo);
      state.tick = tick;
    },
    setPlaying(playing) { state.playing = playing; state.timeWhenPlayRequested = state.videoTime; }
  };
  return { state, play: vm.runInNewContext(handlerCode, context) };
}

{
  const { state, play } = playbackHarness();
  play(job);
  assert.equal(state.videoTime, 0, "Rewatching a mounted clip paused at 20 seconds must rewind its media clock");
  assert.equal(state.tick, 5403);
  assert.equal(state.timeWhenPlayRequested, 0, "Rewind must happen before playback resumes");
  assert.equal(state.playing, true);
}
{
  const { state, play } = playbackHarness({ ...video });
  play(job, 5723);
  assert.equal(state.videoTime, 5, "Watching a cached finding must seek to its requested time in reused media");
  assert.deepEqual(state.seeks, [5723], "Equivalent video objects share the same mounted media identity");
}
{
  const { state, play } = playbackHarness();
  play(job, video.tickEnd);
  assert.equal(state.videoTime, 0, "The exclusive clip end falls back to replaying from the start");
}
{
  const previousVideo = { ...video, renderJobId: "older", tickStart: 5000, tickEnd: 7560 };
  const { state, play } = playbackHarness(previousVideo);
  play(job, 5723);
  assert.deepEqual(state.seeks, [], "Changing clips must not seek the previous element using its old calibration");
  assert.equal(state.videoTime, 20);
  assert.equal(state.tick, 5723, "New media receives the requested shared tick when mounted");
  assert.equal(state.selection.video.renderJobId, "saved");
}
{
  const { state, play } = playbackHarness(video, false);
  play(job);
  assert.equal(state.tick, 5403, "Opening from tactical replay still initializes the new element at clip start");
  assert.equal(state.playing, true);
}
{
  const { state, play } = playbackHarness();
  play({ ...job, status: "queued", video: null });
  assert.equal(state.playing, false);
  assert.deepEqual(state.seeks, []);
}
console.log("Mounted saved-clip restart, cached seek, media replacement and tactical handoff passed.");
