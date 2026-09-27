// The shared side-rule cases in fixtures/match-rules/, run against the review page's
// implementation. backend/tests/test_match_side_rules.py runs the same files against the
// stored match summary; both must produce every case's `expected` exactly.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = resolve(__dirname, "../../fixtures/match-rules");

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

const { matchSideRules, matchTeams, sideOfPlayerInRound, teamKeyOfPlayer } = loadTypeScriptModule("./match-stats.ts");

const files = readdirSync(FIXTURE_DIR).filter((file) => file.endsWith(".json")).sort();
const cases = files.map((file) => ({ file, ...JSON.parse(readFileSync(resolve(FIXTURE_DIR, file), "utf8")) }));
// The vm context has its own Object/Array prototypes; compare plain JSON values.
const plain = (value) => JSON.parse(JSON.stringify(value));
// Code point order, as the backend's sorted() and the fixtures have it (not UTF-16 `<`).
const byId = (left, right) => {
  const [a, b] = [Array.from(left, (char) => char.codePointAt(0)), Array.from(right, (char) => char.codePointAt(0))];
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) if (a[index] !== b[index]) return a[index] - b[index];
  return a.length - b.length;
};

const tests = [];
const test = (name, run) => tests.push([name, run]);

test("the shared fixture directory has every case", () => {
  assert.ok(cases.length >= 11, `only ${cases.length} cases in ${FIXTURE_DIR}`);
  for (const { file, name } of cases) assert.equal(`${name}.json`, file);
});

for (const { name, replay, expected } of cases) {
  test(`${name}: the side rule gives the expected outcome`, () => {
    assert.deepEqual(plain(matchSideRules(replay)), expected);
  });

  test(`${name}: the review page's public helpers agree`, () => {
    const teams = matchTeams(replay).map((team) => ({
      key: team.key,
      playerIds: [...team.playerIds].sort(byId),
      startSide: team.startSide,
      score: team.score
    }));
    assert.deepEqual(plain(teams), expected.teams);
    for (const [roundNumber, sides] of Object.entries(expected.sidesByRound)) {
      for (const [playerId, side] of Object.entries(sides)) {
        assert.equal(sideOfPlayerInRound(replay, playerId, Number(roundNumber)), side, `${playerId} in round ${roundNumber}`);
      }
    }
    for (const team of expected.teams) {
      for (const playerId of team.playerIds) assert.equal(teamKeyOfPlayer(replay, playerId), team.key, playerId);
    }
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
  console.log(`match-side-rules: ${tests.length} tests passed (${cases.length} shared cases)`);
}
