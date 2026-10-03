export type PlayerSide = "T" | "CT";
export type BombStatus = "unknown" | "carried" | "planted" | "dropped" | "defused" | "exploded";

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
  z?: number;
  alive: boolean;
  hp: number;
  hasBomb: boolean;
}

export interface BombState {
  status: BombStatus;
  carrierPlayerId?: string;
  x?: number;
  y?: number;
  z?: number;
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
  lowerLevelMaxZ?: number;
  calibrationSource?: string;
  calibrated: boolean;
  confidence: ReplayMapConfidence;
  attribution: string;
  source: string | null;
  transform: ReplayMapTransform;
  /** World units per radar percentage point (v2 API responses; absent on older ones). */
  worldUnitsPerPercent?: { x: number; y: number };
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
  durationSeconds: number;
  tickStart: number;
  tickEnd: number;
  tickRate: number;
  source: ReplayVideoSource;
  errorCode?: string | null;
  errorMessage?: string | null;
  timeOriginSeconds: number;
  povSteamId?: string | null;
  renderJobId?: string | null;
}

export type ReplayEventType =
  | "kill"
  | "death"
  | "damage"
  | "bomb_planted"
  | "bomb_defused"
  | "bomb_exploded"
  | "bomb_pickup"
  | "bomb_dropped"
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
  z?: number | null;
  label: string;
  metadata?: Record<string, unknown>;
}

export type ReplayEmptyState =
  | "no-rounds"
  | "no-parser-events"
  | "no-coaching-events"
  | "no-frames";

/** The five grenade kinds carried in `playerStates` and thrown in `utility`. */
export type UtilityType = "smoke" | "flash" | "he" | "molotov" | "decoy";

/**
 * One change point of a player's equipment/economy (replay contract v2).
 * A new entry is written only when at least one field changes; entries are
 * sorted by tick and the state at tick t is the last entry with tick <= t.
 * Fields absent from an entry are unknown for that demo (not "zero").
 */
export interface ReplayPlayerState {
  tick: number;
  money?: number;
  armor?: number;
  helmet?: boolean;
  defuser?: boolean;
  /** Active weapon display name as the demo reports it (e.g. "AK-47"); null when dead or holding nothing. */
  weapon?: string | null;
  /** One entry per carried grenade (two flashes = ["flash", "flash"]). */
  grenades?: UtilityType[];
  equipValue?: number;
}

/** A trajectory point in the same radar-percent space as frame players (x/y 0..100). */
export interface ReplayUtilityPoint {
  tick: number;
  x: number;
  y: number;
  /** World-unit z, kept for multi-floor maps (Nuke lower-level test). */
  z?: number;
}

/** One thrown grenade (replay contract v2). */
export interface ReplayUtility {
  /** Deterministic: `utility-{type}-{entityId}-{throwTick}`. */
  id: string;
  type: UtilityType;
  throwerId: string | null;
  throwerName: string | null;
  throwerSide: PlayerSide | null;
  roundNumber: number;
  throwTick: number;
  /** Detonation / settle tick (the matching detonate event when found). */
  detonateTick: number;
  /** Effect end: smoke expired / fire expired; = detonateTick for flash, HE, decoy. */
  endTick: number;
  /** Flight path, sorted by tick, first point at throwTick, last at detonation (landing point). */
  points: ReplayUtilityPoint[];
}

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
  /** v2: number of utility throws kept. Absent on older API responses. */
  utilityCount?: number;
  /** v2: number of players with a playerStates track. Absent on older API responses. */
  playerStateCount?: number;
  /** v3: `"usercmd"` when key inputs were extracted, else null. Absent on older API responses. */
  inputSource?: string | null;
  /** v3: number of players with an inputs track. Absent on older API responses. */
  inputPlayerCount?: number;
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
  /** v2 only. The API always sends `{}` for older replays; optional so fixtures stay valid. */
  playerStates?: Record<string, ReplayPlayerState[]>;
  /** v2 only. The API always sends `[]` for older replays; optional so fixtures stay valid. */
  utility?: ReplayUtility[];
  /**
   * v3 only: per-player key change points as compact `[tick, mask]` tuples, sorted by tick.
   * Mask bits: 1 attack, 2 jump, 4 duck, 8 forward, 16 back, 512 left, 1024 right,
   * 2048 attack2, 0x10000 walk. The mask holds from its tick until the next entry; a
   * `[tick, 0]` entry marks death. Optional per demo (needs usercmd data); older replays get `{}`.
   */
  inputs?: Record<string, Array<[number, number]>>;
  generatedAt: string;
  diagnostics?: ReplayContractDiagnostics | null;
}
