'use client';

import { useState, useEffect } from 'react';
import { usePathname } from 'next/navigation';
import Sidebar from './Sidebar';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Close drawer on route change
  useEffect(() => { setOpen(false); }, [pathname]);

  // Prevent body scroll when drawer is open
  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  return (
    <div className="flex min-h-screen bg-brand-bg">

      {/* ── Mobile top bar ─────────────────────────────────────── */}
      <header className="md:hidden fixed top-0 inset-x-0 z-30 h-14 bg-white border-b border-brand-border flex items-center px-4 gap-3">
        <button
          onClick={() => setOpen(true)}
          aria-label="Open menu"
          className="p-2 -ml-1 rounded-xl hover:bg-brand-bg transition"
        >
          <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" className="text-brand-text">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <span className="text-base font-semibold text-brand-text">PEPODDS21</span>
      </header>

      {/* ── Backdrop ───────────────────────────────────────────── */}
      {open && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px]"
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Sidebar ────────────────────────────────────────────── */}
      {/*   Desktop: always visible. Mobile: slide in/out.          */}
      <div className={[
        'fixed inset-y-0 left-0 z-50 transition-transform duration-200 ease-in-out',
        'md:static md:translate-x-0 md:z-auto md:transition-none',
        open ? 'translate-x-0' : '-translate-x-full',
      ].join(' ')}>
        <Sidebar onClose={() => setOpen(false)} />
      </div>

      {/* ── Main content ───────────────────────────────────────── */}
      <main className="flex-1 pt-14 md:pt-0 overflow-auto">
        <div className="p-4 md:p-8">
          {children}
        </div>
      </main>

    </div>
  );
}
