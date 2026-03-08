'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { sendChatMessage, fetchMessages, fetchModels, uploadFile, getUploadUrl, getUploadExtraction, MessageAttachment } from '../lib/api';
import ArtifactModal, { ArtifactSource } from './ArtifactModal';

// ---------------------------------------------------------------------------
// File attachment types
// ---------------------------------------------------------------------------

interface FileAttachment {
  file: File;
  preview?: string; // object URL for image preview
}

// Spreadsheet MIME types (match backend SPREADSHEET_CONTENT_TYPES)
const SPREADSHEET_TYPES = new Set([
  'text/csv',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]);

function isSpreadsheet(typeOrName: string): boolean {
  return SPREADSHEET_TYPES.has(typeOrName) || typeOrName.toLowerCase().endsWith('.csv') || typeOrName.toLowerCase().endsWith('.xlsx');
}

// Recursively collect File objects from a FileSystemEntry tree. readEntries()
// returns at most 100 entries per call (Chrome quirk) so we loop until empty.
// Used by handleDrop to support folder drag-and-drop — dataTransfer.files
// only contains the folder itself, not its contents.
async function collectEntryFiles(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    out.push(file);
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    while (true) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
        reader.readEntries(resolve, reject),
      );
      if (batch.length === 0) break;
      await Promise.all(batch.map((child) => collectEntryFiles(child, out)));
    }
  }
}

/**
 * Derive a session title from attached filenames when the user sends with
 * no text. The first message `content` ends up in the sidebar and dashboard,
 * so "(attached files)" is useless — "foo.csv extraction" is a real handle.
 */
function attachmentsLabel(filenames: string[]): string {
  if (filenames.length === 0) return '(attached files)';
  if (filenames.length === 1) return `${filenames[0]} extraction`;
  const shown = filenames.slice(0, 2).join(', ');
  const rest = filenames.length - 2;
  return rest > 0 ? `${shown} +${rest} more — extraction` : `${shown} — extraction`;
}

// Upload a batch of files with at most `limit` in flight at once. Results
// preserve input order so the caller can zip them back onto UI state.
// Inline p-limit — not worth a dependency for ~15 lines.
async function throttledUpload(
  files: File[],
  sessionId: string | undefined,
  limit: number,
): Promise<Awaited<ReturnType<typeof uploadFile>>[]> {
  const results = new Array(files.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, files.length) }, async () => {
    while (cursor < files.length) {
      const idx = cursor++;
      results[idx] = await uploadFile(files[idx], sessionId);
    }
  });
  await Promise.all(workers);
  return results;
}

// Compact icon for non-image attachment chips (preview strip + sent messages)
function fileTypeIcon(contentType: string, filename: string): string {
  const ext = filename.toLowerCase().split('.').pop() || '';
  if (contentType.startsWith('image/')) return '🖼️';
  if (contentType === 'application/pdf') return '📕';
  if (contentType.startsWith('audio/') || ['mp3', 'wav', 'm4a', 'ogg'].includes(ext)) return '🎵';
  if (contentType.startsWith('video/') || ['mp4', 'mov', 'webm', 'mkv'].includes(ext)) return '🎬';
  if (['csv', 'xlsx', 'xls'].includes(ext)) return '📊';
  if (ext === 'docx') return '📄';
  return '📝';
}

// SpeechRecognition type shim for browsers that support it
interface SpeechRecognitionEvent {
  results: { length: number; [index: number]: { isFinal: boolean; 0: { transcript: string } } };
}

interface SpeechRecognitionInstance {
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
}

interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionInstance;
}

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

// ---------------------------------------------------------------------------
// Block types for structured assistant messages
// ---------------------------------------------------------------------------

interface ValidationIssue {
  field: string;
  message: string;
  severity: string;
}

interface ToolValidation {
  status: string;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

interface ArtifactRef {
  id: string;
  type: string;
  title: string;
}

/** Attachment cards rendered above the user bubble (claude.ai layout).
 * Images show as thumbnails; everything else as square file cards.
 * Right-aligned and wrapped; scrolls at >2 rows so a 20-file dump
 * doesn't eat the whole viewport. */
function UserAttachmentGroup({
  attachments,
  extractionStatus,
  onOpenSpreadsheet,
}: {
  attachments: MessageAttachment[];
  extractionStatus: Record<string, 'pending' | 'done' | 'error'>;
  onOpenSpreadsheet: (fileId: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 justify-end max-h-64 overflow-y-auto pr-1">
      {attachments.map((att, ai) => {
        if (att.content_type.startsWith('image/')) {
          return (
            <a key={ai} href={getUploadUrl(att.file_id)} target="_blank" rel="noopener noreferrer"
               className="block shrink-0">
              <img
                src={getUploadUrl(att.file_id)}
                alt={att.filename}
                className="w-28 h-28 rounded-xl object-cover border border-sand-200 shadow-sm"
              />
            </a>
          );
        }
        const extStatus = extractionStatus[att.file_id];
        const spreadsheet = isSpreadsheet(att.content_type) || isSpreadsheet(att.filename);
        // Square card with large icon + filename overlay, like PDF tiles in claude.ai
        const card = (
          <div className="w-28 h-28 rounded-xl border border-sand-200 bg-white shadow-sm
                          flex flex-col items-center justify-center relative overflow-hidden shrink-0">
            <span className="text-3xl leading-none">{fileTypeIcon(att.content_type, att.filename)}</span>
            <span className="absolute bottom-0 inset-x-0 bg-white/95 border-t border-sand-100
                             px-1.5 py-1 text-[10px] text-sand-600 truncate text-center"
                  title={att.filename}>
              {att.filename}
            </span>
            {extStatus === 'pending' && (
              <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-brand-fig animate-pulse"
                    title="processing…" />
            )}
          </div>
        );
        return spreadsheet ? (
          <button key={ai} onClick={() => onOpenSpreadsheet(att.file_id)}
                  className="shrink-0 hover:scale-[1.03] transition-transform" title="View spreadsheet">
            {card}
          </button>
        ) : (
          <div key={ai}>{card}</div>
        );
      })}
    </div>
  );
}

interface MessageBlock {
  type: 'text' | 'thinking' | 'tool_use';
  content: string;
  name?: string;       // tool name, only for tool_use
  toolUseId?: string;  // tool use ID, for matching results
  validation?: ToolValidation; // validation result from capture_metadata
  artifact?: ArtifactRef;      // artifact rendered by this tool_use
}

interface StructuredMessage {
  role: 'user' | 'assistant';
  content: string;       // plain text (persistence & history fallback)
  blocks?: MessageBlock[]; // structured blocks built during streaming
  attachments?: MessageAttachment[]; // file attachments (images, PDFs)
  /** Set when the user sent only files with no typed text — `content` is
   * then a derived label for the session title, not user-authored text,
   * and shouldn't be rendered in the bubble. */
  attachmentOnly?: boolean;
}

// ---------------------------------------------------------------------------
// Collapsible block renderers
// ---------------------------------------------------------------------------

function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!content) return null;
  const wordCount = content.trim().split(/\s+/).length;
  return (
    <div className="my-1.5 border border-sand-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-1.5 bg-sand-50 hover:bg-sand-100 transition-colors text-left"
      >
        <svg className={`w-3.5 h-3.5 text-sand-400 transition-transform ${expanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M9 18l6-6-6-6" /></svg>
        <svg className="w-3.5 h-3.5 text-brand-fig" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M12 2a7 7 0 017 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 01-1 1h-6a1 1 0 01-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 017-7zM9 21h6M10 21v-1h4v1" /></svg>
        <span className="text-xs font-medium text-sand-600">Thinking</span>
        {!expanded && <span className="text-xs text-sand-400 ml-auto">{wordCount} words</span>}
      </button>
      {expanded && (
        <div className="px-3 py-2 text-xs text-sand-600 whitespace-pre-wrap border-t border-sand-100 max-h-64 overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  );
}

// Friendly status labels for tool names
const TOOL_STATUS_LABELS: Record<string, { active: string; done: string }> = {
  WebSearch: { active: 'Searching the web...', done: 'Web search complete' },
  WebFetch: { active: 'Fetching page...', done: 'Page fetched' },
  Bash: { active: 'Running command...', done: 'Command finished' },
  Read: { active: 'Reading file...', done: 'File read' },
  Write: { active: 'Writing file...', done: 'File written' },
  Grep: { active: 'Searching files...', done: 'Search complete' },
  Glob: { active: 'Finding files...', done: 'Files found' },
  capture_metadata: { active: 'Extracting metadata...', done: 'Metadata captured' },
  validate_metadata: { active: 'Validating metadata...', done: 'Validation complete' },
  registry_lookup: { active: 'Checking external registry...', done: 'Registry lookup complete' },
  find_records: { active: 'Searching records...', done: 'Records found' },
  link_records: { active: 'Linking records...', done: 'Records linked' },
  render_artifact: { active: 'Creating artifact...', done: 'Artifact created' },
};

function getToolLabel(name: string, active: boolean): string {
  // Strip MCP prefix (e.g., mcp__capture__render_artifact -> render_artifact)
  const bare = name.replace(/^mcp__[^_]+__/, '');
  const labels = TOOL_STATUS_LABELS[bare] || TOOL_STATUS_LABELS[name];
  if (labels) return active ? labels.active : labels.done;
  // Fallback: humanize the tool name
  const humanized = bare.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ');
  return active ? `Running ${humanized}...` : `${humanized} done`;
}

function ElapsedTimer() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);
  if (elapsed < 2) return null; // don't show for quick operations
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return (
    <span className="text-xs text-sand-400 tabular-nums ml-auto">
      {mins > 0 ? `${mins}m ${secs}s` : `${secs}s`}
    </span>
  );
}

function ValidationBadges({ validation }: { validation: ToolValidation }) {
  const { errors, warnings } = validation;
  if (errors.length === 0 && warnings.length === 0) return null;
  return (
    <div className="px-3 py-2 border-t border-sand-100 space-y-1">
      {errors.map((e, i) => (
        <div key={`e-${i}`} className="flex items-start gap-1.5 text-xs">
          <svg className="w-3.5 h-3.5 text-red-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>
          <span className="text-red-700"><span className="font-medium">{e.field}:</span> {e.message}</span>
        </div>
      ))}
      {warnings.map((w, i) => (
        <div key={`w-${i}`} className="flex items-start gap-1.5 text-xs">
          <svg className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>
          <span className="text-amber-700"><span className="font-medium">{w.field}:</span> {w.message}</span>
        </div>
      ))}
    </div>
  );
}

function ArtifactChip({ artifact, onOpen }: { artifact: ArtifactRef; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="flex items-center gap-2 px-3 py-2 mt-1.5 rounded-lg border border-brand-fig/20 bg-brand-magenta-100/30 hover:bg-brand-magenta-100/60 transition-colors text-left w-full"
    >
      <svg className="w-4 h-4 text-brand-fig shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25A2.25 2.25 0 015.25 3h13.5A2.25 2.25 0 0121 5.25z" />
      </svg>
      <span className="flex-1 text-xs font-medium text-sand-700 truncate">{artifact.title}</span>
      <span className="px-1.5 py-0.5 rounded text-[9px] font-medium uppercase tracking-wide bg-brand-fig/10 text-brand-fig">
        {artifact.type}
      </span>
    </button>
  );
}

function ToolUseBlock({ name, content, isStreaming, validation, artifact, onOpenArtifact }: {
  name: string;
  content: string;
  isStreaming?: boolean;
  validation?: ToolValidation;
  artifact?: ArtifactRef;
  onOpenArtifact?: (ref: ArtifactRef) => void;
}) {
  const hasIssues = validation && (validation.errors.length > 0 || validation.warnings.length > 0);
  const [manualExpand, setManualExpand] = useState<boolean | null>(null);
  // Auto-expand while streaming, or if there are validation issues; manual toggle overrides
  const expanded = manualExpand !== null ? manualExpand : (!!isStreaming || !!hasIssues);
  let prettyInput = content;
  try { prettyInput = JSON.stringify(JSON.parse(content), null, 2); } catch { /* show raw */ }

  // Choose icon based on validation status
  const doneIcon = hasIssues && validation?.errors.length ? (
    <svg className="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>
  ) : hasIssues ? (
    <svg className="w-3.5 h-3.5 text-amber-500" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>
  ) : (
    <svg className="w-3.5 h-3.5 text-brand-aqua-500" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7" /></svg>
  );

  // Choose label based on validation status
  const doneLabel = hasIssues && validation?.errors.length
    ? `Validation errors (${validation.errors.length})`
    : hasIssues && validation?.warnings.length
    ? `Metadata captured with warnings (${validation!.warnings.length})`
    : getToolLabel(name, false);

  return (
    <div className={`my-1.5 rounded-lg overflow-hidden border transition-colors ${
      isStreaming ? 'border-brand-fig/30 bg-brand-magenta-100/40'
        : hasIssues && validation?.errors.length ? 'border-red-200 bg-red-50/30'
        : hasIssues ? 'border-amber-200 bg-amber-50/30'
        : 'border-sand-200'
    }`}>
      <button
        onClick={() => setManualExpand(expanded ? false : true)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-sand-50 hover:bg-sand-100 transition-colors text-left"
      >
        {isStreaming ? (
          <span className="relative flex h-3.5 w-3.5 items-center justify-center">
            <span className="absolute inline-flex h-full w-full rounded-full bg-brand-fig/30 animate-ping" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-fig" />
          </span>
        ) : doneIcon}
        <span className={`text-xs font-medium ${isStreaming ? 'text-sand-700' : 'text-sand-500'}`}>
          {isStreaming ? getToolLabel(name, true) : doneLabel}
        </span>
        {isStreaming && <ElapsedTimer />}
        {!isStreaming && (
          <svg className={`w-3.5 h-3.5 text-sand-400 ml-auto transition-transform ${expanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M9 18l6-6-6-6" /></svg>
        )}
      </button>
      {isStreaming && (
        <div className="h-0.5 bg-sand-100 overflow-hidden">
          <div className="h-full w-1/3 bg-brand-fig/40 rounded-full animate-shimmer" />
        </div>
      )}
      {expanded && hasIssues && validation && <ValidationBadges validation={validation} />}
      {expanded && prettyInput && (
        <div className="px-3 py-2 text-xs font-mono text-sand-600 border-t border-sand-100 max-h-64 overflow-y-auto whitespace-pre-wrap bg-sand-50">
          {prettyInput}
        </div>
      )}
      {artifact && onOpenArtifact && (
        <div className="px-2 pb-2">
          <ArtifactChip artifact={artifact} onOpen={() => onOpenArtifact(artifact)} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Persistence helpers — store the full messages array (content + blocks) in
// localStorage so partial responses and structured blocks survive page
// refreshes, navigation, and mid-stream aborts.
// ---------------------------------------------------------------------------

function saveMessagesToStorage(sessionId: string, messages: StructuredMessage[]) {
  try {
    localStorage.setItem(`chat-data-${sessionId}`, JSON.stringify(messages));
  } catch { /* quota exceeded — best-effort */ }
}

// ---------------------------------------------------------------------------
// Module-level stream registry — claude.ai keyed-store pattern
// ---------------------------------------------------------------------------
//
// Each in-flight stream gets an entry here, keyed by its session ID (once
// known — new chats start with a temporary key then rebind). Switching
// sessions does NOT abort the stream; it keeps writing to its registry
// entry + localStorage. When the user switches back to that session, the
// ChatPanel instance subscribes to the entry and live tokens resume.
//
// This lives at module scope so it survives ChatPanel re-renders and even
// remounts (e.g. switching tabs). The only thing that kills a stream is
// the explicit Stop button or component tree unmount (page nav).

interface StreamEntry {
  sid: string;                         // session_id once assigned (or temp key for brand-new chats)
  messages: StructuredMessage[];       // accumulated stream output, canonical
  done: boolean;
  subscribers: Set<(msgs: StructuredMessage[], done: boolean) => void>;
  abort: () => void;
}

const streamRegistry = new Map<string, StreamEntry>();

/** Get the live stream entry for a session, if any. A brand-new chat's
 * stream is registered under a temp key until the first SSE chunk
 * delivers its real session_id — callers with sessionId=null can't
 * find it, which is correct (the empty chat view is intentional). */
function getStreamForSession(sessionId: string | null): StreamEntry | undefined {
  if (!sessionId) return undefined;
  return streamRegistry.get(sessionId);
}

/** Best-effort check for messages that were sent files-only. The backend
 * doesn't persist attachmentOnly, so when localStorage is absent we infer
 * it: user message with attachments whose content matches the derived label
 * (or the legacy '(attached files)' fallback). */
function inferAttachmentOnly(msg: StructuredMessage): boolean {
  if (msg.role !== 'user' || !msg.attachments?.length) return false;
  if (msg.content === '(attached files)') return true;
  return msg.content === attachmentsLabel(msg.attachments.map(a => a.filename));
}

function restoreMessages(sessionId: string, backendMessages: StructuredMessage[]): StructuredMessage[] {
  // Infer attachmentOnly on backend messages first (for legacy rows + when
  // localStorage is empty on a fresh browser).
  const withInferred = backendMessages.map(m =>
    m.role === 'user' && m.attachments?.length && inferAttachmentOnly(m)
      ? { ...m, attachmentOnly: true }
      : m
  );
  try {
    const stored = localStorage.getItem(`chat-data-${sessionId}`);
    if (!stored) return withInferred;
    const local: StructuredMessage[] = JSON.parse(stored);

    // Backend is authoritative for message content; localStorage provides
    // blocks, attachmentOnly, and any trailing partial messages the backend
    // never received (e.g. an assistant response interrupted by abort).
    const merged: StructuredMessage[] = withInferred.map((msg, i) => ({
      ...msg,
      blocks: local[i]?.blocks,
      attachmentOnly: local[i]?.attachmentOnly ?? msg.attachmentOnly,
    }));

    // Append messages that exist locally but not in the backend
    for (let i = withInferred.length; i < local.length; i++) {
      merged.push(local[i]);
    }

    return merged;
  } catch {
    return withInferred;
  }
}

// ---------------------------------------------------------------------------

interface ChatPanelProps {
  sessionId: string | null;
  /** Increments when the user clicks "New Chat" — lets us abort a stream
   * even when sessionId stays null. See useEffect below. */
  newChatNonce?: number;
  onSessionChange: (sessionId: string) => void;
  agentOnline: boolean;
}

export default function ChatPanel({ sessionId, newChatNonce, onSessionChange, agentOnline }: ChatPanelProps) {
  const [messages, setMessages] = useState<StructuredMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [pendingFiles, setPendingFiles] = useState<FileAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [openArtifact, setOpenArtifact] = useState<ArtifactSource | null>(null);
  // Background extraction status per uploaded file (non-image/pdf only)
  const [extractionStatus, setExtractionStatus] = useState<Record<string, 'pending' | 'done' | 'error'>>({});
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  // Subscription to the live stream (if any) for the current session.
  // Set when we detect a registry entry on session switch, cleared on
  // next switch or when the stream completes.
  const streamSubRef = useRef<(() => void) | null>(null);
  const mountedRef = useRef(true);
  // Track whether the user has scrolled away from the bottom. While pinned,
  // every streaming token auto-scrolls; once the user scrolls up, we stop
  // fighting them until they scroll back down or send a new message.
  const isPinnedToBottomRef = useRef(true);
  // Mirror of `messages` state for stream callbacks. When the user navigates
  // away mid-stream, ChatPanel unmounts and React silently discards any
  // further setState calls — but the SSE fetch keeps running. Writing to
  // this ref lets us keep accumulating tokens and persist the full response
  // to localStorage so it's there when the user returns.
  const messagesRef = useRef<StructuredMessage[]>([]);

  // Track mount state so async polls don't update state after unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // Keep the ref in sync with state for non-stream updates (history load etc.)
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const handleMessagesScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    // 50px threshold — close enough to bottom counts as pinned, so small
    // sub-pixel layout shifts during rendering don't accidentally unpin.
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    isPinnedToBottomRef.current = atBottom;
  }, []);

  // Only follow the stream if the user hasn't scrolled away. Using a ref
  // (not state) for the pin flag avoids re-renders on every scroll event.
  useEffect(() => {
    if (isPinnedToBottomRef.current) scrollToBottom();
  }, [messages, scrollToBottom]);

  // Fetch available models on mount
  useEffect(() => {
    fetchModels().then((info) => {
      setAvailableModels(info.models);
      setSelectedModel(info.default);
    });
  }, []);

  // Load messages when sessionId changes. Does NOT abort in-flight
  // streams — they keep running in the background, writing to the
  // module-level registry + localStorage. If the session we're
  // switching TO has an active stream, subscribe to it so live tokens
  // resume in this view.
  useEffect(() => {
    // Unsubscribe from the previous session's stream (if any). The
    // stream itself keeps running — we just stop receiving its ticks.
    streamSubRef.current?.();
    streamSubRef.current = null;

    // Check for a live stream already running for this session.
    const live = getStreamForSession(sessionId);
    if (live && !live.done) {
      // Registry has the canonical in-progress messages — use them
      // directly instead of the DB (which is behind by up to 250ms of
      // persist throttle + hasn't got the assistant turn until done).
      setMessages(live.messages);
      setIsStreaming(true);
      // Subscribe: the stream's callback pushes updates to every
      // subscriber after each chunk. This is how we get live tokens
      // after switching back mid-stream.
      const sub = (msgs: StructuredMessage[], done: boolean) => {
        if (!mountedRef.current) return;
        setMessages(msgs);
        if (done) {
          setIsStreaming(false);
          streamSubRef.current = null;
        }
      };
      live.subscribers.add(sub);
      streamSubRef.current = () => live.subscribers.delete(sub);
      return;
    }

    // No live stream — load from DB + localStorage as usual.
    setIsStreaming(false);
    if (sessionId) {
      fetchMessages(sessionId)
        .then((msgs) => setMessages(restoreMessages(sessionId, msgs)))
        .catch(() => setMessages([]));
    } else {
      setMessages([]);
    }
    // newChatNonce forces a re-run when "New Chat" is clicked while
    // already in an unsaved new chat (sessionId stays null).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, newChatNonce]);

  // Unsubscribe on unmount — the stream keeps running, but this
  // component instance is gone.
  useEffect(() => {
    return () => { streamSubRef.current?.(); };
  }, []);

  // Reset textarea height when input is cleared
  useEffect(() => {
    if (!input && inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
  }, [input]);

  // Cleanup file previews on unmount
  useEffect(() => {
    return () => {
      pendingFiles.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // File attachment helpers
  // ---------------------------------------------------------------------------

  // Server validates MIME/extension; client only enforces the size cap and
  // drops OS junk files that come along with directory picks.
  const JUNK_FILES = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini']);
  const addFiles = (files: FileList | File[]) => {
    const newAttachments: FileAttachment[] = [];
    for (const file of Array.from(files)) {
      // Skip OS metadata and hidden dotfiles — webkitdirectory recursively
      // collects everything, including .DS_Store in every subdir.
      if (JUNK_FILES.has(file.name) || file.name.startsWith('.')) continue;
      if (file.size > 100 * 1024 * 1024) continue;  // 100 MB — matches server MAX_UPLOAD_SIZE
      const preview = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
      newAttachments.push({ file, preview });
    }
    if (newAttachments.length) setPendingFiles((prev) => [...prev, ...newAttachments]);
  };

  // Poll /uploads/{id}/extraction until done/error or max attempts.
  // Recursive setTimeout — terminates naturally, no interval to leak.
  const pollExtraction = useCallback((fileId: string, attempt = 0) => {
    if (attempt >= 10 || !mountedRef.current) return;
    getUploadExtraction(fileId)
      .then((result) => {
        if (!mountedRef.current) return;
        setExtractionStatus((prev) => ({ ...prev, [fileId]: result.status }));
        if (result.status === 'pending') {
          setTimeout(() => pollExtraction(fileId, attempt + 1), 1000);
        }
      })
      .catch(() => {
        if (!mountedRef.current) return;
        setExtractionStatus((prev) => ({ ...prev, [fileId]: 'error' }));
      });
  }, []);

  const removeFile = (index: number) => {
    setPendingFiles((prev) => {
      const removed = prev[index];
      if (removed?.preview) URL.revokeObjectURL(removed.preview);
      return prev.filter((_, i) => i !== index);
    });
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    if (!agentOnline) return;
    const items = e.clipboardData?.items;
    if (!items) return;
    const imageFiles: File[] = [];
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/') && item.kind === 'file') {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }
    if (imageFiles.length) {
      e.preventDefault();
      addFiles(imageFiles);
    }
  };

  // ---------------------------------------------------------------------------
  // Drag-and-drop
  // ---------------------------------------------------------------------------

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (!agentOnline) return;
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (!agentOnline) return;

    // webkitGetAsEntry lets us distinguish files from directories. When a
    // folder is dragged, dataTransfer.files contains only the folder itself
    // (empty File on Chrome) — we have to recurse the FileSystemEntry tree
    // to get the actual contents.
    const items = Array.from(e.dataTransfer.items);
    const entries = items
      .map((it) => (it.kind === 'file' && 'webkitGetAsEntry' in it ? it.webkitGetAsEntry() : null))
      .filter((en): en is FileSystemEntry => en !== null);

    if (entries.length) {
      const collected: File[] = [];
      await Promise.all(entries.map((en) => collectEntryFiles(en, collected)));
      if (collected.length) addFiles(collected);
    } else if (e.dataTransfer.files.length) {
      // Fallback: browsers without webkitGetAsEntry — plain file drop only.
      addFiles(e.dataTransfer.files);
    }
  };

  // ---------------------------------------------------------------------------
  // Speech-to-text (Web Speech API)
  // ---------------------------------------------------------------------------

  const [speechSupported, setSpeechSupported] = useState(false);
  useEffect(() => {
    setSpeechSupported(!!(window.SpeechRecognition || window.webkitSpeechRecognition));
  }, []);

  const toggleListening = () => {
    if (isListening && recognitionRef.current) {
      recognitionRef.current.stop();
      setIsListening(false);
      return;
    }

    const SRConstructor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SRConstructor) return;

    const recognition = new SRConstructor() as SpeechRecognitionInstance;
    recognition.continuous = true;
    recognition.interimResults = true;

    let finalTranscript = '';

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interim = '';
      for (let i = 0; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finalTranscript += result[0].transcript;
        } else {
          interim += result[0].transcript;
        }
      }
      setInput((prev) => {
        const base = prev.replace(/\u200B.*$/, '').trimEnd();
        const prefix = base ? base + ' ' : '';
        return prefix + finalTranscript + (interim ? '\u200B' + interim : '');
      });
    };

    recognition.onend = () => {
      setIsListening(false);
      recognitionRef.current = null;
      // Clean up zero-width space markers from interim results
      setInput((prev) => prev.replace(/\u200B/g, ''));
    };

    recognition.onerror = () => {
      setIsListening(false);
      recognitionRef.current = null;
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsListening(true);
  };

  const handleSend = async () => {
    const trimmed = input.trim();
    if ((!trimmed && pendingFiles.length === 0) || isStreaming || !agentOnline) return;

    setError(null);

    // Upload pending files first. Throttled to 4 concurrent POSTs so a
    // folder upload with dozens of files doesn't saturate the connection
    // or overwhelm the single-worker backend.
    let attachmentRefs: MessageAttachment[] | undefined;
    const filesToUpload = [...pendingFiles];
    if (filesToUpload.length > 0) {
      try {
        const uploaded = await throttledUpload(
          filesToUpload.map((f) => f.file),
          sessionId || undefined,
          4,
        );
        attachmentRefs = uploaded.map((u) => ({
          file_id: u.id,
          filename: u.filename,
          content_type: u.content_type,
        }));
        // Kick off extraction polling for non-image/pdf files — fire-and-forget, doesn't block SSE
        uploaded.forEach((u) => {
          if (u.content_type.startsWith('image/') || u.content_type === 'application/pdf') return;
          setExtractionStatus((prev) => ({ ...prev, [u.id]: 'pending' }));
          setTimeout(() => pollExtraction(u.id), 1000);
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Upload failed');
        return;
      }
    }

    // Derive a descriptive label from filenames when there's no text — this
    // content becomes the sidebar/session title, so "(attached files)" is
    // useless. "foo.csv extraction" gives the chat a real name.
    const fallbackLabel = attachmentsLabel(filesToUpload.map((f) => f.file.name));
    const userText = trimmed || fallbackLabel;

    const userMsg: StructuredMessage = {
      role: 'user',
      content: userText,
      attachments: attachmentRefs,
      attachmentOnly: !trimmed,  // no typed text → bubble skips content
    };
    // Re-pin on send so the new assistant response auto-scrolls even if
    // the user had scrolled up earlier in the conversation.
    isPinnedToBottomRef.current = true;

    // Seed initial state. From here the stream writes to its registry
    // entry (canonical) and notifies subscribers — this component is
    // just the first subscriber. Switching sessions keeps the stream
    // running; switching back re-subscribes.
    const initialMsgs = [...messagesRef.current, userMsg, { role: 'assistant' as const, content: '' }];
    messagesRef.current = initialMsgs;
    setMessages(initialMsgs);
    setInput('');
    pendingFiles.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
    setPendingFiles([]);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // New chats start with a null sessionId — we use a temp key until
    // the first SSE chunk arrives with the real one, then rebind.
    let sid = sessionId;
    // Temp registry key stays separate from the real session so a
    // second brand-new chat doesn't collide with the first's stream.
    let registryKey = sid ?? `__pending_${Date.now()}_${Math.random().toString(36).slice(2)}`;

    // Register this stream. Subscribers (initially just us) get notified
    // on every chunk + on completion. Switching away unsubscribes but
    // leaves the entry; switching back finds it via getStreamForSession.
    const entry: StreamEntry = {
      sid: registryKey,
      messages: initialMsgs,
      done: false,
      subscribers: new Set(),
      abort: () => controller.abort(),
    };
    streamRegistry.set(registryKey, entry);

    // We ourselves subscribe — same callback the session-switch effect
    // would install. This is how we get live tokens in the UI.
    const selfSub = (msgs: StructuredMessage[], done: boolean) => {
      if (!mountedRef.current) return;
      messagesRef.current = msgs;
      setMessages(msgs);
      if (done) {
        setIsStreaming(false);
        streamSubRef.current = null;
        // For a brand-new chat, this is where sessionId finally
        // settles — tell the parent so the URL/sidebar update.
        if (sid && sessionId !== sid) onSessionChange(sid);
      }
    };
    entry.subscribers.add(selfSub);
    // Hook up the sub ref so the session-switch effect can unsubscribe
    // this handler when we leave this session.
    streamSubRef.current?.();
    streamSubRef.current = () => entry.subscribers.delete(selfSub);

    // Persist throttle — final save on done is unconditional.
    let lastPersist = 0;
    const maybePersist = () => {
      const now = Date.now();
      if (sid && now - lastPersist > 250) {
        saveMessagesToStorage(sid, entry.messages);
        lastPersist = now;
      }
    };

    // Push updated messages to all subscribers. Usually just us, but
    // if the user switched away and back, the re-subscribed handler
    // is in here too (and we were removed). May be empty — nobody
    // watching — in which case we just persist and keep going.
    const notify = (done: boolean) => {
      entry.messages = [...entry.messages];  // new ref for React ===
      entry.done = done;
      entry.subscribers.forEach(sub => sub(entry.messages, done));
    };

    await sendChatMessage(
      userText,
      sessionId,
      (event) => {
        // First chunk carries session_id. Rebind the registry entry
        // from temp key → real sid so getStreamForSession() finds it.
        if (!sid) {
          sid = sessionStorage.getItem('chat_session_id');
          if (sid && registryKey !== sid) {
            streamRegistry.delete(registryKey);
            registryKey = sid;
            entry.sid = sid;
            streamRegistry.set(sid, entry);
          }
        }

        // Apply event to the entry's canonical message array.
        const msgs = entry.messages;
        const last = msgs[msgs.length - 1];
        if (!last || last.role !== 'assistant') return;

        const blocks: MessageBlock[] = [...(last.blocks || [])];
        let content = last.content;

        if (event.content) {
          const lastBlock = blocks[blocks.length - 1];
          if (lastBlock && lastBlock.type === 'text') {
            blocks[blocks.length - 1] = { ...lastBlock, content: lastBlock.content + event.content };
          } else {
            blocks.push({ type: 'text', content: event.content as string });
          }
          content += event.content as string;
        } else if (event.thinking_start) {
          blocks.push({ type: 'thinking', content: '' });
        } else if (event.thinking) {
          const lastBlock = blocks[blocks.length - 1];
          if (lastBlock && lastBlock.type === 'thinking') {
            blocks[blocks.length - 1] = { ...lastBlock, content: lastBlock.content + (event.thinking as string) };
          }
        } else if (event.tool_use_start) {
          const info = event.tool_use_start as { name: string; id: string };
          blocks.push({ type: 'tool_use', content: '', name: info.name, toolUseId: info.id });
        } else if (event.tool_use_input) {
          const lastBlock = blocks[blocks.length - 1];
          if (lastBlock && lastBlock.type === 'tool_use') {
            blocks[blocks.length - 1] = { ...lastBlock, content: lastBlock.content + (event.tool_use_input as string) };
          }
        } else if (event.tool_result) {
          const result = event.tool_result as { tool_use_id: string; validation: ToolValidation };
          const idx = blocks.findIndex(b => b.type === 'tool_use' && b.toolUseId === result.tool_use_id);
          if (idx !== -1) blocks[idx] = { ...blocks[idx], validation: result.validation };
        } else if (event.artifact) {
          const art = event.artifact as { id: string; type: string; title: string; tool_use_id?: string | null };
          const ref: ArtifactRef = { id: art.id, type: art.type, title: art.title };
          let idx = art.tool_use_id
            ? blocks.findIndex(b => b.type === 'tool_use' && b.toolUseId === art.tool_use_id)
            : -1;
          if (idx === -1) {
            for (let k = blocks.length - 1; k >= 0; k--) {
              if (blocks[k].type === 'tool_use') { idx = k; break; }
            }
          }
          if (idx !== -1) blocks[idx] = { ...blocks[idx], artifact: ref };
        }

        entry.messages = [...msgs.slice(0, -1), { ...last, content, blocks }];
        notify(false);
        maybePersist();
      },
      () => {
        if (abortControllerRef.current === controller) abortControllerRef.current = null;
        sid ||= sessionStorage.getItem('chat_session_id');
        if (sid) saveMessagesToStorage(sid, entry.messages);
        notify(true);
        // Registry cleanup — entry is no longer live, future session
        // loads go via DB + localStorage.
        streamRegistry.delete(registryKey);
      },
      (err) => {
        if (abortControllerRef.current === controller) abortControllerRef.current = null;
        // Strip empty assistant slot on error
        if (entry.messages.at(-1)?.content === '') {
          entry.messages = entry.messages.slice(0, -1);
        }
        if (sid) saveMessagesToStorage(sid, entry.messages);
        // Report the error only if this session is still active — we
        // don't want a background-stream error banner popping up while
        // the user is in a different chat.
        if (mountedRef.current && entry.subscribers.size > 0) {
          setError(err.message);
        }
        notify(true);
        streamRegistry.delete(registryKey);
      },
      controller.signal,
      selectedModel || undefined,
      attachmentRefs,
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      className="flex flex-col h-full relative"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-50 bg-brand-fig/10 border-2 border-dashed border-brand-fig rounded-xl flex items-center justify-center pointer-events-none">
          <div className="text-brand-fig font-medium text-lg">Drop files here</div>
        </div>
      )}
      {/* Messages */}
      <div
        ref={scrollContainerRef}
        onScroll={handleMessagesScroll}
        className="flex-1 overflow-y-auto chat-scroll px-6 py-6"
      >
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full text-sand-400 text-sm pt-32">
              <div className="text-center space-y-3">
                <div className="w-12 h-12 mx-auto rounded-full bg-brand-coral/30 flex items-center justify-center">
                  <svg className="w-6 h-6 text-brand-fig" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                      d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714a2.25 2.25 0 00.659 1.591L19 14.5M14.25 3.104c.251.023.501.05.75.082M19 14.5l-2.47 2.47a2.25 2.25 0 01-1.591.659H9.061a2.25 2.25 0 01-1.591-.659L5 14.5m14 0V17a2 2 0 01-2 2H7a2 2 0 01-2-2v-2.5" />
                  </svg>
                </div>
                <p className="text-sand-600">Start a conversation to capture experiment metadata.</p>
                <p className="text-xs text-sand-400">
                  Try: &quot;I ran a two-photon calcium imaging session on mouse
                  123 today&quot;
                </p>
              </div>
            </div>
          )}

          {messages.map((msg, i) => {
            // Render attachments as separate cards above the bubble (claude.ai
            // style) — not chips crammed inside it. If the user sent only files,
            // the bubble is skipped since `content` is just a derived label
            // for the session title, not user-authored text.
            const hasBubble = msg.role !== 'user' || !msg.attachmentOnly;
            return (
              <div key={i} className="space-y-2">
                {msg.role === 'user' && msg.attachments && msg.attachments.length > 0 && (
                  <UserAttachmentGroup
                    attachments={msg.attachments}
                    extractionStatus={extractionStatus}
                    onOpenSpreadsheet={(id) => setOpenArtifact({ type: 'upload', id })}
                  />
                )}
                {hasBubble && (
                  <div className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    <div className={msg.role === 'user' ? 'chat-bubble-user' : 'chat-bubble-agent'}>
                      <div className="text-sm leading-relaxed prose prose-sm max-w-none
                                    prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5
                                    prose-strong:text-inherit prose-headings:text-inherit
                                    prose-headings:text-base prose-headings:mt-2 prose-headings:mb-1">
                        {msg.role === 'assistant' ? (
                          msg.blocks && msg.blocks.length > 0 ? (
                            <>
                              {msg.blocks.map((block, idx) =>
                                block.type === 'text' ? (
                                  <ReactMarkdown key={idx} remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
                                ) : block.type === 'thinking' ? (
                                  <ThinkingBlock key={idx} content={block.content} />
                                ) : block.type === 'tool_use' ? (
                                  <ToolUseBlock
                                    key={idx}
                                    name={block.name || 'tool'}
                                    content={block.content}
                                    isStreaming={isStreaming && i === messages.length - 1 && idx === msg.blocks!.length - 1}
                                    validation={block.validation}
                                    artifact={block.artifact}
                                    onOpenArtifact={(ref) => setOpenArtifact({ type: 'artifact', id: ref.id })}
                                  />
                                ) : null
                              )}
                            </>
                          ) : (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                          )
                        ) : (
                          <p className="whitespace-pre-wrap">{msg.content}</p>
                        )}
                        {isStreaming &&
                          i === messages.length - 1 &&
                          msg.role === 'assistant' && (
                            <span className="streaming-cursor" />
                          )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {error && (
            <div className="bg-brand-orange-100 border border-brand-orange-500/20 text-brand-orange-600 rounded-lg px-4 py-3 text-sm">
              Connection error: {error}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="px-6 pb-6 pt-2">
        <div className="max-w-3xl mx-auto relative">
          {/* Attachment preview strip — two rows max, then scrolls. Every
              file stays visible/removable; clear-all is a sticky header so
              it doesn't scroll off with the chips. */}
          {pendingFiles.length > 0 && (
            <div className="mb-2 rounded-xl border border-sand-200 bg-sand-50/40 overflow-hidden">
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-sand-200/60 bg-white/60">
                <span className="text-xs text-sand-600 font-medium">
                  {pendingFiles.length} {pendingFiles.length === 1 ? 'file' : 'files'}
                </span>
                <button
                  onClick={() => {
                    pendingFiles.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
                    setPendingFiles([]);
                  }}
                  className="text-xs text-sand-400 hover:text-sand-600"
                >
                  Clear all
                </button>
              </div>
              {/* max-h ≈ two chip rows; overflow-y-auto so a folder dump
                  doesn't push the textarea off-screen. pr-1 gives the
                  scrollbar room so the remove button isn't clipped. */}
              <div className="flex flex-wrap gap-2 p-2 pr-1 max-h-44 overflow-y-auto chat-scroll">
                {pendingFiles.map((att, i) => (
                  <div key={i} className="relative group">
                    {att.preview ? (
                      <img src={att.preview} alt={att.file.name} className="w-16 h-16 rounded-lg object-cover border border-sand-200" />
                    ) : (
                      <div className="w-16 h-16 rounded-lg border border-sand-200 bg-white flex flex-col items-center justify-center">
                        <span className="text-xl leading-none">{fileTypeIcon(att.file.type, att.file.name)}</span>
                        <span className="text-[9px] text-sand-500 mt-0.5 truncate max-w-[56px] px-1" title={att.file.name}>{att.file.name}</span>
                      </div>
                    )}
                    <button
                      onClick={() => removeFile(i)}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-sand-600 text-white
                                 flex items-center justify-center text-xs opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain,text/markdown,text/csv,application/json,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,audio/mpeg,audio/wav,audio/mp4,audio/ogg,video/mp4,video/quicktime,video/webm,.txt,.md,.json,.yaml,.yml,.csv,.xlsx,.xls,.docx,.mp3,.wav,.m4a,.ogg,.mp4,.mov,.webm"
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              e.target.value = '';
            }}
          />
          {/* Hidden folder input — webkitdirectory can't coexist with plain
              `multiple` file selection on a single element, so it needs its
              own input. React types don't know webkitdirectory/directory so
              we spread them as data attributes to appease TS. */}
          <input
            ref={folderInputRef}
            type="file"
            multiple
            {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              e.target.value = '';
            }}
          />
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = e.target.scrollHeight + 'px';
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder="Describe your experiment..."
            rows={1}
            className="w-full resize-none rounded-2xl border border-sand-200 shadow-sm
                       px-4 pt-3 pb-12 text-sm
                       focus:outline-none focus:ring-2 focus:ring-brand-fig/30 focus:border-brand-fig/50
                       disabled:bg-sand-50 disabled:text-sand-400
                       placeholder:text-sand-400"
            disabled={isStreaming || !agentOnline}
          />
          {!agentOnline && (
            <div className="absolute inset-0 rounded-2xl flex items-center justify-center bg-sand-50/80 pointer-events-none">
              <span className="text-sm text-sand-500 font-medium">Agent is starting up&hellip;</span>
            </div>
          )}
          {/* Model selector */}
          {availableModels.length > 0 && (
            <div className="absolute left-3 bottom-3">
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                disabled={isStreaming || !agentOnline}
                className="appearance-none text-xs text-sand-500 bg-transparent
                           hover:text-sand-700 focus:text-sand-700 focus:outline-none
                           cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed
                           pr-4 py-1"
                title="Select model"
              >
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m.replace('claude-', '').replace(/-\d{8}$/, '')}
                  </option>
                ))}
              </select>
              <svg className="pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 text-sand-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
            </div>
          )}
          <div className="absolute right-3 bottom-3 flex items-center gap-1.5">
            {/* Folder — upload entire directory */}
            {!isStreaming && agentOnline && (
              <button
                onClick={() => folderInputRef.current?.click()}
                className="w-9 h-9 rounded-xl border border-sand-200
                           flex items-center justify-center text-sand-400
                           hover:text-sand-600 hover:bg-sand-50 transition-colors"
                title="Attach a folder — all files inside will be uploaded"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                </svg>
              </button>
            )}
            {/* Paperclip — attach files */}
            {!isStreaming && agentOnline && (
              <button
                onClick={() => fileInputRef.current?.click()}
                className="w-9 h-9 rounded-xl border border-sand-200
                           flex items-center justify-center text-sand-400
                           hover:text-sand-600 hover:bg-sand-50 transition-colors"
                title="Attach files (images, PDF, text, spreadsheets, audio, video)"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
                </svg>
              </button>
            )}
            {/* Microphone — speech-to-text */}
            {!isStreaming && agentOnline && speechSupported && (
              <button
                onClick={toggleListening}
                className={`w-9 h-9 rounded-xl border flex items-center justify-center transition-colors ${
                  isListening
                    ? 'border-red-300 bg-red-50 text-red-500 animate-pulse'
                    : 'border-sand-200 text-sand-400 hover:text-sand-600 hover:bg-sand-50'
                }`}
                title={isListening ? 'Stop listening' : 'Voice input'}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
                </svg>
              </button>
            )}
            {/* Send / Stop */}
            {isStreaming ? (
              <button
                onClick={() => {
                  // Stop whatever is streaming in THIS view. With
                  // background streams, abortControllerRef may point at
                  // a stream we started in a different session; the
                  // registry lookup guarantees we hit the right one.
                  const live = getStreamForSession(sessionId) ??
                    // Fallback: Stop right after sending in a new chat,
                    // before sid is known — abortControllerRef is
                    // still the stream we just started.
                    (abortControllerRef.current ? { abort: () => abortControllerRef.current?.abort() } : undefined);
                  live?.abort();
                }}
                className="w-9 h-9 rounded-xl border border-sand-300 bg-white
                           flex items-center justify-center text-sand-600
                           hover:bg-sand-50 transition-colors"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
                  <rect x="3" y="3" width="10" height="10" rx="1.5" />
                </svg>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={(!input.trim() && pendingFiles.length === 0) || !agentOnline}
                className="w-9 h-9 rounded-xl bg-brand-fig
                           flex items-center justify-center text-white
                           hover:bg-brand-magenta-600 transition-colors
                           disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                  <path d="M12 19V5M5 12l7-7 7 7" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>

      <ArtifactModal source={openArtifact} onClose={() => setOpenArtifact(null)} />
    </div>
  );
}
