'use client';

import { createContext, useContext, useState, useEffect, useCallback, useMemo, ReactNode } from 'react';

// Width constants — mirrored from claude.ai's SidebarNav but slightly narrower
// to suit this app's simpler nav. Slim rail shows icons only; expanded shows
// labels + the full sessions list.
export const SLIM_WIDTH = '3.25rem';
export const EXPANDED_WIDTH = '16rem';

interface SidebarContextValue {
  /** Whether the sidebar is currently expanded (showing labels + sessions). */
  isExpanded: boolean;
  setIsExpanded: (v: boolean) => void;
  /** True when viewport < md (768px). Sidebar becomes an overlay drawer. */
  isMobile: boolean;
  /** Close the sidebar iff on mobile — call after nav clicks. */
  closeSidebarOnMobile: () => void;
  /** True once the media query has evaluated (client-side). */
  hasMeasured: boolean;
}

const SidebarContext = createContext<SidebarContextValue>({
  isExpanded: false,
  setIsExpanded: () => undefined,
  isMobile: false,
  closeSidebarOnMobile: () => undefined,
  hasMeasured: false,
});

export function useSidebar(): SidebarContextValue {
  return useContext(SidebarContext);
}

const STORAGE_KEY = 'aind-sidebar-expanded';

export function SidebarProvider({ children }: { children: ReactNode }) {
  // SSR-safe defaults: assume desktop + collapsed until the client measures.
  // This avoids hydration mismatches and a flash of the wrong layout.
  const [isMobile, setIsMobile] = useState(false);
  const [hasMeasured, setHasMeasured] = useState(false);
  const [isExpanded, setIsExpandedState] = useState(false);

  // Client-side viewport measurement via matchMedia. Using `md` (768px) to
  // match Tailwind's breakpoint so responsive classes stay consistent with
  // JS-driven behavior.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const apply = () => {
      setIsMobile(mq.matches);
      setHasMeasured(true);
    };
    apply();
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, []);

  // Restore expand state from localStorage once we know we're on desktop.
  // On mobile, always start collapsed (overlay closed).
  useEffect(() => {
    if (!hasMeasured) return;
    if (isMobile) {
      setIsExpandedState(false);
    } else {
      const stored = localStorage.getItem(STORAGE_KEY);
      setIsExpandedState(stored === '1');
    }
  }, [hasMeasured, isMobile]);

  const setIsExpanded = useCallback((v: boolean) => {
    setIsExpandedState(v);
    // Only persist on desktop — on mobile, the drawer should always
    // start closed on the next page load.
    if (!isMobile) {
      localStorage.setItem(STORAGE_KEY, v ? '1' : '0');
    }
  }, [isMobile]);

  const closeSidebarOnMobile = useCallback(() => {
    if (isMobile) setIsExpandedState(false);
  }, [isMobile]);

  const value = useMemo(() => ({
    isExpanded, setIsExpanded, isMobile, closeSidebarOnMobile, hasMeasured,
  }), [isExpanded, setIsExpanded, isMobile, closeSidebarOnMobile, hasMeasured]);

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>;
}
