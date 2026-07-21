import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnOrderState,
  type ColumnSizingState,
  type Row,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { loadJSON, removeKey, saveJSON } from "./persist";

interface PersistedTableState {
  sorting?: SortingState;
  columnVisibility?: VisibilityState;
  columnSizing?: ColumnSizingState;
  columnOrder?: ColumnOrderState;
}

export interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  /** Stable row id (defaults to index). */
  getRowId?: (row: T, index: number) => string;
  /** Free-text filter across all columns. */
  globalFilter?: string;
  /** Render expandable detail for a row; when provided, an expander column is shown. */
  renderDetail?: (row: T) => ReactNode;
  /** Initial column visibility. */
  initialVisibility?: VisibilityState;
  emptyLabel?: string;
  /** localStorage key: when set, sorting/visibility/sizing persist across mounts and reloads. */
  persistKey?: string;
  /** Bump this number to reset sorting/visibility/sizing to defaults and clear persistence. */
  resetNonce?: number;
  /** Row id to highlight + scroll into view (e.g. when arriving from a deep link). */
  highlightId?: string;
  /**
   * Controlled sorting. When provided (with onSortingChange), the table does NOT sort client-side —
   * the parent owns the sort and maps it to its query (server-driven sort over the whole dataset).
   */
  sorting?: SortingState;
  onSortingChange?: (next: SortingState) => void;
  /** With controlled sorting, skip the client sorted-row model (rows arrive pre-sorted). */
  manualSorting?: boolean;
  /** Extra class(es) per row, e.g. selected/unidentifiable decorations. Kept out of the core. */
  rowClassName?: (row: T) => string | undefined;
}

/**
 * A cell whose content is kept to a single line; long text is ellipsised and a tooltip with the
 * full text is shown ONLY when it is actually truncated (so untruncated cells get no noisy tooltip).
 */
function TruncatedCell({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [title, setTitle] = useState<string | undefined>(undefined);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const truncated = el.scrollWidth > el.clientWidth;
    const next = truncated ? el.textContent || undefined : undefined;
    setTitle((prev) => (prev === next ? prev : next));
  });
  return (
    <div ref={ref} className="datatable-cell" title={title}>
      {children}
    </div>
  );
}

/**
 * Reusable headless data table (TanStack Table v8). Sorting, global filtering, column show/hide,
 * column resizing, single-line truncated cells (tooltip when clipped), and optional expandable
 * detail rows. Styling reuses the app's hand-written CSS (`table.jobs`).
 */
export function DataTable<T>({
  data,
  columns,
  getRowId,
  globalFilter,
  renderDetail,
  initialVisibility,
  emptyLabel = "Nothing to show.",
  persistKey,
  resetNonce,
  highlightId,
  sorting: sortingProp,
  onSortingChange,
  manualSorting,
  rowClassName,
}: DataTableProps<T>) {
  const persisted = useMemo<PersistedTableState>(
    () => (persistKey ? loadJSON<PersistedTableState>(persistKey, {}) : {}),
    [persistKey],
  );
  const controlledSorting = sortingProp !== undefined;
  const [internalSorting, setInternalSorting] = useState<SortingState>(persisted.sorting ?? []);
  const sorting = controlledSorting ? sortingProp : internalSorting;
  const handleSortingChange = (updater: SortingState | ((old: SortingState) => SortingState)) => {
    const next = typeof updater === "function" ? updater(sorting) : updater;
    if (controlledSorting) onSortingChange?.(next);
    else setInternalSorting(next);
  };
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    persisted.columnVisibility ?? initialVisibility ?? {},
  );
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>(persisted.columnSizing ?? {});
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(persisted.columnOrder ?? []);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showColumns, setShowColumns] = useState(false);
  // Header drag-to-reorder: id of the column currently being dragged.
  const [draggingCol, setDraggingCol] = useState<string | null>(null);

  // Persist table layout only on GENUINE CHANGES (best-effort; #522). The mount run adopts the
  // restored envelope as the baseline WITHOUT writing it back: storage already holds it, and a
  // stale/duplicate instance mounting with empty state must never clobber the saved layout - a
  // write happens only when the state actually diverges from the last adopted/written one.
  // Sorting is only persisted when the table owns it; under controlled sorting the parent (its
  // query) is the source of truth.
  const lastSavedLayout = useRef<string | null>(null);
  useEffect(() => {
    if (!persistKey) return;
    const envelope = {
      sorting: controlledSorting ? persisted.sorting : internalSorting,
      columnVisibility,
      columnSizing,
      columnOrder,
    };
    const serialized = JSON.stringify(envelope);
    if (lastSavedLayout.current === null) {
      lastSavedLayout.current = serialized;
      return;
    }
    if (lastSavedLayout.current === serialized) return;
    lastSavedLayout.current = serialized;
    saveJSON(persistKey, envelope);
  }, [
    persistKey,
    controlledSorting,
    persisted.sorting,
    internalSorting,
    columnVisibility,
    columnSizing,
    columnOrder,
  ]);

  // Reset to factory defaults when the parent bumps resetNonce to a NEW value. The guard compares
  // values (not a "skip the first run" boolean): under React StrictMode every mount double-invokes
  // the effects with refs preserved, so a boolean guard lets the reset fire on the second pass and
  // wipe the just-restored layout (#522 root cause - dev-only, invisible without StrictMode).
  const lastResetNonce = useRef(resetNonce);
  useEffect(() => {
    if (lastResetNonce.current === resetNonce) return;
    lastResetNonce.current = resetNonce;
    setInternalSorting([]);
    setColumnVisibility(initialVisibility ?? {});
    setColumnSizing({});
    setColumnOrder([]);
    if (persistKey) removeKey(persistKey);
  }, [resetNonce]);

  const table = useReactTable<T>({
    data,
    columns,
    state: { sorting, columnVisibility, columnSizing, columnOrder, globalFilter: globalFilter ?? "" },
    onSortingChange: handleSortingChange,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnSizingChange: setColumnSizing,
    onColumnOrderChange: setColumnOrder,
    getRowId,
    columnResizeMode: "onChange",
    enableColumnResizing: true,
    manualSorting: manualSorting ?? false,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  // Drag a header onto another to reorder columns (persisted). Order defaults to the leaf columns.
  const reorderColumn = (fromId: string, toId: string) => {
    if (fromId === toId) return;
    setColumnOrder((prev) => {
      const current = prev.length ? [...prev] : table.getAllLeafColumns().map((c) => c.id);
      const from = current.indexOf(fromId);
      const to = current.indexOf(toId);
      if (from < 0 || to < 0) return current;
      current.splice(from, 1);
      current.splice(to, 0, fromId);
      return current;
    });
  };

  const toggle = (id: string) => setExpanded((e) => ({ ...e, [id]: !e[id] }));
  const leafCount = table.getVisibleLeafColumns().length + (renderDetail ? 1 : 0);
  const rows = table.getRowModel().rows;

  return (
    <div className="datatable">
      <div className="datatable-toolbar">
        <button
          type="button"
          className="datatable-cols-toggle"
          aria-expanded={showColumns}
          onClick={() => setShowColumns((v) => !v)}
        >
          Columns
        </button>
        {showColumns && (
          <div className="datatable-cols-menu" role="menu">
            {table.getAllLeafColumns().map((column) => (
              <label key={column.id} className="datatable-col-option">
                <input
                  type="checkbox"
                  checked={column.getIsVisible()}
                  disabled={!column.getCanHide()}
                  onChange={column.getToggleVisibilityHandler()}
                />
                {typeof column.columnDef.header === "string" ? column.columnDef.header : column.id}
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="datatable-scroll">
        <table className="jobs datatable-table" style={{ width: table.getTotalSize() }}>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {renderDetail && <th className="datatable-expander-col" aria-label="Expand" />}
                {group.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      style={{ width: header.getSize() }}
                      className={
                        [
                          canSort ? "datatable-sortable" : "",
                          draggingCol && draggingCol !== header.column.id
                            ? "datatable-drop-target"
                            : "",
                        ]
                          .filter(Boolean)
                          .join(" ") || undefined
                      }
                      aria-sort={
                        sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"
                      }
                      onDragOver={(e) => {
                        if (draggingCol && draggingCol !== header.column.id) e.preventDefault();
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        if (draggingCol) reorderColumn(draggingCol, header.column.id);
                        setDraggingCol(null);
                      }}
                    >
                      <span
                        className="datatable-th-label"
                        draggable
                        onDragStart={(e) => {
                          setDraggingCol(header.column.id);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        onDragEnd={() => setDraggingCol(null)}
                        title="Drag to reorder column"
                        onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && (
                          <span className="datatable-sort-ind">
                            {sorted === "asc" ? " ↑" : sorted === "desc" ? " ↓" : ""}
                          </span>
                        )}
                      </span>
                      {header.column.getCanResize() && (
                        <span
                          role="separator"
                          aria-orientation="vertical"
                          className={`datatable-resizer${
                            header.column.getIsResizing() ? " is-resizing" : ""
                          }`}
                          onMouseDown={header.getResizeHandler()}
                          onTouchStart={header.getResizeHandler()}
                          onClick={(e) => e.stopPropagation()}
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={leafCount} className="empty">
                  {emptyLabel}
                </td>
              </tr>
            )}
            {rows.map((row: Row<T>) => {
              const isOpen = !!expanded[row.id];
              return (
                <FragmentRow
                  key={row.id}
                  row={row}
                  isOpen={isOpen}
                  highlight={highlightId === row.id}
                  extraClassName={rowClassName?.(row.original)}
                  onToggle={renderDetail ? () => toggle(row.id) : undefined}
                  detailColSpan={leafCount}
                  renderDetail={renderDetail}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FragmentRow<T>({
  row,
  isOpen,
  highlight,
  extraClassName,
  onToggle,
  detailColSpan,
  renderDetail,
}: {
  row: Row<T>;
  isOpen: boolean;
  highlight?: boolean;
  extraClassName?: string;
  onToggle?: () => void;
  detailColSpan: number;
  renderDetail?: (row: T) => ReactNode;
}) {
  const rowRef = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    if (highlight) rowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlight]);
  const cls = [highlight ? "datatable-row-highlight" : "", extraClassName ?? ""]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <tr ref={rowRef} className={cls || undefined}>
        {onToggle && (
          <td className="datatable-expander-col">
            <button
              type="button"
              className="datatable-expander"
              aria-expanded={isOpen}
              aria-label={isOpen ? "Collapse details" : "Expand details"}
              onClick={onToggle}
            >
              {isOpen ? "▾" : "▸"}
            </button>
          </td>
        )}
        {row.getVisibleCells().map((cell) => (
          <td key={cell.id} style={{ width: cell.column.getSize() }}>
            <TruncatedCell>
              {flexRender(cell.column.columnDef.cell, cell.getContext())}
            </TruncatedCell>
          </td>
        ))}
      </tr>
      {renderDetail && isOpen && (
        <tr className="datatable-detail-row">
          <td colSpan={detailColSpan}>{renderDetail(row.original)}</td>
        </tr>
      )}
    </>
  );
}
