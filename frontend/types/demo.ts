export type DemoProcessingStatus =
  | "queued"
  | "parsing"
  | "analyzing"
  | "completed"
  | "failed";

export interface DemoSummary {
  id: string;
  name: string;
  original_filename: string;
  map_name: string;
  tick_rate: number;
  round_count: number;
  coaching_event_count: number;
  status: DemoProcessingStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface DemoStatus {
  id: string;
  status: DemoProcessingStatus;
  map_name: string;
  round_count: number;
  coaching_event_count: number;
  error_message: string | null;
  updated_at: string;
  completed_at: string | null;
}
