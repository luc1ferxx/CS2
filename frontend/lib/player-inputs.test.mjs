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
    compilerOptions: { esModuleInterop: true, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename
  });
  const module = { exports: {} };
  vm.runInNewContext(outputText, {
    console,
    exports: module.exports,
    module,
    require(specifier) {
      if (specifier.startsWith("@/types/")) return {};
      throw new Error(`Unexpected import ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const { INPUT_BITS, decodeButtons, describePressedKeys, hasInputs, inputMaskAt, pressedKeyLabels } =
  loadTypeScriptModule("./player-inputs.ts");

// Objects from the vm context have that context's prototypes: compare plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));

const { attack: FIRE, duck: DUCK, forward: W, left: A, right: D, back: S, jump: JUMP, attack2: RIGHT, speed: WALK } = INPUT_BITS;
const XELEX = "76561198998266210";

// Ground truth (spec S16): xelex, round 10 of spirit-vs-mouz-m2-mirage, change points as decoded from
// usercmd_buttonstate_1, then the [tick, 0] entry at his death on 77874. The first row is his state at
// 77756 (the real track turns W+D at 77752), so the fixture's track starts there.
const GROUND_TRUTH = [
  [77756, W | D, "W、D"],
  [77805, D, "D"],
  [77831, A | D, "A、D"],
  [77832, A, "A"],
  [77837, A | FIRE, "A、左键"],
  [77841, A | D | FIRE, "A、D、左键"],
  [77842, A | D, "A、D"],
  [77849, D | DUCK | FIRE, "D、蹲、左键"],
  [77855, A | D | DUCK | FIRE, "A、D、蹲、左键"],
  [77857, A | D | DUCK, "A、D、蹲"],
  [77858, D | DUCK, "D、蹲"],
  [77866, D | DUCK | FIRE, "D、蹲、左键"],
  [77870, D | DUCK, "D、蹲"],
  [77872, D, "D"],
  [77874, 0, null]
];
const replay = { inputs: { [XELEX]: GROUND_TRUTH.map(([tick, mask]) => [tick, mask]) } };

const tests = [];
const test = (name, run) => tests.push({ name, run });

test("the bits are the raw Source 2 InputBitMask values", () => {
  assert.deepEqual(plain(INPUT_BITS), {
    attack: 1, jump: 2, duck: 4, forward: 8, back: 16, left: 512, right: 1024, attack2: 2048, speed: 0x10000
  });
});

test("inputMaskAt returns the ground-truth mask at every change point and holds it until the next", () => {
  for (const [index, [tick, mask]] of GROUND_TRUTH.entries()) {
    assert.equal(inputMaskAt(replay, XELEX, tick), mask, `at ${tick}`);
    const next = GROUND_TRUTH[index + 1]?.[0];
    if (next !== undefined && next - tick > 1) {
      assert.equal(inputMaskAt(replay, XELEX, next - 1), mask, `just before ${next}`);
      assert.equal(inputMaskAt(replay, XELEX, tick + 0.5), mask, `fractional tick after ${tick}`);
    }
  }
  // After death the last entry (0: nothing pressed) holds.
  assert.equal(inputMaskAt(replay, XELEX, 90000), 0);
});

test("inputMaskAt is null before the first point, without a track and without inputs", () => {
  assert.equal(inputMaskAt(replay, XELEX, 77755), null);
  assert.equal(inputMaskAt(replay, XELEX, 77755.9), null);
  assert.equal(inputMaskAt(replay, "someone-else", 77800), null);
  assert.equal(inputMaskAt(replay, null, 77800), null);
  assert.equal(inputMaskAt(replay, XELEX, Number.NaN), null);
  assert.equal(inputMaskAt({ inputs: {} }, XELEX, 77800), null);
  assert.equal(inputMaskAt({ inputs: { [XELEX]: [] } }, XELEX, 77800), null);
  assert.equal(inputMaskAt({}, XELEX, 77800), null);
  assert.equal(inputMaskAt(null, XELEX, 77800), null);
  // An inherited key is not a player.
  assert.equal(inputMaskAt(replay, "toString", 77800), null);
});

test("decodeButtons reads the nine displayed bits and ignores the rest (USE, RELOAD)", () => {
  assert.deepEqual(plain(decodeButtons(0)), {
    forward: false, back: false, left: false, right: false, attack: false, attack2: false, jump: false, duck: false, speed: false
  });
  assert.deepEqual(plain(decodeButtons(W | S | A | D | FIRE | RIGHT | JUMP | DUCK | WALK)), {
    forward: true, back: true, left: true, right: true, attack: true, attack2: true, jump: true, duck: true, speed: true
  });
  assert.deepEqual(plain(decodeButtons(32 | 8192)), plain(decodeButtons(0)));
  assert.deepEqual(plain(decodeButtons(D | 32 | 8192)), plain(decodeButtons(D)));
  // Each bit lands on its own key.
  assert.equal(decodeButtons(WALK).speed, true);
  assert.equal(decodeButtons(WALK).duck, false);
  assert.equal(decodeButtons(RIGHT).attack2, true);
  assert.equal(decodeButtons(RIGHT).attack, false);
});

test("the pressed keys are spoken in panel order, with the player's name", () => {
  for (const [tick, , spoken] of GROUND_TRUTH) {
    const buttons = decodeButtons(inputMaskAt(replay, XELEX, tick));
    assert.equal(
      describePressedKeys(buttons, "xelex"),
      spoken ? `xelex 正在按：${spoken}` : "xelex 没有按键",
      `at ${tick}`
    );
  }
  assert.equal(describePressedKeys(decodeButtons(D | DUCK | FIRE), "xelex"), "xelex 正在按：D、蹲、左键");
  assert.deepEqual(plain(pressedKeyLabels(decodeButtons(W | S | A | D | FIRE | RIGHT | JUMP | DUCK | WALK))),
    ["W", "A", "S", "D", "静步", "蹲", "跳", "左键", "右键"]);
  assert.equal(describePressedKeys(decodeButtons(0)), "没有按键");
  assert.equal(describePressedKeys(decodeButtons(JUMP), "  "), "正在按：跳");
});

test("hasInputs is true only when some player has a track", () => {
  assert.equal(hasInputs(replay), true);
  assert.equal(hasInputs({ inputs: {} }), false);
  assert.equal(hasInputs({ inputs: { [XELEX]: [] } }), false);
  assert.equal(hasInputs({}), false);
  assert.equal(hasInputs(undefined), false);
});

let failed = 0;
for (const { name, run } of tests) {
  try {
    run();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}`);
    console.error(error);
  }
}
if (failed > 0) {
  process.exitCode = 1;
} else {
  console.log(`${tests.length} player-inputs tests passed`);
}
