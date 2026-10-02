// The shared buy-type cases in fixtures/round-economy/, run against the review page's
// lib/round-economy.ts. backend/tests/test_round_economy.py runs the same files against the
// analyzer's Python port (backend/app/analysis/round_economy.py); both must produce every
// case's `expected` exactly.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = resolve(__dirname, "../../fixtures/round-economy");
const loaded = new Map();

function loadTypeScriptModule(relativePath) {
  const filename = resolve(__dirname, relativePath);
  if (loaded.has(filename)) return loaded.get(filename);
  const source = readFileSync(filename, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { esModuleInterop: true, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename
  });
  const module = { exports: {} };
  loaded.set(filename, module.exports);
  vm.runInNewContext(outputText, {
    console,
    exports: module.exports,
    module,
    require(specifier) {
      if (specifier.startsWith("@/types/")) return {};
      if (specifier === "@/lib/match-stats") return loadTypeScriptModule("./match-stats.ts");
      if (specifier === "@/lib/player-state") return loadTypeScriptModule("./player-state.ts");
      if (specifier === "@/lib/replay-frames") return loadTypeScriptModule("./replay-frames.ts");
      throw new Error(`Unexpected import ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const { roundEconomies } = loadTypeScriptModule("./round-economy.ts");

const files = readdirSync(FIXTURE_DIR).filter((file) => file.endsWith(".json")).sort();
const cases = files.map((file) => ({ file, ...JSON.parse(readFileSync(resolve(FIXTURE_DIR, file), "utf8")) }));
// The vm context has its own Object/Array prototypes; compare plain JSON values.
const plain = (value) => JSON.parse(JSON.stringify(value));

const tests = [];
const test = (name, run) => tests.push([name, run]);

test("the shared fixture directory has every case", () => {
  assert.ok(cases.length >= 11, `only ${cases.length} cases in ${FIXTURE_DIR}`);
  for (const { file, name } of cases) assert.equal(`${name}.json`, file);
});

for (const { name, replay, expected } of cases) {
  test(`${name}: the buy types match the expected outcome`, () => {
    assert.deepEqual(plain(roundEconomies(replay)), expected);
  });
}

let failed = 0;
for (const [name, run] of tests) {
  try {
    run();
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}`);
    console.error(error);
  }
}
if (failed > 0) {
  process.exitCode = 1;
} else {
  console.log(`round-economy-fixtures: ${tests.length} tests passed (${cases.length} shared cases)`);
}
