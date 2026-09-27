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
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: filename
  });
  const module = { exports: {} };
  const context = {
    console,
    exports: module.exports,
    module,
    require(specifier) {
      if (specifier.startsWith("@/types/")) {
        return {};
      }
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

const {
  describeParserEvent,
  parserEventPresentation,
  parserEventPresentationForType,
  recentMapParserEvents,
  timelineParserEventMarkersForRound,
  clusterTimelineMarkers,
  weaponName
} = loadTypeScriptModule("./replay-events.ts");

const parserEvents = [
  replayEvent({ id: "kill-1", type: "kill", tick: 150, label: "T One killed CT One" }),
  replayEvent({ id: "plant-1", type: "bomb_planted", tick: 200, label: "Bomb planted A" }),
  replayEvent({ id: "smoke-1", type: "smoke", tick: 300, label: "Smoke", x: 45, y: 55 }),
  replayEvent({ id: "flash-2", type: "flash", tick: 420, roundNumber: 2, label: "Flash" })
];

{
  // Chinese labels, and one distinct glyph per type on both the map and the timeline.
  assert.deepEqual(normalize(parserEventPresentationForType("bomb_pickup")), {
    label: "拾取炸弹", tone: "objective", shortLabel: "拾"
  });
  assert.deepEqual(normalize(parserEventPresentationForType("bomb_planted")), {
    label: "安装炸弹", tone: "objective", shortLabel: "包"
  });
  assert.deepEqual(normalize(parserEventPresentationForType("he")), {
    label: "手雷", tone: "damage", shortLabel: "雷"
  });
  assert.deepEqual(normalize(parserEventPresentationForType("unknown_event")), {
    label: "事件", tone: "objective", shortLabel: "事"
  });
  const types = ["kill", "death", "damage", "bomb_pickup", "bomb_dropped", "bomb_planted", "bomb_defused",
    "bomb_exploded", "smoke", "flash", "molotov", "he", "round_start", "round_end"];
  const glyphs = types.map((type) => parserEventPresentationForType(type).shortLabel);
  assert.equal(new Set(glyphs).size, types.length, "Two event types must not share a glyph");
  assert.ok(types.every((type) => !/[A-Za-z]/.test(parserEventPresentationForType(type).label)));
}

{
  // Damage stays on the map but off the timeline lane.
  const markers = timelineParserEventMarkersForRound([
    ...parserEvents,
    replayEvent({ id: "damage-1", type: "damage", tick: 160, label: "T One damaged CT One" })
  ], 1, 100, 500);
  assert.deepEqual(
    normalize(markers.map((marker) => ({
      id: marker.event.id,
      leftPercent: marker.leftPercent,
      seekTick: marker.seekTick,
      label: marker.presentation.label,
      tone: marker.presentation.tone,
      shortLabel: marker.presentation.shortLabel
    }))),
    [
      { id: "kill-1", leftPercent: 12.5, seekTick: 150, label: "击杀", tone: "combat", shortLabel: "击" },
      { id: "plant-1", leftPercent: 25, seekTick: 200, label: "安装炸弹", tone: "objective", shortLabel: "包" },
      { id: "smoke-1", leftPercent: 50, seekTick: 300, label: "烟雾弹", tone: "utility", shortLabel: "烟" }
    ]
  );
}

{
  // The reviewed player's own kill and own death are told apart, with who killed whom.
  const kill = replayEvent({ id: "kill-2", type: "kill", tick: 180, label: "xelex killed donk", metadata: {
    attackerId: "me", attackerName: "xelex", victimId: "enemy", victimName: "donk", weapon: "usp_silencer", headshot: true
  } });
  const death = replayEvent({ id: "kill-3", type: "kill", tick: 240, label: "donk killed xelex", metadata: {
    attackerId: "enemy", attackerName: "donk", victimId: "me", victimName: "xelex", weapon: "weapon_ak47"
  } });
  const assist = replayEvent({ id: "kill-4", type: "kill", tick: 260, label: "mate killed b1t", metadata: {
    attackerId: "mate", attackerName: "mate", victimId: "enemy-2", victimName: "b1t", assisterId: "me"
  } });
  assert.deepEqual(normalize(parserEventPresentation(kill, "me")), { label: "击杀", tone: "own-kill", shortLabel: "杀" });
  assert.deepEqual(normalize(parserEventPresentation(death, "me")), { label: "阵亡", tone: "own-death", shortLabel: "亡" });
  assert.deepEqual(normalize(parserEventPresentation(assist, "me")), { label: "助攻", tone: "combat", shortLabel: "助" });
  assert.deepEqual(normalize(parserEventPresentation(kill, null)), { label: "击杀", tone: "combat", shortLabel: "击" });
  assert.equal(describeParserEvent(kill), "xelex 用 USP-S 击杀 donk（爆头）");
  assert.equal(describeParserEvent(death), "donk 用 AK-47 击杀 xelex");
  assert.equal(describeParserEvent(replayEvent({ id: "k", type: "kill", tick: 1, label: "x", metadata: { victimName: "donk" } })), "donk 阵亡");
  assert.equal(describeParserEvent(parserEvents[1]), "安装炸弹 · T One");
  assert.equal(weaponName("mystery_gun"), "MYSTERY_GUN");
  assert.equal(weaponName(null), null);

  const markers = timelineParserEventMarkersForRound([kill, death], 1, 100, 500, "me");
  assert.deepEqual(normalize(markers.map((marker) => [marker.presentation.shortLabel, marker.description])), [
    ["杀", "xelex 用 USP-S 击杀 donk（爆头）"],
    ["亡", "donk 用 AK-47 击杀 xelex"]
  ]);

  // Kills take the killer's team colour; utility and the bomb stay neutral.
  const ctKill = replayEvent({ id: "kill-5", type: "kill", tick: 190, label: "k", metadata: { attackerSide: "CT", victimSide: "T" } });
  const sides = timelineParserEventMarkersForRound([ctKill, parserEvents[0], parserEvents[1], parserEvents[2]], 1, 100, 500);
  assert.deepEqual(normalize(sides.map((marker) => [marker.event.id, marker.side])), [
    ["kill-1", "T"], ["kill-5", "CT"], ["plant-1", null], ["smoke-1", null]
  ]);
}

{
  // Round start and end stay on the round lane, not the event lane.
  const markers = timelineParserEventMarkersForRound([
    replayEvent({ id: "start-1", type: "round_start", tick: 100, label: "start" }),
    parserEvents[0],
    replayEvent({ id: "end-1", type: "round_end", tick: 500, label: "end" })
  ], 1, 100, 500);
  assert.deepEqual(normalize(markers.map((marker) => marker.event.id)), ["kill-1"]);
}

{
  // Markers closer than the gap share one chip, anchored at the first; an unmeasured lane keeps them apart.
  const at = (id, leftPercent) => ({ id, leftPercent });
  const markers = [at("a", 10), at("b", 11), at("c", 12.5), at("d", 20), at("e", 90)];
  assert.deepEqual(
    normalize(clusterTimelineMarkers(markers, 800, 20).map((group) => group.map((marker) => marker.id))),
    [["a", "b"], ["c"], ["d"], ["e"]]
  );
  assert.deepEqual(
    normalize(clusterTimelineMarkers(markers, 0, 20).map((group) => group.map((marker) => marker.id))),
    [["a"], ["b"], ["c"], ["d"], ["e"]]
  );
}

{
  const nearby = recentMapParserEvents(
    [
      ...parserEvents,
      replayEvent({ id: "bad-x", type: "flash", tick: 302, label: "Bad X", x: Number.NaN, y: 50 }),
      replayEvent({ id: "bad-y", type: "he", tick: 304, label: "Bad Y", x: 50, y: Number.POSITIVE_INFINITY })
    ],
    1,
    290,
    64
  );
  assert.deepEqual(normalize(nearby.map((event) => event.id)), ["smoke-1"]);
}

function replayEvent(overrides) {
  return {
    id: overrides.id,
    type: overrides.type,
    tick: overrides.tick,
    roundNumber: overrides.roundNumber ?? 1,
    playerId: "t-1",
    playerName: "T One",
    side: "T",
    x: overrides.x,
    y: overrides.y,
    label: overrides.label,
    metadata: overrides.metadata ?? {}
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}
