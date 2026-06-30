import { useCallback, useEffect, useState } from "react";

import { fetchAiSettings, fetchHealth, type HealthStatus } from "./api";
import { useInterval } from "./hooks";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthStatus }
  | { kind: "error"; message: string };

/**
 * The fixed bottom status line (M8): a thin, small, grayed bar showing backend service/version/
 * environment/health. Replaces the old Status tab; polls the backend so it stays current.
 * Also surfaces a red warning when no-egress is off (document data may leave this host).
 */
export function StatusBar() {
  const [state, setState] = useState<State>({ kind: "loading" });
  // null = unknown (not yet loaded / unavailable); false = no-egress OFF -> show the warning.
  const [noEgress, setNoEgress] = useState<boolean | null>(null);

  const load = useCallback(() => {
    fetchHealth()
      .then((data) => setState({ kind: "ok", data }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
    fetchAiSettings()
      .then((s) => setNoEgress(s.no_egress ?? false))
      .catch(() => setNoEgress(null));
  }, []);

  useEffect(load, [load]);
  useInterval(load, 10000);

  return (
    <footer className="app-statusbar" aria-label="Backend status">
      <div className="app-inner app-statusbar-inner">
        {state.kind === "error" ? (
          <span className="status-error" role="alert">
            Backend unreachable: {state.message}
          </span>
        ) : state.kind === "ok" ? (
          <span role="status">
            {state.data.service} &middot; v{state.data.version} &middot; {state.data.environment}{" "}
            &middot; {state.data.status}
          </span>
        ) : (
          <span role="status">Connecting to backend…</span>
        )}
        {noEgress === false && (
          <span className="status-error statusbar-egress-warning" role="alert">
            &#9888; No-egress is off — document data may be sent to external services
          </span>
        )}
      </div>
    </footer>
  );
}
