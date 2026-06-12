import { useEffect, useState } from "react";

import { fetchHealth, type HealthStatus } from "./api";
import { ActivityPanel } from "./ActivityPanel";
import { ChatPanel } from "./ChatPanel";
import { DocumentDetail } from "./DocumentDetail";
import { DocumentsPanel } from "./DocumentsPanel";
import { EntitiesPanel } from "./EntitiesPanel";
import { JobsPanel } from "./JobsPanel";
import { OverviewPanel } from "./OverviewPanel";
import { SearchPanel } from "./SearchPanel";
import { SettingsPanel } from "./SettingsPanel";
import { TokenSearchPanel } from "./TokenSearchPanel";

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

type View =
  | "overview"
  | "status"
  | "ingestion"
  | "documents"
  | "search"
  | "tokensearch"
  | "chat"
  | "entities"
  | "activity"
  | "settings";

const TABS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "documents", label: "Documents" },
  { id: "search", label: "Search" },
  { id: "tokensearch", label: "Token Search" },
  { id: "chat", label: "Chat" },
  { id: "entities", label: "Entities" },
  { id: "ingestion", label: "Ingestion" },
  { id: "activity", label: "Activity" },
  { id: "status", label: "Status" },
  { id: "settings", label: "Settings" },
];

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [docsNeedsAttention, setDocsNeedsAttention] = useState(false);

  function go(next: View) {
    setOpenDoc(null);
    setDocsNeedsAttention(false);
    setView(next);
  }

  function showPendingFeatures() {
    setOpenDoc(null);
    setDocsNeedsAttention(true);
    setView("documents");
  }

  return (
    <main className="app">
      <header className="app-header">
        <h1>DokTok NG</h1>
        <p className="tagline">Local-first document intelligence</p>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={view === tab.id && !openDoc ? "active" : ""}
              aria-pressed={view === tab.id && !openDoc}
              onClick={() => go(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </header>

      {openDoc ? (
        <DocumentDetail id={openDoc} onClose={() => setOpenDoc(null)} onOpenDocument={setOpenDoc} />
      ) : (
        <>
          {view === "overview" && <OverviewPanel onShowPendingFeatures={showPendingFeatures} />}
          {view === "documents" && (
            <DocumentsPanel
              key={`docs-${docsNeedsAttention}`}
              onOpenDocument={setOpenDoc}
              initialNeedsAttention={docsNeedsAttention}
            />
          )}
          {view === "search" && <SearchPanel onOpenDocument={setOpenDoc} />}
          {view === "tokensearch" && <TokenSearchPanel onOpenDocument={setOpenDoc} />}
          {view === "chat" && <ChatPanel onOpenDocument={setOpenDoc} />}
          {view === "entities" && <EntitiesPanel onOpenDocument={setOpenDoc} />}
          {view === "ingestion" && <JobsPanel onOpenDocument={setOpenDoc} />}
          {view === "activity" && <ActivityPanel />}
          {view === "status" && <HealthPanel />}
          {view === "settings" && <SettingsPanel />}
        </>
      )}
    </main>
  );
}
