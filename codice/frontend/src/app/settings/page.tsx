'use client';

import { useState } from 'react';
import { api } from '@/lib/api';

export default function SettingsPage() {
  const [fetching, setFetching] = useState(false);
  const [fetchDone, setFetchDone] = useState(false);

  const handleFetchOdds = async () => {
    setFetching(true);
    try {
      await api.fetchOddsNow();
      setFetchDone(true);
      setTimeout(() => setFetchDone(false), 5000);
    } finally {
      setFetching(false);
    }
  };

  return (
    <div className="space-y-8 max-w-lg">
      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Impostazioni</h1>
        <p className="text-brand-muted text-sm mt-1">Operazioni manuali e informazioni sul piano</p>
      </div>

      {/* Fetch manuale */}
      <div className="bg-white rounded-card shadow-card p-6 space-y-4">
        <h2 className="text-base font-semibold text-brand-text">Aggiorna quote ora</h2>
        <p className="text-sm text-brand-muted">
          Il sistema aggiorna le quote automaticamente ogni mattina alle 07:45.
          Usa questo bottone solo se vuoi un aggiornamento immediato.
          <br />
          <span className="text-brand-orange font-medium">Usa 10 richieste API su 500 disponibili al mese.</span>
        </p>

        {fetchDone && (
          <div className="p-3 bg-green-50 border border-green-200 rounded-xl text-sm text-brand-green font-medium">
            ✓ Aggiornamento avviato — pronto in 1-2 minuti.
          </div>
        )}

        <button
          onClick={handleFetchOdds}
          disabled={fetching || fetchDone}
          className="px-5 py-2.5 bg-brand-bg border border-brand-border text-brand-text rounded-xl text-sm font-medium hover:bg-white disabled:opacity-50 transition"
        >
          {fetching ? 'Avviato…' : '🔄 Aggiorna quote adesso'}
        </button>
      </div>

      {/* Info piano */}
      <div className="bg-white rounded-card shadow-card p-6">
        <h2 className="text-base font-semibold text-brand-text mb-3">Piano API attivo</h2>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-brand-muted">The Odds API</span>
            <span className="text-brand-text font-medium">Free — 500 req/mese</span>
          </div>
          <div className="flex justify-between">
            <span className="text-brand-muted">Fetch automatici</span>
            <span className="text-brand-text font-medium">1/giorno × 10 comp = 300/mese</span>
          </div>
          <div className="flex justify-between">
            <span className="text-brand-muted">Margine disponibile</span>
            <span className="text-brand-green font-medium">200 req/mese per fetch manuali</span>
          </div>
          <div className="flex justify-between">
            <span className="text-brand-muted">Anthropic (Claude)</span>
            <span className="text-brand-text font-medium">Pay-per-use (~$0.01/analisi)</span>
          </div>
          <div className="flex justify-between">
            <span className="text-brand-muted">Bookmaker monitorati</span>
            <span className="text-brand-text font-medium">Bet365 · Eplay24</span>
          </div>
        </div>
      </div>
    </div>
  );
}
