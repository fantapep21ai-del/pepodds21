'use client';

import { useState, useEffect } from 'react';

interface Props {
  onConfirm: (stake: number) => void;
  onCancel: () => void;
}

export default function StakeModal({ onConfirm, onCancel }: Props) {
  const [stake, setStake] = useState('');

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onCancel]);

  const val = parseFloat(stake);
  const valid = val > 0 && !isNaN(val);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={onCancel} />
      <div className="relative bg-white rounded-card shadow-xl w-full max-w-sm p-6 space-y-4">
        <h3 className="text-base font-semibold text-brand-text">Conferma scommessa</h3>

        <div className="space-y-2">
          <label className="text-sm font-medium text-brand-text">Stake (€)</label>
          <input
            autoFocus
            type="number"
            min="1"
            step="1"
            value={stake}
            onChange={e => setStake(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && valid && onConfirm(val)}
            placeholder="Inserisci stake…"
            className="w-full border border-brand-border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue"
          />
        </div>

        <div className="flex gap-3 pt-1">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium bg-brand-bg text-brand-muted hover:text-brand-text transition"
          >
            Annulla
          </button>
          <button
            onClick={() => valid && onConfirm(val)}
            disabled={!valid}
            className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium bg-brand-green text-white hover:opacity-90 disabled:opacity-40 transition"
          >
            Conferma
          </button>
        </div>
      </div>
    </div>
  );
}
