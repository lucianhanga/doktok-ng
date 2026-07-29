import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as SecureStore from "expo-secure-store";
import AsyncStorage from "@react-native-async-storage/async-storage";

import { login as apiLogin, me as apiMe, type AuthUser } from "../api/auth";

// Auth state (#771): bearer token + saved credentials, persisted so a dev build logs in once.
// The OS keystore (SecureStore) is used when available; on emulators WITHOUT a lock screen the
// Android keystore is unusable (secure entries silently fail), so we fall back to AsyncStorage
// (unencrypted - acceptable for dev builds; real devices with a lock screen keep the keystore).
const TOKEN_KEY = "doktok.auth.token";
const CREDS_KEY = "doktok.auth.creds";

async function kvGet(key: string): Promise<string | null> {
  try {
    const v = await SecureStore.getItemAsync(key);
    if (v !== null) return v;
  } catch {
    // keystore unavailable (emulator without a lock screen) -> fall through
  }
  return AsyncStorage.getItem(key);
}

async function kvSet(key: string, value: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(key, value);
    return;
  } catch {
    // keystore unavailable -> AsyncStorage below
  }
  await AsyncStorage.setItem(key, value);
}

async function kvDelete(key: string): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(key);
  } catch {
    // ignore - remove the fallback too
  }
  await AsyncStorage.removeItem(key);
}

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
      const credsRaw = await kvGet(CREDS_KEY);
      const creds = credsRaw ? (JSON.parse(credsRaw) as SavedCredentials) : null;
      setSavedCredentials(creds);
      if (creds) {
        try {
          const res = await apiLogin(creds.tenantId, creds.email, creds.password);
          await kvSet(TOKEN_KEY, res.access_token);
          setToken(res.access_token);
          setUser(res.user);
          setStatus("signedIn");
          return;
        } catch {
          // saved credentials no longer valid (password changed/server down) -> show login
        }
      }
      const saved = await kvGet(TOKEN_KEY);
      if (saved) {
        try {
          const u = await apiMe(saved);
          setToken(saved);
          setUser(u);
          setStatus("signedIn");
          return;
        } catch {
          await kvDelete(TOKEN_KEY);
        }
      }
      setStatus("signedOut");
    })();
  }, []);

  const signIn = useCallback(async (creds: SavedCredentials, opts?: { remember?: boolean }) => {
    const res = await apiLogin(creds.tenantId.trim(), creds.email.trim(), creds.password);
    await kvSet(TOKEN_KEY, res.access_token);
    if (opts?.remember !== false) {
      await kvSet(CREDS_KEY, JSON.stringify(creds));
      setSavedCredentials(creds);
    } else {
      await kvDelete(CREDS_KEY);
      setSavedCredentials(null);
    }
    setToken(res.access_token);
    setUser(res.user);
    setStatus("signedIn");
  }, []);

  const signOut = useCallback(async () => {
    await kvDelete(TOKEN_KEY);
    await kvDelete(CREDS_KEY);
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
