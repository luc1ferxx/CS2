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
  getAuthSession,
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
  refreshSession: () => Promise<boolean>;
  signIn: (returnTo?: string) => void;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reduceAuthState, { status: "checking" });

  const refreshSession = useCallback(async () => {
    try {
      await getAuthSession();
      dispatch({ type: "sessionAuthenticated" });
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

  const value = useMemo(
    () => ({ state, refreshSession, signIn, signOut }),
    [refreshSession, signIn, signOut, state]
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
