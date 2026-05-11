export function degradedReplay() {
  return {
    demoId: "fixture-degraded",
    contractVersion: "legacy",
    mapName: "de_inferno",
    tickRate: 64,
    video: video(),
    rounds: [],
    players: [],
    frames: [],
    events: [],
    generatedAt: "2026-05-08T00:00:00Z",
    diagnostics: {
      contractVersion: "legacy",
      normalizedLegacy: true,
      parserEventCount: 0,
      roundCount: 0,
      playerCount: 0,
      frameCount: 0,
      missingFields: ["events"],
      degradedFields: [],
      eventFamilyCounts: { combat: 0, damage: 0, objective: 0, utility: 0 },
      missingEventFamilies: ["combat", "damage", "objective", "utility"]
    }
  };
}

export function parserEventReplay() {
  return {
    demoId: "fixture-parser-events",
    contractVersion: "replay_contract_v1",
    mapName: "de_mirage",
    tickRate: 64,
    video: video(),
    rounds: [{ roundNumber: 1, startTick: 100, freezeEndTick: 164, endTick: 500, winnerSide: "CT" }],
    players: replayPlayers(),
    frames: [frame(100), frame(320)],
    events: [
      replayEvent("kill-180", "kill", 180, "T Entry killed CT Anchor"),
      replayEvent("plant-a", "bomb_planted", 320, "Bomb planted A"),
      replayEvent("smoke-execute", "smoke", 360, "Smoke"),
      replayEvent("flash-entry", "flash", 420, "Flash")
    ],
    generatedAt: "2026-05-08T00:00:00Z",
    diagnostics: {
      contractVersion: "replay_contract_v1",
      normalizedLegacy: false,
      parserEventCount: 4,
      roundCount: 1,
      playerCount: 3,
      frameCount: 2,
      missingFields: [],
      degradedFields: [],
      eventFamilyCounts: { combat: 1, damage: 0, objective: 1, utility: 2 },
      missingEventFamilies: ["damage"]
    }
  };
}

export function coachingEvidenceEvents() {
  return [
    {
      id: "weak-utility",
      demo_id: "fixture-parser-events",
      round_number: 1,
      player_id: "t-entry",
      player_name: "T Entry",
      tick_start: 1200,
      tick_end: 1200,
      category: "utility",
      severity: "medium",
      title: "Execute lacked utility before the plant",
      message: "The plant happened with one utility event in the prior 12 seconds.",
      structured_context_json: {
        ruleId: "weak_utility_before_execute",
        involvedPlayerIds: ["t-entry", "t-support"],
        evidenceTicks: [900, 1200],
        relatedEventIds: ["plant-a", "smoke-execute"],
        windowSeconds: 12,
        utilityCount: 1
      },
      confidence: 0.64,
      created_at: "2026-05-08T00:00:00Z"
    }
  ];
}

export function replayPlayers() {
  return [
    { id: "t-entry", name: "T Entry", side: "T", color: "#f5b542" },
    { id: "t-support", name: "T Support", side: "T", color: "#f5b542" },
    { id: "ct-anchor", name: "CT Anchor", side: "CT", color: "#2ed3d0" }
  ];
}

function video() {
  return {
    status: "pending",
    url: null,
    durationSeconds: 0,
    tickStart: 0,
    tickEnd: 0,
    tickRate: 64,
    source: "mock",
    errorMessage: null,
    timeOriginSeconds: 0
  };
}

function frame(tick) {
  return {
    tick,
    timeSeconds: tick / 64,
    roundNumber: 1,
    players: [],
    bombState: { status: "carried" }
  };
}

function replayEvent(id, type, tick, label) {
  return {
    id,
    type,
    tick,
    roundNumber: 1,
    source: "parser",
    playerIds: ["t-entry"],
    playerId: "t-entry",
    playerName: "T Entry",
    side: "T",
    label,
    metadata: {}
  };
}
