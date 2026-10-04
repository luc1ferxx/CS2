import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const directory = dirname(fileURLToPath(import.meta.url));
const loaded = new Map();

function load(relativePath) {
  const filename = resolve(directory, relativePath);
  if (loaded.has(filename)) return loaded.get(filename);
  const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename
  });
  const module = { exports: {} };
  loaded.set(filename, module.exports);
  vm.runInNewContext(outputText, {
    exports: module.exports, module,
    require(specifier) {
      if (specifier.startsWith("@/types/")) return {};
      if (specifier === "@/lib/map-config") return load("./map-config.ts");
      if (specifier === "@/lib/match-stats") return load("./match-stats.ts");
      if (specifier === "@/lib/replay-time") return load("./replay-time.ts");
      if (specifier === "@/lib/player-inputs") return load("./player-inputs.ts");
      if (specifier === "@/lib/utility") return load("./utility.ts");
      throw new Error(`Unexpected import ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const analysisLib = load("./throw-analysis.ts");
const utilityLib = load("./utility.ts");
const {
  FULL_VIEW_BOX, MOVING_SPEED, THROW_BUTTON_LABELS, THROW_WINDOW_SECONDS,
  analyzeThrow, formatRelativeSeconds, setposCommand, throwOriginOf, throwerPositionAt
} = analysisLib;
const round2 = (value) => Math.round(value * 100) / 100;
const plain = (value) => JSON.parse(JSON.stringify(value));
// Results come from another realm (vm), so compare their JSON shape.
const same = (actual, expected, message) => assert.deepEqual(plain(actual), plain(expected), message);

const XERTION = "76561198000000101";
const MATE = "76561198000000102";
const MIRAGE_BOUNDS = { type: "bounds", minX: -3400, maxX: 1720, minY: -3220, maxY: 1880 };
const XERTION_ORIGIN = { x: 1376.2, y: -119.3, z: -129.3, pitch: -20.33, yaw: 163.28, speed: 120, airborne: false };

// xertioN's round-10 smoke on Mirage (spirit vs mouz map 2): he holds attack, adds A, lets go of
// attack at 77301 still holding A, and stops at 77340.
function smoke(overrides = {}) {
  return {
    id: "utility-smoke-412-77300", type: "smoke", throwerId: XERTION, throwerName: "xertioN", throwerSide: "T",
    roundNumber: 10, throwTick: 77300, detonateTick: 77420, endTick: 78572,
    points: [
      { tick: 77300, x: 93.3, y: 39.2, z: -70 },
      { tick: 77330, x: 80, y: 44, z: -10 },
      { tick: 77360, x: 68, y: 49, z: -40 },
      { tick: 77420, x: 60, y: 52, z: -150 }
    ],
    throwOrigin: { ...XERTION_ORIGIN },
    ...overrides
  };
}

function frame(tick, players) {
  return { tick, timeSeconds: tick / 64, roundNumber: 10, players, bombState: { status: "unknown" } };
}

function framePlayer(id, x, y, alive = true) {
  return { id, name: id === XERTION ? "xertioN" : "mate", side: "T", x, y, alive, hp: alive ? 100 : 0, hasBomb: false };
}

// Frames every 16 ticks; the window is [77237, 77365].
function frames({ diesAt = null } = {}) {
  const list = [];
  for (let tick = 77216; tick <= 77376; tick += 16) {
    const step = (tick - 77216) / 16;
    const alive = diesAt === null || tick < diesAt;
    list.push(frame(tick, [framePlayer(MATE, 50, 50), framePlayer(XERTION, round2(95 - step * 0.2), round2(37 + step * 0.4), alive)]));
  }
  return list;
}

function replay(overrides = {}) {
  return {
    demoId: "demo-s17", contractVersion: "replay_contract_v4", mapName: "de_mirage",
    mapMetadata: { mapName: "de_mirage", displayName: "Mirage", radarImagePath: null, calibrated: true, confidence: "calibrated",
      attribution: "", source: null, transform: MIRAGE_BOUNDS },
    tickRate: 64,
    video: {},
    rounds: [
      { roundNumber: 9, startTick: 68000, freezeEndTick: 69280, endTick: 72000, winnerSide: "T" },
      { roundNumber: 10, startTick: 73000, freezeEndTick: 74280, endTick: 80000, winnerSide: "CT" }
    ],
    players: [],
    frames: frames(),
    events: [],
    utility: [smoke()],
    inputs: { [XERTION]: [[77242, 1], [77248, 513], [77301, 512], [77340, 0]] },
    generatedAt: "2026-10-03T00:00:00Z",
    ...overrides
  };
}

// --- constants ---
same(THROW_BUTTON_LABELS, { left: "左键扔", right: "右键扔", both: "左右键一起扔" });
assert.equal(THROW_WINDOW_SECONDS, 1);
assert.equal(MOVING_SPEED, 40);
same(FULL_VIEW_BOX, { x: 0, y: 0, size: 100 });

// --- the xertioN fixture, pinned field by field ---
{
  const data = replay();
  const analysis = analyzeThrow(data, data.utility[0]);
  assert.equal(analysis.utilityId, "utility-smoke-412-77300");
  assert.equal(analysis.throwerId, XERTION);
  assert.equal(analysis.releaseTick, 77301, "attack goes up at 77301");
  assert.equal(analysis.windowStart, 77237);
  assert.equal(analysis.windowEnd, 77365);
  assert.equal(analysis.releaseMask, 513, "attack + A just before release");
  assert.equal(analysis.button, "left");
  assert.equal(analysis.style, "走投");
  assert.equal(analysis.moving, true);
  assert.equal(analysis.speed, 120);
  same(analysis.heldMoveKeys, ["A"]);
  assert.equal(analysis.command, "setpos 1376.2 -119.3 -129.3; setang -20.33 163.28 0");
  assert.equal(analysis.hint, "边走边扔（按着 A）：站在出手点不动扔，落点会偏");
  // Frames 77248..77360 in the window, cut at its ends (77237, 77365) from the frames either side; the thrower only.
  same(analysis.throwerPath.map((point) => point.tick), [77237, 77248, 77264, 77280, 77296, 77312, 77328, 77344, 77360, 77365]);
  same(analysis.throwerPath[0], { tick: 77237, x: 94.74, y: 37.53 });
  same(analysis.throwerPath[1], { tick: 77248, x: 94.6, y: 37.8 });
  // Flight 60..93.3 x 39.2..52, the smoke (144 u = 2.82 % on Mirage) at 60,52 and the path up to 94.74:
  // x 57.18..94.74 is the larger span, padded by 12 % a side, square around the centre.
  same(analysis.bounds, { x: 52.67, y: 22.89, size: 46.57 });
  assert.ok(analysis.bounds.x >= 0 && analysis.bounds.x + analysis.bounds.size <= 100);
  assert.ok(analysis.bounds.y >= 0 && analysis.bounds.y + analysis.bounds.size <= 100);
}

// --- the pose: carried through replayUtility() (which drops unknown fields) from the raw entry ---
{
  const data = replay();
  const sanitized = utilityLib.replayUtility(data)[0];
  assert.equal(sanitized.id, "utility-smoke-412-77300");
  const analysis = analyzeThrow(data, sanitized);
  assert.equal(analysis.command, "setpos 1376.2 -119.3 -129.3; setang -20.33 163.28 0");
  assert.equal(analysis.speed, 120);
  same(throwOriginOf(data, { id: sanitized.id }), XERTION_ORIGIN);
}

// --- a pose with a non-finite field is dropped whole; a negative speed or a non-boolean airborne alone ---
{
  const broken = replay({ utility: [smoke({ throwOrigin: { ...XERTION_ORIGIN, yaw: Number.NaN } })] });
  assert.equal(analyzeThrow(broken, broken.utility[0]).command, null);
  assert.equal(analyzeThrow(broken, broken.utility[0]).speed, null);
  const partial = replay({ utility: [smoke({ throwOrigin: { x: 1, y: 2, z: 3, pitch: 0, yaw: 0, speed: -5, airborne: "yes" } })] });
  same(throwOriginOf(partial, partial.utility[0]), { x: 1, y: 2, z: 3, pitch: 0, yaw: 0 });
  const missing = replay({ utility: [smoke({ throwOrigin: undefined })] });
  assert.equal(analyzeThrow(missing, missing.utility[0]).command, null);
}

// --- no key track: everything key-based is unknown, the speed still says "moving" ---
{
  const data = replay({ inputs: {} });
  const analysis = analyzeThrow(data, data.utility[0]);
  assert.equal(analysis.releaseTick, 77300, "no edge to find: the throw tick");
  assert.equal(analysis.releaseMask, null);
  assert.equal(analysis.style, null);
  assert.equal(analysis.button, null);
  same(analysis.heldMoveKeys, []);
  assert.equal(analysis.moving, true);
  assert.equal(analysis.hint, "出手时还在移动：站在出手点不动扔，落点会偏");
  const slow = replay({ inputs: {}, utility: [smoke({ throwOrigin: { ...XERTION_ORIGIN, speed: 12 } })] });
  assert.equal(analyzeThrow(slow, slow.utility[0]).moving, false);
  assert.equal(analyzeThrow(slow, slow.utility[0]).hint, null);
  const unknown = replay({ inputs: {}, utility: [smoke({ throwOrigin: undefined })] });
  assert.equal(analyzeThrow(unknown, unknown.utility[0]).moving, null);
  assert.equal(analyzeThrow(unknown, unknown.utility[0]).hint, null);
  // A track that starts after the release counts as no data too.
  const late = replay({ inputs: { [XERTION]: [[77400, 0]] } });
  assert.equal(analyzeThrow(late, late.utility[0]).releaseMask, null);
}

// --- the release: the throw button's falling edge closest to the throw tick ---
function releaseFor(track, throwTick = 77300) {
  const data = replay({ inputs: { [XERTION]: track }, utility: [smoke({ throwTick })] });
  return analyzeThrow(data, data.utility[0]);
}
{
  // Two edges 4 ticks either side: the earlier wins the tie.
  assert.equal(releaseFor([[77200, 1], [77296, 0], [77300, 1], [77304, 0]]).releaseTick, 77296);
  // The closer one wins otherwise.
  assert.equal(releaseFor([[77200, 1], [77290, 0], [77300, 1], [77303, 0]]).releaseTick, 77303);
  // attack2 (right click) counts the same, and so does letting go of both.
  assert.equal(releaseFor([[77200, 2048], [77298, 0]]).releaseTick, 77298);
  assert.equal(releaseFor([[77200, 2049], [77299, 8]]).releaseTick, 77299);
  // Up to 8 ticks after the throw tick; 9 is too late.
  assert.equal(releaseFor([[77200, 1], [77308, 0]]).releaseTick, 77308);
  assert.equal(releaseFor([[77200, 1], [77309, 0]]).releaseTick, 77300);
  // Up to 1 s before; an older edge is ignored.
  assert.equal(releaseFor([[77100, 1], [77236, 0]]).releaseTick, 77236);
  assert.equal(releaseFor([[77100, 1], [77235, 0]]).releaseTick, 77300);
  // Swapping attack for attack2 is not a release; the first point has no "before".
  assert.equal(releaseFor([[77200, 1], [77290, 2048], [77320, 2048]]).releaseTick, 77300);
  assert.equal(releaseFor([[77299, 0]]).releaseTick, 77300);
  // The mask is read the tick before the release.
  assert.equal(releaseFor([[77200, 1], [77296, 9], [77298, 8]]).releaseMask, 9);
}

// --- button, style priority and hints ---
function styleFor(maskBefore, { origin = { ...XERTION_ORIGIN, speed: undefined, airborne: undefined }, extra = [] } = {}) {
  const track = [[77100, 0], ...extra, [77250, maskBefore], [77301, maskBefore & ~(1 | 2048)]].sort((a, b) => a[0] - b[0]);
  const data = replay({ inputs: { [XERTION]: track }, utility: [smoke({ throwOrigin: origin })] });
  return analyzeThrow(data, data.utility[0]);
}
{
  assert.equal(styleFor(1).button, "left");
  assert.equal(styleFor(2048).button, "right");
  assert.equal(styleFor(1 | 2048).button, "both");
  // Standing still, keys only (no speed): 站投, no hint.
  const stand = styleFor(1);
  assert.equal(stand.style, "站投");
  assert.equal(stand.moving, false);
  assert.equal(stand.hint, null);
  // Moving keys without a speed: 走投, keys in W A S D order.
  const walk = styleFor(1 | 8 | 1024);
  assert.equal(walk.style, "走投");
  same(walk.heldMoveKeys, ["W", "D"]);
  assert.equal(walk.hint, "边走边扔（按着 W、D）：站在出手点不动扔，落点会偏");
  // The speed decides over the keys when it is known.
  const coasting = styleFor(1, { origin: { ...XERTION_ORIGIN, speed: 90 } });
  assert.equal(coasting.style, "走投");
  same(coasting.heldMoveKeys, []);
  assert.equal(coasting.hint, "出手时还在移动：站在出手点不动扔，落点会偏");
  const stopped = styleFor(1 | 512, { origin: { ...XERTION_ORIGIN, speed: 20 } });
  assert.equal(stopped.style, "站投");
  assert.equal(stopped.moving, false);
  // Duck: 蹲投 (over moving).
  assert.equal(styleFor(1 | 4 | 8).style, "蹲投");
  assert.equal(styleFor(1 | 4).hint, null);
  // Jump held at release: 跳投 (over duck = 跳蹲投, over moving).
  const jump = styleFor(1 | 2 | 8);
  assert.equal(jump.style, "跳投");
  assert.equal(jump.hint, "跑跳投（按着 W）：出手时还在移动，原地跳投落点会偏", "120 u/s: still moving");
  const standingJump = styleFor(1 | 2, { origin: { ...XERTION_ORIGIN, speed: 5 } });
  assert.equal(standingJump.hint, "跳投：练习时用跳投绑定，不然每次出手高度不一样");
  assert.equal(styleFor(1 | 2 | 4).style, "跳蹲投");
  assert.equal(styleFor(1 | 2 | 4, { origin: { ...XERTION_ORIGIN, speed: 5 } }).hint, "跳投：练习时用跳投绑定，不然每次出手高度不一样");
  // A jump press within 0.25 s (16 ticks) before the release counts, an older one does not.
  assert.equal(styleFor(1, { extra: [[77286, 3], [77290, 1]] }).style, "跳投");
  assert.equal(styleFor(1, { extra: [[77283, 3], [77284, 1]] }).style, "站投");
  // In the air per the pose.
  assert.equal(styleFor(1, { origin: { ...XERTION_ORIGIN, speed: 5, airborne: true } }).style, "跳投");
  // The real xertioN smoke: a scroll-wheel jump (no jump bit) at 245 u/s holding A is a running jump throw.
  const running = styleFor(1 | 512, { origin: { ...XERTION_ORIGIN, speed: 245, airborne: true } });
  assert.equal(running.style, "跳投");
  assert.equal(running.hint, "跑跳投（按着 A）：出手时还在移动，原地跳投落点会偏");
  assert.equal(styleFor(1, { origin: { ...XERTION_ORIGIN, speed: 245, airborne: true } }).hint,
    "跑跳投：出手时还在移动，原地跳投落点会偏");
}

// --- the window never starts before the round (nor below 0) ---
{
  const early = replay({
    rounds: [{ roundNumber: 10, startTick: 77280, freezeEndTick: 77290, endTick: 80000, winnerSide: "CT" }]
  });
  const analysis = analyzeThrow(early, early.utility[0]);
  assert.equal(analysis.windowStart, 77280);
  assert.equal(analysis.windowEnd, 77365);
  same(analysis.throwerPath.map((point) => point.tick), [77280, 77296, 77312, 77328, 77344, 77360, 77365]);
  const first = replay({ rounds: [], inputs: {}, frames: [], utility: [smoke({ throwTick: 20 })] });
  const atStart = analyzeThrow(first, first.utility[0]);
  assert.equal(atStart.windowStart, 0);
  assert.equal(atStart.windowEnd, 84);
  same(atStart.throwerPath, []);
}

// --- the thrower's path: alive only, interpolated, nothing outside it ---
{
  const data = replay({ frames: frames({ diesAt: 77320 }) });
  const analysis = analyzeThrow(data, data.utility[0]);
  same(analysis.throwerPath.map((point) => point.tick), [77237, 77248, 77264, 77280, 77296, 77312]);
  same(throwerPositionAt(analysis, 77248), { x: 94.6, y: 37.8 });
  same(throwerPositionAt(analysis, 77256), { x: 94.5, y: 38 });
  same(throwerPositionAt(analysis, 77312), { x: 93.8, y: 39.4 });
  assert.equal(throwerPositionAt(analysis, 77236), null, "before the window");
  assert.equal(throwerPositionAt(analysis, 77313), null, "dead after 77312");
  assert.equal(throwerPositionAt(analysis, Number.NaN), null);
  assert.equal(throwerPositionAt({ ...analysis, throwerPath: [] }, 77300), null);
}

// --- the zoom box ---
function boxFor(points, overrides = {}) {
  const decoy = smoke({ id: "utility-decoy-1-77300", type: "decoy", points, throwOrigin: undefined });
  const data = replay({ frames: [], utility: [decoy], ...overrides });
  return analyzeThrow(data, decoy).bounds;
}
{
  // A decoy's mark is 1.2 % without a scale. Small throws open to the 24 % minimum around their centre.
  same(boxFor([{ tick: 77300, x: 50, y: 50 }, { tick: 77320, x: 52, y: 51 }]), { x: 39.6, y: 39, size: 24 });
  // Shifted to stay inside the map.
  same(boxFor([{ tick: 77300, x: 2, y: 3 }, { tick: 77320, x: 4, y: 5 }]), { x: 0, y: 0, size: 24 });
  same(boxFor([{ tick: 77300, x: 97, y: 98 }, { tick: 77320, x: 99, y: 96 }]), { x: 76, y: 76, size: 24 });
  // Padded by 12 % of the larger span (41.2 → 4.944 a side), square on the centre.
  same(boxFor([{ tick: 77300, x: 20, y: 40 }, { tick: 77320, x: 60, y: 50 }]), { x: 15.06, y: 20.06, size: 51.09 });
  // Never more than the whole map.
  same(boxFor([{ tick: 77300, x: 5, y: 5 }, { tick: 77320, x: 95, y: 90 }]), { x: 0, y: 0, size: 100 });
  same(boxFor([]), FULL_VIEW_BOX);
  // The smoke's area counts: 144 u on Mirage is 2.82 % (from the replay's map, or the map passed in).
  const withMap = analyzeThrow(replay({ frames: [] }), smoke({ points: [{ tick: 77300, x: 50, y: 50 }] }));
  same(withMap.bounds, { x: 38, y: 38, size: 24 });
  const passedMap = analyzeThrow(replay({ frames: [], mapMetadata: undefined }), smoke({ points: [{ tick: 77300, x: 50, y: 50 }, { tick: 77310, x: 80, y: 50 }] }), { transform: MIRAGE_BOUNDS });
  // x 50..82.82 (80 + 2.82), span 32.82, pad 4 → 40.82.
  same(passedMap.bounds, { x: 46, y: 29.59, size: 40.82 });
}

// --- setpos / setang ---
assert.equal(setposCommand(XERTION_ORIGIN), "setpos 1376.2 -119.3 -129.3; setang -20.33 163.28 0");
assert.equal(setposCommand({ x: -0.04, y: 0.04, z: -0.0, pitch: -0.001, yaw: -180 }), "setpos 0.0 0.0 0.0; setang 0.00 -180.00 0");
assert.equal(setposCommand({ x: 12.345, y: -7.06, z: 100, pitch: 89.999, yaw: 5 }), "setpos 12.3 -7.1 100.0; setang 90.00 5.00 0");

// --- relative time ---
assert.equal(formatRelativeSeconds(-1, 64), "-0.02s");
assert.equal(formatRelativeSeconds(0, 64), "0.00s");
assert.equal(formatRelativeSeconds(64, 64), "+1.00s");
assert.equal(formatRelativeSeconds(-64, 64), "-1.00s");
assert.equal(formatRelativeSeconds(0.1, 64), "0.00s", "no sign on a rounded zero");
assert.equal(formatRelativeSeconds(-0.1, 64), "0.00s");
assert.equal(formatRelativeSeconds(32, 0), "+0.50s", "a bad tick rate falls back to 64");
assert.equal(formatRelativeSeconds(-64, 64, 1), "-1.0s");
assert.equal(formatRelativeSeconds(20, 64, 1), "+0.3s");

console.log("throw-analysis tests passed");
