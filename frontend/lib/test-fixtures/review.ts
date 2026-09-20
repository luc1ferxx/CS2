// Typed factories for component and page tests. Every factory returns a
// complete, valid object with the smallest realistic content, and takes
// overrides so a test states only what it cares about.
import type { RenderJobStatus, RenderWorkerStatus } from "@/lib/api";
import type { CoachingEvent } from "@/types/coaching";
import type { DemoIngestionStatus, DemoStatus, DemoSummary } from "@/types/demo";
import type { ReplayData, ReplayFrame, ReplayPlayer, ReplayRound, ReplayVideo } from "@/types/replay";

export const FIXTURE_TIME = "2026-09-18T12:00:00Z";
export const T_ENTRY_ID = "76561198000000001";
export const CT_ANCHOR_ID = "76561198000000002";

export function ingestion(overrides: Partial<DemoIngestionStatus> = {}): DemoIngestionStatus {
  return {
    phase: "ready",
    active: false,
    stale: false,
    retryable: false,
    attemptCount: 1,
    jobId: "job-parse-1",
    jobType: "real_parse",
    jobStatus: "completed",
    hasSourceDemo: true,
    updatedAt: FIXTURE_TIME,
    startedAt: FIXTURE_TIME,
    finishedAt: FIXTURE_TIME,
    failure: null,
    ...overrides
  };
}

export function demoStatus(overrides: Partial<DemoStatus> = {}): DemoStatus {
  return {
    id: "demo-1",
    name: "Mock Match demo-1",
    original_filename: "mock_demo_1.dem",
    status: "completed",
    archived: false,
    map_name: "de_inferno",
    round_count: 2,
    coaching_event_count: 1,
    error_message: null,
    updated_at: FIXTURE_TIME,
    completed_at: FIXTURE_TIME,
    ingestion: ingestion(),
    ...overrides
  };
}

export function demoSummary(overrides: Partial<DemoSummary> = {}): DemoSummary {
  return {
    id: "demo-1",
    name: "Mock Match demo-1",
    original_filename: "mock_demo_1.dem",
    map_name: "de_inferno",
    tick_rate: 64,
    round_count: 2,
    coaching_event_count: 1,
    status: "completed",
    archived: false,
    error_message: null,
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
    completed_at: FIXTURE_TIME,
    video_status: "pending",
    video_source: "mock",
    latest_render_status: null,
    ingestion: ingestion(),
    ...overrides
  };
}

export function replayVideo(overrides: Partial<ReplayVideo> = {}): ReplayVideo {
  return {
    status: "pending",
    url: null,
    durationSeconds: 0,
    tickStart: 0,
    tickEnd: 0,
    tickRate: 64,
    source: "mock",
    errorCode: null,
    errorMessage: null,
    timeOriginSeconds: 0,
    povSteamId: null,
    renderJobId: null,
    ...overrides
  };
}

export function replayPlayers(): ReplayPlayer[] {
  return [
    { id: T_ENTRY_ID, name: "T Entry", side: "T", color: "#f5b542" },
    { id: CT_ANCHOR_ID, name: "CT Anchor", side: "CT", color: "#2ed3d0" }
  ];
}

export function replayRounds(): ReplayRound[] {
  return [
    { roundNumber: 1, startTick: 100, freezeEndTick: 164, endTick: 900, winnerSide: "CT" },
    { roundNumber: 2, startTick: 1000, freezeEndTick: 1064, endTick: 1800, winnerSide: "T" }
  ];
}

function replayFrame(tick: number, roundNumber: number): ReplayFrame {
  return {
    tick,
    timeSeconds: tick / 64,
    roundNumber,
    players: replayPlayers().map((player, index) => ({
      id: player.id,
      name: player.name,
      side: player.side,
      x: 100 * (index + 1),
      y: 200 * (index + 1),
      alive: true,
      hp: 100,
      hasBomb: index === 0
    })),
    bombState: { status: "carried", carrierPlayerId: T_ENTRY_ID }
  };
}

export function replayData(overrides: Partial<ReplayData> = {}): ReplayData {
  return {
    demoId: "demo-1",
    contractVersion: "replay_contract_v1",
    mapName: "de_inferno",
    tickRate: 64,
    video: replayVideo(),
    rounds: replayRounds(),
    players: replayPlayers(),
    frames: [replayFrame(100, 1), replayFrame(500, 1), replayFrame(1000, 2), replayFrame(1400, 2)],
    events: [],
    generatedAt: FIXTURE_TIME,
    diagnostics: null,
    ...overrides
  };
}

export function coachingEvent(overrides: Partial<CoachingEvent> = {}): CoachingEvent {
  return {
    id: "event-1",
    demo_id: "demo-1",
    round_number: 1,
    player_id: T_ENTRY_ID,
    player_name: "T Entry",
    tick_start: 400,
    tick_end: 400,
    category: "positioning",
    severity: "medium",
    title: "Isolated entry",
    message: "Entry pushed without a trade partner.",
    structured_context_json: { ruleId: "isolated_entry" },
    confidence: 0.7,
    created_at: FIXTURE_TIME,
    ...overrides
  };
}

export function renderJob(overrides: Partial<RenderJobStatus> = {}): RenderJobStatus {
  return {
    job_id: "job-render-1",
    demo_id: "demo-1",
    job_type: "render_clip",
    status: "queued",
    source: "rendered",
    video_status: "queued",
    video: null,
    tick_start: 400,
    tick_end: 1040,
    tick_rate: 64,
    duration_seconds: 10,
    event_id: "event-1",
    player_id: T_ENTRY_ID,
    pov_steam_id: T_ENTRY_ID,
    round_number: 1,
    render_preset: "event_clip_v1",
    metadata: { tickStart: 400, tickEnd: 1040, tickRate: 64, durationSeconds: 10 },
    error_code: null,
    error_message: null,
    created_at: FIXTURE_TIME,
    started_at: null,
    finished_at: null,
    ...overrides
  };
}

export function renderWorkerStatus(overrides: Partial<RenderWorkerStatus> = {}): RenderWorkerStatus {
  return {
    mode: "external",
    required: true,
    connected: false,
    status: "never_seen",
    last_seen_at: null,
    age_seconds: null,
    busy_rendering: false,
    ...overrides
  };
}
