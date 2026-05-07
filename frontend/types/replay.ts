export type PlayerSide = "T" | "CT";
export type BombStatus = "carried" | "planted" | "dropped";

export interface ReplayRound {
  roundNumber: number;
  startTick: number;
  freezeEndTick: number;
  endTick: number;
  winnerSide: PlayerSide;
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

export interface ReplayFrame {
  tick: number;
  timeSeconds: number;
  roundNumber: number;
  players: ReplayFramePlayer[];
  bombState: BombState;
}

export type ReplayVideoStatus = "pending" | "queued" | "rendering" | "ready" | "failed";
export type ReplayVideoSource = "mock" | "rendered";

export interface ReplayVideo {
  status: ReplayVideoStatus;
  url: string | null;
  durationSeconds: number;
  tickStart: number;
  tickEnd: number;
  tickRate: number;
  source: ReplayVideoSource;
  errorMessage?: string | null;
}

export interface ReplayData {
  demoId: string;
  mapName: string;
  tickRate: number;
  video: ReplayVideo;
  rounds: ReplayRound[];
  players: ReplayPlayer[];
  frames: ReplayFrame[];
  generatedAt: string;
}
