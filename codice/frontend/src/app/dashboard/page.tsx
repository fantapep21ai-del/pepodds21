'use client';

import useSWR from 'swr';
import { api, BetStats, Opportunity } from '@/lib/api';
import StatCard from '@/components/StatCard';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';

const fmt = (n: number, decimals = 0) =>
  new Intl.NumberFormat('it-IT', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(n);

const pnlStr = (n: number) =>
  `${n >= 0 ? '+' : ''}€ ${fmt(Math.abs(n), 2)}`;

export default function DashboardPage() {
  const { data: betStats } = useSWR<BetStats>('bet-stats', api.getBetStats, { refreshInterval: 30000 });
  const { data: opportunities } = useSWR<Opportunity[]>('opps-pending', () => api.getOpportunities('pending'), { refreshInterval: 15000 });

  const pnl = betStats?.total_pnl ?? 0;
  const staked = betStats?.total_staked ?? 0;
  const roi = betStats?.roi_pct ?? 0;
  const winRate = betStats?.win_rate ?? 0;
  const totalBets = betStats?.total_bets ?? 0;
  const openBets = betStats?.open_bets ?? 0;
  const won = betStats?.won ?? 0;
  const lost = betStats?.lost ?? 0;

  const cumulativeChart: { date: string; pnl: number }[] = [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Dashboard</h1>
        <p className="text-brand-muted text-sm mt-1">Statistiche basate sulle scommesse effettive</p>
      </div>

      {/* KPI principali — tutti da scommesse reali */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Totale puntato"
          value={staked > 0 ? `€ ${fmt(staked, 2)}` : '€ 0'}
          sub={`${totalBets} scommess${totalBets === 1 ? 'a' : 'e'}`}
        />
        <StatCard
          label="P&L netto"
          value={staked > 0 ? pnlStr(pnl) : '€ 0'}
          sub={staked > 0 ? `ROI: ${roi >= 0 ? '+' : ''}${fmt(roi, 1)}%` : 'Nessuna scommessa'}
          color={pnl > 0 ? 'green' : pnl < 0 ? 'red' : undefined}
        />
        <StatCard
          label="Win rate"
          value={totalBets > 0 ? `${fmt(winRate, 1)}%` : '—'}
          sub={totalBets > 0 ? `${won} vinte · ${lost} perse` : 'Nessuna chiusa'}
          color={winRate >= 50 ? 'green' : winRate > 0 ? 'red' : undefined}
        />
        <StatCard
          label="Opportunità"
          value={opportunities?.length ?? 0}
          sub={`Aperte: ${openBets}`}
          color="blue"
        />
      </div>

      {/* Grafico P&L cumulativo */}
      {cumulativeChart.length > 0 ? (
        <div className="bg-white rounded-card shadow-card p-6">
          <h2 className="text-base font-semibold text-brand-text mb-4">P&L cumulativo — ultimi 30 giorni</h2>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={cumulativeChart}>
              <defs>
                <linearGradient id="gradGreen" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#34C759" stopOpacity={0.15} />
                  <stop offset="95%" stopColor="#34C759" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E5EA" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#6E6E73' }} />
              <YAxis tick={{ fontSize: 11, fill: '#6E6E73' }} tickFormatter={v => `€${v}`} />
              <Tooltip formatter={(v: number) => [pnlStr(v), 'P&L cumulativo']} />
              <Area
                type="monotone"
                dataKey="pnl"
                stroke="#34C759"
                strokeWidth={2}
                fill="url(#gradGreen)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">
          <div className="text-3xl mb-2">📊</div>
          <div className="font-medium">Nessun dato ancora</div>
          <div className="text-sm mt-1">Il grafico si aggiorna dopo la prima scommessa chiusa</div>
        </div>
      )}

      {/* Riepilogo per tipo */}
      {totalBets > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Scommesse totali" value={totalBets} />
          <StatCard label="Vinte" value={won} color="green" />
          <StatCard label="Perse" value={lost} color="red" />
          <StatCard
            label="P&L totale"
            value={pnlStr(pnl)}
            color={pnl >= 0 ? 'green' : 'red'}
          />
        </div>
      )}

      {/* Opportunità pendenti */}
      {opportunities && opportunities.length > 0 && (
        <div className="bg-white rounded-card shadow-card p-6">
          <h2 className="text-base font-semibold text-brand-text mb-4">
            Opportunità in attesa ({opportunities.length})
          </h2>
          <div className="space-y-3">
            {opportunities.slice(0, 5).map(opp => (
              <div key={opp.id} className="flex items-center justify-between p-3 bg-brand-bg rounded-xl">
                <div>
                  <div className="text-sm font-medium text-brand-text">
                    {opp.market} — {opp.outcome}
                  </div>
                  <div className="text-xs text-brand-muted">
                    {opp.bookmaker} · Prob. {(opp.model_probability * 100).toFixed(1)}%
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold text-brand-green">
                    EV {opp.expected_value >= 0 ? '+' : ''}{(opp.expected_value * 100).toFixed(1)}%
                  </div>
                  <div className="text-xs text-brand-muted">
                    @ {opp.best_odds.toFixed(2)} · {opp.tier && opp.tier !== 'C' ? `Tier ${opp.tier}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
