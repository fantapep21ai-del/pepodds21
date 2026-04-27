'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { api, Scalata, ScalataStats } from '@/lib/api';
import StatCard from '@/components/StatCard';
import Toast from '@/components/Toast';

interface ToastState { message: string; type: 'success' | 'error' }

const STATUS_COLOR: Record<string, string> = {
  attiva:  'bg-blue-50 text-brand-blue',
  vinta:   'bg-green-100 text-brand-green',
  persa:   'bg-red-100 text-brand-red',
};

const STEP_COLOR: Record<string, string> = {
  in_attesa: 'bg-gray-100 text-brand-muted',
  attivo:    'bg-blue-50 text-brand-blue',
  vinto:     'bg-green-100 text-brand-green',
  perso:     'bg-red-100 text-brand-red',
};

const STEP_ICON: Record<string, string> = {
  in_attesa: '○',
  attivo:    '▶',
  vinto:     '✓',
  perso:     '✕',
};

export default function ScalatePage() {
  const { data: scalate, mutate } = useSWR<Scalata[]>('scalate', api.getScalate, { refreshInterval: 15000 });
  const { data: stats } = useSWR<ScalataStats>('scalate-stats', api.getScalataStats, { refreshInterval: 30000 });

  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [startAmount, setStartAmount] = useState('20');
  const [toast, setToast] = useState<ToastState | null>(null);

  const handleConferma = async (scalata: Scalata) => {
    const amount = parseFloat(startAmount);
    if (!amount || amount <= 0) return;
    try {
      await api.confermaStep(scalata.id, amount);
      setToast({ message: `Step ${scalata.current_step + 1} confermato — €${amount}`, type: 'success' });
      setConfirmingId(null);
      mutate();
    } catch (e: any) {
      setToast({ message: e.message ?? 'Errore', type: 'error' });
    }
  };

  const attive = scalate?.filter(s => s.status === 'attiva') ?? [];
  const chiuse = scalate?.filter(s => s.status !== 'attiva') ?? [];

  return (
    <div className="space-y-8">
      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}

      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Scalate</h1>
        <p className="text-brand-muted text-sm mt-1">
          Sequenze all-in selezionate dal sistema AI — vinci o perdi tutto al primo passo sbagliato
        </p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Totale scalate" value={stats.totale} />
          <StatCard label="Tasso successo" value={`${stats.success_rate}%`} color={stats.success_rate >= 50 ? 'green' : 'red'} />
          <StatCard label="Profitto totale" value={`${stats.profitto_totale >= 0 ? '+' : ''}€ ${stats.profitto_totale.toFixed(2)}`} color={stats.profitto_totale >= 0 ? 'green' : 'red'} />
          <StatCard label="Profitto medio" value={`${stats.profitto_medio >= 0 ? '+' : ''}€ ${stats.profitto_medio.toFixed(2)}`} color={stats.profitto_medio >= 0 ? 'green' : 'red'} />
        </div>
      )}

      {/* Scalate attive */}
      {attive.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-base font-semibold text-brand-text">🎰 Scalate Attive ({attive.length})</h2>
          {attive.map(scalata => (
            <ScalataCard
              key={scalata.id}
              scalata={scalata}
              confirmingId={confirmingId}
              startAmount={startAmount}
              onStartAmountChange={setStartAmount}
              onConferma={() => handleConferma(scalata)}
              onToggleConfirm={() => setConfirmingId(confirmingId === scalata.id ? null : scalata.id)}
            />
          ))}
        </div>
      )}

      {/* Nessuna scalata attiva */}
      {attive.length === 0 && (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">
          <div className="text-4xl mb-3">🎰</div>
          <div className="font-medium">Nessuna scalata attiva</div>
          <div className="text-sm mt-1">
            Il sistema le rileva automaticamente dopo l&apos;analisi giornaliera alle 11:45
          </div>
        </div>
      )}

      {/* Storico */}
      {chiuse.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-base font-semibold text-brand-text">Storico scalate</h2>
          {chiuse.map(scalata => (
            <ScalataCard key={scalata.id} scalata={scalata} readonly />
          ))}
        </div>
      )}
    </div>
  );
}


function ScalataCard({
  scalata,
  readonly = false,
  confirmingId,
  startAmount,
  onStartAmountChange,
  onConferma,
  onToggleConfirm,
}: {
  scalata: Scalata;
  readonly?: boolean;
  confirmingId?: string | null;
  startAmount?: string;
  onStartAmountChange?: (v: string) => void;
  onConferma?: () => void;
  onToggleConfirm?: () => void;
}) {
  const isConfirming = confirmingId === scalata.id;
  const nextStep = scalata.steps.find(s => s.status === 'attivo' || s.status === 'in_attesa');
  const canConfirm = !readonly && scalata.status === 'attiva' && nextStep;

  // Calcola potential_win dai dati disponibili
  const potentialWin = scalata.potential_win ?? 0;

  return (
    <div className="bg-white rounded-card shadow-card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-brand-text">
              Scalata {scalata.total_steps} step
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLOR[scalata.status] ?? 'bg-gray-100'}`}>
              {scalata.status}
            </span>
          </div>
          <div className="text-xs text-brand-muted mt-0.5">
            Creata il {new Date(scalata.created_at).toLocaleDateString('it-IT')}
            {scalata.notes && ` · ${scalata.notes}`}
          </div>
        </div>
        <div className="text-right">
          {scalata.total_pnl != null ? (
            <div className={`text-lg font-semibold ${scalata.total_pnl >= 0 ? 'text-brand-green' : 'text-brand-red'}`}>
              {scalata.total_pnl >= 0 ? '+' : ''}€{scalata.total_pnl.toFixed(2)}
            </div>
          ) : potentialWin > 0 ? (
            <div className="text-right">
              <div className="text-xs text-brand-muted">Vincita potenziale</div>
              <div className="text-lg font-semibold text-brand-blue">€{potentialWin.toFixed(0)}</div>
            </div>
          ) : null}
        </div>
      </div>

      {/* Steps */}
      <div className="space-y-2">
        {scalata.steps.map(step => (
          <div
            key={step.id}
            className={`flex items-center gap-3 p-3 rounded-xl ${
              step.status === 'attivo' ? 'bg-blue-50 border border-brand-blue/20' : 'bg-brand-bg'
            }`}
          >
            <span className={`w-6 h-6 flex items-center justify-center rounded-full text-xs font-bold flex-shrink-0 ${STEP_COLOR[step.status] ?? 'bg-gray-100'}`}>
              {STEP_ICON[step.status] ?? step.step_number}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-brand-text truncate">{step.match_name}</div>
              <div className="text-xs text-brand-muted">
                {step.outcome} @ {step.odds.toFixed(2)} · {step.bookmaker}
                {step.match_date && ` · ${new Date(step.match_date).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}`}
              </div>
            </div>
            <div className="text-right flex-shrink-0">
              <div className="text-sm font-semibold text-brand-text">
                {step.stake > 0 ? `€${step.stake.toFixed(0)}` : '—'}
              </div>
              <div className="text-xs text-brand-muted">Step {step.step_number}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Conferma prossimo step */}
      {canConfirm && (
        <div className="pt-1">
          {!isConfirming ? (
            <button
              onClick={onToggleConfirm}
              className="w-full py-2.5 bg-brand-blue text-white rounded-xl text-sm font-medium hover:opacity-90 transition"
            >
              {scalata.current_step === 0
                ? '▶ Inizia scalata — imposta stake'
                : `▶ Conferma Step ${scalata.current_step + 1} — stake €${scalata.current_amount.toFixed(0)}`}
            </button>
          ) : (
            <div className="space-y-3 p-3 bg-brand-bg rounded-xl">
              <div className="text-sm font-medium text-brand-text">
                {scalata.current_step === 0
                  ? 'Con quanto vuoi iniziare la scalata?'
                  : `Step ${scalata.current_step + 1} — stake automatico: €${scalata.current_amount.toFixed(0)}`}
              </div>
              {scalata.current_step === 0 && (
                <div className="flex gap-2">
                  {['20', '30', '50'].map(v => (
                    <button
                      key={v}
                      onClick={() => onStartAmountChange?.(v)}
                      className={`flex-1 py-2 rounded-xl text-sm font-medium border transition ${
                        startAmount === v
                          ? 'bg-brand-blue text-white border-brand-blue'
                          : 'bg-white text-brand-muted border-brand-border hover:text-brand-text'
                      }`}
                    >
                      €{v}
                    </button>
                  ))}
                  <input
                    type="number"
                    min="1"
                    value={startAmount}
                    onChange={e => onStartAmountChange?.(e.target.value)}
                    className="flex-1 border border-brand-border rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue"
                    placeholder="Custom"
                  />
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={onToggleConfirm}
                  className="flex-1 py-2 bg-white border border-brand-border rounded-xl text-sm text-brand-muted hover:text-brand-text transition"
                >
                  Annulla
                </button>
                <button
                  onClick={onConferma}
                  className="flex-1 py-2 bg-brand-green text-white rounded-xl text-sm font-medium hover:opacity-90 transition"
                >
                  Conferma
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
