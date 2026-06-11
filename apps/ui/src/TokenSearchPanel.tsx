import { useCallback, useEffect, useRef, useState } from "react";

import {
  searchByTokens,
  suggestTokens,
  type DokDocument,
  type TokenSuggestion,
} from "./api";

export function TokenSearchPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [tokens, setTokens] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<TokenSuggestion[]>([]);
  const [docs, setDocs] = useState<DokDocument[]>([]);
  const [searched, setSearched] = useState(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch suggestions for the current input, narrowed by already-selected tokens (case-insensitive).
  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    const prefix = input.trim();
    if (!prefix) {
      setSuggestions([]);
      return;
    }
    const controller = new AbortController();
    debounce.current = setTimeout(() => {
      suggestTokens(prefix, tokens, controller.signal)
        .then(setSuggestions)
        .catch(() => setSuggestions([]));
    }, 180);
    return () => controller.abort();
  }, [input, tokens]);

  const runSearch = useCallback((selected: string[]) => {
    if (selected.length === 0) {
      setDocs([]);
      setSearched(false);
      return;
    }
    setSearched(true);
    searchByTokens(selected)
      .then(setDocs)
      .catch(() => setDocs([]));
  }, []);

  function addToken(value: string) {
    if (tokens.some((t) => t.toLowerCase() === value.toLowerCase())) return;
    const next = [...tokens, value];
    setTokens(next);
    setInput("");
    setSuggestions([]);
    runSearch(next);
  }

  function removeToken(value: string) {
    const next = tokens.filter((t) => t !== value);
    setTokens(next);
    runSearch(next);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && suggestions.length > 0) {
      e.preventDefault();
      addToken(suggestions[0].value);
    } else if (e.key === "Backspace" && input === "" && tokens.length > 0) {
      removeToken(tokens[tokens.length - 1]);
    }
  }

  return (
    <section aria-label="Token search" className="panel">
      <h2>Token search</h2>
      <p className="muted">
        Build a query from indexed tokens. Documents must contain <strong>all</strong> selected
        tokens (AND).
      </p>

      <div className="token-bar">
        {tokens.map((t) => (
          <span className="token-chip" key={t}>
            {t}
            <button type="button" aria-label={`Remove ${t}`} onClick={() => removeToken(t)}>
              &times;
            </button>
          </span>
        ))}
        <div className="token-input-wrap">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={tokens.length ? "add another token..." : "start typing a token..."}
            aria-label="Token input"
          />
          {suggestions.length > 0 && (
            <ul className="token-suggestions" role="listbox">
              {suggestions.map((s) => (
                <li key={s.value} role="option" aria-selected="false">
                  <button type="button" onClick={() => addToken(s.value)}>
                    <span>{s.value}</span>
                    <span className="muted">{s.document_count}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {searched && (
        <div className="doc-section">
          <h3>
            Documents matching {tokens.length} token{tokens.length === 1 ? "" : "s"} ({docs.length})
          </h3>
          {docs.length === 0 ? (
            <p className="empty">No documents contain all of these tokens.</p>
          ) : (
            <table className="jobs">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>File</th>
                  <th>Type</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr
                    key={d.id}
                    onClick={() => onOpenDocument?.(d.id)}
                    style={{ cursor: onOpenDocument ? "pointer" : "default" }}
                  >
                    <td>{d.title ?? "-"}</td>
                    <td>{d.original_filename}</td>
                    <td>{d.detected_mime ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
