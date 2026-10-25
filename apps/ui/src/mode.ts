// UI detail level (#848, story 5.1): "simple" shows the daily-driver minimum, "advanced" adds
// metrics/configuration/insights. Persisted via the shared preference store (persist.ts), so the
// choice syncs across devices like the other UI prefs.
import { loadJSON, saveJSON } from "./persist";

export type UiMode = "simple" | "advanced";

const KEY = "doktok-ui-mode";

export function loadUiMode(): UiMode {
  return loadJSON<unknown>(KEY, "simple") === "advanced" ? "advanced" : "simple";
}

export function saveUiMode(mode: UiMode): void {
  saveJSON(KEY, mode);
}
