'use client';

import { useState, useEffect, useCallback, Fragment } from 'react';
import Link from 'next/link';
import { fetchRecords, confirmRecord, updateRecordData, MetadataRecord, fetchSessions, Session } from '../lib/api';
import Header from '../components/Header';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RECORD_TYPE_LABELS: Record<string, string> = {
  subject: 'Subject',
  procedures: 'Procedures',
  instrument: 'Instrument',
  rig: 'Rig',
  data_description: 'Data Description',
  acquisition: 'Acquisition',
  session: 'Session',
  processing: 'Processing',
  quality_control: 'Quality Control',
};

const SHARED_TYPES = ['subject', 'procedures', 'instrument', 'rig'];
const ASSET_TYPES = ['data_description', 'acquisition', 'session', 'processing', 'quality_control'];


/** Known fields per record type — shown as placeholders. */
const FIELD_SCHEMAS: Record<string, { label: string; key: string }[]> = {
  subject: [
    { label: 'Subject ID', key: 'subject_id' },
    { label: 'Species', key: 'species' },
    { label: 'Sex', key: 'sex' },
    { label: 'Genotype', key: 'genotype' },
  ],
  procedures: [
    { label: 'Procedure Type', key: 'procedure_type' },
    { label: 'Protocol ID', key: 'protocol_id' },
    { label: 'Notes', key: 'notes' },
  ],
  data_description: [
    { label: 'Project Name', key: 'project_name' },
    { label: 'Modality', key: 'modality' },
    { label: 'Institution', key: 'institution' },
  ],
  session: [
    { label: 'Start Time', key: 'session_start_time' },
    { label: 'End Time', key: 'session_end_time' },
    { label: 'Rig ID', key: 'rig_id' },
  ],
  instrument: [{ label: 'Instrument ID', key: 'instrument_id' }],
  rig: [{ label: 'Rig ID', key: 'rig_id' }],
  acquisition: [{ label: 'Notes', key: 'notes' }],
  processing: [{ label: 'Pipeline', key: 'pipeline' }],
  quality_control: [{ label: 'Status', key: 'status' }],
};

// ---------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------

/** Render any value as a human-readable string. */
function humanize(v: unknown): string {
  // Try to parse JSON strings first
  let parsed = v;
  if (typeof v === 'string') {
    const trimmed = v.trim();
    if ((trimmed.startsWith('[') || trimmed.startsWith('{')) && trimmed.length > 2) {
      try { parsed = JSON.parse(trimmed); } catch { /* not JSON */ }
    }
  }
  if (parsed == null) return '';
  if (typeof parsed !== 'object') return String(parsed);
  if (Array.isArray(parsed)) {
    return parsed.map((item) => humanize(item)).join(', ');
  }
  const obj = parsed as Record<string, unknown>;
  if ('name' in obj && Object.keys(obj).length <= 2) return String(obj.name);
  return Object.entries(obj)
    .map(([k, val]) => `${k.replace(/_/g, ' ')}: ${humanize(val)}`)
    .join(', ');
}

function _StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    draft: 'bg-brand-orange-100 text-brand-orange-600 border-brand-orange-500/20',
    validated: 'bg-brand-violet-500/10 text-brand-violet-600 border-brand-violet-500/20',
    confirmed: 'bg-brand-aqua-500/10 text-brand-aqua-700 border-brand-aqua-500/20',
    error: 'bg-brand-magenta-100 text-brand-magenta-600 border-brand-magenta-200',
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${styles[status] || styles.draft}`}>
      {status}
    </span>
  );
}

function CategoryBadge({ category }: { category: string }) {
  const colors =
    category === 'shared'
      ? 'bg-brand-violet-500/10 text-brand-violet-600 border-brand-violet-500/20'
      : 'bg-sand-100 text-sand-500 border-sand-200';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${colors}`}>
      {category}
    </span>
  );
}

interface EditorRow {
  key: string;
  label: string;
  value: string;
  /** If set, this row is a section header (non-editable divider). */
  isHeader?: boolean;
}

/** Try to parse a JSON string; return parsed value or original. */
function tryParseJson(v: unknown): unknown {
  if (typeof v !== 'string') return v;
  const trimmed = v.trim();
  if ((trimmed.startsWith('[') || trimmed.startsWith('{')) && trimmed.length > 2) {
    try { return JSON.parse(trimmed); } catch { /* not JSON */ }
  }
  return v;
}

/** Flatten nested data into rows for the record editor. */
function flattenForEditor(
  obj: Record<string, unknown>,
  schemaFields: { label: string; key: string }[],
): EditorRow[] {
  const schemaLabelMap = new Map(schemaFields.map((s) => [s.key, s.label]));
  const rows: EditorRow[] = [];

  function walk(data: unknown, keyPath: string, label: string) {
    if (data == null) return;
    const parsed = tryParseJson(data);
    if (typeof parsed === 'string') {
      rows.push({ key: keyPath, label, value: parsed });
      return;
    }
    if (typeof parsed !== 'object') {
      rows.push({ key: keyPath, label, value: String(parsed) });
      return;
    }
    if (Array.isArray(parsed)) {
      // Simple arrays (strings, numbers, {name:"..."}) → one row
      const isSimple = parsed.every((item) =>
        typeof item !== 'object' || (item && 'name' in item && Object.keys(item).length <= 2)
      );
      if (isSimple || parsed.length === 0) {
        rows.push({ key: keyPath, label, value: parsed.map((item) => humanize(item)).join(', ') });
      } else {
        // Complex arrays → section header per item, then flat child fields
        parsed.forEach((item, i) => {
          // Add a visual header for each array item
          const headerLabel = parsed.length === 1 ? label : `${label} #${i + 1}`;
          rows.push({ key: `${keyPath}[${i}]`, label: headerLabel, value: '', isHeader: true });
          // Flatten item's fields directly (no deep nesting in labels)
          if (item && typeof item === 'object' && !Array.isArray(item)) {
            for (const [k, v] of Object.entries(item as Record<string, unknown>)) {
              walk(v, `${keyPath}[${i}].${k}`, k.replace(/_/g, ' '));
            }
          } else {
            rows.push({ key: `${keyPath}[${i}]`, label: `item`, value: humanize(item) });
          }
        });
      }
      return;
    }
    const obj = parsed as Record<string, unknown>;
    // {name: "..."} with 1-2 keys → collapse
    if ('name' in obj && Object.keys(obj).length <= 2) {
      rows.push({ key: keyPath, label, value: String(obj.name) });
      return;
    }
    // Recurse into nested objects — use just the child key name as label
    for (const [k, v] of Object.entries(obj)) {
      walk(v, `${keyPath}.${k}`, k.replace(/_/g, ' '));
    }
  }

  for (const [k, v] of Object.entries(obj)) {
    const label = schemaLabelMap.get(k) || k.replace(/_/g, ' ');
    walk(v, k, label);
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Inline field editor for a single record
// ---------------------------------------------------------------------------

function RecordEditor({
  record,
  onSaved,
}: {
  record: MetadataRecord;
  onSaved: () => void;
}) {
  const data = (record.data_json || {}) as Record<string, unknown>;
  const schema = FIELD_SCHEMAS[record.record_type] || [];

  // Build field list: existing fields (flattened) + missing schema fields
  const existingKeys = new Set(Object.keys(data));
  const existingFields = flattenForEditor(data, schema);
  const missingFields: EditorRow[] = schema
    .filter((s) => !existingKeys.has(s.key))
    .map((s) => ({ key: s.key, label: s.label, value: '' }));

  const allFields = [...existingFields, ...missingFields];

  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [addingField, setAddingField] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');

  const saveField = async (key: string, value: string) => {
    setEditingKey(null);
    if (!value.trim()) return;
    // Support dot-path keys like "coordinates.x"
    const parts = key.split('.');
    const newData = structuredClone(data);
    let target: Record<string, unknown> = newData;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!(parts[i] in target) || typeof target[parts[i]] !== 'object') {
        target[parts[i]] = {};
      }
      target = target[parts[i]] as Record<string, unknown>;
    }
    target[parts[parts.length - 1]] = value.trim();
    try {
      await updateRecordData(record.id, newData);
      onSaved();
    } catch { /* retry on next reload */ }
  };

  const addField = async () => {
    const cleanKey = newKey.trim().replace(/\s+/g, '_');
    if (!cleanKey || !newValue.trim()) { setAddingField(false); return; }
    const newData = { ...data, [cleanKey]: newValue.trim() };
    try {
      await updateRecordData(record.id, newData);
      onSaved();
    } catch { /* fail silently */ }
    setNewKey('');
    setNewValue('');
    setAddingField(false);
  };

  const deleteField = async (key: string) => {
    const parts = key.split('.');
    const topKey = parts[0];
    const { [topKey]: _unused, ...rest } = data;
    try {
      await updateRecordData(record.id, rest);
      onSaved();
    } catch { /* fail silently */ }
  };

  return (
    <div className="bg-white rounded-lg border border-sand-200 p-3">
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-2 h-2 rounded-full ${existingFields.length > 0 ? 'bg-brand-aqua-500' : 'bg-sand-300'}`} />
        <h5 className="text-sm font-medium text-sand-800">
          {record.name || RECORD_TYPE_LABELS[record.record_type]}
        </h5>
        <CategoryBadge category={record.category} />
        <span className="text-[10px] text-sand-400 ml-auto">{record.id.slice(0, 8)}</span>
      </div>
      <div className="space-y-0.5">
        {allFields.map((field) =>
          field.isHeader ? (
            <div key={field.key} className="pt-2 pb-0.5 first:pt-0">
              <span className="text-[10px] font-semibold text-sand-400 uppercase tracking-wider capitalize">{field.label}</span>
            </div>
          ) : (
            <div
              key={field.key}
              className="flex text-xs gap-2 items-center rounded px-1 -mx-1 py-0.5 group hover:bg-sand-50"
            >
              <span className="text-sand-400 shrink-0 w-36 truncate capitalize">{field.label}:</span>
              {editingKey === field.key ? (
                <input
                  autoFocus
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => saveField(field.key, editValue)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                    if (e.key === 'Escape') setEditingKey(null);
                  }}
                  className="text-sand-700 flex-1 border-b border-brand-fig/50 bg-transparent py-0.5 focus:outline-none"
                />
              ) : field.value ? (
                <>
                  <span
                    className="text-sand-700 flex-1 cursor-pointer"
                    onClick={() => { setEditingKey(field.key); setEditValue(field.value); }}
                  >
                    {field.value}
                  </span>
                  <button
                    onClick={() => deleteField(field.key)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity text-sand-300 hover:text-brand-orange-600 shrink-0"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                      <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6" />
                    </svg>
                  </button>
                </>
              ) : (
                <span
                  className="text-sand-300 italic cursor-pointer hover:text-sand-400"
                  onClick={() => { setEditingKey(field.key); setEditValue(''); }}
                >
                  click to add
                </span>
              )}
            </div>
          )
        )}

        {addingField ? (
          <div className="flex text-xs gap-2 items-center pt-1.5">
            <input
              autoFocus
              placeholder="field name"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') { setAddingField(false); setNewKey(''); setNewValue(''); }
              }}
              className="w-36 shrink-0 border-b border-sand-300 bg-transparent py-0.5 focus:outline-none focus:border-brand-fig placeholder:text-sand-300"
            />
            <span className="text-sand-400">:</span>
            <input
              placeholder="value"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onBlur={addField}
              onKeyDown={(e) => {
                if (e.key === 'Enter') addField();
                if (e.key === 'Escape') { setAddingField(false); setNewKey(''); setNewValue(''); }
              }}
              className="flex-1 border-b border-sand-300 bg-transparent py-0.5 text-sand-700 focus:outline-none focus:border-brand-fig placeholder:text-sand-300"
            />
          </div>
        ) : (
          <button
            onClick={() => setAddingField(true)}
            className="text-xs text-sand-400 hover:text-brand-fig flex items-center gap-1 pt-1.5 transition-colors"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" viewBox="0 0 24 24"><path d="M12 4v16m8-8H4" /></svg>
            Add field
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Session View — records grouped by chat session
// ---------------------------------------------------------------------------

function SessionView({
  records,
  sessions,
  expandedId,
  onToggle,
  onConfirm,
  onFieldSaved,
}: {
  records: MetadataRecord[];
  sessions: Session[];
  expandedId: string | null;
  onToggle: (id: string) => void;
  onConfirm: (id: string) => void;
  onFieldSaved: () => void;
}) {
  // Group records by session_id
  const bySession: Record<string, MetadataRecord[]> = {};
  for (const r of records) {
    (bySession[r.session_id] ||= []).push(r);
  }

  // Order sessions by most recent
  const sessionIds = sessions.map((s) => s.session_id);
  // Include any session_ids that have records but aren't in the sessions list
  const recordSessionIds = Array.from(new Set(records.map((r) => r.session_id)));
  const allSessionIds = Array.from(new Set(sessionIds.concat(recordSessionIds)));

  if (allSessionIds.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-sand-400">
        <p className="text-lg">No metadata records found</p>
        <p className="text-sm mt-1">Start a chat to capture experiment metadata</p>
      </div>
    );
  }

  return (
    <table className="w-full bg-white rounded-xl border border-sand-200 overflow-hidden">
      <thead>
        <tr className="bg-sand-50 border-b border-sand-200">
          <th className="text-left px-6 py-3 text-xs font-semibold text-sand-500 uppercase tracking-wider">Session</th>
          <th className="text-left px-6 py-3 text-xs font-semibold text-sand-500 uppercase tracking-wider">Records</th>
          <th className="text-left px-6 py-3 text-xs font-semibold text-sand-500 uppercase tracking-wider">Created</th>
          <th className="text-left px-6 py-3 text-xs font-semibold text-sand-500 uppercase tracking-wider">Actions</th>
        </tr>
      </thead>
      <tbody>
        {allSessionIds.map((sid) => {
          const sessionRecords = bySession[sid] || [];
          const session = sessions.find((s) => s.session_id === sid);
          const title = session?.first_message?.slice(0, 60) || sid.slice(0, 12);
          const isExpanded = expandedId === sid;

          return (
            <Fragment key={sid}>
              <tr
                id={`row-${sid}`}
                onClick={() => onToggle(sid)}
                className="border-b border-sand-100 hover:bg-sand-50 cursor-pointer transition-colors"
              >
                <td className="px-6 py-4">
                  <div className="text-sm font-medium text-sand-800 truncate max-w-xs">{title}</div>
                  <div className="text-xs text-sand-400">{sid.slice(0, 8)}</div>
                </td>
                <td className="px-6 py-4">
                  <div className="flex flex-wrap gap-1">
                    {sessionRecords.length > 0 ? sessionRecords.map((r) => (
                      <span key={r.id} className="inline-flex items-center px-2 py-0.5 rounded bg-brand-coral/30 text-brand-fig text-xs">
                        {r.record_type.replace(/_/g, ' ')}
                      </span>
                    )) : (
                      <span className="text-xs text-sand-400 italic">No records</span>
                    )}
                  </div>
                </td>
                <td className="px-6 py-4 text-sm text-sand-500">
                  {session ? new Date(session.created_at).toLocaleString() : '—'}
                </td>
                <td className="px-6 py-4">
                  <button className="text-xs text-brand-fig hover:text-brand-magenta-800 font-medium">
                    {isExpanded ? 'Collapse' : 'Expand'}
                  </button>
                </td>
              </tr>
              {isExpanded && (
                <tr key={`${sid}-expanded`}>
                  <td colSpan={4} className="px-6 py-4 bg-sand-50 border-b border-sand-200">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {sessionRecords.map((r) => (
                        <div key={r.id} id={`record-${r.id}`}>
                          <RecordEditor record={r} onSaved={onFieldSaved} />
                        </div>
                      ))}
                    </div>
                    {sessionRecords.some((r) => r.status === 'draft') && (
                      <div className="flex gap-2 pt-3 mt-3 border-t border-sand-200">
                        {sessionRecords.filter((r) => r.status === 'draft').map((r) => (
                          <button
                            key={r.id}
                            onClick={() => onConfirm(r.id)}
                            className="px-3 py-1.5 bg-brand-aqua-500 text-white text-xs font-medium rounded-lg hover:bg-brand-aqua-700 transition-colors"
                          >
                            Confirm {RECORD_TYPE_LABELS[r.record_type]}
                          </button>
                        ))}
                        <Link
                          href="/"
                          className="px-3 py-1.5 bg-sand-100 text-sand-600 text-xs font-medium rounded-lg hover:bg-sand-200 transition-colors border border-sand-200"
                        >
                          Continue Capture
                        </Link>
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Library View — records grouped by type
// ---------------------------------------------------------------------------

function LibraryView({
  records,
  expandedId: _expandedId,
  onToggle: _onToggle,
  onConfirm,
  onFieldSaved,
}: {
  records: MetadataRecord[];
  expandedId: string | null;
  onToggle: (id: string) => void;
  onConfirm: (id: string) => void;
  onFieldSaved: () => void;
}) {
  const byType: Record<string, MetadataRecord[]> = {};
  for (const r of records) {
    (byType[r.record_type] ||= []).push(r);
  }

  const hasRecords = records.length > 0;

  if (!hasRecords) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-sand-400">
        <p className="text-lg">No metadata records found</p>
        <p className="text-sm mt-1">Start a chat to capture experiment metadata</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Shared records section */}
      <div>
        <h3 className="text-xs font-semibold text-sand-500 uppercase tracking-wider mb-3 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-brand-violet-500" />
          Shared Records
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {SHARED_TYPES.flatMap((type) =>
            (byType[type] || []).map((r) => (
              <div key={r.id} id={`row-${r.id}`}>
                <RecordEditor record={r} onSaved={onFieldSaved} />
                {r.status === 'draft' && (
                  <button
                    onClick={() => onConfirm(r.id)}
                    className="mt-1 w-full px-3 py-1.5 bg-brand-aqua-500 text-white text-xs font-medium rounded-lg hover:bg-brand-aqua-700 transition-colors"
                  >
                    Confirm
                  </button>
                )}
              </div>
            ))
          )}
        </div>
        {SHARED_TYPES.every((t) => !(byType[t]?.length)) && (
          <p className="text-xs text-sand-400 italic">No shared records yet</p>
        )}
      </div>

      {/* Asset-specific records section */}
      <div>
        <h3 className="text-xs font-semibold text-sand-500 uppercase tracking-wider mb-3 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-sand-400" />
          Asset-Specific Records
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {ASSET_TYPES.flatMap((type) =>
            (byType[type] || []).map((r) => (
              <div key={r.id} id={`row-${r.id}`}>
                <RecordEditor record={r} onSaved={onFieldSaved} />
                {r.status === 'draft' && (
                  <button
                    onClick={() => onConfirm(r.id)}
                    className="mt-1 w-full px-3 py-1.5 bg-brand-aqua-500 text-white text-xs font-medium rounded-lg hover:bg-brand-aqua-700 transition-colors"
                  >
                    Confirm
                  </button>
                )}
              </div>
            ))
          )}
        </div>
        {ASSET_TYPES.every((t) => !(byType[t]?.length)) && (
          <p className="text-xs text-sand-400 italic">No asset-specific records yet</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dashboard Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const [records, setRecords] = useState<MetadataRecord[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [view, setView] = useState<'session' | 'library'>('session');
  const [filter, setFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    try {
      const [recs, sess] = await Promise.all([fetchRecords(), fetchSessions()]);
      setRecords(recs);
      setSessions(sess);
    } catch {
      // API not available
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  // Auto-expand and scroll to record when navigated via hash (e.g., /dashboard#record-id)
  useEffect(() => {
    if (loading || records.length === 0) return;
    const hash = window.location.hash.slice(1);
    if (!hash) return;

    // The hash is a record ID. Find the record to get its session_id.
    const targetRecord = records.find((r) => r.id === hash);
    if (targetRecord && view === 'session') {
      // In session view, expand the session that contains this record
      setExpandedId(targetRecord.session_id);
    } else if (targetRecord && view === 'library') {
      // In library view, records are always shown — just scroll
      setExpandedId(hash);
    } else {
      // Fallback: try using hash directly (could be a session_id)
      setExpandedId(hash);
    }

    // Scroll to the record element after a brief delay for rendering
    requestAnimationFrame(() => {
      setTimeout(() => {
        const el = document.getElementById(`row-${hash}`) || document.getElementById(`record-${hash}`);
        el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Add a brief highlight effect
        if (el) {
          el.classList.add('ring-2', 'ring-brand-fig/40', 'rounded-lg');
          setTimeout(() => el.classList.remove('ring-2', 'ring-brand-fig/40', 'rounded-lg'), 2000);
        }
      }, 100);
    });
  }, [loading, records, view]); // eslint-disable-line react-hooks/exhaustive-deps


  const handleConfirm = async (recordId: string) => {
    try {
      await confirmRecord(recordId);
      load();
    } catch (err) {
      console.error('Failed to confirm:', err);
    }
  };

  // Apply filters
  const filtered = records.filter((r) => {
    if (filter === 'draft' && r.status !== 'draft') return false;
    if (filter === 'confirmed' && r.status !== 'confirmed') return false;
    if (search) {
      const s = search.toLowerCase();
      return (
        r.name?.toLowerCase().includes(s) ||
        r.record_type.includes(s) ||
        r.session_id.toLowerCase().includes(s) ||
        JSON.stringify(r.data_json).toLowerCase().includes(s)
      );
    }
    return true;
  });

  const counts = {
    all: records.length,
    draft: records.filter((r) => r.status === 'draft').length,
    confirmed: records.filter((r) => r.status === 'confirmed').length,
  };

  return (
    <div className="h-screen flex flex-col bg-white">
      <Header />

      {/* Toolbar */}
      <div className="bg-white border-b border-sand-200 px-6 py-3">
        <div className="flex items-center gap-4">
          {/* View toggle */}
          <div className="flex gap-0.5 bg-sand-100 rounded-lg p-0.5">
            <button
              onClick={() => setView('session')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                view === 'session' ? 'bg-white text-sand-800 shadow-sm' : 'text-sand-500 hover:text-sand-700'
              }`}
            >
              Sessions
            </button>
            <button
              onClick={() => setView('library')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                view === 'library' ? 'bg-white text-sand-800 shadow-sm' : 'text-sand-500 hover:text-sand-700'
              }`}
            >
              Library
            </button>
          </div>

          {/* Status filters */}
          <div className="flex gap-1">
            {(['all', 'draft', 'confirmed'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                  filter === f
                    ? 'bg-sand-800 text-white'
                    : 'bg-sand-100 text-sand-500 hover:bg-sand-200'
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)} ({counts[f]})
              </button>
            ))}
          </div>

          <div className="flex-1" />

          <input
            type="text"
            placeholder="Search records..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-64 px-3 py-1.5 text-sm border border-sand-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-fig/30 focus:border-brand-fig/50"
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center justify-center h-64 text-sand-400">Loading...</div>
        ) : view === 'session' ? (
          <SessionView
            records={filtered}
            sessions={sessions}
            expandedId={expandedId}
            onToggle={(id) => setExpandedId(expandedId === id ? null : id)}
            onConfirm={handleConfirm}
            onFieldSaved={load}
          />
        ) : (
          <LibraryView
            records={filtered}
            expandedId={expandedId}
            onToggle={(id) => setExpandedId(expandedId === id ? null : id)}
            onConfirm={handleConfirm}
            onFieldSaved={load}
          />
        )}
      </div>
    </div>
  );
}
