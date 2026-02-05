const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001';

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

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
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

export async function sendChatMessage(
  message: string,
  sessionId: string | null,
  onChunk: (event: Record<string, unknown>) => void,
  onDone: () => void,
  onError: (err: Error) => void,
  signal?: AbortSignal,
  model?: string,
) {
  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(model ? { model } : {}),
      }),
      signal,
    });

    if (!res.ok) {
      throw new Error(`Chat request failed: ${res.status}`);
    }

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
          if (data === '[DONE]') {
            onDone();
            return;
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed.session_id) {
              sessionStorage.setItem('chat_session_id', parsed.session_id);
            }
            // Forward any content-bearing event to the chunk handler
            if (parsed.content || parsed.thinking_start || parsed.thinking ||
                parsed.tool_use_start || parsed.tool_use_input || parsed.block_stop) {
              onChunk(parsed);
            }
          } catch {
            // Plain text chunk — wrap as a content event
            onChunk({ content: data });
          }
        }
      }
    }
    onDone();
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      onDone(); // Treat abort as a graceful stop, not an error
      return;
    }
    onError(err as Error);
  }
}

export async function fetchMessages(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`Failed to fetch messages: ${res.status}`);
  const data: { role: string; content: string }[] = await res.json();
  return data.map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content }));
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
