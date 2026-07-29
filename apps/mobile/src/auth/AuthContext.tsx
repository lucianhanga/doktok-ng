import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as SecureStore from "expo-secure-store";

import { login as apiLogin, me as apiMe, type AuthUser } from "../api/auth";

// Auth state (#771): bearer token in the OS keystore (SecureStore), restored on start, cleared on
// logout. Saved credentials (also keystore-encrypted) let the app re-login transparently when the
// 1h token expires - on a dev build you only type the password once.
const TOKEN_KEY = "doktok.auth.token";
const CREDS_KEY = "doktok.auth.creds";

export interface SavedCredentials {
  tenantId: string;
  email: string;
  password: string;
}

interface AuthState {
  status: "loading" | "signedOut" | "signedIn";
  token: string | null;
  user: AuthUser | null;
  savedCredentials: SavedCredentials | null;
  signIn: (creds: SavedCredentials, opts?: { remember?: boolean }) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  status: "loading",
  token: null,
  user: null,
  savedCredentials: null,
  signIn: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [savedCredentials, setSavedCredentials] = useState<SavedCredentials | null>(null);

  // Restore once at startup: saved credentials first (fresh token), then any persisted token.
  useEffect(() => {
    (async () => {
      const credsRaw = await SecureStore.getItemAsync(CREDS_KEY);
      const creds = credsRaw ? (JSON.parse(credsRaw) as SavedCredentials) : null;
      setSavedCredentials(creds);
      if (creds) {
        try {
          const res = await apiLogin(creds.tenantId, creds.email, creds.password);
          await SecureStore.setItemAsync(TOKEN_KEY, res.access_token);
          setToken(res.access_token);
          setUser(res.user);
          setStatus("signedIn");
          return;
        } catch {
          // saved credentials no longer valid (password changed/server down) -> show login
        }
      }
      const saved = await SecureStore.getItemAsync(TOKEN_KEY);
      if (saved) {
        try {
          const u = await apiMe(saved);
          setToken(saved);
          setUser(u);
          setStatus("signedIn");
          return;
        } catch {
          await SecureStore.deleteItemAsync(TOKEN_KEY);
        }
      }
      setStatus("signedOut");
    })();
  }, []);

  const signIn = useCallback(async (creds: SavedCredentials, opts?: { remember?: boolean }) => {
    const res = await apiLogin(creds.tenantId.trim(), creds.email.trim(), creds.password);
    await SecureStore.setItemAsync(TOKEN_KEY, res.access_token);
    if (opts?.remember !== false) {
      await SecureStore.setItemAsync(CREDS_KEY, JSON.stringify(creds));
      setSavedCredentials(creds);
    } else {
      await SecureStore.deleteItemAsync(CREDS_KEY);
      setSavedCredentials(null);
    }
    setToken(res.access_token);
    setUser(res.user);
    setStatus("signedIn");
  }, []);

  const signOut = useCallback(async () => {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    await SecureStore.deleteItemAsync(CREDS_KEY);
    setToken(null);
    setUser(null);
    setSavedCredentials(null);
    setStatus("signedOut");
  }, []);

  const value = useMemo(
    () => ({ status, token, user, savedCredentials, signIn, signOut }),
    [status, token, user, savedCredentials, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
