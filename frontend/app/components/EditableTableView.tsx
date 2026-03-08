'use client';

import React, { cloneElement, memo, useCallback, useEffect, useMemo, useState } from 'react';
import { TableView, type CellRenderer } from './vendor/TableView';

// ============================================================================
// EditableTableView
//
// Wraps the vendored claude.ai TableView (read-only file-preview grid) and
// layers an edit state machine on top via the `cellRenderer` injection point.
//
// In display mode, each cell IS the upstream <td> (pixel-identical to
// claude.ai). In edit mode we clone that <td> and swap its children for an
// <input> or <select>, preserving width / borders / selection ring.
//
// Data shape adapter: metadata-capture's {columns, rows} is converted to
// TableView's 2D array with columns prepended as row 0 + isFirstRowHeader.
// All cellRenderer row indices are TableView-space; subtract HEADER_ROWS
// to get back to data-space.
// ============================================================================

type CellValue = string | number | null;

export interface EditableTableViewProps {
  columns: string[];
  rows: CellValue[][];
  totalRows?: number;
  sheetName?: string | null;
  // Edit-mode props — all optional. Absence → pure read-only TableView.
  recordIdColumn?: number;
  enums?: Record<string, string[]>;
  missingRows?: Set<number>; // data-space row indices
  onCellCommit?: (recordId: string, column: string, value: string) => Promise<void>;
}

const HEADER_ROWS = 1; // columns prepended as row 0 in TableView-space
const EMPTY_ENUMS: Record<string, string[]> = {};

type CellMode = 'text' | 'enum' | 'readonly';

// ---------------------------------------------------------------------------
// EditableCell — the edit state machine
//
// Four-state model: isEditing / draft / isSaving / error.
//   - blur or Enter commits; Esc reverts
//   - no-change short-circuit (skip network if draft === value)
//   - on save error: STAY in edit mode with error shown (don't revert)
//   - external-sync: when not editing, draft follows the prop
//
// Rendering strategy: receives the upstream `defaultTd` and clones it.
// Display mode → clone with overridden onClick/onKeyDown (preserves all
// upstream styling). Edit mode → clone with children swapped for <input>
// or <select> (preserves width/selection ring, swaps content).
// ---------------------------------------------------------------------------

interface EditableCellProps {
  defaultTd: React.ReactElement;
  value: string | null;
  mode: CellMode;
  enumValues?: string[];
  isSelected: boolean;
  onSelect: () => void;
  onCommit?: (value: string) => Promise<void>;
}

export const EditableCell = memo(function EditableCell({
  defaultTd,
  value,
  mode,
  enumValues,
  isSelected,
  onSelect,
  onCommit,
}: EditableCellProps) {
  const display = value ?? '';

  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(display);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // External sync — overlay updates (commit elsewhere, initial load) flow in here.
  useEffect(() => {
    if (!isEditing) setDraft(display);
  }, [display, isEditing]);

  // Deselecting exits edit mode without committing.
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
      if (trimmed === display) {
        // No-change short-circuit — never calls onCommit
        setIsEditing(false);
        return;
      }
      setIsSaving(true);
      setError(null);
      try {
        await onCommit(trimmed);
        setIsEditing(false);
      } catch (e) {
        // Stay in edit mode with error — don't silently revert
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setIsSaving(false);
      }
    },
    [onCommit, display],
  );

  // Second click on selected cell enters edit; first click selects.
  const handleClick = useCallback(() => {
    if (isSelected && !isEditing) {
      startEdit();
    } else {
      onSelect();
    }
  }, [isSelected, isEditing, startEdit, onSelect]);

  // Selected, not yet editing: Enter or printable key enters edit mode.
  const handleDisplayKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (mode === 'readonly' || !onCommit || isEditing) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      startEdit();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // Type-to-edit. preventDefault so the autoFocused input doesn't double it.
      e.preventDefault();
      setDraft(e.key);
      setError(null);
      setIsEditing(true);
    }
  }, [mode, onCommit, isEditing, startEdit]);

  const handleEditKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      cancelEdit();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      void commit(draft);
    }
  }, [cancelEdit, commit, draft]);

  // --- Read-only: cloned td + muted text, content always from `value` ---
  // (defaultTd is a styling vehicle only — display is always driven by `value` prop)
  if (mode === 'readonly') {
    return cloneElement(defaultTd, {
      onClick: onSelect,
      className: (defaultTd.props.className ?? '') + ' !text-sand-400',
      children: <span className="truncate block">{display}</span>,
    });
  }

  // --- Enum editing ---
  if (mode === 'enum' && isEditing && enumValues) {
    return cloneElement(defaultTd, {
      onClick: undefined,
      // ring-brand-orange signals "live editing" — warmer than the
      // selection fig ring so the two states read differently at a glance.
      className: (defaultTd.props.className ?? '') + ' !p-0 relative !ring-brand-orange-500',
      children: (
        <>
          <select
            autoFocus
            value={draft}
            disabled={isSaving}
            onChange={(e) => {
              setDraft(e.target.value);
              void commit(e.target.value); // selects commit immediately
            }}
            onBlur={() => !isSaving && cancelEdit()}
            onKeyDown={(e) => e.key === 'Escape' && cancelEdit()}
            className="w-full h-full px-3 py-1.5 bg-white border-0 focus:outline-none text-sm text-sand-800 disabled:opacity-50 cursor-pointer"
          >
            {!enumValues.includes(display) && display && (
              <option value={display}>{display} (current)</option>
            )}
            {enumValues.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          {error && <CellError message={error} />}
        </>
      ),
    });
  }

  // --- Text editing ---
  if (isEditing) {
    return cloneElement(defaultTd, {
      onClick: undefined,
      className: (defaultTd.props.className ?? '') + ' !p-0 relative !ring-brand-orange-500',
      children: (
        <>
          <input
            autoFocus
            value={draft}
            disabled={isSaving}
            onChange={(e) => {
              setDraft(e.target.value);
              if (error) setError(null);
            }}
            onBlur={() => !isSaving && void commit(draft)}
            onKeyDown={handleEditKeyDown}
            className="w-full h-full px-3 py-1.5 bg-white border-0 focus:outline-none text-sm text-sand-800 disabled:opacity-50"
          />
          {isSaving && <CellSpinner />}
          {error && <CellError message={error} />}
        </>
      ),
    });
  }

  // --- Display (not editing) — clone td with edit-entry handlers ---
  // Keep {display} as a direct text child so screen.getByText().focus()
  // (and keyboard events generally) land on the td, not a wrapping span.
  return cloneElement(defaultTd, {
    onClick: handleClick,
    onKeyDown: handleDisplayKeyDown,
    tabIndex: isSelected ? 0 : -1,
    children: (
      <>
        {display}
        {mode === 'enum' && isSelected && (
          <svg
            data-testid="enum-hint"
            className="float-right w-3 h-3 mt-0.5 text-sand-400 pointer-events-none"
            fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </>
    ),
  });
});

function CellSpinner() {
  return (
    <span className="absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5
                     border-2 border-sand-200 border-t-brand-fig rounded-full animate-spin" />
  );
}

function CellError({ message }: { message: string }) {
  return (
    <div className="absolute left-0 top-full mt-px z-30 px-2.5 py-1.5
                    bg-brand-orange-600 text-white text-xs rounded-b-lg shadow-lg max-w-xs
                    animate-[slideDown_0.15s_ease-out]">
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wrapper
// ---------------------------------------------------------------------------

export default function EditableTableView({
  columns,
  rows,
  totalRows,
  sheetName,
  recordIdColumn,
  enums = EMPTY_ENUMS,
  missingRows,
  onCellCommit,
}: EditableTableViewProps) {
  const editable = onCellCommit !== undefined && recordIdColumn !== undefined;

  // Selection lives here — TableView is controlled.
  // Stored in TableView-space (includes header row offset).
  const [selected, setSelected] = useState<{ row: number; col: number } | null>(null);

  // Adapt metadata-capture's {columns, rows} → TableView's 2D array.
  // Prepend columns as row 0, set isFirstRowHeader. Numbers → strings.
  const tableData: (string | null)[][] = useMemo(() => {
    const data: (string | null)[][] = [columns];
    for (const row of rows) {
      data.push(row.map((v) => (v == null ? null : String(v))));
    }
    return data;
  }, [columns, rows]);

  // Per-column edit mode. Memoized — identity matters for cellRenderer's memo.
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

  // cellRenderer — the injection point. Returns upstream <td> for header/readonly,
  // wraps it in EditableCell for everything else.
  const cellRenderer: CellRenderer | undefined = useMemo(() => {
    // When not editable, skip injection entirely — upstream handles everything.
    // TS narrows onCellCommit + recordIdColumn to defined via `editable` below.
    if (!editable || !onCellCommit || recordIdColumn === undefined) return undefined;
    return (tvRow, col, value, defaultTd) => {
      // Row 0 in TableView-space is the header — leave it as upstream's bold <td>.
      if (tvRow < HEADER_ROWS) return defaultTd;

      const dataRow = tvRow - HEADER_ROWS;
      const isMissing = missingRows?.has(dataRow) ?? false;
      const mode: CellMode = isMissing ? 'readonly' : columnModes[col];
      const isSelected = selected?.row === tvRow && selected?.col === col;

      const recordId = rows[dataRow]?.[recordIdColumn];
      const ridStr = recordId == null ? null : String(recordId).trim() || null;

      const handleCommit =
        ridStr && mode !== 'readonly'
          ? (v: string) => onCellCommit(ridStr, columns[col], v)
          : undefined;

      return (
        <EditableCell
          key={col}
          defaultTd={defaultTd}
          value={value}
          mode={mode}
          enumValues={mode === 'enum' ? enums[columns[col].toLowerCase()] : undefined}
          isSelected={isSelected}
          onSelect={() => setSelected({ row: tvRow, col })}
          onCommit={handleCommit}
        />
      );
    };
    // `rows` in deps because ridStr reads from it — stable-ref from ArtifactModal,
    // so this doesn't thrash.
  }, [editable, columns, rows, columnModes, enums, missingRows, recordIdColumn, onCellCommit, selected]);

  const shownRows = rows.length;
  const truncated = typeof totalRows === 'number' && totalRows > shownRows ? totalRows - shownRows : 0;

  if (columns.length === 0 && rows.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500 text-sm">
        No data
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {sheetName && (
        <div className="px-4 py-1.5 text-xs text-gray-500 border-b border-gray-200 shrink-0 bg-white">
          Sheet: <span className="font-medium text-gray-700">{sheetName}</span>
        </div>
      )}

      <div className="flex-1 min-h-0">
        <TableView
          sheetName={sheetName ?? 'Data'}
          data={tableData}
          selectedCell={selected}
          onCellSelect={setSelected}
          isFirstRowHeader
          formulaBar={editable}
          cellRenderer={cellRenderer}
        />
      </div>

      {truncated > 0 && (
        <div className="px-4 py-2 text-xs text-gray-500 border-t border-gray-200 bg-gray-50 shrink-0">
          Showing {shownRows} of {totalRows} rows ({truncated} more not shown)
        </div>
      )}
    </div>
  );
}
