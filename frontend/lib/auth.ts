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
}

export type AuthAction =
  | { type: "unauthorized" }
  | { type: "sessionAuthenticated" }
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
    return { status: "authenticated" };
  }
  if (action.type === "unauthorized") {
    return state.status === "authenticated" || state.status === "expired"
      ? { status: "expired" }
      : { status: "anonymous" };
  }
  return state;
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
