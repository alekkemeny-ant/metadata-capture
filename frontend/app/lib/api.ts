const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '';

export interface ValidationResults {
  status: 'valid' | 'warnings' | 'errors' | 'pending';
  completeness_score: number;
  record_type?: string;
  errors: { field: string; message: string; severity: string }[];
  warnings: { field: string; message: string; severity: string }[];
  missing_required: string[];
  valid_fields: string[];
}

export interface MetadataRecord {
  id: string;
  session_id: string;
  record_type: string;
  category: 'shared' | 'asset';
  name: string | null;
  data_json: Record<string, unknown>;
  status: 'draft' | 'validated' | 'confirmed' | 'error';
  validation_json: ValidationResults | null;
  links?: MetadataRecord[];
  created_at: string;
  updated_at: string;
}

export interface Session {
  session_id: string;
  created_at: string;
  last_active: string;
  message_count: number;
  first_message: string | null;
}

export interface MessageAttachment {
  file_id: string;
  filename: string;
  content_type: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  attachments?: MessageAttachment[];
}

export interface UploadedFile {
  id: string;
  filename: string;
  content_type: string;
  size: number;
}

export interface SpreadsheetData {
  columns: string[];
  rows: (string | number | null)[][];
  total_rows: number;
  sheet_name: string | null;
  filename?: string;
}

export interface Artifact {
  id: string;
  session_id: string;
  artifact_type: 'table' | 'json' | 'markdown' | 'code';
  title: string;
  content: unknown;
  language?: string | null;
  created_at: string;
}

export interface ModelInfo {
  models: string[];
  default: string;
}

export async function fetchModels(): Promise<ModelInfo> {
  try {
    const res = await fetch(`${API_BASE}/models`);
    if (!res.ok) throw new Error('Failed to fetch models');
    return res.json();
  } catch {
    return { models: ['claude-opus-4-6', 'claude-sonnet-4-5-20250929', 'claude-haiku-4-5-20251001'], default: 'claude-opus-4-6' };
  }
}

export async function uploadFile(file: File, sessionId?: string): Promise<UploadedFile> {
  const formData = new FormData();
  formData.append('file', file);
  const url = sessionId
    ? `${API_BASE}/upload?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/upload`;
  const res = await fetch(url, { method: 'POST', body: formData });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Upload failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export function getUploadUrl(fileId: string): string {
  return `${API_BASE}/uploads/${fileId}`;
}

export async function fetchUploadTable(fileId: string): Promise<SpreadsheetData> {
  const res = await fetch(`${API_BASE}/uploads/${fileId}/table`);
  if (!res.ok) throw new Error(`Failed to parse spreadsheet: ${res.status}`);
  return res.json();
}

export interface UploadExtraction {
  status: 'pending' | 'done' | 'error';
  text_preview: string;
  meta: Record<string, unknown>;
  error: string | null;
  image_count: number;
}

export async function getUploadExtraction(fileId: string): Promise<UploadExtraction> {
  const res = await fetch(`${API_BASE}/uploads/${fileId}/extraction`);
  if (!res.ok) throw new Error(`extraction fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchArtifact(artifactId: string): Promise<Artifact> {
  const res = await fetch(`${API_BASE}/artifacts/${artifactId}`);
  if (!res.ok) throw new Error(`Failed to fetch artifact: ${res.status}`);
  return res.json();
}

export async function fetchSessionArtifacts(sessionId: string): Promise<Artifact[]> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/artifacts`);
  if (!res.ok) throw new Error(`Failed to fetch artifacts: ${res.status}`);
  return res.json();
}

type ChatCallbacks = {
  onChunk: (event: Record<string, unknown>) => void;
  onDone: () => void;
  onError: (err: Error) => void;
};

function handleEvent(parsed: Record<string, unknown>, cb: ChatCallbacks): 'done' | 'error' | 'continue' {
  if (parsed.ping) return 'continue';
  if (parsed.session_id) {
    sessionStorage.setItem('chat_session_id', parsed.session_id as string);
  }
  if (parsed.done) { cb.onDone(); return 'done'; }
  if (parsed.error) { cb.onError(new Error(parsed.error as string)); return 'error'; }
  if (parsed.content || parsed.thinking_start || parsed.thinking ||
      parsed.tool_use_start || parsed.tool_use_input || parsed.block_stop ||
      parsed.tool_result || parsed.artifact) {
    cb.onChunk(parsed);
  }
  return 'continue';
}

function sendViaWebSocket(
  payload: Record<string, unknown>,
  cb: ChatCallbacks,
  signal?: AbortSignal,
) {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/chat`);
  let msgCount = 0;
  let gotDone = false;

  const cleanup = () => {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  };

  if (signal) {
    signal.addEventListener('abort', () => { cleanup(); cb.onDone(); gotDone = true; });
  }

  ws.onopen = () => { ws.send(JSON.stringify(payload)); };

  ws.onmessage = (event) => {
    msgCount++;
    try {
      const result = handleEvent(JSON.parse(event.data), cb);
      if (result === 'done') { gotDone = true; cleanup(); }
      else if (result === 'error') { gotDone = true; cleanup(); }
    } catch {
      cb.onChunk({ content: event.data });
    }
  };

  ws.onerror = () => {
    if (!gotDone) { gotDone = true; cb.onError(new Error('WebSocket connection failed')); }
  };
  ws.onclose = () => {
    if (!gotDone) { gotDone = true; cb.onDone(); }
  };
}

async function sendViaSSE(
  payload: Record<string, unknown>,
  cb: ChatCallbacks,
  signal?: AbortSignal,
) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok) throw new Error(`Chat request failed: ${res.status}`);

  const reader = res.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') { cb.onDone(); return; }
        try {
          const result = handleEvent(JSON.parse(data), cb);
          if (result !== 'continue') return;
        } catch {
          cb.onChunk({ content: data });
        }
      }
    }
  }
  cb.onDone();
}

export async function sendChatMessage(
  message: string,
  sessionId: string | null,
  onChunk: (event: Record<string, unknown>) => void,
  onDone: () => void,
  onError: (err: Error) => void,
  signal?: AbortSignal,
  model?: string,
  attachments?: MessageAttachment[],
) {
  const payload: Record<string, unknown> = { message };
  if (sessionId) payload.session_id = sessionId;
  if (model) payload.model = model;
  if (attachments?.length) payload.attachments = attachments;

  const cb: ChatCallbacks = { onChunk, onDone, onError };
  const isReplit = window.location.hostname.includes('.replit.dev')
    || window.location.hostname.includes('.repl.co')
    || window.location.hostname.includes('.replit.app');

  try {
    if (isReplit) {
      sendViaWebSocket(payload, cb, signal);
    } else {
      await sendViaSSE(payload, cb, signal);
    }
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      onDone();
      return;
    }
    onError(err as Error);
  }
}

export async function fetchMessages(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`Failed to fetch messages: ${res.status}`);
  const data: { role: string; content: string; attachments_json?: MessageAttachment[] | null }[] = await res.json();
  return data.map((m) => ({
    role: m.role as 'user' | 'assistant',
    content: m.content,
    ...(m.attachments_json ? { attachments: m.attachments_json } : {}),
  }));
}

// ---------------------------------------------------------------------------
// Records API
// ---------------------------------------------------------------------------

export async function fetchRecords(params?: {
  type?: string;
  category?: string;
  session_id?: string;
  status?: string;
}): Promise<MetadataRecord[]> {
  const searchParams = new URLSearchParams();
  if (params?.type) searchParams.set('type', params.type);
  if (params?.category) searchParams.set('category', params.category);
  if (params?.session_id) searchParams.set('session_id', params.session_id);
  if (params?.status) searchParams.set('status', params.status);

  const qs = searchParams.toString();
  const res = await fetch(`${API_BASE}/records${qs ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(`Failed to fetch records: ${res.status}`);
  return res.json();
}

export async function fetchRecord(recordId: string): Promise<MetadataRecord> {
  const res = await fetch(`${API_BASE}/records/${recordId}`);
  if (!res.ok) throw new Error(`Failed to fetch record: ${res.status}`);
  return res.json();
}

export async function updateRecordData(recordId: string, data: Record<string, unknown>): Promise<MetadataRecord> {
  const res = await fetch(`${API_BASE}/records/${recordId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data }),
  });
  if (!res.ok) throw new Error(`Failed to update record: ${res.status}`);
  return res.json();
}

export interface SchemaEnums {
  species: string[];
  sex: string[];
}

export async function fetchSchemaEnums(): Promise<SchemaEnums> {
  const res = await fetch(`${API_BASE}/schema/enums`);
  if (!res.ok) throw new Error(`Failed to fetch enums: ${res.status}`);
  return res.json();
}

/**
 * Single-field update with server-side shape mapping. Unlike updateRecordData,
 * this knows that `species` is a nested dict and reconstructs it correctly,
 * and it rejects unknown fields with 400 instead of warn-and-storing them.
 */
export async function patchRecordField(
  recordId: string,
  field: string,
  value: string,
): Promise<MetadataRecord> {
  const res = await fetch(`${API_BASE}/records/${recordId}/field`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field, value }),
  });
  if (!res.ok) {
    // FastAPI HTTPException detail is in {detail: "..."}; surface it so the
    // cell can show "Unknown field 'genotype_string'" instead of a code.
    let detail = `Update failed: ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch { /* non-JSON body */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function fetchRecordsByIds(ids: string[]): Promise<MetadataRecord[]> {
  if (ids.length === 0) return [];
  const res = await fetch(`${API_BASE}/records?ids=${encodeURIComponent(ids.join(','))}`);
  if (!res.ok) throw new Error(`Failed to fetch records: ${res.status}`);
  return res.json();
}

export async function confirmRecord(recordId: string): Promise<MetadataRecord> {
  const res = await fetch(`${API_BASE}/records/${recordId}/confirm`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to confirm record: ${res.status}`);
  return res.json();
}

export async function deleteRecord(recordId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/records/${recordId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Failed to delete record: ${res.status}`);
}

export async function linkRecords(sourceId: string, targetId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/records/link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
  });
  if (!res.ok) throw new Error(`Failed to link records: ${res.status}`);
}

export async function fetchSessionRecords(sessionId: string): Promise<MetadataRecord[]> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/records`);
  if (!res.ok) throw new Error(`Failed to fetch session records: ${res.status}`);
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Failed to delete session: ${res.status}`);
}

export async function fetchSessions(): Promise<Session[]> {
  const res = await fetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error(`Failed to fetch sessions: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Legacy compat — MetadataEntry shape used by old dashboard code
// ---------------------------------------------------------------------------

export interface MetadataEntry {
  id: string;
  subject_id: string;
  session_id: string;
  status: 'draft' | 'confirmed';
  fields: Record<string, unknown>;
  validation: ValidationResults | null;
  created_at: string;
  updated_at: string;
}
