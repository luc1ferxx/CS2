"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type ReactNode
} from "react";

import {
  getAuthLoginUrl,
  getAuthMe,
  getConfiguredAuthProvider,
  isApiError,
  logoutAuthSession,
  onUnauthorized
} from "@/lib/api";
import {
  reduceAuthState,
  sanitizeReturnTo,
  type AuthState
} from "@/lib/auth";

interface AuthContextValue {
  state: AuthState;
  provider: "steam" | "oidc";
  refreshSession: () => Promise<boolean>;
  signIn: (returnTo?: string) => void;
  signOut: () => Promise<void>;
  // The session is already gone on the server (the account was deleted): show signed out, send nothing.
  markSignedOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reduceAuthState, { status: "checking" });
  const provider = getConfiguredAuthProvider();

  const refreshSession = useCallback(async () => {
    try {
      const session = await getAuthMe();
      dispatch({
        type: "sessionAuthenticated",
        account: session.account,
        capabilities: session.capabilities
      });
      return true;
    } catch (error) {
      if (isApiError(error) && error.status === 401) {
        dispatch({ type: "unauthorized" });
        return false;
      }
      dispatch({
        type: "sessionFailed",
        message: error instanceof Error ? error.message : "Authentication service unavailable"
      });
      return false;
    }
  }, []);

  useEffect(() => onUnauthorized(() => dispatch({ type: "unauthorized" })), []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const signIn = useCallback((returnTo?: string) => {
    const currentPath = `${window.location.pathname}${window.location.search}`;
    window.location.assign(
      getAuthLoginUrl(sanitizeReturnTo(returnTo ?? currentPath))
    );
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logoutAuthSession();
      dispatch({ type: "signedOut" });
    } catch (error) {
      if (isApiError(error) && error.status === 401) {
        dispatch({ type: "signedOut" });
        return;
      }
      dispatch({
        type: "sessionFailed",
        message: error instanceof Error ? error.message : "Sign out failed"
      });
    }
  }, []);

  const markSignedOut = useCallback(() => dispatch({ type: "signedOut" }), []);

  const value = useMemo(
    () => ({ state, provider, refreshSession, signIn, signOut, markSignedOut }),
    [markSignedOut, provider, refreshSession, signIn, signOut, state]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
