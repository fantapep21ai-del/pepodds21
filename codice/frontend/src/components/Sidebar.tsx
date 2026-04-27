'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { clsx } from 'clsx';

const NAV = [
  { href: '/dashboard',     label: 'Dashboard',    icon: '◼' },
  { href: '/matches',       label: 'Partite',       icon: '⚽' },
  { href: '/opportunities', label: 'Opportunità',   icon: '🎯' },
  { href: '/scalate',       label: 'Scalate',       icon: '🎰' },
  { href: '/bets',          label: 'Scommesse',     icon: '📋' },
  { href: '/analytics',     label: 'Analytics',     icon: '📈' },
  { href: '/settings',      label: 'Impostazioni',  icon: '⚙️' },
];

interface Props {
  onClose?: () => void;
}

export default function Sidebar({ onClose }: Props) {
  const path = usePathname();

  return (
    <aside className="w-56 min-h-screen bg-white border-r border-brand-border flex flex-col">
      <div className="p-6 border-b border-brand-border flex items-center justify-between">
        <div>
          <div className="text-base font-semibold text-brand-text">PEPODDS21</div>
          <div className="text-xs text-brand-muted mt-0.5">Sistema AI</div>
        </div>
        {/* Close button — only visible on mobile */}
        {onClose && (
          <button
            onClick={onClose}
            aria-label="Close menu"
            className="md:hidden p-1.5 rounded-lg hover:bg-brand-bg transition text-brand-muted"
          >
            <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      <nav className="flex-1 p-3 space-y-1">
        {NAV.map(item => (
          <Link
            key={item.href}
            href={item.href}
            className={clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition',
              path.startsWith(item.href)
                ? 'bg-brand-blue text-white font-medium'
                : 'text-brand-muted hover:bg-brand-bg hover:text-brand-text'
            )}
          >
            <span>{item.icon}</span>
            {item.label}
          </Link>
        ))}
      </nav>

    </aside>
  );
}
