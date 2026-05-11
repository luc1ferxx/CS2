export type PlayerSide = "T" | "CT";
export type BombStatus = "carried" | "planted" | "dropped";

export interface ReplayRound {
  roundNumber: number;
  startTick: number;
  freezeEndTick: number;
  endTick: number;
  winnerSide: PlayerSide;
  winnerReason?: string;
}

export interface ReplayPlayer {
  id: string;
  name: string;
  side: PlayerSide;
  color: string;
}

export interface ReplayFramePlayer {
  id: string;
  name: string;
  side: PlayerSide;
  x: number;
  y: number;
  alive: boolean;
  hp: number;
  hasBomb: boolean;
}

export interface BombState {
  status: BombStatus;
  carrierPlayerId?: string;
  x?: number;
  y?: number;
  site?: string;
}

export type ReplayMapConfidence = "calibrated" | "approximate" | "fallback";

export type ReplayMapTransform =
  | {
      type: "overview";
      posX: number;
      posY: number;
      scale: number;
      imageSize: number;
    }
  | {
      type: "bounds";
      minX: number;
      maxX: number;
      minY: number;
      maxY: number;
    }
  | {
      type: "dynamicBounds";
    };

export interface ReplayMapMetadata {
  mapName: string;
  displayName: string;
  radarImagePath: string | null;
  secondaryRadarImagePath?: string | null;
  calibrated: boolean;
  confidence: ReplayMapConfidence;
  attribution: string;
  source: string | null;
  transform: ReplayMapTransform;
}

export interface ReplayFrame {
  tick: number;
  timeSeconds: number;
  roundNumber: number;
  players: ReplayFramePlayer[];
  bombState: BombState;
}

export type ReplayVideoStatus = "pending" | "queued" | "rendering" | "ready" | "failed";
export type ReplayVideoSource = "manual_upload" | "mock" | "rendered";

export interface ReplayVideo {
  status: ReplayVideoStatus;
  url: string | null;
  storageKey?: string | null;
  durationSeconds: number;
  tickStart: number;
  tickEnd: number;
  tickRate: number;
  source: ReplayVideoSource;
  errorMessage?: string | null;
  timeOriginSeconds: number;
}

export type ReplayEventType =
  | "kill"
  | "death"
  | "damage"
  | "bomb_planted"
  | "bomb_defused"
  | "bomb_exploded"
  | "smoke"
  | "flash"
  | "molotov"
  | "he"
  | "round_start"
  | "round_end";

export type ReplayEventSource = "parser" | "mock" | "legacy";

export interface ReplayEvent {
  id: string;
  type: ReplayEventType;
  tick: number;
  roundNumber: number;
  source: ReplayEventSource | string;
  playerIds: string[];
  playerId?: string | null;
  playerName?: string | null;
  side?: PlayerSide | null;
  x?: number | null;
  y?: number | null;
  label: string;
  metadata?: Record<string, unknown>;
}

export type ReplayEmptyState =
  | "no-rounds"
  | "no-parser-events"
  | "no-coaching-events"
  | "no-frames";

export interface ReplayContractDiagnostics {
  contractVersion: string;
  normalizedLegacy: boolean;
  parserEventCount: number;
  roundCount: number;
  playerCount: number;
  frameCount: number;
  missingFields: string[];
  degradedFields: string[];
  eventFamilyCounts: Record<string, number>;
  missingEventFamilies: string[];
}

export interface ReplayData {
  demoId: string;
  contractVersion?: string;
  mapName: string;
  mapMetadata?: ReplayMapMetadata;
  tickRate: number;
  video: ReplayVideo;
  rounds: ReplayRound[];
  players: ReplayPlayer[];
  frames: ReplayFrame[];
  events: ReplayEvent[];
  generatedAt: string;
  diagnostics?: ReplayContractDiagnostics | null;
}
