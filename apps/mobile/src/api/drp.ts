import { apiFetch } from "./client";

// DRP status (#779): read-only Disaster Recovery Plan view - backup-leg freshness + static config.
// Mirrors the web's /api/v1/settings/drp contract (apps/ui/src/api.ts).
export interface BackupLegStatus {
  state: string; // ok | stale | failed | unknown
  last_run_at: string | null;
  age_seconds: number | null;
  detail: string;
  size: string;
  file_count: number | null;
  backup_id: string;
}

export interface DrpStatus {
  files: BackupLegStatus;
  pg: BackupLegStatus;
  offsite: BackupLegStatus;
  drill: BackupLegStatus;
  wal_lag_seconds: number | null;
  status_source_available: boolean;
}

export interface DrpConfig {
  rpo_files_seconds: number;
  rpo_pg_seconds: number;
  rpo_offsite_seconds: number;
  rto_seconds: number;
  deploy_mode?: string;
  repo_location: string;
  azure_container: string;
  immutability_enabled: boolean;
  encryption_keys_configured: boolean;
  azure_credentials_configured: boolean;
}

export interface DrpStatusResponse {
  status: DrpStatus;
  config: DrpConfig;
  read_only: boolean;
}

export function fetchDrpStatus(token: string): Promise<DrpStatusResponse> {
  return apiFetch<DrpStatusResponse>("/api/v1/settings/drp", { token });
}
