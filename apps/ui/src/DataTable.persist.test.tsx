import { fireEvent, render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, test } from "vitest";
import type { ColumnDef, SortingState } from "@tanstack/react-table";

import { DataTable } from "./DataTable";

// Reproduction for #522: column resize/order/visibility must survive an unmount+remount
// (the Documents list unmounts on every tab switch and remounts after the fetch).
type Row = { id: string; name: string; size: number };

const DATA: Row[] = [
  { id: "r1", name: "alpha", size: 1 },
  { id: "r2", name: "beta", size: 2 },
];

const COLS: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Name" },
  { accessorKey: "size", header: "Size" },
];

const KEY = "test-table-persist";

afterEach(() => localStorage.clear());

function mount() {
  return render(
    <DataTable<Row> data={DATA} columns={COLS} getRowId={(r) => r.id} persistKey={KEY} />,
  );
}

function nameHeader() {
  return screen.getByRole("columnheader", { name: /Name/ });
}

test("column sizing survives an unmount+remount (#522)", () => {
  const first = mount();
  const before = nameHeader().style.width;
  // Drag the Name column's resizer 80px to the right.
  const resizer = within(nameHeader()).getByRole("separator");
  fireEvent.mouseDown(resizer, { clientX: 100 });
  fireEvent.mouseMove(document, { clientX: 180 });
  fireEvent.mouseUp(document);
  const widened = nameHeader().style.width;
  expect(widened).not.toBe(before);
  // The save effect persisted it.
  const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
  expect(saved.columnSizing?.name).toBeGreaterThan(0);

  first.unmount();
  mount();
  expect(nameHeader().style.width).toBe(widened);
});

test("column visibility survives an unmount+remount (#522)", () => {
  const first = mount();
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Size" }));
  expect(screen.queryByRole("columnheader", { name: /Size/ })).not.toBeInTheDocument();
  first.unmount();

  mount();
  expect(screen.queryByRole("columnheader", { name: /Size/ })).not.toBeInTheDocument();
});

test("column order survives an unmount+remount (#522)", () => {
  const first = mount();
  // Drag "Size" onto "Name" to reorder. jsdom has no DataTransfer: stub it on the event init
  // (browsers always provide one; the app code is correct as-is).
  const dt = { effectAllowed: "", dropEffect: "" };
  const sizeLabel = within(screen.getByRole("columnheader", { name: /Size/ })).getByText("Size");
  fireEvent.dragStart(sizeLabel, { dataTransfer: dt });
  fireEvent.drop(nameHeader(), { dataTransfer: dt });
  fireEvent.dragEnd(sizeLabel, { dataTransfer: dt });
  let headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers[0]).toMatch(/Size/);
  first.unmount();

  mount();
  headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers[0]).toMatch(/Size/);
});

test("round-trip with the DocumentsPanel prop shape: controlled sorting, initialVisibility, manualSorting (#522)", () => {
  const panelProps = {
    data: DATA,
    columns: COLS,
    getRowId: (r: Row) => r.id,
    persistKey: KEY,
    initialVisibility: { size: false },
    sorting: [] as SortingState,
    onSortingChange: () => {},
    manualSorting: true,
  };
  const first = render(<DataTable<Row> {...panelProps} />);
  const before = nameHeader().style.width;
  const resizer = within(nameHeader()).getByRole("separator");
  fireEvent.mouseDown(resizer, { clientX: 100 });
  fireEvent.mouseMove(document, { clientX: 180 });
  fireEvent.mouseUp(document);
  const widened = nameHeader().style.width;
  expect(widened).not.toBe(before);
  first.unmount();

  render(<DataTable<Row> {...panelProps} />);
  expect(nameHeader().style.width).toBe(widened);
  // initialVisibility still applies to a fresh store only: the saved envelope wins.
  expect(screen.queryByRole("columnheader", { name: /Size/ })).not.toBeInTheDocument();
});

test("column sizing survives a mount under React StrictMode (#522 root cause)", () => {
  // The app runs in StrictMode, which double-mounts every component and re-runs mount effects
  // with refs preserved: the reset effect's old boolean "skip first run" guard let the reset
  // fire on the second pass, wiping the just-restored layout. The value guard must not.
  localStorage.setItem(KEY, JSON.stringify({ columnSizing: { name: 230 } }));
  render(
    <StrictMode>
      <DataTable<Row> data={DATA} columns={COLS} getRowId={(r) => r.id} persistKey={KEY} />
    </StrictMode>,
  );
  expect(nameHeader().style.width).toBe("230px");
  const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
  expect(saved.columnSizing?.name).toBe(230);
});
