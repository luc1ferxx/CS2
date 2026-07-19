export type SteamMatchStatus =
  | "discovered"
  | "demo_pending"
  | "downloading"
  | "parsing"
  | "ready"
  | "unavailable";

export type SteamMatchSource = "steam_match_history";

export type SteamConnectionStatus =
  | "disconnected"
  | "connected"
  | "syncing"
  | "caught_up"
  | "retry_wait"
  | "authorization_required"
  | "error";

export interface SteamConnection {
  connected: boolean;
  status: SteamConnectionStatus;
  credentials_configured: boolean;
  scheduled_sync_enabled: boolean;
  last_sync_started_at: string | null;
  last_sync_completed_at: string | null;
  next_retry_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
}

export interface SteamConnectionCredentials {
  game_auth_code: string;
  initial_match_sharing_code: string;
}

export interface SteamMatch {
  id: string;
  status: SteamMatchStatus;
  source: SteamMatchSource;
  discovered_at: string;
  updated_at: string;
  demo_id: string | null;
}

export interface SteamSyncResult {
  discovered_count: number;
  caught_up: boolean;
  limit_reached: boolean;
  status: SteamConnectionStatus;
}
