export type DemoProcessingStatus =
  | "queued"
  | "parsing"
  | "analyzing"
  | "completed"
  | "failed";

export interface ParseFailureMetadata {
  errorCode: string;
  message: string;
  failedAt: string | null;
  updatedAt: string;
  retryable: boolean;
  attemptCount: number;
}

export interface DemoIngestionStatus {
  phase: string;
  active: boolean;
  stale: boolean;
  retryable: boolean;
  attemptCount: number;
  jobId: string | null;
  jobType: string | null;
  jobStatus: string | null;
  hasSourceDemo: boolean;
  updatedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  failure: ParseFailureMetadata | null;
}

export interface DemoSummary {
  id: string;
  name: string;
  original_filename: string;
  map_name: string;
  tick_rate: number;
  round_count: number;
  coaching_event_count: number;
  status: DemoProcessingStatus;
  archived: boolean;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  video_status: string | null;
  video_source: string | null;
  latest_render_status: string | null;
  ingestion: DemoIngestionStatus | null;
}

export interface DemoStatus {
  id: string;
  name: string | null;
  original_filename: string | null;
  status: DemoProcessingStatus;
  archived: boolean;
  map_name: string;
  round_count: number;
  coaching_event_count: number;
  error_message: string | null;
  updated_at: string;
  completed_at: string | null;
  ingestion: DemoIngestionStatus | null;
}
