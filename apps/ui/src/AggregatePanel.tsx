import { useState } from "react";

import {
  aggregate,
  formatMoneyMinor,
  type AggregationIntent,
  type AggregationResult,
} from "./api";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; result: AggregationResult }
  | { kind: "error"; message: string };

/** Structured aggregation (M6.3): a deterministic SUM/COUNT over extracted transaction records -
 * the answer to "how much did I spend at X" that top-k RAG cannot give. Money is rolled up per
 * currency (never summed across currencies) with sample rows for provenance. */
export function AggregatePanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [operation, setOperation] = useState<"sum" | "count">("sum");
  const [merchant, setMerchant] = useState("");
  const [direction, setDirection] = useState<"" | "debit" | "credit">("");
  const [currency, setCurrency] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  function run() {
    const intent: AggregationIntent = {
      operation,
      merchant: merchant.trim() || null,
      direction: direction || null,
      currency: currency.trim().toUpperCase() || null,
      date_from: dateFrom || null,
      date_to: dateTo || null,
      sample_limit: 10,
    };
    setState({ kind: "loading" });
    aggregate(intent)
      .then((result) => setState({ kind: "ok", result }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }

  return (
    <section aria-label="Aggregate" className="panel">
      <div className="result-head">
        <h2>Totals</h2>
      </div>
      <p className="muted">
        Deterministic sum/count over extracted transaction records - the answer to questions like
        &ldquo;how much did I spend at a merchant&rdquo; that semantic search can&rsquo;t total.
      </p>

      <div className="agg-form">
        <label>
          Operation{" "}
          <select
            aria-label="Operation"
            value={operation}
            onChange={(e) => setOperation(e.target.value as "sum" | "count")}
          >
            <option value="sum">Sum amount</option>
            <option value="count">Count records</option>
          </select>
        </label>
        <label>
          Merchant{" "}
          <input
            type="text"
            aria-label="Merchant"
            placeholder="(any) substring"
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
          />
        </label>
        <label>
          Direction{" "}
          <select
            aria-label="Direction"
            value={direction}
            onChange={(e) => setDirection(e.target.value as "" | "debit" | "credit")}
          >
            <option value="">Any</option>
            <option value="debit">Debit (spend)</option>
            <option value="credit">Credit (refund/in)</option>
          </select>
        </label>
        <label>
          Currency{" "}
          <input
            type="text"
            aria-label="Currency"
            placeholder="(any)"
            maxLength={3}
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
          />
        </label>
        <label>
          From{" "}
          <input
            type="date"
            aria-label="From date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </label>
        <label>
          To{" "}
          <input
            type="date"
            aria-label="To date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </label>
        <button type="button" onClick={run} disabled={state.kind === "loading"}>
          {state.kind === "loading" ? "Calculating…" : "Calculate"}
        </button>
      </div>

      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not aggregate: {state.message}
        </p>
      )}

      {state.kind === "ok" && (
        <div className="agg-result">
          {state.result.count === 0 ? (
            <p className="empty">No records match this filter.</p>
          ) : (
            <>
              <div className="cards">
                {state.result.operation === "count" ? (
                  <div className="card">
                    <div className="card-value">{state.result.count}</div>
                    <div className="card-label">Records</div>
                  </div>
                ) : (
                  state.result.by_currency.map((b) => (
                    <div className="card" key={b.currency ?? "none"}>
                      <div className="card-value" title={formatMoneyMinor(b.total_minor, b.currency)}>
                        {formatMoneyMinor(b.total_minor, b.currency)}
                      </div>
                      <div className="card-label">
                        {b.count} record{b.count === 1 ? "" : "s"}
                        {b.currency ? ` · ${b.currency}` : ""}
                      </div>
                    </div>
                  ))
                )}
              </div>

              {state.result.samples.length > 0 && (
                <div className="doc-section">
                  <h3>Sample records</h3>
                  <table className="jobs">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Merchant</th>
                        <th>Amount</th>
                        <th>Dir</th>
                        <th>Document</th>
                      </tr>
                    </thead>
                    <tbody>
                      {state.result.samples.map((r) => (
                        <tr key={r.id}>
                          <td>{r.occurred_on ?? "-"}</td>
                          <td
                            className="cell-truncate"
                            title={
                              r.merchant_normalized || r.merchant_raw || r.description || undefined
                            }
                          >
                            {r.merchant_normalized || r.merchant_raw || r.description || "-"}
                          </td>
                          <td>
                            {r.amount_minor != null
                              ? formatMoneyMinor(r.amount_minor, r.currency)
                              : "-"}
                          </td>
                          <td>{r.direction ?? "-"}</td>
                          <td>
                            <button
                              type="button"
                              className="link-button"
                              onClick={() => onOpenDocument?.(r.document_id)}
                            >
                              open
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
