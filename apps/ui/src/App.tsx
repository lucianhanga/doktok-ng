import { useEffect, useState } from "react";

import { fetchHealth, type HealthStatus } from "./api";
import { ActivityPanel } from "./ActivityPanel";
import { AggregatePanel } from "./AggregatePanel";
import { ChatPanel } from "./ChatPanel";
import { DocumentDetail } from "./DocumentDetail";
import { DocumentsPanel } from "./DocumentsPanel";
import { EntitiesPanel } from "./EntitiesPanel";
import { InsightsPanel } from "./InsightsPanel";
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
  | "totals"
  | "insights"
  | "activity"
  | "settings";

const TABS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "documents", label: "Documents" },
  { id: "search", label: "Search" },
  { id: "tokensearch", label: "Token Search" },
  { id: "chat", label: "Chat" },
  { id: "entities", label: "Entities" },
  { id: "totals", label: "Totals" },
  { id: "insights", label: "Insights" },
  { id: "ingestion", label: "Ingestion" },
  { id: "activity", label: "Activity" },
  { id: "status", label: "Status" },
  { id: "settings", label: "Settings" },
];

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [docsNeedsAttention, setDocsNeedsAttention] = useState(false);
  // Unread badge on the Chat tab: set when a backgrounded answer finishes off-tab, cleared on visit.
  const [chatUnread, setChatUnread] = useState(false);

  function go(next: View) {
    setOpenDoc(null);
    setDocsNeedsAttention(false);
    if (next === "chat") setChatUnread(false);
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
              {tab.id === "chat" && chatUnread && view !== "chat" && (
                <span className="tab-unread" aria-label="new answer" title="New answer ready" />
              )}
            </button>
          ))}
        </nav>
      </header>

      {openDoc && (
        <DocumentDetail id={openDoc} onClose={() => setOpenDoc(null)} onOpenDocument={setOpenDoc} />
      )}
      {/* Keep the active panel MOUNTED (just hidden) while a document is open, so its in-progress
          state - e.g. a chat conversation or document-list filters - survives opening a document and
          coming back. Unmounting it (the old ternary) reset that state to empty. */}
      <div hidden={openDoc !== null}>
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
        {/* Chat stays MOUNTED across tab switches (hidden when inactive) so a streamed answer keeps
            running in the background and the transcript survives; an answer that finishes off-tab
            marks the Chat tab unread. */}
        <div hidden={view !== "chat"}>
          <ChatPanel
            onOpenDocument={setOpenDoc}
            active={view === "chat" && !openDoc}
            onBackgroundDone={() => setChatUnread(true)}
          />
        </div>
        {view === "entities" && <EntitiesPanel onOpenDocument={setOpenDoc} />}
        {view === "totals" && <AggregatePanel onOpenDocument={setOpenDoc} />}
        {view === "insights" && <InsightsPanel onOpenDocument={setOpenDoc} />}
        {view === "ingestion" && <JobsPanel onOpenDocument={setOpenDoc} />}
        {view === "activity" && <ActivityPanel onOpenDocument={setOpenDoc} />}
        {view === "status" && <HealthPanel />}
        {view === "settings" && <SettingsPanel />}
      </div>
    </main>
  );
}
