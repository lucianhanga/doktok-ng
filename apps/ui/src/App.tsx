import { useState } from "react";

import { ActivityPanel } from "./ActivityPanel";
import { AggregatePanel } from "./AggregatePanel";
import { ChatPanel } from "./ChatPanel";
import { DocumentDetail } from "./DocumentDetail";
import { DocumentsPanel } from "./DocumentsPanel";
import { EntitiesPanel } from "./EntitiesPanel";
import { InsightsPanel } from "./InsightsPanel";
import { JobsPanel } from "./JobsPanel";
import { OverviewPanel } from "./OverviewPanel";
import { SettingsPanel } from "./SettingsPanel";
import { StatusBar } from "./StatusBar";
import { ThemeToggle } from "./ThemeToggle";

type View =
  | "overview"
  | "ingestion"
  | "documents"
  | "chat"
  | "entities"
  | "totals"
  | "insights"
  | "activity"
  | "settings";

const TABS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "documents", label: "Documents" },
  { id: "chat", label: "Chat" },
  { id: "entities", label: "Entities" },
  { id: "totals", label: "Totals" },
  { id: "insights", label: "Insights" },
  { id: "ingestion", label: "Ingestion" },
  { id: "activity", label: "Activity" },
  { id: "settings", label: "Settings" },
];

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [docsNeedsAttention, setDocsNeedsAttention] = useState(false);
  // Unread badge on the Chat tab: set when a backgrounded answer finishes off-tab, cleared on visit.
  const [chatUnread, setChatUnread] = useState(false);
  // The activity event to highlight when opening the Activity tab from the Overview timeline.
  const [activityFocusId, setActivityFocusId] = useState<string | null>(null);

  function go(next: View) {
    setOpenDoc(null);
    setDocsNeedsAttention(false);
    if (next === "chat") setChatUnread(false);
    if (next !== "activity") setActivityFocusId(null);
    setView(next);
  }

  function openActivity(eventId: string) {
    setOpenDoc(null);
    setActivityFocusId(eventId);
    setView("activity");
  }

  function showPendingFeatures() {
    setOpenDoc(null);
    setDocsNeedsAttention(true);
    setView("documents");
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-inner">
          <div className="app-header-top">
            <h1>DokTok NG</h1>
            <ThemeToggle />
          </div>
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
        </div>
      </header>

      <main className="app-main">
        <div className="app-inner">
          {openDoc && (
            <DocumentDetail
              id={openDoc}
              onClose={() => setOpenDoc(null)}
              onOpenDocument={setOpenDoc}
            />
          )}
          {/* Keep the active panel MOUNTED (just hidden) while a document is open, so its in-progress
              state - e.g. a chat conversation or document-list filters - survives opening a document
              and coming back. Unmounting it (the old ternary) reset that state to empty. */}
          <div hidden={openDoc !== null}>
            {view === "overview" && (
              <OverviewPanel
                onShowPendingFeatures={showPendingFeatures}
                onOpenActivity={openActivity}
              />
            )}
            {view === "documents" && (
              <DocumentsPanel
                key={`docs-${docsNeedsAttention}`}
                onOpenDocument={setOpenDoc}
                initialNeedsAttention={docsNeedsAttention}
              />
            )}
            {/* Chat stays MOUNTED across tab switches (hidden when inactive) so a streamed answer
                keeps running in the background and the transcript survives; an answer that finishes
                off-tab marks the Chat tab unread. */}
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
            {view === "activity" && (
              <ActivityPanel onOpenDocument={setOpenDoc} focusId={activityFocusId} />
            )}
            {view === "settings" && <SettingsPanel />}
          </div>
        </div>
      </main>

      <StatusBar />
    </div>
  );
}
