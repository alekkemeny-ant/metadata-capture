'use client';

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type CellValue = string | number | null;

interface SpreadsheetViewerProps {
  columns: string[];
  rows: CellValue[][];
  totalRows?: number;
  sheetName?: string | null;
  // Edit-mode props — all optional. Absence → read-only behavior.
  recordIdColumn?: number;
  enums?: Record<string, string[]>;
  missingRows?: Set<number>;
  onCellCommit?: (recordId: string, column: string, value: string) => Promise<void>;
}

const MIN_COL_WIDTH = 80;
const DEFAULT_COL_WIDTH = 140;
// Module-level so the default doesn't create a fresh {} every render and defeat Row's memo.
const EMPTY_ENUMS: Record<string, string[]> = {};

// ---------------------------------------------------------------------------
// EditableCell — the edit state machine
//
// Ported from claude-ai's KnowledgeBases/EditableField.tsx:
//   - isEditing / draft / isSaving / error four-state model
//   - blur or Enter commits; Esc reverts
//   - no-change short-circuit (skip the network if draft === value)
//   - on save error: STAY in edit mode with error shown (don't silently revert)
//   - external-sync effect: when not editing, draft follows the prop
//
// Extended with an `enum` mode that renders a <select> instead of an <input>.
// ---------------------------------------------------------------------------

type CellMode = 'text' | 'enum' | 'readonly';

interface EditableCellProps {
  value: CellValue;
  mode: CellMode;
  enumValues?: string[];
  isSelected: boolean;
  onSelect: () => void;
  onCommit?: (value: string) => Promise<void>;
}

export const EditableCell = memo(function EditableCell({
  value,
  mode,
  enumValues,
  isSelected,
  onSelect,
  onCommit,
}: EditableCellProps) {
  const display = value == null ? '' : String(value);
  const isNumeric = typeof value === 'number' || /^-?\d+(\.\d+)?$/.test(display);

  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(display);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // External sync — when not editing, track the prop. Overlay updates after a
  // commit elsewhere (or initial load) flow through here.
  useEffect(() => {
    if (!isEditing) setDraft(display);
  }, [display, isEditing]);

  // Deselecting exits edit mode without committing. (User clicked another cell.)
  useEffect(() => {
    if (!isSelected && isEditing && !isSaving) {
      setIsEditing(false);
      setError(null);
    }
  }, [isSelected, isEditing, isSaving]);

  const startEdit = useCallback(() => {
    if (mode === 'readonly' || !onCommit) return;
    setDraft(display);
    setError(null);
    setIsEditing(true);
  }, [mode, onCommit, display]);

  const cancelEdit = useCallback(() => {
    setDraft(display);
    setError(null);
    setIsEditing(false);
  }, [display]);

  const commit = useCallback(
    async (val: string) => {
      if (!onCommit) return;
      const trimmed = val.trim();
      // No-change short-circuit — EditableField.tsx:91-94
      if (trimmed === display) {
        setIsEditing(false);
        return;
      }
      setIsSaving(true);
      setError(null);
      try {
        await onCommit(trimmed);
        setIsEditing(false);
      } catch (e) {
        // Stay in edit mode with the error — EditableField.tsx:101-110
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setIsSaving(false);
      }
    },
    [onCommit, display],
  );

  const handleClick = () => {
    if (isSelected && !isEditing) {
      startEdit(); // second click on an already-selected cell → edit
    } else {
      onSelect();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      cancelEdit();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      void commit(draft);
    }
  };

  // Selected, not yet editing: Enter or printable key enters edit mode.
  const handleDisplayKeyDown = (e: React.KeyboardEvent) => {
    if (mode === 'readonly' || !onCommit || isEditing) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      startEdit();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // Type-to-edit: printable char replaces content. preventDefault so the
      // same keystroke doesn't also flow into the autoFocused input (which
      // would double it — 'X' → setDraft('X') → keypress into input → 'XX').
      e.preventDefault();
      setDraft(e.key);
      setError(null);
      setIsEditing(true);
    }
  };

  // No inline width style: tableLayout:fixed drives <td> widths from the <th> row.
  const baseTd =
    'px-3 py-1.5 border-b border-r border-sand-100 truncate relative ' +
    (isNumeric ? 'tabular-nums ' : '') +
    (isSelected ? 'outline outline-2 outline-brand-fig -outline-offset-1 bg-brand-magenta-100/30 ' : '');

  // --- Read-only mode --------------------------------------------------------
  if (mode === 'readonly') {
    return (
      <td
        className={baseTd + 'text-sand-400 cursor-cell'}
        title={display}
        onClick={onSelect}
      >
        {display}
      </td>
    );
  }

  // --- Enum mode — editing ---------------------------------------------------
  if (mode === 'enum' && isEditing && enumValues) {
    return (
      <td className={baseTd + 'p-0'}>
        <select
          autoFocus
          value={draft}
          disabled={isSaving}
          onChange={(e) => {
            setDraft(e.target.value);
            void commit(e.target.value); // selects are single-action
          }}
          onBlur={() => !isSaving && cancelEdit()}
          onKeyDown={(e) => e.key === 'Escape' && cancelEdit()}
          className="w-full h-full px-2 py-1.5 bg-white border-0 focus:outline-none text-sm disabled:opacity-50"
        >
          {!enumValues.includes(display) && display && (
            <option value={display}>{display} (current)</option>
          )}
          {enumValues.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        {error && <CellError message={error} />}
      </td>
    );
  }

  // --- Text mode — editing ---------------------------------------------------
  if (isEditing) {
    return (
      <td className={baseTd + 'p-0'}>
        <input
          autoFocus
          value={draft}
          disabled={isSaving}
          onChange={(e) => {
            setDraft(e.target.value);
            if (error) setError(null);
          }}
          onBlur={() => !isSaving && void commit(draft)}
          onKeyDown={handleKeyDown}
          className="w-full h-full px-3 py-1.5 bg-white border-0 focus:outline-none text-sm disabled:opacity-50"
        />
        {isSaving && <CellSpinner />}
        {error && <CellError message={error} />}
      </td>
    );
  }

  // --- Display mode (text or enum, not editing) ------------------------------
  return (
    <td
      className={baseTd + 'cursor-cell'}
      title={display}
      tabIndex={isSelected ? 0 : -1}
      onClick={handleClick}
      onKeyDown={handleDisplayKeyDown}
    >
      {display}
      {mode === 'enum' && isSelected && (
        <span className="absolute right-2 top-1/2 -translate-y-1/2 text-sand-400 text-xs pointer-events-none">▾</span>
      )}
    </td>
  );
});

function CellSpinner() {
  return (
    <span className="absolute right-1.5 top-1/2 -translate-y-1/2 w-3 h-3 border-2 border-sand-200 border-t-brand-fig rounded-full animate-spin" />
  );
}

function CellError({ message }: { message: string }) {
  return (
    <div className="absolute left-0 top-full mt-px z-20 px-2 py-1 bg-red-600 text-white text-xs rounded-b shadow-lg max-w-xs">
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row — memoized so selection/edit in one row doesn't re-render the others
// ---------------------------------------------------------------------------

interface RowProps {
  rowIndex: number;
  row: CellValue[];
  columns: string[];
  columnModes: CellMode[];
  enums: Record<string, string[]>;
  selectedCol: number | null; // column selected in THIS row, else null
  isMissing: boolean;
  recordId: string | null;
  onSelect: (row: number, col: number) => void;
  onCommit?: (recordId: string, column: string, value: string) => Promise<void>;
}

const Row = memo(function Row({
  rowIndex, row, columns, columnModes, enums,
  selectedCol, isMissing, recordId, onSelect, onCommit,
}: RowProps) {
  const bg = rowIndex % 2 === 0 ? 'bg-white' : 'bg-sand-50/50';
  const missingBorder = isMissing ? 'border-l-4 border-l-amber-400' : '';

  return (
    <tr className={`${bg} ${missingBorder}`} title={isMissing ? 'Record not found — read-only' : undefined}>
      <td className="px-3 py-1.5 text-xs text-sand-400 border-b border-r border-sand-100 tabular-nums text-right sticky left-0 bg-inherit">
        {rowIndex + 1}
      </td>
      {columns.map((col, ci) => {
        const mode: CellMode = isMissing ? 'readonly' : columnModes[ci];
        const enumKey = col.toLowerCase();
        const handleCommit =
          onCommit && recordId && mode !== 'readonly'
            ? (v: string) => onCommit(recordId, col, v)
            : undefined;
        return (
          <EditableCell
            key={ci}
            value={row[ci] ?? null}
            mode={mode}
            enumValues={mode === 'enum' ? enums[enumKey] : undefined}
            isSelected={selectedCol === ci}
            onSelect={() => onSelect(rowIndex, ci)}
            onCommit={handleCommit}
          />
        );
      })}
    </tr>
  );
});

// ---------------------------------------------------------------------------
// Main component
//
// Visual shell ported from claude-ai's user-content-renderer/TableView.tsx:
//   - formula bar showing full content of selected cell
//   - column resize via drag handle (document-level mousemove/mouseup)
//   - sticky header row + row-number column
//   - selection ring (ours is brand-fig, theirs is blue-500)
// Skipped: A/B/C column letters (we have meaningful field names).
// ---------------------------------------------------------------------------

export default function SpreadsheetViewer({
  columns,
  rows,
  totalRows,
  sheetName,
  recordIdColumn,
  enums = EMPTY_ENUMS,
  missingRows,
  onCellCommit,
}: SpreadsheetViewerProps) {
  const shownRows = rows.length;
  const truncated = typeof totalRows === 'number' && totalRows > shownRows ? totalRows - shownRows : 0;
  const editable = onCellCommit !== undefined && recordIdColumn !== undefined;

  // Selection
  const [selected, setSelected] = useState<{ row: number; col: number } | null>(null);
  const handleSelect = useCallback((row: number, col: number) => {
    setSelected({ row, col });
  }, []);

  // Column widths + resize. TableView.tsx:89-125 pattern.
  const [colWidths, setColWidths] = useState<number[]>(() =>
    columns.map(() => DEFAULT_COL_WIDTH),
  );
  useEffect(() => {
    // Columns changed (different artifact) → reset widths.
    // `columns` is a stable reference from ArtifactModal's artifact.content,
    // so this only fires on genuine artifact switches, not every render.
    setColWidths(columns.map(() => DEFAULT_COL_WIDTH));
  }, [columns]);

  const resizeState = useRef<{ col: number; startX: number; startW: number } | null>(null);

  const handleResizeMove = useCallback((e: MouseEvent) => {
    const s = resizeState.current;
    if (!s) return;
    const w = Math.max(MIN_COL_WIDTH, s.startW + (e.clientX - s.startX));
    setColWidths((prev) => {
      const next = [...prev];
      next[s.col] = w;
      return next;
    });
  }, []);

  const handleResizeEnd = useCallback((e: MouseEvent) => {
    // stopPropagation on mouseup too — ArtifactModal's backdrop has
    // onClick={onClose} and TableView's pattern only stops on mousedown.
    e.stopPropagation();
    resizeState.current = null;
    document.removeEventListener('mousemove', handleResizeMove);
    document.removeEventListener('mouseup', handleResizeEnd);
  }, [handleResizeMove]);

  const handleResizeStart = useCallback(
    (e: React.MouseEvent, col: number) => {
      e.preventDefault();
      e.stopPropagation();
      resizeState.current = { col, startX: e.clientX, startW: colWidths[col] };
      document.addEventListener('mousemove', handleResizeMove);
      document.addEventListener('mouseup', handleResizeEnd);
    },
    [colWidths, handleResizeMove, handleResizeEnd],
  );

  // Per-column mode. Memoized — passed to every Row, so a fresh array here
  // would defeat Row's memo() and re-render the whole grid on every selection.
  const columnModes: CellMode[] = useMemo(
    () =>
      columns.map((col, ci) => {
        if (!editable) return 'readonly';
        if (ci === recordIdColumn) return 'readonly';
        if (enums[col.toLowerCase()]) return 'enum';
        return 'text';
      }),
    [columns, enums, recordIdColumn, editable],
  );

  // Formula bar content
  const selectedValue =
    selected && rows[selected.row]
      ? rows[selected.row][selected.col]
      : null;
  const selectedDisplay = selectedValue == null ? '' : String(selectedValue);
  const selectedColName = selected ? columns[selected.col] : '';

  if (columns.length === 0 && rows.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-sand-400 text-sm">
        No data
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {sheetName && (
        <div className="px-4 py-1.5 text-xs text-sand-500 border-b border-sand-100 shrink-0">
          Sheet: <span className="font-medium text-sand-600">{sheetName}</span>
        </div>
      )}

      {/* Formula bar — full content of selected cell */}
      {editable && (
        <div className="px-4 py-2 border-b border-sand-200 bg-sand-50/50 shrink-0 min-h-[40px] flex items-center gap-3">
          {selected ? (
            <>
              <span className="text-xs font-mono text-sand-500 shrink-0">
                {selectedColName}[{selected.row + 1}]
              </span>
              <span className="text-sm text-sand-700 font-mono truncate flex-1">
                {selectedDisplay || <span className="text-sand-400 italic">empty</span>}
              </span>
              {columnModes[selected.col] !== 'readonly' && (
                <span className="text-[10px] text-sand-400 shrink-0">
                  click again to edit · esc to cancel
                </span>
              )}
            </>
          ) : (
            <span className="text-xs text-sand-400">Select a cell</span>
          )}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        <table className="border-collapse text-sm" style={{ tableLayout: 'fixed' }}>
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="px-3 py-2 text-left text-xs font-medium text-sand-400 border-b border-r border-sand-200 bg-sand-100 w-12 sticky left-0">
                #
              </th>
              {columns.map((col, ci) => (
                <th
                  key={ci}
                  className="relative px-3 py-2 text-left text-xs font-semibold text-sand-700 border-b border-r border-sand-200 bg-sand-100 whitespace-nowrap"
                  style={{ width: `${colWidths[ci]}px`, minWidth: `${colWidths[ci]}px` }}
                >
                  {col}
                  {ci === recordIdColumn && (
                    <span className="ml-1.5 text-[9px] text-sand-400 font-normal">(link)</span>
                  )}
                  {/* Resize handle */}
                  <span
                    className="absolute top-0 -right-1 w-2 h-full cursor-col-resize hover:bg-brand-fig/40"
                    onMouseDown={(e) => handleResizeStart(e, ci)}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => {
              const rid =
                recordIdColumn !== undefined
                  ? String(row[recordIdColumn] ?? '').trim() || null
                  : null;
              return (
                <Row
                  key={ri}
                  rowIndex={ri}
                  row={row}
                  columns={columns}
                  columnModes={columnModes}
                  enums={enums}
                  selectedCol={selected?.row === ri ? selected.col : null}
                  isMissing={missingRows?.has(ri) ?? false}
                  recordId={rid}
                  onSelect={handleSelect}
                  onCommit={onCellCommit}
                />
              );
            })}
          </tbody>
        </table>
      </div>

      {truncated > 0 && (
        <div className="px-4 py-2 text-xs text-sand-500 border-t border-sand-200 bg-sand-50 shrink-0">
          Showing {shownRows} of {totalRows} rows ({truncated} more not shown)
        </div>
      )}
    </div>
  );
}
