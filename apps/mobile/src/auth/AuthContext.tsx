import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as SecureStore from "expo-secure-store";

import { login as apiLogin, me as apiMe, type AuthUser } from "../api/auth";

// Auth state (#771): bearer token in the OS keystore (SecureStore), restored on start, cleared on
// logout or when the backend rejects it (401 on /auth/me).
const TOKEN_KEY = "doktok.auth.token";

interface AuthState {
  status: "loading" | "signedOut" | "signedIn";
  token: string | null;
  user: AuthUser | null;
  signIn: (tenantId: string, email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  status: "loading",
  token: null,
  user: null,
  signIn: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);

  // Restore the persisted token once at startup and validate it against the backend.
  useEffect(() => {
    (async () => {
      const saved = await SecureStore.getItemAsync(TOKEN_KEY);
      if (!saved) {
        setStatus("signedOut");
        return;
      }
      try {
        const u = await apiMe(saved);
        setToken(saved);
        setUser(u);
        setStatus("signedIn");
      } catch {
        await SecureStore.deleteItemAsync(TOKEN_KEY);
        setStatus("signedOut");
      }
    })();
  }, []);

  const signIn = useCallback(async (tenantId: string, email: string, password: string) => {
    const res = await apiLogin(tenantId.trim(), email.trim(), password);
    await SecureStore.setItemAsync(TOKEN_KEY, res.access_token);
    setToken(res.access_token);
    setUser(res.user);
    setStatus("signedIn");
  }, []);

  const signOut = useCallback(async () => {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    setToken(null);
    setUser(null);
    setStatus("signedOut");
  }, []);

  const value = useMemo(
    () => ({ status, token, user, signIn, signOut }),
    [status, token, user, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
