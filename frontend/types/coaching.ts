export type CoachingCategory = "objective" | "positioning" | "trading" | "timing" | "utility";
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
  // Per-rule evidence; see CoachingDeathImpact and CoachingExtraReason for the death-card fields.
  structured_context_json: Record<string, unknown>;
  confidence: number;
  created_at: string;
  // The current viewer's own verdict; null or absent when they have not rated it.
  feedback?: CoachingFeedback | null;
}

// Optional context the analyzer stores on death cards (untraded_death, isolated_entry) in
// structured_context_json; older events lack it. Read it through coachingImpact,
// coachingExtraReasons and coachingWeapon in lib/coaching-review, which drop malformed values.
export interface CoachingAliveCount {
  own: number;
  enemy: number;
}

// `impact`: what the death cost. Fields the analyzer could not tell are left out.
export interface CoachingDeathImpact {
  // null when the round winner is unknown.
  roundLost?: boolean | null;
  firstDeath?: boolean;
  aliveBefore?: CoachingAliveCount;
  aliveAfter?: CoachingAliveCount;
  manDisadvantage?: boolean;
}

// `extraReasons`: another rule's finding about the same death, folded into this card.
export interface CoachingExtraReason {
  ruleId: string;
  spacingType?: string;
  // World units.
  distance?: number;
  durationSeconds?: number;
  tick?: number;
}

export type CoachingVerdict = "helpful" | "irrelevant" | "unsure";

export interface CoachingFeedback {
  verdict: CoachingVerdict;
  note: string | null;
  updated_at: string;
}
