'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { fetchSessions, deleteSession, Session } from '../lib/api';
import { useSidebar, SLIM_WIDTH, EXPANDED_WIDTH } from './SidebarContext';

// ---------------------------------------------------------------------------
// AppSidebar — unified left rail containing brand, nav, sessions, and agent
// status. Replaces the old top Header + separate SessionsSidebar.
//
// Responsive behavior (patterned after claude.ai's SidebarNav):
//   - Desktop (≥ md): fixed-width rail. SLIM shows icon-only nav; EXPANDED
//     adds labels + the scrollable sessions list. Width transition is CSS.
//   - Mobile (< md): hidden by default; when expanded, renders as a fixed
//     overlay drawer with a backdrop. Closes on backdrop tap or nav select.
// ---------------------------------------------------------------------------

interface AppSidebarProps {
  agentOnline: boolean;
  /** Currently active chat session (for highlight). Null on dashboard or empty chat. */
  activeSessionId?: string | null;
  onSelectSession?: (id: string) => void;
  onNewChat?: () => void;
  onDeleteSession?: (id: string) => void;
  /** If false, the sessions list is suppressed (e.g. on /dashboard). */
  showSessions?: boolean;
}

// Navigation destinations. Icon paths are heroicon-style outlines so we
// don't pull in an icon library dependency.
const NAV_ITEMS = [
  {
    href: '/',
    label: 'Chat',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    href: '/dashboard',
    label: 'Dashboard',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 13a1 1 0 011-1h4a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6zM3 4a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm10 0a1 1 0 011-1h6a1 1 0 011 1v8a1 1 0 01-1 1h-6a1 1 0 01-1-1V4zm0 12a1 1 0 011-1h6a1 1 0 011 1v4a1 1 0 01-1 1h-6a1 1 0 01-1-1v-4z" />
      </svg>
    ),
  },
];

export default function AppSidebar({
  agentOnline,
  activeSessionId = null,
  onSelectSession,
  onNewChat,
  onDeleteSession,
  showSessions = true,
}: AppSidebarProps) {
  const { isExpanded, setIsExpanded, isMobile, closeSidebarOnMobile } = useSidebar();
  const pathname = usePathname();

  // Sessions list state — polled every 5s like the old SessionsSidebar.
  const [sessions, setSessions] = useState<Session[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    if (!showSessions) return;
    try {
      const data = await fetchSessions();
      setSessions(data);
    } catch { /* backend offline — silently retry next interval */ }
  }, [showSessions]);

  useEffect(() => {
    loadSessions();
    const interval = setInterval(loadSessions, 5000);
    return () => clearInterval(interval);
  }, [loadSessions]);

  const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setDeletingId(sessionId);
    try {
      await deleteSession(sessionId);
      // Animate out, then prune from local state
      setTimeout(() => {
        setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
        setDeletingId(null);
        if (activeSessionId === sessionId) onDeleteSession?.(sessionId);
      }, 300);
    } catch {
      setDeletingId(null);
    }
  };

  // Mobile: render nothing when closed; overlay when open.
  // Desktop: always render, width toggles between slim and expanded.
  if (isMobile && !isExpanded) return null;

  // The inner content is shared; only the outer container differs.
  const sidebarContent = (
    <nav
      className={`flex flex-col h-full bg-sand-50 border-r border-sand-200
                  transition-[width] duration-200 ease-out overflow-hidden`}
      style={{ width: isMobile ? EXPANDED_WIDTH : isExpanded ? EXPANDED_WIDTH : SLIM_WIDTH }}
    >
      {/* ─────────────────────────────── Brand + toggle ─────────────────────────────── */}
      <div className="flex items-center gap-2 p-2.5 shrink-0">
        <div className="w-8 h-8 rounded-lg bg-brand-fig/10 flex items-center justify-center shrink-0">
          <svg className="w-5 h-5 text-brand-fig" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714a2.25 2.25 0 00.659 1.591L19 14.5M14.25 3.104c.251.023.501.05.75.082M19 14.5l-2.47 2.47a2.25 2.25 0 01-1.591.659H9.061a2.25 2.25 0 01-1.591-.659L5 14.5m14 0V17a2 2 0 01-2 2H7a2 2 0 01-2-2v-2.5" />
          </svg>
        </div>
        {isExpanded && (
          <div className="min-w-0 flex-1">
            <h1 className="text-sm font-semibold text-sand-800 truncate">AIND Metadata</h1>
            <p className="text-[10px] text-sand-400 truncate">Allen Institute</p>
          </div>
        )}
        {/* Toggle — on mobile this closes the drawer; on desktop it collapses to slim */}
        {isExpanded && (
          <button
            onClick={() => setIsExpanded(false)}
            className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0
                       text-sand-400 hover:text-sand-600 hover:bg-sand-100 transition-colors"
            title={isMobile ? 'Close' : 'Collapse sidebar'}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" viewBox="0 0 24 24">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
        )}
      </div>

      {/* ─────────────────────────────── New Chat ─────────────────────────────── */}
      {onNewChat && (
        <div className="px-2 pb-2 shrink-0">
          <button
            onClick={() => { onNewChat(); closeSidebarOnMobile(); }}
            className={`w-full flex items-center gap-2 rounded-lg border border-sand-200
                       hover:bg-sand-100 transition-colors text-sm font-medium text-sand-600
                       ${isExpanded ? 'px-3 py-2 justify-start' : 'p-2 justify-center'}`}
            title="New chat"
          >
            <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            {isExpanded && <span>New Chat</span>}
          </button>
        </div>
      )}

      {/* ─────────────────────────────── Navigation ─────────────────────────────── */}
      <div className="px-2 space-y-0.5 shrink-0">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={closeSidebarOnMobile}
              className={`flex items-center gap-2 rounded-lg text-sm font-medium transition-colors
                         ${isExpanded ? 'px-3 py-2' : 'p-2 justify-center'}
                         ${active
                           ? 'bg-sand-200 text-sand-800'
                           : 'text-sand-500 hover:bg-sand-100 hover:text-sand-700'}`}
              title={item.label}
            >
              <span className="shrink-0">{item.icon}</span>
              {isExpanded && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}
      </div>

      {/* ─────────────────────────────── Expand button (slim only) ─────────────────────────────── */}
      {!isExpanded && !isMobile && (
        <button
          onClick={() => setIsExpanded(true)}
          className="mx-2 mt-2 p-2 rounded-lg flex items-center justify-center shrink-0
                     text-sand-400 hover:text-sand-600 hover:bg-sand-100 transition-colors"
          title="Expand sidebar"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" viewBox="0 0 24 24">
            <path d="M9 18l6-6-6-6" />
          </svg>
        </button>
      )}

      {/* ─────────────────────────────── Sessions list (expanded only) ─────────────────────────────── */}
      {isExpanded && showSessions && (
        <div className="flex-1 overflow-y-auto chat-scroll px-2 pt-3 pb-2 min-h-0">
          <p className="text-[10px] font-semibold text-sand-400 uppercase tracking-wider px-2 mb-1.5">
            Recent
          </p>
          {sessions.length === 0 && (
            <p className="text-xs text-sand-400 text-center mt-2">No conversations yet</p>
          )}
          <div className="space-y-0.5">
            {sessions.map((s) => {
              const isDeleting = deletingId === s.session_id;
              const isActive = activeSessionId === s.session_id;
              return (
                <div
                  key={s.session_id}
                  onClick={() => {
                    if (isDeleting) return;
                    onSelectSession?.(s.session_id);
                    closeSidebarOnMobile();
                  }}
                  className={`group w-full text-left px-3 py-2 rounded-lg text-sm cursor-pointer
                              transition-all duration-300 overflow-hidden
                              ${isDeleting
                                ? 'opacity-0 max-h-0 py-0 translate-x-4'
                                : isActive
                                  ? 'bg-sand-200 text-sand-800 max-h-12'
                                  : 'text-sand-500 hover:bg-sand-100 hover:text-sand-700 max-h-12'}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      {s.first_message?.slice(0, 40) || 'New conversation'}
                    </span>
                    <button
                      onClick={(e) => handleDelete(e, s.session_id)}
                      className="transition-opacity text-sand-300 hover:text-brand-orange-600 p-0.5 shrink-0
                                 opacity-100 md:opacity-0 md:group-hover:opacity-100"
                      title="Delete chat"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                        <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                      </svg>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Spacer — push agent status to bottom when sessions aren't shown or slim */}
      {(!isExpanded || !showSessions) && <div className="flex-1" />}

      {/* ─────────────────────────────── Agent status ─────────────────────────────── */}
      <div className={`p-2.5 shrink-0 border-t border-sand-200 ${isExpanded ? '' : 'flex justify-center'}`}>
        <span
          className={`inline-flex items-center rounded-full text-xs font-medium transition-colors
                     ${isExpanded ? 'gap-1.5 px-2.5 py-1 border' : ''}
                     ${agentOnline
                       ? isExpanded ? 'bg-brand-aqua-500/10 text-brand-aqua-700 border-brand-aqua-500/20' : ''
                       : isExpanded ? 'bg-sand-100 text-sand-500 border-sand-200' : ''}`}
          title={agentOnline ? 'Agent Online' : 'Agent Offline'}
        >
          <span className={`w-2 h-2 rounded-full ${agentOnline ? 'bg-brand-aqua-500' : 'bg-sand-400'}`} />
          {isExpanded && (agentOnline ? 'Agent Online' : 'Agent Offline')}
        </span>
      </div>
    </nav>
  );

  // ─────────────────────────────── Outer container ───────────────────────────────
  // Mobile: fixed overlay with backdrop. Desktop: in-flow flex child.
  if (isMobile) {
    return (
      <>
        {/* Backdrop — clicking closes the drawer */}
        <div
          className="fixed inset-0 bg-black/30 z-40"
          onClick={() => setIsExpanded(false)}
        />
        <div className="fixed inset-y-0 left-0 z-50 shadow-xl">
          {sidebarContent}
        </div>
      </>
    );
  }

  return <div className="shrink-0">{sidebarContent}</div>;
}
