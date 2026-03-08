'use client';

import { useState, useEffect, useCallback } from 'react';
import AppSidebar from './components/AppSidebar';
import ChatPanel from './components/ChatPanel';
import MetadataSidebar from './components/MetadataSidebar';
import ModelPicker from './components/ModelPicker';
import { useSidebar } from './components/SidebarContext';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '';

export default function Home() {
  const { isMobile, isExpanded, setIsExpanded } = useSidebar();

  const [sessionId, setSessionId] = useState<string | null>(null);
  // Bumped on "New Chat" so ChatPanel can abort a stream even when sessionId
  // stays null (new-chat → new-chat). Without this React skips the re-render
  // since state didn't change, and the old stream keeps writing.
  const [newChatNonce, setNewChatNonce] = useState(0);
  const [agentOnline, setAgentOnline] = useState(false);
  // Model picker state now lives here (lifted from ChatPanel) so the TopBar
  // can render the selector while ChatPanel still uses the value on send.
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [isStreaming, setIsStreaming] = useState(false);

  // Metadata drawer — on desktop it's a right sidebar panel; on mobile it's
  // a full-height overlay drawer (matching the left app-sidebar pattern).
  const [metadataOpen, setMetadataOpen] = useState(false);

  // Default metadata panel open on desktop, closed on mobile.
  useEffect(() => {
    setMetadataOpen(!isMobile);
  }, [isMobile]);

  // Close mobile metadata when the left sidebar drawer opens — it has a
  // backdrop that would block the metadata toggle icon, trapping the user.
  useEffect(() => {
    if (isMobile && isExpanded) setMetadataOpen(false);
  }, [isMobile, isExpanded]);

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
      setAgentOnline(res.ok);
    } catch {
      setAgentOnline(false);
    }
  }, []);

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 5000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  // Restore session from sessionStorage on mount
  useEffect(() => {
    const stored = sessionStorage.getItem('chat_session_id');
    if (stored) setSessionId(stored);
  }, []);

  const handleSelectSession = (id: string) => {
    setSessionId(id);
    sessionStorage.setItem('chat_session_id', id);
  };

  const handleNewChat = () => {
    setSessionId(null);
    setNewChatNonce((n) => n + 1);
    sessionStorage.removeItem('chat_session_id');
  };

  const handleDeleteSession = () => {
    setSessionId(null);
    sessionStorage.removeItem('chat_session_id');
  };

  return (
    <div className="h-screen flex bg-white overflow-hidden">
      {/* ═════════════════ Left rail: brand + nav + sessions + agent status ═════════════════ */}
      <AppSidebar
        agentOnline={agentOnline}
        activeSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        onDeleteSession={handleDeleteSession}
        showSessions
      />

      {/* ═════════════════ Main content: top bar + chat ═════════════════ */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar — hamburger (mobile) | model picker | metadata toggle */}
        <div className="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-sand-200 shrink-0">
          {/* Hamburger — mobile only, opens the app sidebar drawer */}
          <button
            onClick={() => setIsExpanded(true)}
            className="md:hidden w-8 h-8 flex items-center justify-center rounded-lg
                       text-sand-500 hover:text-sand-700 hover:bg-sand-100 transition-colors"
            title="Open menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" viewBox="0 0 24 24">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>

          <ModelPicker
            value={selectedModel}
            onChange={setSelectedModel}
            disabled={isStreaming || !agentOnline}
          />

          <div className="flex-1" />

          {/* Metadata toggle — on desktop collapses the right panel; on mobile
              swaps the chat body for a full-screen MetadataSidebar. The icon
              stays visible in the top bar either way, so it's the sole toggle. */}
          <button
            onClick={() => setMetadataOpen((v) => !v)}
            className={`w-8 h-8 flex items-center justify-center rounded-lg transition-colors
                       ${metadataOpen && !isMobile
                         ? 'bg-sand-100 text-sand-700'
                         : 'text-sand-500 hover:text-sand-700 hover:bg-sand-100'}`}
            title={metadataOpen ? 'Hide metadata' : 'Show metadata'}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </button>
        </div>

        {/* Chat body — relative so the mobile metadata overlay can position
            itself over exactly this area (below the top bar). */}
        <div className="flex-1 min-h-0 relative">
          <ChatPanel
            sessionId={sessionId}
            newChatNonce={newChatNonce}
            onSessionChange={handleSelectSession}
            agentOnline={agentOnline}
            selectedModel={selectedModel}
            onStreamingChange={setIsStreaming}
          />

          {/* Mobile metadata: full-screen overlay over the chat body.
              The top bar stays visible above so the document icon remains
              tappable to toggle this closed — no backdrop or × button needed. */}
          {isMobile && metadataOpen && (
            <div className="absolute inset-0 z-30 bg-white">
              <MetadataSidebar />
            </div>
          )}
        </div>
      </div>

      {/* ═════════════════ Right: metadata panel (desktop only) ═════════════════ */}
      {!isMobile && (
        <div
          className={`border-l border-sand-200 transition-all duration-200 overflow-hidden shrink-0
                      ${metadataOpen ? 'w-96' : 'w-0'}`}
        >
          {metadataOpen && <MetadataSidebar />}
        </div>
      )}
    </div>
  );
}
