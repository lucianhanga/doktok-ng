import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentsFilterControls } from "./DocumentsFilterControls";
import type { CategorySummary, TagOut } from "./api";
import { defaultDocumentsUiPrefs, type DocumentsUiPrefs } from "./documentsState";

const MOBILE_QUERY = "(max-width: 48rem), (pointer: coarse)";

const CATEGORIES: CategorySummary[] = [
  { name: "Invoices", document_count: 4 },
  { name: "Receipts", document_count: 2 },
];

const TAG: TagOut = {
  id: "tag-1",
  tenant_id: "tenant-1",
  name: "Receipts",
  normalized: "receipts",
  description: "",
  color: "teal",
  status: "active",
  merged_into: null,
  scope: "tenant",
  owner_user_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: null,
  document_count: 2,
};

function prefs(overrides: Partial<DocumentsUiPrefs> = {}): DocumentsUiPrefs {
  return { ...defaultDocumentsUiPrefs(), ...overrides };
}

function renderControls(
  committed: DocumentsUiPrefs = prefs(),
  options: { count?: (draft: DocumentsUiPrefs, signal: AbortSignal) => Promise<number> } = {},
) {
  const onChange = vi.fn();
  const onClearAll = vi.fn();
  const count = vi.fn(
    options.count ?? (async () => 3),
  );
  function Controlled() {
    const [current, setCurrent] = useState(committed);
    return (
      <DocumentsFilterControls
        prefs={current}
        categories={CATEGORIES}
        tags={[TAG]}
        loading={false}
        onChange={(next) => {
          onChange(next);
          setCurrent(next);
        }}
        onClearAll={onClearAll}
        count={count}
      />
    );
  }
  render(<Controlled />);
  return { onChange, onClearAll, count };
}

function mobile() {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === MOBILE_QUERY,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

function responsiveMobile(initial: boolean) {
  let matches = initial;
  const listeners = new Set<() => void>();
  const mediaQuery = {
    get matches() {
      return matches;
    },
    media: MOBILE_QUERY,
    onchange: null,
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    addListener: (listener: () => void) => listeners.add(listener),
    removeListener: (listener: () => void) => listeners.delete(listener),
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
  vi.spyOn(window, "matchMedia").mockReturnValue(mediaQuery);
  return {
    set(value: boolean) {
      matches = value;
      act(() => listeners.forEach((listener) => listener()));
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("the trigger counts active facets and every applied value has a removable chip", () => {
  const { onClearAll } = renderControls(
    prefs({
      query: "invoice",
      statuses: ["active"],
      categories: ["Invoices"],
      tags: [TAG.id],
      needsAttention: true,
      unidentifiable: true,
      tokens: ["Acme"],
      tokenMatch: "any",
    }),
  );

  expect(screen.getByRole("button", { name: "Filters (7)" })).toBeInTheDocument();
  for (const name of [
    "Remove query: invoice",
    "Remove status: active",
    "Remove category: Invoices",
    "Remove tag: Receipts",
    "Remove needs attention filter",
    "Remove unidentifiable filter",
    "Remove legacy token: Acme",
  ]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
  expect(onClearAll).toHaveBeenCalledTimes(1);
});

test.each([
  {
    name: "Remove query: invoice",
    initial: prefs({ query: "invoice" }),
    expected: prefs(),
  },
  {
    name: "Remove status: active",
    initial: prefs({ statuses: ["active", "failed"] }),
    expected: prefs({ statuses: ["failed"] }),
  },
  {
    name: "Remove category: Invoices",
    initial: prefs({ categories: ["Invoices", "Receipts"] }),
    expected: prefs({ categories: ["Receipts"] }),
  },
  {
    name: "Remove tag: Receipts",
    initial: prefs({ tags: [TAG.id], tagMatch: "any" }),
    expected: prefs({ tagMatch: "all" }),
  },
  {
    name: "Remove needs attention filter",
    initial: prefs({ needsAttention: true }),
    expected: prefs(),
  },
  {
    name: "Remove unidentifiable filter",
    initial: prefs({ unidentifiable: true }),
    expected: prefs(),
  },
  {
    name: "Remove legacy token: Acme",
    initial: prefs({ tokens: ["Acme", "Bob"], tokenMatch: "any" }),
    expected: prefs({ tokens: ["Bob"], tokenMatch: "any" }),
  },
])("a chip commits only its own removal: $name", ({ name, initial, expected }) => {
  const { onChange } = renderControls(initial);
  fireEvent.click(screen.getByRole("button", { name }));
  expect(onChange).toHaveBeenCalledWith(expected);
});

test("desktop multi-select keeps OR within an attribute and AND across attributes", () => {
  const { onChange } = renderControls(
    prefs({ query: "invoice", statuses: ["active"], categories: ["Invoices"] }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Filters (3)" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Failed" }));
  expect(onChange).toHaveBeenLastCalledWith(
    prefs({ query: "invoice", statuses: ["active", "failed"], categories: ["Invoices"] }),
  );

  fireEvent.click(screen.getByRole("checkbox", { name: /Receipts \(2\)/ }));
  expect(onChange).toHaveBeenLastCalledWith(
    prefs({
      query: "invoice",
      statuses: ["active", "failed"],
      categories: ["Invoices", "Receipts"],
    }),
  );
});

test("mobile Cancel discards the draft and Apply commits once with the debounced result count", async () => {
  mobile();
  const { onChange, count } = renderControls(prefs({ query: "before" }), {
    count: async () => 12,
  });

  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  fireEvent.change(screen.getByLabelText("Search documents"), {
    target: { value: "discarded" },
  });
  await screen.findByRole("button", { name: "Show 12 documents" });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(dialog).not.toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  expect(screen.getByLabelText("Search documents")).toHaveValue("before");
  fireEvent.change(screen.getByLabelText("Search documents"), {
    target: { value: "after" },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Show 12 documents" }));
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(onChange).toHaveBeenCalledWith(prefs({ query: "after" }));
  expect(count).toHaveBeenCalled();
});

test("a stale mobile count is aborted and cannot replace the newest draft count", async () => {
  mobile();
  const first = deferred<number>();
  const second = deferred<number>();
  const signals: AbortSignal[] = [];
  const { count } = renderControls(prefs(), {
    count: (_draft, signal) => {
      signals.push(signal);
      return signals.length === 1 ? first.promise : second.promise;
    },
  });

  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  const query = screen.getByLabelText("Search documents");
  fireEvent.change(query, { target: { value: "a" } });
  await waitFor(() => expect(count).toHaveBeenCalledTimes(1));
  fireEvent.change(query, { target: { value: "ab" } });
  await waitFor(() => expect(count).toHaveBeenCalledTimes(2));
  expect(signals[0].aborted).toBe(true);

  second.resolve(2);
  await screen.findByRole("button", { name: "Show 2 documents" });
  first.resolve(9);
  await waitFor(() => expect(screen.getByRole("button", { name: "Show 2 documents" })).toBeInTheDocument());
});

test("native token suggestions feed the one unified query input", async () => {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify([{ value: "Acme", document_count: 4 }]), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const { onChange } = renderControls();

  const query = screen.getByLabelText("Search documents");
  fireEvent.change(query, { target: { value: "Ac" } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(document.querySelector('datalist option[value="Acme"]')).toBeInTheDocument();
  expect(screen.getAllByRole("combobox", { name: "Search documents" })).toHaveLength(1);
  expect(screen.queryByLabelText("Filter by token")).not.toBeInTheDocument();

  fireEvent.change(query, { target: { value: "Acme" } });
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(prefs({ query: "Acme" })));
});


test("desktop uses the native Popover API when it is available", () => {
  const prototype = HTMLElement.prototype;
  const names = ["popover", "showPopover", "hidePopover"] as const;
  const originals = names.map((name) => Object.getOwnPropertyDescriptor(prototype, name));
  const showPopover = vi.fn();
  const hidePopover = vi.fn();
  Object.defineProperty(prototype, "popover", { configurable: true, value: "auto" });
  Object.defineProperty(prototype, "showPopover", { configurable: true, value: showPopover });
  Object.defineProperty(prototype, "hidePopover", { configurable: true, value: hidePopover });

  try {
    renderControls();
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    expect(showPopover).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".docs-filter-popover")).toHaveAttribute("data-fallback", "false");
  } finally {
    names.forEach((name, index) => {
      const descriptor = originals[index];
      if (descriptor) Object.defineProperty(prototype, name, descriptor);
      else delete (prototype as unknown as Record<string, unknown>)[name];
    });
  }
});


test("Escape closes the mobile draft and aborts its pending count", async () => {
  mobile();
  const pending = deferred<number>();
  let signal: AbortSignal | undefined;
  const { count } = renderControls(prefs(), {
    count: (_draft, requestSignal) => {
      signal = requestSignal;
      return pending.promise;
    },
  });

  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "draft" } });
  await waitFor(() => expect(count).toHaveBeenCalledTimes(1));
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  fireEvent(dialog, new Event("cancel", { cancelable: true }));

  expect(dialog).not.toBeVisible();
  expect(signal?.aborted).toBe(true);
});

test("media-query transitions reset open overlays and abort the mobile count", async () => {
  const media = responsiveMobile(true);
  const pending = deferred<number>();
  let signal: AbortSignal | undefined;
  const { count } = renderControls(prefs(), {
    count: (_draft, requestSignal) => {
      signal = requestSignal;
      return pending.promise;
    },
  });

  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "draft" } });
  await waitFor(() => expect(count).toHaveBeenCalledTimes(1));

  media.set(false);
  expect(signal?.aborted).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  expect(screen.getByRole("checkbox", { name: "Failed" })).toBeInTheDocument();

  media.set(true);
  expect(screen.getByRole("button", { name: "Filters" })).toHaveAttribute("aria-expanded", "false");
  expect(document.querySelector(".docs-filter-dialog")).not.toBeVisible();

  media.set(false);
  expect(screen.getByRole("button", { name: "Filters" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("checkbox", { name: "Failed" })).not.toBeInTheDocument();
});


test("desktop fallback Done restores focus to the Filters trigger", () => {
  renderControls();
  const trigger = screen.getByRole("button", { name: "Filters" });
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "Done" }));

  expect(screen.queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

test("mobile fallback Cancel restores focus to the Filters trigger", async () => {
  mobile();
  renderControls();
  const trigger = screen.getByRole("button", { name: "Filters" });
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "Filters" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(dialog).not.toBeVisible();
  expect(trigger).toHaveFocus();
});


test("desktop search has a visible label and an associated scope hint", () => {
  renderControls();

  const input = screen.getByLabelText("Search documents");
  expect(screen.getByText("Search documents", { selector: "label span" })).toBeVisible();
  expect(screen.getByText("Title, filename, or entities")).toBeVisible();
  expect(input).toHaveAccessibleDescription("Title, filename, or entities");
});

test("desktop fallback Escape closes Filters and restores trigger focus", () => {
  renderControls();
  const trigger = screen.getByRole("button", { name: "Filters" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  expect(dialog).toHaveAttribute("data-fallback", "true");
  screen.getByRole("checkbox", { name: "Failed" }).focus();

  fireEvent.keyDown(dialog, { key: "Escape" });

  expect(screen.queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveFocus();
});
