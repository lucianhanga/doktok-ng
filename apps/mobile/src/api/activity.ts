import { apiFetch } from "./client";
import type { AuditEvent } from "./documentDetail";

// Activity/audit feed (#779): tenant-scoped, read-only. Non-admin callers are already scoped
// server-side to document/feature/entity events (F-19); admins see everything.
export type { AuditEvent };

export function fetchActivity(
  token: string,
  opts: { limit?: number; offset?: number } = {},
): Promise<AuditEvent[]> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 100));
  params.set("offset", String(opts.offset ?? 0));
  return apiFetch<AuditEvent[]>(`/api/v1/audit?${params.toString()}`, { token });
}
