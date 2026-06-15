import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnSizingState,
  type Row,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

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
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(initialVisibility ?? {});
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showColumns, setShowColumns] = useState(false);

  const table = useReactTable<T>({
    data,
    columns,
    state: { sorting, columnVisibility, columnSizing, globalFilter: globalFilter ?? "" },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnSizingChange: setColumnSizing,
    getRowId,
    columnResizeMode: "onChange",
    enableColumnResizing: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

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
                      className={canSort ? "datatable-sortable" : undefined}
                      aria-sort={
                        sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"
                      }
                    >
                      <span
                        className="datatable-th-label"
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
  onToggle,
  detailColSpan,
  renderDetail,
}: {
  row: Row<T>;
  isOpen: boolean;
  onToggle?: () => void;
  detailColSpan: number;
  renderDetail?: (row: T) => ReactNode;
}) {
  return (
    <>
      <tr>
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
