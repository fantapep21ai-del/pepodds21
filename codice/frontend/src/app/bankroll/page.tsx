'use client';

import useSWR from 'swr';
import { api, BetStats } from '@/lib/api';
import StatCard from '@/components/StatCard';

const fmt = (n: number, d = 2) =>
  new Intl.NumberFormat('it-IT', { minimumFractionDigits: d, maximumFractionDigits: d }).format(n);

export default function PipelinePage() {
  const { data: betStats } = useSWR<BetStats>('bet-stats-pipeline', api.getBetStats, { refreshInterval: 30000 });

  const totalBets = betStats?.total_bets ?? 0;
  const staked = betStats?.total_staked ?? 0;
  const pnl = betStats?.total_pnl ?? 0;
  const roi = betStats?.roi_pct ?? 0;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Pipeline</h1>
        <p className="text-brand-muted text-sm mt-1">Attività del sistema e statistiche scommesse</p>
      </div>

      {/* KPI sintetici */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Totale puntato"
          value={staked > 0 ? `€ ${fmt(staked)}` : '€ 0'}
          sub={`${totalBets} scommes${totalBets === 1 ? 'sa' : 'se'}`}
        />
        <StatCard
          label="P&L netto"
          value={staked > 0 ? `${pnl >= 0 ? '+' : ''}€ ${fmt(Math.abs(pnl))}` : '€ 0'}
          sub={staked > 0 ? `ROI: ${roi >= 0 ? '+' : ''}${fmt(roi, 1)}%` : 'Nessuna scommessa'}
          color={pnl > 0 ? 'green' : pnl < 0 ? 'red' : undefined}
        />
        <StatCard
          label="Vinte"
          value={betStats?.won ?? 0}
          color="green"
        />
        <StatCard
          label="Perse"
          value={betStats?.lost ?? 0}
          color="red"
        />
      </div>

      <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">
        <div className="text-3xl mb-2">⚙️</div>
        <div className="font-medium">Pipeline automatica attiva</div>
        <div className="text-sm mt-1">La pipeline gira automaticamente alle 09:45, 15:45 e 18:45 UTC</div>
      </div>
    </div>
  );
}
