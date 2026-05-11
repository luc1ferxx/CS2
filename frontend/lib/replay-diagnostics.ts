import type { CoachingEvent } from "@/types/coaching";
import type {
  ReplayContractDiagnostics,
  ReplayData,
  ReplayEmptyState,
  ReplayVideoStatus
} from "@/types/replay";
import type { RenderJobStatus } from "./api";

export interface RenderStateSummary {
  label: string;
  tone: "idle" | "active" | "ready" | "failed";
  active: boolean;
  message: string;
}

export interface ReplayDetailDiagnostics {
  contractVersion: string;
  normalizedLegacy: boolean;
  counts: {
    parserEvents: number;
    coachingEvents: number;
    rounds: number;
    players: number;
    frames: number;
  };
  missingFields: string[];
  degradedFields: string[];
  missingEventFamilies: string[];
  emptyStates: ReplayEmptyState[];
  warnings: string[];
  usableReplay: boolean;
  renderState: RenderStateSummary;
}

const ACTIVE_RENDER_STATES = new Set(["queued", "processing", "rendering"]);

export function buildReplayDiagnostics(
  replay: ReplayData,
  coachingEvents: CoachingEvent[],
  renderJobs: Pick<RenderJobStatus, "status" | "job_type" | "error_message">[]
): ReplayDetailDiagnostics {
  const contractDiagnostics = replay.diagnostics ?? runtimeDiagnostics(replay);
  const parserEventCount = safeArray(replay.events).length;
  const coachingEventCount = safeArray(coachingEvents).length;
  const roundCount = safeArray(replay.rounds).length;
  const playerCount = safeArray(replay.players).length;
  const frameCount = safeArray(replay.frames).length;
  const emptyStates: ReplayEmptyState[] = [];

  if (roundCount === 0) {
    emptyStates.push("no-rounds");
  }
  if (parserEventCount === 0) {
    emptyStates.push("no-parser-events");
  }
  if (coachingEventCount === 0) {
    emptyStates.push("no-coaching-events");
  }
  if (frameCount === 0) {
    emptyStates.push("no-frames");
  }

  const renderJob = renderJobs[0];
  const renderStatus = renderJob?.status ?? replay.video?.status ?? "pending";
  const renderError = renderJob?.error_message ?? replay.video?.errorMessage ?? null;

  return {
    contractVersion: contractDiagnostics.contractVersion,
    normalizedLegacy: contractDiagnostics.normalizedLegacy,
    counts: {
      parserEvents: parserEventCount,
      coachingEvents: coachingEventCount,
      rounds: roundCount,
      players: playerCount,
      frames: frameCount
    },
    missingFields: safeArray(contractDiagnostics.missingFields),
    degradedFields: safeArray(contractDiagnostics.degradedFields),
    missingEventFamilies: safeArray(contractDiagnostics.missingEventFamilies),
    emptyStates,
    warnings: diagnosticWarnings(contractDiagnostics, emptyStates),
    usableReplay: roundCount > 0 && frameCount > 0,
    renderState: renderStateSummary(renderStatus, renderError)
  };
}

export function replayHasUsableFrames(replay: ReplayData): boolean {
  return safeArray(replay.frames).length > 0;
}

export function renderStateSummary(
  status: ReplayVideoStatus | string | null | undefined,
  errorMessage: string | null | undefined
): RenderStateSummary {
  const value = typeof status === "string" && status.trim() ? status : "pending";
  if (value === "failed") {
    return {
      label: "Render failed",
      tone: "failed",
      active: false,
      message: errorMessage || "Render output is unavailable; the synced mock shell remains usable."
    };
  }
  if (ACTIVE_RENDER_STATES.has(value)) {
    return {
      label: `Render ${value}`,
      tone: "active",
      active: true,
      message: "Render output is not ready yet; replay controls remain synced to the mock shell."
    };
  }
  if (value === "ready") {
    return {
      label: "Render ready",
      tone: "ready",
      active: false,
      message: "Playable media or mock replay metadata is ready."
    };
  }
  return {
    label: "Render not requested",
    tone: "idle",
    active: false,
    message: "No render output is attached; the interactive replay remains available."
  };
}

function runtimeDiagnostics(replay: ReplayData): ReplayContractDiagnostics {
  const parserEvents = safeArray(replay.events);
  return {
    contractVersion: replay.contractVersion ?? "runtime",
    normalizedLegacy: false,
    parserEventCount: parserEvents.length,
    roundCount: safeArray(replay.rounds).length,
    playerCount: safeArray(replay.players).length,
    frameCount: safeArray(replay.frames).length,
    missingFields: [],
    degradedFields: [],
    eventFamilyCounts: {},
    missingEventFamilies: parserEvents.length === 0 ? ["combat", "damage", "objective", "utility"] : []
  };
}

function diagnosticWarnings(
  diagnostics: ReplayContractDiagnostics,
  emptyStates: ReplayEmptyState[]
): string[] {
  const warnings: string[] = [];
  if (diagnostics.normalizedLegacy) {
    warnings.push("legacy replay contract normalized at load time.");
  }
  if (diagnostics.missingFields.length > 0) {
    warnings.push(`Missing optional fields: ${diagnostics.missingFields.join(", ")}.`);
  }
  if (diagnostics.degradedFields.length > 0) {
    warnings.push(`Malformed optional fields ignored: ${diagnostics.degradedFields.join(", ")}.`);
  }
  if (emptyStates.includes("no-rounds")) {
    warnings.push("No rounds are available; quick jumps and timeline range are limited.");
  }
  if (emptyStates.includes("no-frames")) {
    warnings.push("No frames are available; tactical map and mock first-person replay are paused.");
  }
  if (emptyStates.includes("no-parser-events")) {
    warnings.push("No parser events are available; parser markers and event-derived round metrics are empty.");
  }
  if (emptyStates.includes("no-coaching-events")) {
    warnings.push("No coaching events are available for this replay.");
  }
  return warnings.slice(0, 6);
}

function safeArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}
