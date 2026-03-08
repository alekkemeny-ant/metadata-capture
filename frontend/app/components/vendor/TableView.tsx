"use client";

// ============================================================================
// RESTYLED from the upstream file-attachment preview grid
// (apps/user-content-renderer/components/renderers/shared/TableView.tsx)
//
// Visual language matched to metadata-capture's sand/terracotta palette
// instead of stock gray/blue Tailwind. Structural behavior (A/B/C column
// letters, row numbers, resize handles, formula bar, cell selection) is
// preserved.
//
// PATCHES (search for "METADATA-CAPTURE PATCH"):
//   1. Optional `cellRenderer` prop — EditableTableView clones the default
//      <td> to inject <input>/<select> in edit mode, inheriting all styling.
//
// To re-sync with upstream: structural diff only; classNames are local.
// ============================================================================

import React, { useCallback, useRef, useState } from "react";

const MAX_VISIBLE_ROWS = 100;
const MAX_VISIBLE_COLS = 20;
const MIN_COL_WIDTH = 120;
const ROW_HEADER_WIDTH = 48;

export interface CellData {
  value: string | null;
  formula?: string;
}

// METADATA-CAPTURE PATCH: render-prop for cell injection. Receives the cell
// coordinates + the default <td> that would have rendered. Return the default
// unchanged for read-only cells, or return a custom <td> with input/select.
export type CellRenderer = (
  row: number,
  col: number,
  value: string | null,
  defaultTd: React.ReactElement,
) => React.ReactElement;

interface TableViewProps {
  sheetName: string;
  data: (string | null | CellData)[][];
  selectedCell: { row: number; col: number } | null;
  onCellSelect: (cell: { row: number; col: number } | null) => void;
  isFirstRowHeader?: boolean;
  formulaBar?: boolean;
  // METADATA-CAPTURE PATCH
  cellRenderer?: CellRenderer;
}

const isCellData = (cell: string | null | CellData): cell is CellData =>
  cell !== null && typeof cell === "object" && "value" in cell;

const getCellValue = (cell: string | null | CellData): string | null =>
  isCellData(cell) ? cell.value : cell;

const getCellFormula = (cell: string | null | CellData): string | undefined =>
  isCellData(cell) ? cell.formula : undefined;

function TableViewComponent({
  sheetName: _sheetName,
  data,
  selectedCell,
  onCellSelect,
  isFirstRowHeader = false,
  formulaBar = false,
  cellRenderer, // METADATA-CAPTURE PATCH
}: TableViewProps): React.ReactElement {
  const visibleCols = Math.min(
    Math.max(...data.map((row) => row?.length || 0)),
    MAX_VISIBLE_COLS,
  );

  const [columnWidths, setColumnWidths] = useState<number[]>(() =>
    Array<number>(visibleCols).fill(MIN_COL_WIDTH),
  );
  const [isResizing, setIsResizing] = useState(false);
  const resizeStartX = useRef<number>(0);
  const resizeStartWidth = useRef<number>(0);
  const resizingColumnRef = useRef<number | null>(null);

  const columnToLetter = useCallback((col: number): string => {
    let letter = "";
    while (col >= 0) {
      letter = String.fromCharCode((col % 26) + 65) + letter;
      col = Math.floor(col / 26) - 1;
    }
    return letter;
  }, []);

  const formatCellValue = useCallback((value: string | null): string => {
    if (value === null || value === undefined) return "";
    return String(value);
  }, []);

  const getFormulaBarContent = useCallback(
    (cell: string | null | CellData): string => {
      const formula = getCellFormula(cell);
      if (formula) return `=${formula}`;
      return formatCellValue(getCellValue(cell));
    },
    [formatCellValue],
  );

  // ── Column resize handlers ─────────────────────────────────────────────

  const handleResizeMove = useCallback((e: MouseEvent) => {
    const deltaX = e.clientX - resizeStartX.current;
    const newWidth = Math.max(MIN_COL_WIDTH, resizeStartWidth.current + deltaX);
    setColumnWidths((prev) => {
      const updated = [...prev];
      if (resizingColumnRef.current !== null) {
        updated[resizingColumnRef.current] = newWidth;
      }
      return updated;
    });
  }, []);

  const handleResizeEnd = useCallback(() => {
    document.removeEventListener("mousemove", handleResizeMove);
    document.removeEventListener("mouseup", handleResizeEnd);
    resizingColumnRef.current = null;
    setIsResizing(false);
  }, [handleResizeMove]);

  const handleResizeStart = useCallback(
    (e: React.MouseEvent, colIndex: number) => {
      e.preventDefault();
      e.stopPropagation();
      setColumnWidths((prev) => {
        resizeStartWidth.current = prev[colIndex];
        return prev;
      });
      resizeStartX.current = e.clientX;
      resizingColumnRef.current = colIndex;
      setIsResizing(true);
      document.addEventListener("mousemove", handleResizeMove);
      document.addEventListener("mouseup", handleResizeEnd);
    },
    [handleResizeMove, handleResizeEnd],
  );

  // ── Empty state ────────────────────────────────────────────────────────

  if (!data.length) {
    return (
      <div className="flex items-center justify-center min-h-[400px] bg-sand-50 rounded-xl border border-sand-200">
        <div className="text-center">
          <svg className="w-10 h-10 mx-auto mb-3 text-sand-300" fill="none" stroke="currentColor" strokeWidth={1.25} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 01-1.125-1.125M3.375 19.5h7.5c.621 0 1.125-.504 1.125-1.125m-9.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 3.75h-7.5A1.125 1.125 0 0112 18.375m9.75-12.75c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125m19.5 0v1.5c0 .621-.504 1.125-1.125 1.125M2.25 5.625v1.5c0 .621.504 1.125 1.125 1.125m0 0h17.25m-17.25 0h7.5c.621 0 1.125.504 1.125 1.125M3.375 8.25c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125m17.25-3.75h-7.5c-.621 0-1.125.504-1.125 1.125m8.625-1.125c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h7.5m-7.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125M12 10.875v-1.5m0 1.5c0 .621-.504 1.125-1.125 1.125M12 10.875c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125M13.125 12h7.5m-7.5 0c-.621 0-1.125.504-1.125 1.125M20.625 12c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h7.5M12 14.625v-1.5m0 1.5c0 .621-.504 1.125-1.125 1.125M12 14.625c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125m0 1.5v-1.5m0 0c0-.621.504-1.125 1.125-1.125M12 16.5c0-.621.504-1.125 1.125-1.125m0 0h7.5" />
          </svg>
          <p className="text-sand-500 text-sm">No data to display</p>
        </div>
      </div>
    );
  }

  // ── Grid ───────────────────────────────────────────────────────────────

  const hasTruncatedRows = data.length > MAX_VISIBLE_ROWS;

  return (
    <div
      className="flex-1 min-h-0 w-full"
      style={{ cursor: isResizing ? "col-resize" : undefined }}
    >
      <div className="flex flex-col h-full bg-white rounded-xl border border-sand-200 overflow-hidden shadow-sm">
        {/* ─── Formula bar ─────────────────────────────────────────────── */}
        {formulaBar && (
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-sand-200 bg-sand-50/60 min-h-[44px]">
            {selectedCell ? (
              <>
                <span className="font-mono text-xs font-semibold text-brand-fig tabular-nums tracking-wide px-2 py-0.5 rounded bg-brand-magenta-100/60">
                  {columnToLetter(selectedCell.col)}{selectedCell.row + 1}
                </span>
                <span className="font-mono text-sm text-sand-700 truncate">
                  {data[selectedCell.row]?.[selectedCell.col]
                    ? getFormulaBarContent(data[selectedCell.row]?.[selectedCell.col])
                    : <span className="text-sand-400 italic">empty</span>}
                </span>
              </>
            ) : (
              <span className="text-sand-400 text-sm italic">Select a cell</span>
            )}
          </div>
        )}

        {/* ─── Grid ─────────────────────────────────────────────────────── */}
        <div className="flex-1 overflow-auto relative bg-white chat-scroll">
          <table className="border-collapse" style={{ tableLayout: "fixed" }}>
            {/* Column letter headers (A, B, C, …) — sticky top */}
            <thead className="sticky top-0 z-20">
              <tr>
                {/* Corner gutter — both-sticky */}
                <th
                  className="sticky left-0 z-30 border-r border-b border-sand-200 bg-sand-100"
                  style={{ width: ROW_HEADER_WIDTH, minWidth: ROW_HEADER_WIDTH }}
                />
                {Array.from({ length: visibleCols }).map((_, i) => (
                  <th
                    key={i}
                    className="relative border-r border-b border-sand-200 bg-sand-50 px-3 py-1.5
                               text-[11px] font-mono font-semibold text-sand-500 tracking-[0.12em]
                               select-none transition-colors"
                    style={{
                      width: columnWidths[i],
                      minWidth: columnWidths[i],
                    }}
                  >
                    {columnToLetter(i)}
                    {/* Resize handle — terracotta on hover */}
                    <div
                      className="absolute top-0 -right-px w-3 h-full cursor-col-resize
                                 hover:bg-brand-fig/30 active:bg-brand-fig/50 transition-colors z-10"
                      onMouseDown={(e) => handleResizeStart(e, i)}
                    />
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {data.slice(0, MAX_VISIBLE_ROWS).map((row, rowIndex) => {
                const isHeader = isFirstRowHeader && rowIndex === 0;
                return (
                  <tr key={rowIndex} className="group">
                    {/* Row number — sticky left, tabular-nums */}
                    <td
                      className="sticky left-0 z-10 border-r border-b border-sand-200 bg-sand-50
                                 text-center text-[11px] font-mono font-medium text-sand-400 tabular-nums
                                 group-hover:text-sand-600 group-hover:bg-sand-100 transition-colors"
                      style={{ width: ROW_HEADER_WIDTH, minWidth: ROW_HEADER_WIDTH }}
                    >
                      {rowIndex + 1}
                    </td>

                    {Array.from({ length: visibleCols }).map((_, colIndex) => {
                      const cell = row?.[colIndex];
                      const cellValue = getCellValue(cell);
                      const cellFormula = getCellFormula(cell);
                      const isSelected =
                        selectedCell?.row === rowIndex &&
                        selectedCell?.col === colIndex;

                      // The canonical <td>. EditableCell clones this (via
                      // cellRenderer) so all styling here — border, ring,
                      // hover, widths — carries through to edit mode.
                      const defaultTd = (
                        <td
                          key={colIndex}
                          onClick={() => onCellSelect({ row: rowIndex, col: colIndex })}
                          className={`
                            border-r border-b border-sand-200 px-3 py-1.5 text-sm
                            cursor-cell transition-all duration-75
                            ${isHeader
                              ? "bg-sand-50 text-sand-800 font-semibold"
                              : "text-sand-700"}
                            ${isSelected
                              ? // Terracotta ring — inset so it doesn't push neighbors.
                                // bg tint so the selection reads even at a glance.
                                "bg-brand-magenta-100/40 ring-2 ring-inset ring-brand-fig"
                              : isHeader
                                ? ""
                                : "hover:bg-sand-50/70"}
                          `}
                          style={{
                            width: columnWidths[colIndex],
                            minWidth: columnWidths[colIndex],
                            maxWidth: columnWidths[colIndex],
                          }}
                        >
                          {cellFormula && (!cellValue || cellValue === "") ? (
                            <span className="font-mono text-xs text-sand-400 italic">
                              ={cellFormula}
                            </span>
                          ) : (
                            <span className="truncate block">
                              {formatCellValue(cellValue)}
                            </span>
                          )}
                        </td>
                      );

                      // METADATA-CAPTURE PATCH: let wrapper inject editable cell.
                      // When cellRenderer is undefined this collapses to the default.
                      return cellRenderer
                        ? cellRenderer(rowIndex, colIndex, cellValue, defaultTd)
                        : defaultTd;
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* ─── Truncation footer ────────────────────────────────────────── */}
        {hasTruncatedRows && (
          <div className="px-4 py-2 border-t border-sand-200 bg-sand-50/60 text-xs text-sand-500 text-center">
            Showing first {MAX_VISIBLE_ROWS} of {data.length.toLocaleString()} rows
          </div>
        )}
      </div>
    </div>
  );
}

export const TableView = React.memo(TableViewComponent);
