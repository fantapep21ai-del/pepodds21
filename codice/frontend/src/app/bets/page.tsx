'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { api, Bet, BetStats } from '@/lib/api';
import StatCard from '@/components/StatCard';

const STATUS_TABS: { value: string; label: string }[] = [
  { value: 'open', label: 'Aperte' },
  { value: 'won',  label: 'Vinte' },
  { value: 'lost', label: 'Perse' },
  { value: 'void', label: 'Annullate' },
];

const STATUS_LABEL: Record<string, string> = {
  open: 'Aperta',
  won:  'Vinta',
  lost: 'Persa',
  void: 'Annullata',
};

export default function BetsPage() {
  const [tab, setTab] = useState('open');
  const { data: bets, isLoading } = useSWR<Bet[]>(`bets-${tab}`, () => api.getBets(tab), { refreshInterval: 30000 });
  const { data: stats } = useSWR<BetStats>('bet-stats', api.getBetStats, { refreshInterval: 30000 });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Scommesse</h1>
        <p className="text-brand-muted text-sm mt-1">Storico di tutte le scommesse e relativi risultati</p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Totale scommesse" value={stats.total_bets} />
          <StatCard label="Tasso vincita" value={`${stats.win_rate.toFixed(1)}%`} color={stats.win_rate >= 50 ? 'green' : 'red'} />
          <StatCard label="P&L totale" value={`${stats.total_pnl >= 0 ? '+' : ''}€ ${stats.total_pnl.toFixed(2)}`} color={stats.total_pnl >= 0 ? 'green' : 'red'} />
          <StatCard label="Rendimento" value={`${stats.roi_pct >= 0 ? '+' : ''}${stats.roi_pct.toFixed(1)}%`} color={stats.roi_pct >= 0 ? 'green' : 'red'} />
        </div>
      )}

      <div className="flex gap-2 overflow-x-auto pb-1">
        {STATUS_TABS.map(t => (
          <button key={t.value} onClick={() => setTab(t.value)}
            className={`flex-shrink-0 px-4 py-2 rounded-xl text-sm font-medium transition ${tab === t.value ? 'bg-brand-blue text-white' : 'bg-white text-brand-muted hover:text-brand-text'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">Caricamento…</div>
      ) : !bets?.length ? (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">
          Nessuna scommessa {STATUS_TABS.find(t => t.value === tab)?.label.toLowerCase() ?? tab}
        </div>
      ) : (
        <>
          {/* ── Tabella desktop ──────────────────────────────────── */}
          <div className="hidden md:block bg-white rounded-card shadow-card overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-brand-border text-xs text-brand-muted uppercase tracking-wide">
                  <th className="text-left p-4">Esito</th>
                  <th className="text-left p-4">Quota</th>
                  <th className="text-left p-4">Importo</th>
                  <th className="text-left p-4">P&L</th>
                  <th className="text-left p-4">Stato</th>
                  <th className="text-left p-4">Data</th>
                </tr>
              </thead>
              <tbody>
                {bets.map((b, i) => (
                  <tr key={b.id} className={`border-b border-brand-border last:border-0 ${i % 2 === 0 ? '' : 'bg-brand-bg/40'}`}>
                    <td className="p-4">
                      <div className="text-sm font-medium text-brand-text">{b.outcome}</div>
                      <div className="text-xs text-brand-muted">{b.market} · {b.bookmaker}</div>
                    </td>
                    <td className="p-4 text-sm text-brand-text">{b.odds.toFixed(2)}</td>
                    <td className="p-4 text-sm text-brand-text">€ {b.stake.toFixed(2)}</td>
                    <td className="p-4 text-sm font-semibold">
                      {b.pnl != null
                        ? <span className={b.pnl >= 0 ? 'text-brand-green' : 'text-brand-red'}>{b.pnl >= 0 ? '+' : ''}€ {b.pnl.toFixed(2)}</span>
                        : <span className="text-brand-muted">—</span>}
                    </td>
                    <td className="p-4">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        b.status === 'won'  ? 'bg-green-100 text-brand-green' :
                        b.status === 'lost' ? 'bg-red-100 text-brand-red' :
                        b.status === 'open' ? 'bg-blue-50 text-brand-blue' :
                        'bg-gray-100 text-brand-muted'}`}>
                        {STATUS_LABEL[b.status] ?? b.status}
                      </span>
                    </td>
                    <td className="p-4 text-xs text-brand-muted">
                      {new Date(b.placed_at).toLocaleDateString('it-IT')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ── Card mobile ──────────────────────────────────────── */}
          <div className="md:hidden space-y-3">
            {bets.map(b => (
              <div key={b.id} className="bg-white rounded-card shadow-card p-4 space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium text-brand-text">{b.outcome}</div>
                    <div className="text-xs text-brand-muted mt-0.5">{b.market} · {b.bookmaker}</div>
                  </div>
                  <span className={`flex-shrink-0 text-xs px-2 py-0.5 rounded-full font-medium ${
                    b.status === 'won'  ? 'bg-green-100 text-brand-green' :
                    b.status === 'lost' ? 'bg-red-100 text-brand-red' :
                    b.status === 'open' ? 'bg-blue-50 text-brand-blue' :
                    'bg-gray-100 text-brand-muted'}`}>
                    {STATUS_LABEL[b.status] ?? b.status}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <div className="text-brand-muted text-xs">
                    Quota {b.odds.toFixed(2)} · Importo € {b.stake.toFixed(2)} · {new Date(b.placed_at).toLocaleDateString('it-IT')}
                  </div>
                  <div className="font-semibold">
                    {b.pnl != null
                      ? <span className={b.pnl >= 0 ? 'text-brand-green' : 'text-brand-red'}>{b.pnl >= 0 ? '+' : ''}€ {b.pnl.toFixed(2)}</span>
                      : <span className="text-brand-muted">—</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
