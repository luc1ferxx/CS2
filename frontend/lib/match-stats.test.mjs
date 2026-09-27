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

const {
  ROUND_END_REASON_LABELS,
  matchTeams,
  openingDuels,
  playerDeaths,
  playerMatchStats,
  playerRoundKills,
  roundEndReason,
  roundHalfLabel,
  sideOfPlayerInRound,
  teamKeyOfPlayer,
  utilityCounts
} = loadTypeScriptModule("./match-stats.ts");
const { matchStatsReplay: replay } = loadTypeScriptModule("./test-fixtures/match-stats.ts");

const plain = (value) => JSON.parse(JSON.stringify(value));
const tests = [];
const test = (name, run) => tests.push([name, run]);

test("teams come from the first round's frames; A started T, B started CT", () => {
  const [a, b] = matchTeams(replay);
  assert.equal(a.key, "A");
  assert.equal(a.startSide, "T");
  assert.deepEqual(plain(a.playerIds), ["a1", "a2"]);
  assert.equal(b.key, "B");
  assert.equal(b.startSide, "CT");
  // Cinder only shows up in round 28, on the side B plays there.
  assert.deepEqual(plain(b.playerIds), ["b1", "b2", "c1"]);
  assert.equal(teamKeyOfPlayer(replay, "c1"), "B");
  assert.equal(teamKeyOfPlayer(replay, "nobody"), null);
});

test("scores follow each team's side per round, through the half-time swap and overtime", () => {
  const [a, b] = matchTeams(replay);
  assert.equal(a.score, 15);
  assert.equal(b.score, 13);
  assert.deepEqual(plain(a.halves), [
    { label: "上半场", side: "T", score: 3 },
    { label: "下半场", side: "CT", score: 9 },
    { label: "加时", side: null, score: 3 }
  ]);
  assert.deepEqual(plain(b.halves), [
    { label: "上半场", side: "CT", score: 9 },
    { label: "下半场", side: "T", score: 3 },
    { label: "加时", side: null, score: 1 }
  ]);
});

test("a regulation-only match has no overtime half", () => {
  const regulation = { ...replay, rounds: replay.rounds.filter((round) => round.roundNumber <= 24) };
  const [a, b] = matchTeams(regulation);
  assert.deepEqual(plain(a.halves.map((half) => half.label)), ["上半场", "下半场"]);
  assert.equal(a.score, 12);
  assert.equal(b.score, 12);
});

test("team names are matched by key and trimmed; blanks stay null", () => {
  const [a, b] = matchTeams(replay, [{ key: "B", name: "  Bravo  " }, { key: "A", name: " " }]);
  assert.equal(a.name, null);
  assert.equal(b.name, "Bravo");
  assert.equal(matchTeams(replay, null)[0].name, null);
});

test("a player's side per round comes from the frames, not players[].side", () => {
  assert.equal(sideOfPlayerInRound(replay, "a1", 1), "T");
  assert.equal(sideOfPlayerInRound(replay, "a1", 13), "CT");
  assert.equal(sideOfPlayerInRound(replay, "a1", 25), "T");
  assert.equal(sideOfPlayerInRound(replay, "a1", 28), "CT");
  assert.equal(sideOfPlayerInRound(replay, "b1", 28), null);
  assert.equal(sideOfPlayerInRound(replay, "a1", 99), null);
});

test("scoreboard: K/D/A, team kills, suicides, world deaths, HS%, ADR, KAST and openings", () => {
  const stats = playerMatchStats(replay);
  assert.deepEqual(plain(stats.map((player) => player.playerId)), ["a1", "a2", "c1", "b2", "b1"]);
  const byId = Object.fromEntries(stats.map((player) => [player.playerId, player]));

  // Ace: 3 kills (one headshot); deaths to Blaze, to his teammate and to himself.
  assert.deepEqual(pick(byId.a1), {
    teamKey: "A", kills: 3, deaths: 3, assists: 0, diff: 0, openingKills: 2, openingDeaths: 0, roundsPlayed: 28
  });
  assert.equal(byId.a1.hsPercent.toFixed(2), "33.33");
  assert.equal(byId.a1.adr.toFixed(4), (100 / 28).toFixed(4));
  // Round 1 is traded; rounds 3 (team kill) and 25 (suicide) are not.
  assert.equal(byId.a1.kastPercent.toFixed(3), ((26 / 28) * 100).toFixed(3));

  // Ash: the team kill is no kill; Ace's "assist" on Ash's death is ignored.
  assert.deepEqual(pick(byId.a2), {
    teamKey: "A", kills: 2, deaths: 2, assists: 1, diff: 0, openingKills: 1, openingDeaths: 2, roundsPlayed: 28
  });
  assert.equal(byId.a2.hsPercent, 50);
  // 20 team damage never counts; the lethal hit counts in full.
  assert.equal(byId.a2.adr.toFixed(4), (100 / 28).toFixed(4));
  // Round 2: Bolt dies 6 s after killing Ash, outside the trade window.
  assert.equal(byId.a2.kastPercent.toFixed(3), ((26 / 28) * 100).toFixed(3));

  assert.deepEqual(pick(byId.b1), {
    teamKey: "B", kills: 1, deaths: 3, assists: 0, diff: -2, openingKills: 1, openingDeaths: 2, roundsPlayed: 27
  });
  assert.equal(byId.b1.hsPercent, 0);
  assert.equal(byId.b1.adr.toFixed(4), (30 / 27).toFixed(4));
  // Round 1 traded within 100 ticks, round 2 has a kill, round 13 has nothing.
  assert.equal(byId.b1.kastPercent.toFixed(3), ((26 / 27) * 100).toFixed(3));

  // Blaze's 123 damage on Ace counts only the 50 health Ace had left.
  assert.deepEqual(pick(byId.b2), {
    teamKey: "B", kills: 2, deaths: 3, assists: 1, diff: -1, openingKills: 1, openingDeaths: 1, roundsPlayed: 28
  });
  assert.equal(byId.b2.adr.toFixed(4), (50 / 28).toFixed(4));
  // The world death in round 4 cannot be traded.
  assert.equal(byId.b2.kastPercent.toFixed(3), ((26 / 28) * 100).toFixed(3));

  assert.deepEqual(pick(byId.c1), {
    teamKey: "B", kills: 0, deaths: 0, assists: 0, diff: 0, openingKills: 0, openingDeaths: 0, roundsPlayed: 1
  });
  assert.equal(byId.c1.hsPercent, null);
  assert.equal(byId.c1.adr, 0);
  assert.equal(byId.c1.kastPercent, 100);
  assert.equal(byId.c1.name, "Cinder");
});

test("kills per round carry the side and whether the player died", () => {
  const rounds = playerRoundKills(replay, "a1");
  assert.equal(rounds.length, 28);
  const summary = (roundNumber) => plain(rounds.find((round) => round.roundNumber === roundNumber));
  assert.deepEqual(summary(1), { roundNumber: 1, kills: 1, died: true, side: "T" });
  assert.deepEqual(summary(2), { roundNumber: 2, kills: 1, died: false, side: "T" });
  assert.deepEqual(summary(3), { roundNumber: 3, kills: 0, died: true, side: "T" });
  assert.deepEqual(summary(13), { roundNumber: 13, kills: 1, died: false, side: "CT" });
  assert.deepEqual(summary(25), { roundNumber: 25, kills: 0, died: true, side: "T" });
  assert.equal(playerRoundKills(replay, "b1").find((round) => round.roundNumber === 28).side, null);
});

test("deaths use the victim's position, falling back to the last frame at the kill tick", () => {
  assert.deepEqual(plain(playerDeaths(replay, "a2")), [
    { roundNumber: 2, tick: 2200, x: 25, y: 35, z: 0, killerId: "b1", killerName: "Bolt", weapon: "awp", headshot: false },
    { roundNumber: 3, tick: 3300, x: 45, y: 50, z: -10, killerId: "b2", killerName: "Blaze", weapon: "m4a1", headshot: true }
  ]);
  const worldDeath = playerDeaths(replay, "b2").find((death) => death.roundNumber === 4);
  assert.equal(worldDeath.killerId, null);
  assert.equal(worldDeath.killerName, null);
  assert.equal(worldDeath.weapon, null);
  assert.deepEqual(plain(playerDeaths(replay, "a1").map((death) => death.roundNumber)), [1, 3, 25]);
});

test("frames a round keeps through the half-time break do not decide its sides", () => {
  // Round 12 keeps frames after its endTick (12900) on the swapped sides; Ace is first seen there.
  const breakFrame = (tick) => ({
    tick, timeSeconds: tick / 64, roundNumber: 12, bombState: { status: "unknown" },
    players: [
      { id: "a1", name: "Ace", side: "CT", x: 1, y: 1, z: 0, alive: true, hp: 100, hasBomb: false },
      { id: "b1", name: "Bolt", side: "T", x: 2, y: 2, z: 0, alive: true, hp: 100, hasBomb: false }
    ]
  });
  const breakFrames = [12950, 13000 - 10].map(breakFrame);
  const withBreak = { ...replay, frames: [breakFrames[0], ...replay.frames, breakFrames[1]] };
  assert.equal(sideOfPlayerInRound(withBreak, "a1", 12), "T");
  const [a, b] = matchTeams(withBreak);
  assert.equal(a.score, 15);
  assert.equal(b.score, 13);
});

// Events the parser files under round `roundNumber` although their tick comes before it starts.
const lateKill = (tick, roundNumber, attackerId, victimId, weapon = "ak47") => ({
  id: `late-kill-${tick}-${victimId}`, type: "kill", tick, roundNumber, playerId: attackerId,
  metadata: { attackerId, victimId, weapon, headshot: false }
});
const lateDamage = (tick, roundNumber, attackerId, victimId, damageHealth, health) => ({
  id: `late-damage-${tick}-${victimId}`, type: "damage", tick, roundNumber, playerId: attackerId,
  metadata: { attackerId, victimId, weapon: "ak47", damageHealth, health }
});
const withEvents = (...events) => ({ ...replay, frames: [...replay.frames], events: [...replay.events, ...events] });
const statsOf = (data, playerId) => playerMatchStats(data).find((player) => player.playerId === playerId);

test("half-time switch suicides and hits count for nothing, and leave the next round's health alone", () => {
  // Round 12 ends at 12900 and round 13 starts at 13000; the parser files both under 13.
  const withSwitch = withEvents(
    lateKill(12950, 13, "a1", "a1", "world"),
    lateKill(13000, 13, "a1", "a1", "world"),
    lateDamage(13000, 13, "a1", "a1", 1, 0),
    // Bolt's first hit on Ace in round 13 counts in full.
    lateDamage(13150, 13, "b1", "a1", 40, 60)
  );
  assert.equal(statsOf(withSwitch, "a1").deaths, statsOf(replay, "a1").deaths);
  assert.deepEqual(plain(playerDeaths(withSwitch, "a1").map((death) => death.roundNumber)), [1, 3, 25]);
  assert.equal(playerRoundKills(withSwitch, "a1").find((round) => round.roundNumber === 12).died, false);
  assert.equal(playerRoundKills(withSwitch, "a1").find((round) => round.roundNumber === 13).died, false);
  assert.equal(statsOf(withSwitch, "b1").adr.toFixed(4), ((30 + 40) / 27).toFixed(4));
});

test("a kill after the round ends counts for the round that just ended, never as its opening", () => {
  // Round 5 (A on T) ends at 5900 with no kills; Ash kills Blaze at 5950, filed under round 6.
  const withExit = withEvents(lateKill(5950, 6, "a2", "b2"));
  assert.equal(statsOf(withExit, "a2").kills, statsOf(replay, "a2").kills + 1);
  assert.equal(statsOf(withExit, "b2").deaths, statsOf(replay, "b2").deaths + 1);
  const ashRounds = playerRoundKills(withExit, "a2");
  assert.equal(ashRounds.find((round) => round.roundNumber === 5).kills, 1);
  assert.equal(ashRounds.find((round) => round.roundNumber === 6).kills, 0);
  const blazeRounds = playerRoundKills(withExit, "b2");
  assert.deepEqual(plain(blazeRounds.find((round) => round.roundNumber === 5)), { roundNumber: 5, kills: 0, died: true, side: "CT" });
  assert.equal(blazeRounds.find((round) => round.roundNumber === 6).died, false);
  assert.deepEqual(plain(playerDeaths(withExit, "b2").find((death) => death.tick === 5950)),
    { roundNumber: 5, tick: 5950, x: 80, y: 65, z: 0, killerId: "a2", killerName: "Ash", weapon: "ak47", headshot: false });
  // Blaze no longer survived round 5.
  assert.equal(statsOf(withExit, "b2").kastPercent.toFixed(3), ((25 / 28) * 100).toFixed(3));
  assert.deepEqual(plain(openingDuels(withExit, "a2")), plain(openingDuels(replay, "a2")));
  assert.equal(statsOf(withExit, "a2").openingKills, statsOf(replay, "a2").openingKills);
});

test("a bomb death after the round ends is a death in that round", () => {
  const withBomb = withEvents({
    id: "late-bomb", type: "kill", tick: 5905, roundNumber: 6, playerId: null,
    metadata: { victimId: "b1", weapon: "planted_c4" }
  });
  assert.equal(statsOf(withBomb, "b1").deaths, statsOf(replay, "b1").deaths + 1);
  assert.equal(playerRoundKills(withBomb, "b1").find((round) => round.roundNumber === 5).died, true);
});

test("post-round damage is capped at the health left in that round, not the next round's 100", () => {
  const withLateHit = withEvents(
    lateDamage(5500, 5, "b1", "a1", 70, 30),
    // Filed under round 6: Ace had 30 left, so 30 counts, and round 6 still starts him at 100.
    lateDamage(5950, 6, "b1", "a1", 169, 0),
    lateDamage(6500, 6, "b2", "a1", 60, 40)
  );
  assert.equal(statsOf(withLateHit, "b1").adr.toFixed(4), ((30 + 70 + 30) / 27).toFixed(4));
  assert.equal(statsOf(withLateHit, "b2").adr.toFixed(4), ((50 + 60) / 28).toFixed(4));
});

test("opening duels skip team kills and suicides", () => {
  assert.deepEqual(plain(openingDuels(replay, "a1")), [
    { roundNumber: 1, tick: 1200, won: true, opponentId: "b1", opponentName: "Bolt" },
    { roundNumber: 13, tick: 13200, won: true, opponentId: "b1", opponentName: "Bolt" }
  ]);
  assert.deepEqual(plain(openingDuels(replay, "a2")), [
    { roundNumber: 2, tick: 2200, won: false, opponentId: "b1", opponentName: "Bolt" },
    { roundNumber: 3, tick: 3300, won: false, opponentId: "b2", opponentName: "Blaze" },
    { roundNumber: 25, tick: 25300, won: true, opponentId: "b2", opponentName: "Blaze" }
  ]);
});

test("utility counts the thrower's grenades only", () => {
  assert.deepEqual(plain(utilityCounts(replay, "a1")), { smoke: 2, flash: 1, molotov: 1, he: 1 });
  assert.deepEqual(plain(utilityCounts(replay, "b1")), { smoke: 0, flash: 1, molotov: 0, he: 0 });
  assert.deepEqual(plain(utilityCounts(replay, "nobody")), { smoke: 0, flash: 0, molotov: 0, he: 0 });
});

test("round end reasons map to five kinds; missing and unknown are other", () => {
  const reasons = replay.rounds.slice(0, 5).map(roundEndReason);
  assert.deepEqual(plain(reasons), ["bomb_exploded", "bomb_defused", "elimination", "time", "other"]);
  assert.equal(roundEndReason({ ...replay.rounds[0], winnerReason: "T_Killed" }), "elimination");
  assert.equal(roundEndReason({ ...replay.rounds[0], winnerReason: "target_saved" }), "time");
  assert.equal(roundEndReason({ ...replay.rounds[0], winnerReason: "surrender" }), "other");
  assert.equal(ROUND_END_REASON_LABELS.other, "其他");
  assert.equal(ROUND_END_REASON_LABELS.bomb_defused, "拆除炸弹");
  assert.deepEqual([12, 13, 24, 25].map(roundHalfLabel), ["上半场", "下半场", "下半场", "加时"]);
});

test("a replay without kill sides still splits teams from the frames", () => {
  const events = replay.events.map((event) => event.type === "kill" && event.metadata
    ? { ...event, metadata: { ...event.metadata, attackerSide: undefined, victimSide: undefined } }
    : event);
  const stats = playerMatchStats({ ...replay, events });
  assert.equal(stats.find((player) => player.playerId === "a1").kills, 3);
  assert.equal(stats.find((player) => player.playerId === "a2").kills, 2);
});

test("kill sides stand in for missing frames", () => {
  const eventsOnly = { ...replay, frames: [] };
  const [a, b] = matchTeams(eventsOnly);
  assert.equal(a.startSide, "T");
  assert.ok(a.playerIds.includes("a1"));
  assert.ok(b.playerIds.includes("b1"));
  assert.equal(sideOfPlayerInRound(eventsOnly, "a1", 13), "CT");
});

test("never throws on empty or malformed replays", () => {
  const broken = [
    { demoId: "x", mapName: "de_dust2", tickRate: 0, rounds: [], players: [], frames: [], events: [] },
    { demoId: "x" },
    { demoId: "x", tickRate: 64, rounds: [null, { roundNumber: "one" }], players: [null], frames: [null, { roundNumber: 1, players: null }], events: [null, 7] }
  ];
  for (const value of broken) {
    assert.deepEqual(plain(matchTeams(value)), []);
    assert.deepEqual(plain(playerMatchStats(value)), []);
    assert.deepEqual(plain(playerRoundKills(value, "a1")), []);
    assert.deepEqual(plain(playerDeaths(value, "a1")), []);
    assert.deepEqual(plain(openingDuels(value, "a1")), []);
    assert.deepEqual(plain(utilityCounts(value, "a1")), { smoke: 0, flash: 0, molotov: 0, he: 0 });
    assert.equal(sideOfPlayerInRound(value, "a1", 1), null);
  }
});

test("a new replay wrapper around the same arrays reuses the computed index", () => {
  const first = playerMatchStats(replay);
  const wrapped = playerMatchStats({ ...replay, video: { ...replay.video, status: "ready" } });
  assert.deepEqual(plain(wrapped), plain(first));
  // Different events are a different match.
  const fewer = playerMatchStats({ ...replay, events: [] });
  assert.equal(fewer.find((player) => player.playerId === "a1").kills, 0);
});

function pick(player) {
  const { teamKey, kills, deaths, assists, diff, openingKills, openingDeaths, roundsPlayed } = player;
  return { teamKey, kills, deaths, assists, diff, openingKills, openingDeaths, roundsPlayed };
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
  console.log(`match-stats: ${tests.length} tests passed`);
}
