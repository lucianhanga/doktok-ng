import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type SyntheticEvent,
} from "react";

import {
  suggestTokens,
  type CategorySummary,
  type TagOut,
  type TokenMatch,
} from "./api";
import {
  migrateDocumentsUiPrefs,
  type DocumentsUiPrefs,
} from "./documentsState";

const MOBILE_QUERY = "(max-width: 48rem), (pointer: coarse)";
const STATUSES = ["active", "failed", "quarantined", "duplicate"] as const;
const EMPTY_TOKENS: string[] = [];

type DocumentsFilterControlsProps = {
  prefs: DocumentsUiPrefs;
  categories: CategorySummary[];
  tags: TagOut[];
  loading: boolean;
  onChange: (prefs: DocumentsUiPrefs) => void;
  onClearAll: () => void;
  count: (draft: DocumentsUiPrefs, signal: AbortSignal) => Promise<number>;
};

function useMobileFilters(): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia?.(MOBILE_QUERY).matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.(MOBILE_QUERY);
    if (!query) return;
    const update = () => setMobile(query.matches);
    update();
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", update);
      return () => query.removeEventListener("change", update);
    }
    query.addListener(update);
    return () => query.removeListener(update);
  }, []);
  return mobile;
}

function QueryInput({
  value,
  legacyTokens,
  immediate,
  onChange,
}: {
  value: string;
  legacyTokens: string[];
  immediate: boolean;
  onChange: (query: string) => void;
}) {
  const [input, setInput] = useState(value);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const listId = useId();
  const hintId = useId();
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setInput((current) => (current.trim() === value ? current : value));
  }, [value]);

  useEffect(() => {
    if (immediate || input.trim() === value) return;
    const timer = setTimeout(() => onChangeRef.current(input.trim()), 300);
    return () => clearTimeout(timer);
  }, [immediate, input, value]);

  useEffect(() => {
    const prefix = input.trim();
    if (!prefix) {
      setSuggestions([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      suggestTokens(prefix, legacyTokens, controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) setSuggestions(items.map((item) => item.value));
        })
        .catch(() => {
          if (!controller.signal.aborted) setSuggestions([]);
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [input, legacyTokens]);

  function update(next: string) {
    setInput(next);
    if (immediate) onChangeRef.current(next.trim());
  }

  return (
    <form className="docs-query" role="search" onSubmit={(event) => event.preventDefault()}>
      <label>
        <span className="docs-query-label">Search documents</span>
        <span id={hintId} className="muted docs-query-hint">Title, filename, or entities</span>
        <input
          type="search"
          aria-label="Search documents"
          aria-describedby={hintId}
          placeholder="Search documents…"
          list={listId}
          value={input}
          onChange={(event) => update(event.target.value)}
        />
      </label>
      <datalist id={listId}>
        {suggestions.slice(0, 8).map((suggestion) => (
          <option key={suggestion} value={suggestion} />
        ))}
      </datalist>
    </form>
  );
}

function toggleValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

export function DocumentsFilterControls({
  prefs,
  categories,
  tags,
  loading,
  onChange,
  onClearAll,
  count,
}: DocumentsFilterControlsProps) {
  const mobile = useMobileFilters();
  const [desktopOpen, setDesktopOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [draft, setDraft] = useState<DocumentsUiPrefs>(prefs);
  const [draftCount, setDraftCount] = useState<number | null>(null);
  const desktopRef = useRef<HTMLDivElement>(null);
  const disclosureRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const countGeneration = useRef(0);
  const titleId = useId();
  const matchName = useId();
  const nativePopover = useMemo(
    () =>
      typeof HTMLElement !== "undefined" &&
      "popover" in HTMLElement.prototype &&
      typeof HTMLElement.prototype.showPopover === "function" &&
      typeof HTMLElement.prototype.hidePopover === "function",
    [],
  );
  const legacyTokens = prefs.tokens ?? EMPTY_TOKENS;

  const commit = useCallback(
    (next: DocumentsUiPrefs) => onChange(migrateDocumentsUiPrefs(next)),
    [onChange],
  );

  useEffect(() => {
    setDesktopOpen(false);
    setMobileOpen(false);
    setDraftCount(null);
  }, [mobile]);

  useEffect(() => {
    if (nativePopover || !desktopOpen) return;
    function outside(event: MouseEvent) {
      if (desktopRef.current && !desktopRef.current.contains(event.target as Node)) {
        setDesktopOpen(false);
      }
    }
    function keydown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setDesktopOpen(false);
      triggerRef.current?.focus({ preventScroll: true });
    }
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", keydown);
    };
  }, [desktopOpen, nativePopover]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (mobileOpen && !dialog.open) {
      try {
        dialog.showModal();
      } catch {
        dialog.setAttribute("open", "");
      }
    } else if (!mobileOpen && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [mobileOpen]);

  useEffect(() => {
    if (!mobileOpen) return;
    const generation = ++countGeneration.current;
    const controller = new AbortController();
    setDraftCount(null);
    const timer = setTimeout(() => {
      count(draft, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted && countGeneration.current === generation) {
            setDraftCount(value);
          }
        })
        .catch(() => {
          if (!controller.signal.aborted && countGeneration.current === generation) {
            setDraftCount(null);
          }
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [count, draft, mobileOpen]);

  function toggleDesktop() {
    const next = !desktopOpen;
    setDesktopOpen(next);
    const disclosure = disclosureRef.current;
    if (!nativePopover) {
      if (!next) triggerRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!disclosure) return;
    if (next) disclosure.showPopover();
    else disclosure.hidePopover();
  }

  function onPopoverToggle(event: SyntheticEvent<HTMLDivElement>) {
    setDesktopOpen(event.currentTarget.matches(":popover-open"));
  }

  function openMobile() {
    setDraft(migrateDocumentsUiPrefs(prefs));
    setMobileOpen(true);
  }

  function closeMobile() {
    if (typeof dialogRef.current?.showModal !== "function") {
      triggerRef.current?.focus({ preventScroll: true });
    }
    setMobileOpen(false);
  }

  function update(updater: (current: DocumentsUiPrefs) => DocumentsUiPrefs) {
    if (mobile) setDraft((current) => migrateDocumentsUiPrefs(updater(current)));
    else commit(updater(prefs));
  }

  function updateQuery(query: string) {
    update((current) => ({ ...current, query }));
  }

  function toggleTagMatch(tagMatch: TokenMatch) {
    update((current) => ({ ...current, tagMatch }));
  }

  function removeLegacyToken(token: string) {
    const tokens = legacyTokens.filter((item) => item !== token);
    commit({
      ...prefs,
      tokens,
      tokenMatch: tokens.length > 0 ? (prefs.tokenMatch ?? "all") : "all",
    });
  }

  const tagName = (id: string) => tags.find((tag) => tag.id === id)?.name ?? id;
  const chips = [
    ...(prefs.query
      ? [{ id: "query", label: `Search: ${prefs.query}`, removeLabel: `Remove query: ${prefs.query}`, remove: () => commit({ ...prefs, query: "" }) }]
      : []),
    ...prefs.statuses.map((status) => ({
      id: `status:${status}`,
      label: `Status: ${status}`,
      removeLabel: `Remove status: ${status}`,
      remove: () => commit({ ...prefs, statuses: prefs.statuses.filter((item) => item !== status) }),
    })),
    ...prefs.categories.map((category) => ({
      id: `category:${category}`,
      label: `Category: ${category}`,
      removeLabel: `Remove category: ${category}`,
      remove: () => commit({ ...prefs, categories: prefs.categories.filter((item) => item !== category) }),
    })),
    ...prefs.tags.map((tag) => ({
      id: `tag:${tag}`,
      label: `Tag: ${tagName(tag)}`,
      removeLabel: `Remove tag: ${tagName(tag)}`,
      remove: () => {
        const nextTags = prefs.tags.filter((item) => item !== tag);
        commit({ ...prefs, tags: nextTags, tagMatch: nextTags.length > 0 ? prefs.tagMatch : "all" });
      },
    })),
    ...(prefs.needsAttention
      ? [{ id: "attention", label: "Needs attention", removeLabel: "Remove needs attention filter", remove: () => commit({ ...prefs, needsAttention: false }) }]
      : []),
    ...(prefs.unidentifiable
      ? [{ id: "unidentifiable", label: "Unidentifiable", removeLabel: "Remove unidentifiable filter", remove: () => commit({ ...prefs, unidentifiable: false }) }]
      : []),
    ...legacyTokens.map((token) => ({
      id: `token:${token}`,
      label: `Legacy token: ${token}`,
      removeLabel: `Remove legacy token: ${token}`,
      remove: () => removeLegacyToken(token),
    })),
  ];

  const current = mobile ? draft : prefs;
  const categoryValues = [
    ...categories.map((category) => ({ name: category.name, count: category.document_count })),
    ...current.categories
      .filter((selected) => !categories.some((category) => category.name === selected))
      .map((name) => ({ name, count: null })),
  ];

  const fields = (
    <div className="docs-filter-fields">
      <fieldset>
        <legend>Status</legend>
        {STATUSES.map((status) => (
          <label key={status}>
            <input
              type="checkbox"
              checked={current.statuses.includes(status)}
              onChange={() => update((value) => ({ ...value, statuses: toggleValue(value.statuses, status) }))}
            />{" "}
            {status[0].toUpperCase() + status.slice(1)}
          </label>
        ))}
      </fieldset>

      {categoryValues.length > 0 && (
        <fieldset>
          <legend>Category</legend>
          {categoryValues.map((category) => (
            <label key={category.name}>
              <input
                type="checkbox"
                checked={current.categories.includes(category.name)}
                onChange={() => update((value) => ({ ...value, categories: toggleValue(value.categories, category.name) }))}
              />{" "}
              {category.name}{category.count === null ? "" : ` (${category.count})`}
            </label>
          ))}
        </fieldset>
      )}

      {tags.length > 0 && (
        <fieldset>
          <legend>Tags</legend>
          <div role="radiogroup" aria-label="Tag match mode" className="docs-tag-match">
            <label>
              <input
                type="radio"
                name={matchName}
                checked={current.tagMatch === "all"}
                onChange={() => toggleTagMatch("all")}
              />{" "}
              All
            </label>
            <label>
              <input
                type="radio"
                name={matchName}
                checked={current.tagMatch === "any"}
                onChange={() => toggleTagMatch("any")}
              />{" "}
              Any
            </label>
          </div>
          {tags.map((tag) => (
            <label key={tag.id}>
              <input
                type="checkbox"
                checked={current.tags.includes(tag.id)}
                onChange={() => update((value) => ({ ...value, tags: toggleValue(value.tags, tag.id) }))}
              />{" "}
              {tag.name}
            </label>
          ))}
        </fieldset>
      )}

      <fieldset>
        <legend>Document health</legend>
        <label title="Documents with a failed feature, not ones still processing">
          <input
            type="checkbox"
            checked={current.needsAttention}
            onChange={(event) => update((value) => ({ ...value, needsAttention: event.target.checked }))}
          />{" "}
          Needs attention
        </label>
        <label title="Extraction succeeded but the content is not meaningful">
          <input
            type="checkbox"
            checked={current.unidentifiable}
            onChange={(event) => update((value) => ({ ...value, unidentifiable: event.target.checked }))}
          />{" "}
          Unidentifiable
        </label>
      </fieldset>
    </div>
  );

  return (
    <div className="docs-filter-controls" aria-busy={loading}>
      {!mobile && (
        <QueryInput
          value={prefs.query}
          legacyTokens={legacyTokens}
          immediate={false}
          onChange={updateQuery}
        />
      )}

      <div className="docs-filter-disclosure" ref={desktopRef}>
        <button
          ref={triggerRef}
          type="button"
          aria-expanded={mobile ? mobileOpen : desktopOpen}
          aria-haspopup="dialog"
          onClick={mobile ? openMobile : toggleDesktop}
        >
          Filters{chips.length > 0 ? ` (${chips.length})` : ""}
        </button>
        {!mobile && (
          <div
            ref={disclosureRef}
            popover="auto"
            role="dialog"
            aria-label="Filters"
            className="docs-filter-popover"
            data-fallback={!nativePopover}
            data-open={desktopOpen}
            onToggle={onPopoverToggle}
          >
            {desktopOpen && (
              <>
                {fields}
                <button type="button" onClick={toggleDesktop}>Done</button>
              </>
            )}
          </div>
        )}
      </div>

      {chips.length > 0 && (
        <div className="docs-applied-filters">
          <ul aria-label="Applied filters">
            {chips.map((chip) => (
              <li key={chip.id}>
                <button
                  type="button"
                  className="docs-applied-chip"
                  aria-label={chip.removeLabel}
                  onClick={chip.remove}
                >
                  {chip.label} <span aria-hidden="true">×</span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="link-button" onClick={onClearAll}>Clear all</button>
        </div>
      )}

      {mobile && (
        <dialog
          ref={dialogRef}
          className="docs-filter-dialog"
          aria-labelledby={titleId}
          onCancel={(event) => {
            event.preventDefault();
            closeMobile();
          }}
          onClose={closeMobile}
        >
          {mobileOpen && (
            <>
              <header className="docs-filter-dialog-head">
                <h2 id={titleId}>Filters</h2>
                <button type="button" aria-label="Close filters" onClick={closeMobile}>×</button>
              </header>
              <div className="docs-filter-dialog-body">
                <QueryInput
                  value={draft.query}
                  legacyTokens={draft.tokens ?? EMPTY_TOKENS}
                  immediate
                  onChange={updateQuery}
                />
                {fields}
              </div>
              <footer className="docs-filter-dialog-actions">
                <button type="button" onClick={closeMobile}>Cancel</button>
                <button
                  type="button"
                  onClick={() => {
                    commit(draft);
                    closeMobile();
                  }}
                >
                  {draftCount === null ? "Show … documents" : `Show ${draftCount} documents`}
                </button>
              </footer>
            </>
          )}
        </dialog>
      )}
    </div>
  );
}
