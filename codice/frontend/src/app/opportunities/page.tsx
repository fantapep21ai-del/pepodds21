'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { api, Opportunity } from '@/lib/api';
import StakeModal from '@/components/StakeModal';
import Toast from '@/components/Toast';

const STATUS_TABS: { value: string; label: string }[] = [
  { value: 'pending',    label: 'In attesa' },
  { value: 'bet_placed', label: 'Confermata' },
  { value: 'rejected',   label: 'Rifiutata' },
];

const TIER_COLOR: Record<string, string> = {
  S: 'bg-orange-100 text-orange-700',
  A: 'bg-green-100 text-brand-green',
  B: 'bg-blue-50 text-brand-blue',
  C: 'bg-gray-100 text-brand-muted',
};

interface ToastState { message: string; type: 'success' | 'error' | 'info' }

function EVBadge({ ev }: { ev: number }) {
  const pct = (ev * 100).toFixed(1);
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ev >= 0.1 ? 'bg-green-100 text-brand-green' : 'bg-yellow-50 text-yellow-700'}`}>
      {ev >= 0 ? '+' : ''}{pct}%
    </span>
  );
}

function TierBadge({ tier }: { tier?: string }) {
  if (!tier || tier === 'C') return null;
  return (
    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${TIER_COLOR[tier] ?? 'bg-gray-100 text-brand-muted'}`}>
      Tier {tier}
    </span>
  );
}

function UncertaintyBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = score < 0.3 ? 'bg-brand-green' : score < 0.55 ? 'bg-brand-orange' : 'bg-brand-red';
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-brand-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-brand-muted">{pct}%</span>
    </div>
  );
}

export default function OpportunitiesPage() {
  const [tab, setTab] = useState('pending');
  const [pendingApprove, setPendingApprove] = useState<Opportunity | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  const { data: opps, isLoading, mutate } = useSWR<Opportunity[]>(
    `opps-${tab}`,
    () => api.getOpportunities(tab),
    { refreshInterval: 15000 }
  );

  const handleApproveConfirm = async (stake: number) => {
    if (!pendingApprove) return;
    const opp = pendingApprove;
    setPendingApprove(null);
    try {
      await api.approveOpportunity(opp.id, stake);
      setToast({ message: `Scommessa confermata — € ${stake.toFixed(0)}`, type: 'success' });
      mutate();
    } catch (e: any) {
      setToast({ message: e.message ?? 'Errore', type: 'error' });
    }
  };

  const handleReject = async (id: string) => {
    try {
      await api.rejectOpportunity(id, 'Rifiutata manualmente');
      setToast({ message: 'Opportunità rifiutata', type: 'info' });
      mutate();
    } catch {
      setToast({ message: 'Errore nel rifiuto', type: 'error' });
    }
  };

  const currentTabLabel = STATUS_TABS.find(t => t.value === tab)?.label ?? tab;

  return (
    <div className="space-y-6">
      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
      {pendingApprove && (
        <StakeModal
          onConfirm={handleApproveConfirm}
          onCancel={() => setPendingApprove(null)}
        />
      )}

      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Opportunità</h1>
        <p className="text-brand-muted text-sm mt-1">Quote di valore identificate dal sistema AI — decidi tu se giocarle</p>
      </div>

      {/* Tab filtro */}
      <div className="flex gap-2 overflow-x-auto pb-1 -mb-1">
        {STATUS_TABS.map(t => (
          <button
            key={t.value}
            onClick={() => setTab(t.value)}
            className={`flex-shrink-0 px-4 py-2 rounded-xl text-sm font-medium transition ${tab === t.value ? 'bg-brand-blue text-white' : 'bg-white text-brand-muted hover:text-brand-text'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">Caricamento…</div>
      ) : !opps?.length ? (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">
          Nessuna opportunità {currentTabLabel.toLowerCase()}
        </div>
      ) : (
        <>
          {/* ── Tabella desktop ──────────────────────────────────── */}
          <div className="hidden md:block bg-white rounded-card shadow-card overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-brand-border text-xs text-brand-muted uppercase tracking-wide">
                  <th className="text-left p-4">Mercato / Esito</th>
                  <th className="text-left p-4">Tier</th>
                  <th className="text-left p-4">Quota</th>
                  <th className="text-left p-4">Prob. modello</th>
                  <th className="text-left p-4">EV</th>
                  <th className="text-left p-4">Incertezza</th>
                  {tab === 'pending' && <th className="text-right p-4">Azioni</th>}
                </tr>
              </thead>
              <tbody>
                {opps.map((opp, i) => (
                  <tr key={opp.id} className={`border-b border-brand-border last:border-0 ${i % 2 === 0 ? '' : 'bg-brand-bg/40'}`}>
                    <td className="p-4">
                      <div className="text-sm font-medium text-brand-text">{opp.outcome}</div>
                      <div className="text-xs text-brand-muted">{opp.market} · {opp.bookmaker}</div>
                    </td>
                    <td className="p-4"><TierBadge tier={opp.tier} /></td>
                    <td className="p-4 text-sm font-semibold text-brand-text">{opp.best_odds.toFixed(2)}</td>
                    <td className="p-4 text-sm text-brand-text">{(opp.model_probability * 100).toFixed(1)}%</td>
                    <td className="p-4"><EVBadge ev={opp.expected_value} /></td>
                    <td className="p-4"><UncertaintyBar score={opp.uncertainty_score} /></td>
                    {tab === 'pending' && (
                      <td className="p-4 text-right">
                        {opp.uncertainty_blocked ? (
                          <span className="text-xs text-brand-red font-medium">Bloccata</span>
                        ) : (
                          <div className="flex gap-2 justify-end">
                            <button
                              onClick={() => setPendingApprove(opp)}
                              className="text-xs px-3 py-1.5 bg-brand-green text-white rounded-lg hover:opacity-90"
                            >
                              Conferma
                            </button>
                            <button
                              onClick={() => handleReject(opp.id)}
                              className="text-xs px-3 py-1.5 bg-brand-bg text-brand-red border border-brand-border rounded-lg hover:bg-red-50"
                            >
                              Rifiuta
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ── Card mobile ──────────────────────────────────────── */}
          <div className="md:hidden space-y-3">
            {opps.map(opp => (
              <div key={opp.id} className="bg-white rounded-card shadow-card p-4 space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium text-brand-text">{opp.outcome}</div>
                    <div className="text-xs text-brand-muted mt-0.5">{opp.market} · {opp.bookmaker}</div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <EVBadge ev={opp.expected_value} />
                    <TierBadge tier={opp.tier} />
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="bg-brand-bg rounded-xl p-2 text-center">
                    <div className="text-brand-muted mb-0.5">Quota</div>
                    <div className="font-semibold text-brand-text">{opp.best_odds.toFixed(2)}</div>
                  </div>
                  <div className="bg-brand-bg rounded-xl p-2 text-center">
                    <div className="text-brand-muted mb-0.5">Prob.</div>
                    <div className="font-semibold text-brand-text">{(opp.model_probability * 100).toFixed(1)}%</div>
                  </div>
                  <div className="bg-brand-bg rounded-xl p-2 text-center">
                    <div className="text-brand-muted mb-0.5">Tier</div>
                    <div className="font-semibold text-brand-text">{opp.tier ?? 'C'}</div>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs text-brand-muted">
                    Incertezza <UncertaintyBar score={opp.uncertainty_score} />
                  </div>
                  {tab === 'pending' && (
                    opp.uncertainty_blocked ? (
                      <span className="text-xs text-brand-red font-medium">Bloccata</span>
                    ) : (
                      <div className="flex gap-2">
                        <button
                          onClick={() => setPendingApprove(opp)}
                          className="text-xs px-3 py-2 bg-brand-green text-white rounded-lg font-medium"
                        >
                          Conferma
                        </button>
                        <button
                          onClick={() => handleReject(opp.id)}
                          className="text-xs px-3 py-2 bg-brand-bg text-brand-red border border-brand-border rounded-lg"
                        >
                          Rifiuta
                        </button>
                      </div>
                    )
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
