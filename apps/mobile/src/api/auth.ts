import { apiFetch } from "./client";

// Auth API shapes (#771) - mirrors /api/v1/auth/login + /auth/me on the backend.
export interface AuthUser {
  id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export function login(tenantId: string, email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: { tenant_id: tenantId, email, password },
  });
}

export function me(token: string): Promise<AuthUser> {
  return apiFetch<AuthUser>("/api/v1/auth/me", { token });
}
