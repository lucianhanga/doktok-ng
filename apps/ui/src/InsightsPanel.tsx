import { useEffect, useState } from "react";

import { CategoriesPanel } from "./CategoriesPanel";
import { EmbeddingMapPanel } from "./EmbeddingMapPanel";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { MemoryPanel } from "./MemoryPanel";
import { WordCloudPanel } from "./WordCloudPanel";
import { loadJSON, saveJSON } from "./persist";

type InsightsSub = "memory" | "graph" | "map" | "cloud" | "categories";

const SUB_TABS: { id: InsightsSub; label: string }[] = [
  { id: "memory", label: "Memory" },
  { id: "graph", label: "Knowledge Graph" },
  { id: "map", label: "Embedding Map" },
  { id: "cloud", label: "Word Cloud" },
  { id: "categories", label: "Categories" },
];

const SUB_IDS = new Set<string>(SUB_TABS.map((t) => t.id));
const STORAGE_KEY = "insights-sub";
const DEFAULT_SUB: InsightsSub = "memory";

/** Extract the insights sub-tab from the current hash, e.g. "#/insights/map" -> "map".
 *  Returns null when the hash has no valid sub-segment. */
function subFromHash(): InsightsSub | null {
  const hash = window.location.hash.replace(/^#\/?/, ""); // "insights/map" or "insights"
  const parts = hash.split("/");
  if (parts.length >= 2 && parts[0] === "insights" && SUB_IDS.has(parts[1] ?? "")) {
    return parts[1] as InsightsSub;
  }
  return null;
}

function initialSub(): InsightsSub {
  const fromHash = subFromHash();
  if (fromHash) return fromHash;
  const stored = loadJSON<string>(STORAGE_KEY, DEFAULT_SUB);
  return SUB_IDS.has(stored) ? (stored as InsightsSub) : DEFAULT_SUB;
}

export function InsightsPanel({
  onFilterByCategory,
}: {
  onFilterByCategory: (category: string) => void;
}) {
  const [sub, setSub] = useState<InsightsSub>(initialSub);

  // Sync the active sub-tab when the user navigates with back/forward or opens a deep-link
  // that includes a sub-segment (e.g. "#/insights/map").
  useEffect(() => {
    function onHashChange() {
      const fromHash = subFromHash();
      if (fromHash) setSub(fromHash);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function selectSub(next: InsightsSub) {
    setSub(next);
    saveJSON(STORAGE_KEY, next);
    // Reflect the sub-tab in the hash so the user can share or bookmark a specific view.
    // App.tsx's sync() will fire, but viewFromHash() extracts "insights" (first segment)
    // so the top-level view stays unchanged.
    window.location.hash = `#/insights/${next}`;
  }

  return (
    <section className="panel settings-page" aria-label="Insights">
      <div className="settings-layout">
        <nav className="settings-submenu" role="tablist" aria-label="Insights sections">
          {SUB_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={sub === t.id}
              className={sub === t.id ? "active" : ""}
              onClick={() => selectSub(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="settings-pane">
          {sub === "memory" && <MemoryPanel />}
          {sub === "graph" && <KnowledgeGraphPanel />}
          {sub === "map" && <EmbeddingMapPanel />}
          {sub === "cloud" && <WordCloudPanel />}
          {sub === "categories" && (
            <CategoriesPanel onFilterByCategory={onFilterByCategory} />
          )}
        </div>
      </div>
    </section>
  );
}
