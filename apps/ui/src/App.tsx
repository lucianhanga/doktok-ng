import { type MouseEvent as ReactMouseEvent, useCallback, useEffect, useState } from "react";

import { ActivityPanel } from "./ActivityPanel";
import { ChatPanel } from "./ChatPanel";
import { DocumentDetail } from "./DocumentDetail";
import { DocumentsPanel } from "./DocumentsPanel";
import { OverviewPanel } from "./OverviewPanel";
import { SettingsPanel } from "./SettingsPanel";
import { StatusBar } from "./StatusBar";
import { ThemeToggle } from "./ThemeToggle";
import { fetchHealth } from "./api";
import { useInterval } from "./hooks";

/** Small header status dot: green=connected, amber=connecting, red=unreachable. */
function BackendDot() {
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");

  const load = useCallback(() => {
    fetchHealth()
      .then(() => setStatus("ok"))
      .catch(() => setStatus("error"));
  }, []);

  useEffect(load, [load]);
  useInterval(load, 10000);

  const label =
    status === "ok"
      ? "Backend connected"
      : status === "error"
        ? "Backend unreachable"
        : "Connecting...";

  const dotColor =
    status === "ok"
      ? "var(--success, #3fb950)"
      : status === "error"
        ? "var(--danger, #d9534f)"
        : "var(--warning, #d6a700)";

  return (
    <span className="app-backend-dot" title={label} aria-label={label} role="status">
      <span className="app-backend-dot-circle" style={{ background: dotColor }} aria-hidden="true" />
      <span className="app-backend-dot-label">{label}</span>
    </span>
  );
}

type View = "overview" | "documents" | "chat" | "activity" | "settings";

const TABS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "documents", label: "Documents" },
  { id: "chat", label: "Chat" },
  { id: "activity", label: "Activity" },
  { id: "settings", label: "Settings" },
];

const TAB_IDS = new Set<string>(TABS.map((t) => t.id));

/** The URL hash IS the active section, so the menu items are real links: right-click "Open in new
 * tab", cmd/middle-click, deep-linking, back/forward all work. "#/chat" -> "chat"; unknown/empty
 * falls back to overview. */
function viewFromHash(): View {
  const raw = window.location.hash.replace(/^#\/?/, "");
  return TAB_IDS.has(raw) ? (raw as View) : "overview";
}

function hrefFor(view: View): string {
  return `#/${view}`;
}

export default function App() {
  const [view, setView] = useState<View>(viewFromHash);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [docsNeedsAttention, setDocsNeedsAttention] = useState(false);
  // Unread badge on the Chat tab: set when a backgrounded answer finishes off-tab, cleared on visit.
  const [chatUnread, setChatUnread] = useState(false);
  // The activity event to highlight when opening the Activity tab from the Overview timeline.
  const [activityFocusId, setActivityFocusId] = useState<string | null>(null);

  // The hash drives the active section. Reacting to `hashchange` covers link clicks, back/forward,
  // and a freshly opened tab landing on "#/chat". The per-view side effects that navigation used to
  // do inline live here so every entry point (link, programmatic, new tab) behaves identically.
  useEffect(() => {
    function sync() {
      const next = viewFromHash();
      setOpenDoc(null);
      if (next !== "documents") setDocsNeedsAttention(false);
      if (next !== "activity") setActivityFocusId(null);
      if (next === "chat") setChatUnread(false);
      setView(next);
    }
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  /** In-app navigation: point the hash at the section; `sync` above applies the state. */
  function navigate(next: View) {
    if (viewFromHash() === next) return; // already there (re-click) - nothing to do
    window.location.hash = hrefFor(next);
  }

  /** Left-click navigates in-app; modified/middle clicks fall through so the browser can open the
   * link in a new tab/window (the whole point of making these real anchors). */
  function onNavClick(e: ReactMouseEvent, next: View) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    navigate(next);
  }

  function openActivity(eventId: string) {
    setActivityFocusId(eventId);
    if (viewFromHash() === "activity") setView("activity");
    else window.location.hash = hrefFor("activity"); // sync() keeps the focus id (it only clears off-Activity)
  }

  function showPendingFeatures() {
    setDocsNeedsAttention(true);
    if (viewFromHash() === "documents") setView("documents");
    else window.location.hash = hrefFor("documents"); // sync() keeps the flag (only clears off-Documents)
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-inner">
          <div className="app-header-bar">
            <h1>DokTok NG</h1>
            <nav className="tabs" aria-label="Sections" role="tablist">
              {TABS.map((tab) => (
                <a
                  key={tab.id}
                  role="tab"
                  aria-selected={view === tab.id && !openDoc}
                  className={view === tab.id && !openDoc ? "active" : ""}
                  href={hrefFor(tab.id)}
                  onClick={(e) => onNavClick(e, tab.id)}
                >
                  {tab.label}
                  {tab.id === "chat" && chatUnread && view !== "chat" && (
                    <span className="tab-unread" aria-label="new answer" title="New answer ready" />
                  )}
                </a>
              ))}
            </nav>
            <div className="app-header-end">
              <BackendDot />
              <ThemeToggle />
            </div>
          </div>
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
          <div className="app-content" hidden={openDoc !== null}>
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
            <div className="app-chat-mount" hidden={view !== "chat"}>
              <ChatPanel
                onOpenDocument={setOpenDoc}
                active={view === "chat" && !openDoc}
                onBackgroundDone={() => setChatUnread(true)}
              />
            </div>
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
