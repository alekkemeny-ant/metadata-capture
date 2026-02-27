'use client';

import { useState, useEffect, useCallback } from 'react';
import Header from './components/Header';
import SessionsSidebar from './components/SessionsSidebar';
import ChatPanel from './components/ChatPanel';
import MetadataSidebar from './components/MetadataSidebar';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '';

export default function Home() {
  const [sessionsSidebarOpen, setSessionsSidebarOpen] = useState(true);
  const [metadataSidebarOpen, setMetadataSidebarOpen] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [agentOnline, setAgentOnline] = useState(false);

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
    sessionStorage.removeItem('chat_session_id');
  };

  const handleDeleteSession = () => {
    setSessionId(null);
    sessionStorage.removeItem('chat_session_id');
  };

  return (
    <div className="h-screen flex flex-col bg-white">
      <Header agentOnline={agentOnline} />
      <div className="flex-1 flex overflow-hidden">
        {/* Sessions Sidebar */}
        <div
          className={`bg-white border-r border-sand-200 transition-all duration-300 overflow-hidden
                      ${sessionsSidebarOpen ? 'w-60' : 'w-0'}
                      hidden md:block shrink-0`}
        >
          {sessionsSidebarOpen && (
            <SessionsSidebar
              activeSessionId={sessionId}
              onSelectSession={handleSelectSession}
              onNewChat={handleNewChat}
              onDeleteSession={handleDeleteSession}
              onToggleSidebar={() => setSessionsSidebarOpen(false)}
            />
          )}
        </div>

        {/* Sidebar re-open button (visible when sidebar is collapsed) */}
        {!sessionsSidebarOpen && (
          <button
            onClick={() => setSessionsSidebarOpen(true)}
            className="hidden md:flex absolute top-[4.25rem] left-3 z-10 w-8 h-8 items-center justify-center rounded-lg
                       text-sand-400 hover:text-sand-600 hover:bg-sand-100 transition-colors cursor-pointer"
            title="Show sidebar"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
              <path d="M3 6h10M3 12h18M3 18h10" />
            </svg>
          </button>
        )}

        {/* Chat Panel */}
        <div className="flex-1 flex flex-col bg-white min-w-0">
          <ChatPanel
            sessionId={sessionId}
            onSessionChange={handleSelectSession}
            agentOnline={agentOnline}
          />
        </div>

        {/* Metadata Toggle button */}
        <button
          onClick={() => setMetadataSidebarOpen(!metadataSidebarOpen)}
          className="hidden md:flex items-center justify-center w-5 bg-sand-50 hover:bg-sand-100
                     border-x border-sand-200 transition-colors text-sand-300 hover:text-sand-500 shrink-0"
          title={metadataSidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
        >
          <svg
            className={`w-3.5 h-3.5 transition-transform ${metadataSidebarOpen ? '' : 'rotate-180'}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 5l7 7-7 7"
            />
          </svg>
        </button>

        {/* Metadata Sidebar */}
        <div
          className={`bg-white border-l border-sand-200 transition-all duration-300 overflow-hidden
                      ${metadataSidebarOpen ? 'w-96' : 'w-0'}
                      hidden md:block`}
        >
          {metadataSidebarOpen && <MetadataSidebar />}
        </div>
      </div>

      {/* Mobile bottom bar for metadata */}
      <div className="md:hidden border-t border-sand-200 bg-white">
        <button
          onClick={() => setMetadataSidebarOpen(!metadataSidebarOpen)}
          className="w-full px-4 py-3 text-sm font-medium text-sand-600 flex items-center justify-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 6h16M4 12h16M4 18h16"
            />
          </svg>
          {metadataSidebarOpen ? 'Hide' : 'Show'} Captured Metadata
        </button>
        {metadataSidebarOpen && (
          <div className="max-h-64 overflow-y-auto border-t border-sand-100">
            <MetadataSidebar />
          </div>
        )}
      </div>
    </div>
  );
}
