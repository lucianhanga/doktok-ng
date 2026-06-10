import { useState, type FormEvent } from "react";

import { search, type SearchHit } from "./api";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; hits: SearchHit[]; query: string }
  | { kind: "error"; message: string };

export function SearchPanel() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setState({ kind: "loading" });
    search(q)
      .then((hits) => setState({ kind: "ok", hits, query: q }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }

  return (
    <section aria-label="Search" className="panel">
      <h2>Search</h2>
      <form onSubmit={onSubmit} className="search-form">
        <input
          type="search"
          aria-label="Search query"
          placeholder="Search your documents..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit">Search</button>
      </form>

      {state.kind === "loading" && <p role="status">Searching...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Search failed: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.hits.length === 0 && (
        <p className="empty">No results for &ldquo;{state.query}&rdquo;.</p>
      )}
      {state.kind === "ok" && state.hits.length > 0 && (
        <ol className="results">
          {state.hits.map((hit) => (
            <li key={hit.chunk_id} className="result">
              <div className="result-head">
                <strong>{hit.title ?? hit.original_filename ?? hit.document_id.slice(0, 8)}</strong>
                {hit.page_start != null && <span className="page">p.{hit.page_start}</span>}
                <span className="score">score {hit.score.toFixed(3)}</span>
              </div>
              <p className="snippet">{hit.snippet}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
