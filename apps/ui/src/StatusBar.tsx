import { useCallback, useEffect, useState } from "react";

import { fetchHealth, type HealthStatus } from "./api";
import { useInterval } from "./hooks";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthStatus }
  | { kind: "error"; message: string };

/**
 * The fixed bottom status line (M8): a thin, small, grayed bar showing backend service/version/
 * environment/health. Replaces the old Status tab; polls the backend so it stays current.
 */
export function StatusBar() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    fetchHealth()
      .then((data) => setState({ kind: "ok", data }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 10000);

  return (
    <footer className="app-statusbar" aria-label="Backend status">
      <div className="app-inner">
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
      </div>
    </footer>
  );
}
