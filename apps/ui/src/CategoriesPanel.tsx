import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchCategories,
  fetchCategoryCoOccurrence,
  type CategoryCoOccurrence,
  type CategorySummary,
} from "./api";
import { paletteColor } from "./categoryPalette";

export function CategoriesPanel({
  onFilterByCategory,
}: {
  onFilterByCategory: (category: string) => void;
}) {
  // ---- categories (bar chart) ----
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    fetchCategories(signal)
      .then((cats) => {
        // Sort by document_count DESC then name ASC — matches the server-side palette ranking
        // so bar chart rank and colour assignment are always consistent.
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

  // ---- co-occurrence (matrix) ----
  const [pairs, setPairs] = useState<CategoryCoOccurrence[]>([]);
  const [coocLoading, setCoocLoading] = useState(true);
  const [coocError, setCoocError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setCoocLoading(true);
    setCoocError(null);
    fetchCategoryCoOccurrence(ctrl.signal)
      .then((data) => {
        setPairs(data);
        setCoocLoading(false);
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setCoocError(e instanceof Error ? e.message : "Failed to load co-occurrence data");
        setCoocLoading(false);
      });
    return () => ctrl.abort();
  }, []);

  // ---- derived: bar chart ----
  const barMaxCount = categories.length > 0 ? (categories[0]?.document_count ?? 1) : 1;

  // ---- derived: co-occurrence matrix ----

  /** Map from sorted-name pair key -> count */
  const coocMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of pairs) {
      const key = [p.a_name, p.b_name].sort().join("\x00");
      m.set(key, p.count);
    }
    return m;
  }, [pairs]);

  /** Categories that appear in at least one pair, in bar-chart order (preserves palette index) */
  const matrixCats = useMemo(() => {
    if (pairs.length === 0) return [];
    const names = new Set(pairs.flatMap((p) => [p.a_name, p.b_name]));
    return categories.map((c, idx) => ({ name: c.name, idx })).filter((c) => names.has(c.name));
  }, [categories, pairs]);

  const maxPairCount = useMemo(
    () => pairs.reduce((mx, p) => Math.max(mx, p.count), 0),
    [pairs],
  );

  /** Count for two different categories; null for the diagonal or an unknown pair. */
  function pairCount(nameA: string, nameB: string): number | null {
    if (nameA === nameB) return null;
    const key = [nameA, nameB].sort().join("\x00");
    return coocMap.get(key) ?? null;
  }

  /** Background colour for a cell: transparent for 0/null, accent-blue ramp for 1..max */
  function cellBg(count: number | null): string | undefined {
    if (count == null || count === 0 || maxPairCount === 0) return undefined;
    // accent blue rgba; alpha 0.06..0.78 so the number stays readable at both ends
    const alpha = 0.06 + (count / maxPairCount) * 0.72;
    return `rgba(90, 160, 230, ${alpha.toFixed(2)})`;
  }

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
              const pct = barMaxCount > 0 ? (cat.document_count / barMaxCount) * 100 : 0;
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

          {/* ---- Co-occurrence matrix ---- */}
          <section aria-label="Category co-occurrence" className="cats-cooc-section">
            <h3 className="cats-cooc-title">Co-occurrence</h3>

            {coocLoading && <p role="status">Loading co-occurrence data...</p>}

            {!coocLoading && coocError && (
              <p role="alert" className="status-error">
                Could not load co-occurrence data: {coocError}
              </p>
            )}

            {!coocLoading && !coocError && pairs.length === 0 && (
              <p className="muted">No category co-occurrences yet.</p>
            )}

            {!coocLoading && !coocError && pairs.length > 0 && matrixCats.length >= 2 && (
              <>
                <p className="muted cats-note cats-cooc-note">
                  Each cell shows how many documents belong to both categories. A document in{" "}
                  <em>N</em> categories contributes to <em>N(N-1)/2</em> pairs (multi-label
                  counting).
                </p>
                {/* tabIndex so keyboard users can scroll the region */}
                <div
                  className="cats-cooc-scroll"
                  role="region"
                  aria-label="Co-occurrence matrix"
                  tabIndex={0}
                >
                  <table className="cats-cooc-table">
                    <thead>
                      <tr>
                        {/* corner cell */}
                        <td className="cats-cooc-corner" aria-hidden="true" />
                        {matrixCats.map((cat) => (
                          <th
                            key={cat.name}
                            scope="col"
                            className="cats-cooc-col-header"
                            title={cat.name}
                          >
                            {/* chip sits below the rotated label */}
                            <div className="cats-cooc-col-label" aria-hidden="true">
                              {cat.name}
                            </div>
                            <span
                              className="cats-cooc-chip"
                              style={{ background: paletteColor(cat.idx) }}
                              aria-hidden="true"
                            />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {matrixCats.map((rowCat) => (
                        <tr key={rowCat.name}>
                          <th
                            scope="row"
                            className="cats-cooc-row-header"
                            title={rowCat.name}
                          >
                            <span
                              className="cats-cooc-chip"
                              style={{ background: paletteColor(rowCat.idx) }}
                              aria-hidden="true"
                            />
                            <span className="cats-cooc-row-name">{rowCat.name}</span>
                          </th>
                          {matrixCats.map((colCat) => {
                            const count = pairCount(rowCat.name, colCat.name);
                            const isDiag = rowCat.name === colCat.name;
                            return (
                              <td
                                key={colCat.name}
                                className={
                                  isDiag ? "cats-cooc-cell cats-cooc-cell-diag" : "cats-cooc-cell"
                                }
                                style={count != null ? { background: cellBg(count) } : undefined}
                              >
                                {!isDiag && count != null
                                  ? count.toLocaleString()
                                  : null}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
        </>
      )}
    </section>
  );
}
