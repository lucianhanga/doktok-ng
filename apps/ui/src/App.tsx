import { useEffect, useState } from "react";

import { fetchHealth, type HealthStatus } from "./api";
import { JobsPanel } from "./JobsPanel";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthStatus }
  | { kind: "error"; message: string };

export function HealthPanel() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((data) => setState({ kind: "ok", data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      });
    return () => controller.abort();
  }, []);

  return (
    <section aria-label="Backend status" className="panel">
      <h2>Backend status</h2>
      {state.kind === "loading" && <p role="status">Checking backend...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Backend unreachable: {state.message}
        </p>
      )}
      {state.kind === "ok" && (
        <dl className="status-ok">
          <div>
            <dt>Status</dt>
            <dd>{state.data.status}</dd>
          </div>
          <div>
            <dt>Service</dt>
            <dd>{state.data.service}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{state.data.version}</dd>
          </div>
          <div>
            <dt>Environment</dt>
            <dd>{state.data.environment}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

type View = "status" | "ingestion";

export default function App() {
  const [view, setView] = useState<View>("status");

  return (
    <main className="app">
      <header className="app-header">
        <h1>DokTok NG</h1>
        <p className="tagline">Local-first document intelligence</p>
        <nav className="tabs" aria-label="Sections">
          <button
            type="button"
            className={view === "status" ? "active" : ""}
            aria-pressed={view === "status"}
            onClick={() => setView("status")}
          >
            Status
          </button>
          <button
            type="button"
            className={view === "ingestion" ? "active" : ""}
            aria-pressed={view === "ingestion"}
            onClick={() => setView("ingestion")}
          >
            Ingestion
          </button>
        </nav>
      </header>
      {view === "status" ? <HealthPanel /> : <JobsPanel />}
    </main>
  );
}
