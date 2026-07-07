import { useCallback, useEffect, useState } from "react";

import { fetchCategories, type CategorySummary } from "./api";
import { paletteColor } from "./categoryPalette";

export function CategoriesPanel({
  onFilterByCategory,
}: {
  onFilterByCategory: (category: string) => void;
}) {
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    fetchCategories(signal)
      .then((cats) => {
        // The API returns categories sorted by document_count DESC then name ASC
        // (matching the server-side palette ranking). Sort locally too so the
        // bar chart rank and colour assignment are always consistent.
        const sorted = [...cats].sort(
          (a, b) => b.document_count - a.document_count || a.name.localeCompare(b.name),
        );
        setCategories(sorted);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (signal?.aborted) return;
        setError(e instanceof Error ? e.message : "Failed to load categories");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const maxCount = categories.length > 0 ? (categories[0]?.document_count ?? 1) : 1;

  return (
    <section aria-label="Categories" className="panel">
      <div className="result-head">
        <h2>Categories</h2>
      </div>

      {loading && <p role="status">Loading categories...</p>}

      {error && (
        <p role="alert" className="status-error">
          Could not load categories: {error}
        </p>
      )}

      {!loading && !error && categories.length === 0 && (
        <p className="empty muted">No categories yet.</p>
      )}

      {!loading && !error && categories.length > 0 && (
        <>
          <p className="muted cats-note">
            A document belonging to multiple categories is counted in each one, so bar totals may
            exceed the total document count. Click a bar to filter the Documents tab by that
            category.
          </p>
          <ul className="cats-list" aria-label="Categories by document count">
            {categories.map((cat, idx) => {
              const pct = maxCount > 0 ? (cat.document_count / maxCount) * 100 : 0;
              const color = paletteColor(idx);
              return (
                <li key={cat.name} className="cats-row">
                  <button
                    type="button"
                    className="cats-row-btn"
                    aria-label={`Show documents in ${cat.name} (${cat.document_count})`}
                    title={`Filter Documents by "${cat.name}"`}
                    onClick={() => onFilterByCategory(cat.name)}
                  >
                    <span className="cats-name">{cat.name}</span>
                    <span className="cats-track" aria-hidden="true">
                      <span
                        className="cats-bar"
                        style={{ width: `${pct}%`, background: color }}
                      />
                    </span>
                    <span className="cats-count">{cat.document_count.toLocaleString()}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
