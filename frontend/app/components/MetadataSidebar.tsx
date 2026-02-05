'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { fetchRecords, confirmRecord, MetadataRecord } from '../lib/api';

const RECORD_TYPE_LABELS: Record<string, string> = {
  subject: 'Subjects',
  procedures: 'Procedures',
  instrument: 'Instruments',
  rig: 'Rigs',
  data_description: 'Data Descriptions',
  acquisition: 'Acquisitions',
  session: 'Sessions',
  processing: 'Processing',
  quality_control: 'Quality Control',
};

const SHARED_TYPES = ['subject', 'procedures', 'instrument', 'rig'];
const ASSET_TYPES = ['data_description', 'acquisition', 'session', 'processing', 'quality_control'];

function StatusBadge({ status }: { status: string }) {
  const colors =
    status === 'confirmed'
      ? 'bg-brand-aqua-500/10 text-brand-aqua-700 border-brand-aqua-500/20'
      : 'bg-brand-orange-100 text-brand-orange-600 border-brand-orange-500/20';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${colors}`}
    >
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

/** Flatten a nested metadata object into readable key-value pairs. */
function flattenFields(obj: unknown, prefix = ''): { label: string; value: string }[] {
  if (obj == null) return [];
  if (typeof obj !== 'object') return [{ label: prefix || 'value', value: String(obj) }];
  const entries: { label: string; value: string }[] = [];
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    const label = prefix ? `${prefix} > ${k.replace(/_/g, ' ')}` : k.replace(/_/g, ' ');
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      const nested = v as Record<string, unknown>;
      if (Object.keys(nested).length === 1 && 'name' in nested) {
        entries.push({ label, value: String(nested.name) });
      } else {
        entries.push(...flattenFields(v, label));
      }
    } else if (Array.isArray(v)) {
      entries.push({ label, value: v.map((item) => (typeof item === 'object' ? (item as Record<string, unknown>).name ?? JSON.stringify(item) : String(item))).join(', ') });
    } else {
      entries.push({ label, value: String(v) });
    }
  }
  return entries;
}

function RecordCard({
  record,
  onConfirm,
}: {
  record: MetadataRecord;
  onConfirm: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const router = useRouter();

  const fields = flattenFields(record.data_json);
  const visibleFields = expanded ? fields : fields.slice(0, 3);

  return (
    <div
      className="metadata-card bg-white border border-sand-200 rounded-xl p-3 space-y-2 cursor-pointer hover:border-sand-300"
      onClick={() => router.push(`/dashboard#${record.id}`)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-sand-800 truncate">
            {record.name || record.record_type}
          </p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-[10px] text-sand-400 uppercase font-medium">{record.record_type.replace(/_/g, ' ')}</span>
            <CategoryBadge category={record.category} />
          </div>
        </div>
        <StatusBadge status={record.status} />
      </div>

      {fields.length > 0 && (
        <div className="space-y-0.5">
          {visibleFields.map((row, i) => (
            <div key={i} className="flex text-xs gap-2">
              <span className="text-sand-400 shrink-0 w-28 truncate capitalize">{row.label}:</span>
              <span className="text-sand-700 truncate">{row.value}</span>
            </div>
          ))}
          {fields.length > 3 && (
            <button
              onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
              className="text-xs text-brand-fig hover:text-brand-magenta-800"
            >
              {expanded ? 'Show less' : `+${fields.length - 3} more`}
            </button>
          )}
        </div>
      )}

      {record.status === 'draft' && (
        <button
          onClick={(e) => { e.stopPropagation(); onConfirm(record.id); }}
          className="w-full rounded-lg bg-sand-100 text-sand-600 text-xs font-medium py-1.5
                     hover:bg-sand-200 transition-colors border border-sand-200"
        >
          Confirm
        </button>
      )}
    </div>
  );
}

function RecordTypeSection({
  type,
  records,
  onConfirm,
}: {
  type: string;
  records: MetadataRecord[];
  onConfirm: (id: string) => void;
}) {
  if (records.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold text-sand-500 uppercase tracking-wider mb-2">
        {RECORD_TYPE_LABELS[type] || type} ({records.length})
      </h3>
      <div className="space-y-2">
        {records.map((r) => (
          <RecordCard key={r.id} record={r} onConfirm={onConfirm} />
        ))}
      </div>
    </div>
  );
}

export default function MetadataSidebar() {
  const [records, setRecords] = useState<MetadataRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'all' | 'shared' | 'asset'>('all');

  const load = useCallback(async () => {
    try {
      const data = await fetchRecords(filter !== 'all' ? { category: filter } : undefined);
      setRecords(data);
    } catch {
      // API not available yet
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [load]);

  const handleConfirm = async (id: string) => {
    try {
      await confirmRecord(id);
      load();
    } catch (err) {
      console.error('Failed to confirm:', err);
    }
  };

  // Group records by type
  const byType: Record<string, MetadataRecord[]> = {};
  for (const r of records) {
    (byType[r.record_type] ||= []).push(r);
  }

  const typeOrder = filter === 'asset' ? ASSET_TYPES : filter === 'shared' ? SHARED_TYPES : [...SHARED_TYPES, ...ASSET_TYPES];

  return (
    <div className="flex flex-col h-full">
      <div className="px-5 py-4 border-b border-sand-200 bg-white">
        <h2 className="text-sm font-semibold text-sand-800">Metadata Records</h2>
        <div className="flex gap-1 mt-2">
          {(['all', 'shared', 'asset'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                filter === f
                  ? 'bg-sand-800 text-white'
                  : 'bg-sand-100 text-sand-500 hover:bg-sand-200'
              }`}
            >
              {f === 'all' ? 'All' : f === 'shared' ? 'Shared' : 'Assets'}
              {f !== 'all' && ` (${records.filter((r) => r.category === f).length})`}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto chat-scroll p-4 space-y-4 bg-sand-50">
        {loading ? (
          <div className="flex items-center justify-center h-32">
            <div className="animate-pulse text-sand-400 text-sm">Loading records...</div>
          </div>
        ) : records.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-sand-400 text-sm text-center">
            <div>
              <p>No metadata records yet.</p>
              <p className="text-xs mt-1">Chat with the agent to capture experiment metadata.</p>
            </div>
          </div>
        ) : (
          typeOrder.map((type) => (
            <RecordTypeSection
              key={type}
              type={type}
              records={byType[type] || []}
              onConfirm={handleConfirm}
            />
          ))
        )}
      </div>

      <div className="px-5 py-3 border-t border-sand-200 bg-white text-xs text-sand-400">
        {records.length} records | Auto-refreshing every 5s
      </div>
    </div>
  );
}
