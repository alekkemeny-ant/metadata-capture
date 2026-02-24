'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '';

const NAV_ITEMS = [
  { href: '/', label: 'Chat' },
  { href: '/dashboard', label: 'Dashboard' },
];

export default function Header() {
  const pathname = usePathname();
  const [online, setOnline] = useState(false);

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
      setOnline(res.ok);
    } catch {
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 5000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  return (
    <header className="bg-white border-b border-sand-200 px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-brand-fig/10 flex items-center justify-center">
            <svg
              className="w-5 h-5 text-brand-fig"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714a2.25 2.25 0 00.659 1.591L19 14.5M14.25 3.104c.251.023.501.05.75.082M19 14.5l-2.47 2.47a2.25 2.25 0 01-1.591.659H9.061a2.25 2.25 0 01-1.591-.659L5 14.5m14 0V17a2 2 0 01-2 2H7a2 2 0 01-2-2v-2.5"
              />
            </svg>
          </div>
          <div>
            <h1 className="text-base font-semibold text-sand-800">
              AIND Metadata Capture
            </h1>
            <p className="text-xs text-sand-400">
              Allen Institute for Neural Dynamics
            </p>
          </div>
        </div>
        <nav className="flex items-center gap-1">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                pathname === item.href
                  ? 'bg-sand-100 text-sand-800'
                  : 'text-sand-400 hover:text-sand-700 hover:bg-sand-50'
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-2">
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
          online
            ? 'bg-brand-aqua-500/10 text-brand-aqua-700 border-brand-aqua-500/20'
            : 'bg-sand-100 text-sand-500 border-sand-200'
        }`}>
          <span className={`w-1.5 h-1.5 rounded-full ${online ? 'bg-brand-aqua-500' : 'bg-sand-400'}`} />
          {online ? 'Agent Online' : 'Agent Offline'}
        </span>
      </div>
    </header>
  );
}
