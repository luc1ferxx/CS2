export type CoachingCategory = "positioning" | "trading" | "timing";
export type CoachingSeverity = "info" | "low" | "medium" | "high" | "critical";

export interface CoachingEvent {
  id: string;
  demo_id: string;
  round_number: number;
  player_id: string;
  player_name: string;
  tick_start: number;
  tick_end: number;
  category: CoachingCategory;
  severity: CoachingSeverity;
  title: string;
  message: string;
  structured_context_json: Record<string, unknown>;
  confidence: number;
  created_at: string;
}
