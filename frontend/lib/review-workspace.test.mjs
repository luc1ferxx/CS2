import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(path, imports = {}) {
  const module = { exports: {} };
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
  });
  vm.runInNewContext(outputText, { module, exports: module.exports, require: (name) => {
    if (imports[name]) return imports[name];
    throw Error(`Unexpected import ${name}`);
  }});
  return module.exports;
}
const clips = load("./render-clips.ts");
const { savedClipAtTick, usesVideoClock, roundClock } = load("./review-workspace.ts", { "@/lib/render-clips": clips });
const makeClip = (id, start, end, player = "xelex") => ({
  job_id: id, demo_id: "match", job_type: "render_clip", status: "completed", pov_steam_id: player, metadata: {},
  video: { status: "ready", source: "rendered", url: `/demos/match/render/jobs/${id}/media/video`,
    povSteamId: player, renderJobId: id, tickStart: start, tickEnd: end, tickRate: 64 }
});
const old = makeClip("old", 640, 1920);
const newer = makeClip("new", 2000, 3280);
const overlapping = makeClip("overlap", 1000, 1800);
const other = makeClip("other", 640, 1920, "another-player");
assert.equal(savedClipAtTick([newer, old], 1200, "xelex", "new"), old,
  "A finding in an older saved clip must reopen that clip, not keep the unrelated latest default");
assert.equal(savedClipAtTick([overlapping, old], 1200, "xelex", "old"), old,
  "An explicit selection wins when multiple clips cover the same moment");
assert.equal(savedClipAtTick([other, old], 1200, "xelex"), old);
assert.equal(savedClipAtTick([old], 1920, "xelex"), null, "Clip end is exclusive: use tactical replay at the end boundary");
assert.equal(savedClipAtTick([{ ...old, status: "rendering" }], 1200, "xelex"), null);
assert.equal(savedClipAtTick([old], 1200, null), null);
assert.equal(savedClipAtTick([old], NaN, "xelex"), null);
assert.equal(usesVideoClock("map", "active"), false, "Explicit tactical mode cannot wait on an unmounted video clock");
assert.equal(usesVideoClock("auto", "active"), true);
for (const state of ["unavailable", "outside-clip", "different-player"]) assert.equal(usesVideoClock("auto", state), false);
assert.equal(roundClock(14299, 12500, 64), "00:28");
assert.equal(roundClock(12500, 12500, 64), "00:00");
console.log("Workspace clip selection, POV isolation, half-open coverage and map/video clock checks passed.");
