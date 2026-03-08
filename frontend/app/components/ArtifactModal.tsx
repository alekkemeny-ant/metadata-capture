'use client';

import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import SpreadsheetViewer from './SpreadsheetViewer';
import { fetchArtifact, fetchUploadTable, Artifact, SpreadsheetData } from '../lib/api';

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

export default function ArtifactModal({ source, onClose }: ArtifactModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [sheet, setSheet] = useState<SpreadsheetData | null>(null);

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
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    setArtifact(null);
    setSheet(null);

    const load = async () => {
      if (source.type === 'upload') {
        setSheet(await fetchUploadTable(source.id));
      } else {
        setArtifact(await fetchArtifact(source.id));
      }
    };

    load()
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [source]);

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
          ) : artifact ? (
            <ArtifactBody artifact={artifact} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ArtifactBody({ artifact }: { artifact: Artifact }) {
  const { artifact_type, content, language } = artifact;

  if (artifact_type === 'table') {
    const table = content as { columns?: string[]; rows?: (string | number | null)[][] };
    return (
      <SpreadsheetViewer
        columns={table.columns || []}
        rows={table.rows || []}
      />
    );
  }

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
