import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Row,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { useState, type ReactNode } from "react";

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
 * Reusable headless data table (TanStack Table v8). Sorting, global filtering, column show/hide and
 * optional expandable detail rows. Styling reuses the app's hand-written CSS (`table.jobs`).
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
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showColumns, setShowColumns] = useState(false);

  const table = useReactTable<T>({
    data,
    columns,
    state: { sorting, columnVisibility, globalFilter: globalFilter ?? "" },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getRowId,
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

      <table className="jobs datatable-table">
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
                    className={canSort ? "datatable-sortable" : undefined}
                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                    aria-sort={
                      sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"
                    }
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {canSort && (
                      <span className="datatable-sort-ind">
                        {sorted === "asc" ? " ↑" : sorted === "desc" ? " ↓" : ""}
                      </span>
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
          <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
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
