export const DEFAULT_RETURN_TO = "/dashboard";

export type AuthStatus =
  | "checking"
  | "authenticated"
  | "anonymous"
  | "expired"
  | "error";

export interface AuthState {
  status: AuthStatus;
  message?: string;
  account?: AuthAccount;
  capabilities?: AuthCapabilities;
}

export interface AuthAccount {
  displayName: string;
  avatarUrl: string | null;
  provider: "steam" | "oidc" | "development";
  // The signed-in viewer's own SteamID64; only Steam accounts carry it.
  steamId?: string | null;
}

// What the API will serve this session. The backend 404s the matching routes
// regardless; these flags only keep the UI from offering them.
export interface AuthCapabilities {
  devTools: boolean;
  renderClips: boolean;
}

export const NO_CAPABILITIES: AuthCapabilities = { devTools: false, renderClips: false };

export type AuthAction =
  | { type: "unauthorized" }
  | { type: "sessionAuthenticated"; account: AuthAccount; capabilities?: unknown }
  | { type: "signedOut" }
  | { type: "sessionFailed"; message: string };

export function reduceAuthState(
  state: AuthState,
  action: AuthAction
): AuthState {
  if (action.type === "signedOut") {
    return { status: "anonymous" };
  }
  if (action.type === "sessionFailed") {
    return { status: "error", message: action.message };
  }
  if (action.type === "sessionAuthenticated") {
    return {
      status: "authenticated",
      account: action.account,
      capabilities: authCapabilities(action.capabilities)
    };
  }
  if (action.type === "unauthorized") {
    return state.status === "authenticated" || state.status === "expired"
      ? { status: "expired" }
      : { status: "anonymous" };
  }
  return state;
}

// An older API omits the object; anything but an explicit true stays off.
export function authCapabilities(value: unknown): AuthCapabilities {
  if (typeof value !== "object" || value === null) {
    return { ...NO_CAPABILITIES };
  }
  const flags = value as { devTools?: unknown; renderClips?: unknown };
  return { devTools: flags.devTools === true, renderClips: flags.renderClips === true };
}

export function sanitizeReturnTo(value: string | null | undefined): string {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  ) {
    return DEFAULT_RETURN_TO;
  }
  return value;
}
