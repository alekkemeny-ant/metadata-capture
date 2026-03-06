'use client';

import { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import SpreadsheetViewer from './SpreadsheetViewer';
import {
  fetchArtifact,
  fetchRecordsByIds,
  fetchSchemaEnums,
  fetchUploadTable,
  patchRecordField,
  Artifact,
  MetadataRecord,
  SchemaEnums,
  SpreadsheetData,
} from '../lib/api';

export type ArtifactSource = { type: 'upload'; id: string } | { type: 'artifact'; id: string };

interface ArtifactModalProps {
  source: ArtifactSource | null;
  onClose: () => void;
}

const TYPE_BADGE_STYLES: Record<string, string> = {
  table: 'bg-brand-aqua-500/10 text-brand-aqua-600 border-brand-aqua-500/20',
  json: 'bg-brand-fig/10 text-brand-fig border-brand-fig/20',
  markdown: 'bg-amber-500/10 text-amber-700 border-amber-500/20',
  code: 'bg-sand-600/10 text-sand-700 border-sand-300',
  spreadsheet: 'bg-brand-aqua-500/10 text-brand-aqua-600 border-brand-aqua-500/20',
};

function TypeBadge({ type }: { type: string }) {
  const style = TYPE_BADGE_STYLES[type] || 'bg-sand-100 text-sand-600 border-sand-200';
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wide border ${style}`}>
      {type}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Editable-table state. Only populated when a `table` artifact has a
// `record_id` column — that's the convention signalling it was rendered from
// metadata records and edits should write back to them.
// ---------------------------------------------------------------------------

interface EditState {
  recordIdColumn: number;
  enums: SchemaEnums;
  missingRows: Set<number>;
  // Live record data overlaid onto the snapshot. Keyed by record_id.
  // When a cell commits, we update this map and re-derive the overlay rows.
  liveData: Map<string, Record<string, unknown>>;
}

type TableContent = { columns: string[]; rows: (string | number | null)[][] };

/** Flatten a data_json value to its display string. species is {name,...} → name. */
function flattenForDisplay(value: unknown): string | number | null {
  if (value == null) return null;
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>;
    if (typeof v.name === 'string') return v.name;
    if (typeof v.abbreviation === 'string') return v.abbreviation;
    return JSON.stringify(value);
  }
  if (typeof value === 'string' || typeof value === 'number') return value;
  return String(value);
}

/** Build the overlay: for each row, replace cells with live data where we have it. */
function overlayRows(
  table: TableContent,
  recordIdColumn: number,
  liveData: Map<string, Record<string, unknown>>,
): (string | number | null)[][] {
  return table.rows.map((row) => {
    const rid = String(row[recordIdColumn] ?? '');
    const live = liveData.get(rid);
    if (!live) return row; // snapshot fallback (missing record, or not fetched yet)
    return table.columns.map((col, ci) => {
      if (ci === recordIdColumn) return row[ci];
      const liveVal = live[col];
      return liveVal !== undefined ? flattenForDisplay(liveVal) : row[ci];
    });
  });
}

export default function ArtifactModal({ source, onClose }: ArtifactModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [sheet, setSheet] = useState<SpreadsheetData | null>(null);
  const [editState, setEditState] = useState<EditState | null>(null);

  // Escape to close
  useEffect(() => {
    if (!source) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [source, onClose]);

  // Fetch on open
  useEffect(() => {
    if (!source) {
      setArtifact(null);
      setSheet(null);
      setEditState(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    setArtifact(null);
    setSheet(null);
    setEditState(null);

    const load = async () => {
      if (source.type === 'upload') {
        setSheet(await fetchUploadTable(source.id));
        return;
      }

      const art = await fetchArtifact(source.id);
      setArtifact(art);

      // If this is a table rendered from metadata records (agent included a
      // record_id column per the render_artifact convention), set up editing:
      // batch-fetch live records and overlay them onto the snapshot so the
      // user sees current truth, and edits write back via PATCH.
      if (art.artifact_type !== 'table') return;
      const table = art.content as TableContent;
      if (!Array.isArray(table?.columns)) return;

      const recordIdColumn = table.columns.findIndex(
        (c) => c.toLowerCase() === 'record_id',
      );
      if (recordIdColumn === -1) return; // no binding → stays read-only

      const ids = Array.from(
        new Set(
          table.rows
            .map((r) => String(r[recordIdColumn] ?? '').trim())
            .filter(Boolean),
        ),
      );

      const [enums, records] = await Promise.all([
        fetchSchemaEnums(),
        fetchRecordsByIds(ids),
      ]);

      const liveData = new Map<string, Record<string, unknown>>(
        records.map((r: MetadataRecord) => [r.id, r.data_json]),
      );
      const missingRows = new Set<number>(
        table.rows
          .map((r, i) => (liveData.has(String(r[recordIdColumn] ?? '')) ? -1 : i))
          .filter((i) => i >= 0),
      );

      setEditState({ recordIdColumn, enums, missingRows, liveData });
    };

    load()
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [source]);

  // Cell commit: PATCH the record, then refresh the overlay map so the cell
  // immediately reflects the new value (and any other fields the backend
  // touched, e.g. species registry enrichment). Throws on failure — the
  // EditableCell catches it and stays in edit mode showing the message.
  const handleCellCommit = useCallback(
    async (recordId: string, column: string, value: string) => {
      const updated = await patchRecordField(recordId, column, value);
      setEditState((prev) => {
        if (!prev) return prev;
        const next = new Map(prev.liveData);
        next.set(recordId, updated.data_json);
        return { ...prev, liveData: next };
      });
    },
    [],
  );

  if (!source) return null;

  // Determine title + type badge
  let title = 'Loading…';
  let badgeType = 'table';
  if (sheet) {
    title = sheet.filename || 'Spreadsheet';
    badgeType = 'spreadsheet';
  } else if (artifact) {
    title = artifact.title;
    badgeType = artifact.artifact_type;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-sand-900/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col overflow-hidden mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Title bar */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-sand-200 shrink-0">
          <TypeBadge type={badgeType} />
          <h2 className="flex-1 text-sm font-medium text-sand-700 truncate">{title}</h2>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-sand-400 hover:text-sand-700 hover:bg-sand-100 transition-colors"
            title="Close (Esc)"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-64 text-sand-400 text-sm">
              <span className="inline-block w-4 h-4 border-2 border-sand-300 border-t-brand-fig rounded-full animate-spin mr-2" />
              Loading…
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-64 text-red-600 text-sm px-6">
              {error}
            </div>
          ) : sheet ? (
            <SpreadsheetViewer
              columns={sheet.columns}
              rows={sheet.rows}
              totalRows={sheet.total_rows}
              sheetName={sheet.sheet_name}
            />
          ) : artifact?.artifact_type === 'table' ? (
            (() => {
              const table = artifact.content as TableContent;
              const cols = table.columns || [];
              const snapshot = table.rows || [];
              // Overlay live record data if this table is record-bound.
              // editState is null while the batch fetch is in flight (first
              // ~50ms of modal open) — render the snapshot meanwhile.
              const rows = editState
                ? overlayRows({ columns: cols, rows: snapshot }, editState.recordIdColumn, editState.liveData)
                : snapshot;
              return (
                <SpreadsheetViewer
                  columns={cols}
                  rows={rows}
                  recordIdColumn={editState?.recordIdColumn}
                  enums={editState ? (editState.enums as unknown as Record<string, string[]>) : undefined}
                  missingRows={editState?.missingRows}
                  onCellCommit={editState ? handleCellCommit : undefined}
                />
              );
            })()
          ) : artifact ? (
            <ArtifactBody artifact={artifact} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ArtifactBody({ artifact }: { artifact: Artifact }) {
  // `table` is handled inline in ArtifactModal (needs editState + commit handler).
  const { artifact_type, content, language } = artifact;

  if (artifact_type === 'json') {
    const pretty = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    return (
      <div className="overflow-auto h-full">
        <pre className="p-5 text-xs font-mono text-sand-700 whitespace-pre-wrap">{pretty}</pre>
      </div>
    );
  }

  if (artifact_type === 'markdown') {
    const md = typeof content === 'string' ? content : String(content);
    return (
      <div className="overflow-auto h-full p-6 prose prose-sm max-w-none
                      prose-headings:text-sand-800 prose-p:text-sand-700
                      prose-strong:text-sand-800 prose-code:text-brand-fig">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </div>
    );
  }

  if (artifact_type === 'code') {
    const code = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    return (
      <div className="overflow-auto h-full relative">
        {language && (
          <div className="absolute top-3 right-4 px-2 py-0.5 rounded bg-sand-100 text-sand-500 text-[10px] font-mono uppercase">
            {language}
          </div>
        )}
        <pre className="p-5 text-xs font-mono text-sand-700 whitespace-pre-wrap">{code}</pre>
      </div>
    );
  }

  return (
    <div className="p-6 text-sand-400 text-sm">Unsupported artifact type: {artifact_type}</div>
  );
}
