'use client';

import { useState, useMemo } from 'react';
import useSWR from 'swr';
import { api, Match, MatchDetail, Opportunity, Odds } from '@/lib/api';
import Toast from '@/components/Toast';

const SPORTS = ['all', 'football', 'tennis', 'basketball'];
const SPORT_EMOJI: Record<string, string> = { football: '⚽', tennis: '🎾', basketball: '🏀' };
const SPORT_LABEL: Record<string, string> = { football: 'Calcio', tennis: 'Tennis', basketball: 'Basket' };

const CONFIDENCE_COLOR: Record<string, string> = {
  alta:    'bg-green-100 text-brand-green',
  normale: 'bg-blue-50 text-brand-blue',
  bassa:   'bg-yellow-50 text-yellow-700',
};
const CONFIDENCE_ICON: Record<string, string> = { alta: '🔥', normale: '✅', bassa: '~' };

// Bookmaker con licenza ADM accessibili in Italia
const ALLOWED_BOOKMAKERS = [
  'williamhill', 'sport888', 'leovegas_se', 'marathonbet',
  'betfair_ex_eu', 'unibet_eu', 'unibet_fr', 'unibet_se', 'unibet_nl',
  'betsson', 'codere_it',
];
const BOOKMAKER_LABEL: Record<string, string> = {
  williamhill:   'William Hill',
  sport888:      '888sport',
  leovegas_se:   'LeoVegas',
  marathonbet:   'Marathonbet',
  betfair_ex_eu: 'Betfair',
  unibet_eu:     'Unibet',
  unibet_fr:     'Unibet',
  unibet_se:     'Unibet',
  unibet_nl:     'Unibet',
  betsson:       'Betsson',
  codere_it:     'Codere',
};

type SortKey = 'orario' | 'sport';

interface ToastState { message: string; type: 'success' | 'error' }

export default function MatchesPage() {
  const [sport, setSport] = useState('all');
  const [onlyValue, setOnlyValue] = useState(false);
  const [sortBy, setSortBy] = useState<SortKey>('orario');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  const params: Record<string, string> = { only_today: 'true' };
  if (sport !== 'all') params.sport = sport;
  if (onlyValue) params.only_value = 'true';

  const { data: matches, isLoading } = useSWR<Match[]>(
    `matches-${sport}-${onlyValue}`,
    () => api.getMatches(params),
    { refreshInterval: 60000 }
  );

  const sorted = useMemo(() => {
    if (!matches) return [];
    return [...matches].sort((a, b) => {
      if (sortBy === 'orario') return new Date(a.match_date).getTime() - new Date(b.match_date).getTime();
      if (sortBy === 'sport') return a.sport.localeCompare(b.sport);
      return 0;
    });
  }, [matches, sortBy]);

  const today = new Date().toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' });

  return (
    <div className="space-y-6">
      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}

      <div>
        <h1 className="text-3xl font-semibold text-brand-text">Partite di oggi</h1>
        <p className="text-brand-muted text-sm mt-1 capitalize">{today}</p>
      </div>

      {/* Controlli */}
      <div className="flex flex-wrap gap-2 items-center">
        {SPORTS.map(s => (
          <button
            key={s}
            onClick={() => setSport(s)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-xl text-sm font-medium transition ${sport === s ? 'bg-brand-blue text-white' : 'bg-white text-brand-muted hover:text-brand-text'}`}
          >
            {s === 'all' ? 'Tutti' : `${SPORT_EMOJI[s]} ${SPORT_LABEL[s]}`}
          </button>
        ))}

        <div className="w-px h-5 bg-brand-border mx-1" />

        <span className="text-xs text-brand-muted">Ordina:</span>
        {(['orario', 'sport'] as SortKey[]).map(k => (
          <button
            key={k}
            onClick={() => setSortBy(k)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-xl text-sm font-medium transition ${sortBy === k ? 'bg-brand-text text-white' : 'bg-white text-brand-muted hover:text-brand-text'}`}
          >
            {k === 'orario' ? '🕐 Orario' : '⚽ Sport'}
          </button>
        ))}

        <div className="w-px h-5 bg-brand-border mx-1" />

        <button
          onClick={() => setOnlyValue(v => !v)}
          className={`flex-shrink-0 px-3 py-1.5 rounded-xl text-sm font-medium transition ${onlyValue ? 'bg-amber-400 text-amber-900 font-semibold' : 'bg-white text-brand-muted hover:text-brand-text'}`}
        >
          🎯 Solo value
        </button>
      </div>

      {isLoading ? (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">Caricamento…</div>
      ) : !sorted.length ? (
        <div className="bg-white rounded-card shadow-card p-8 text-center text-brand-muted">
          <div className="text-3xl mb-2">{onlyValue ? '🎯' : '📅'}</div>
          <div className="font-medium">
            {onlyValue ? 'Nessuna giocata identificata oggi' : 'Nessuna partita oggi'}
          </div>
          <div className="text-sm mt-1">
            {onlyValue
              ? "Il sistema analizza automaticamente tutte le partite alle 11:45 e alle 19:00"
              : "Le partite vengono aggiornate ogni mattina alle 11:30"}
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {sorted.map(m => (
            <MatchCard
              key={m.id}
              match={m}
              selected={selectedId === m.id}
              onSelect={() => setSelectedId(selectedId === m.id ? null : m.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}


function MatchCard({ match, selected, onSelect }: {
  match: Match;
  selected: boolean;
  onSelect: () => void;
}) {
  const { data: detail } = useSWR<MatchDetail>(
    selected ? `match-${match.id}` : null,
    () => api.getMatch(match.id),
  );
  const { data: opps } = useSWR<Opportunity[]>(
    selected ? `match-opps-${match.id}` : null,
    () => api.getMatchOpportunities(match.id),
  );

  const time = new Date(match.match_date).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });

  // Filtra quote: solo Bet365 e Eplay24
  const filteredOdds = detail?.best_odds?.filter(
    o => ALLOWED_BOOKMAKERS.includes(o.bookmaker.toLowerCase())
  ) ?? [];

  // Raggruppa per bookmaker
  const oddsByBookmaker = ALLOWED_BOOKMAKERS.reduce<Record<string, Odds[]>>((acc, bk) => {
    const bkOdds = filteredOdds.filter(o => o.bookmaker.toLowerCase() === bk);
    if (bkOdds.length > 0) acc[bk] = bkOdds;
    return acc;
  }, {});

  const rowBg = match.has_value_bet
    ? 'bg-yellow-200 border border-yellow-400'
    : 'bg-white';

  return (
    <div className={`rounded-card shadow-card overflow-hidden transition-all ${rowBg}`}>
      <button
        onClick={onSelect}
        className="w-full text-left p-4 flex items-center gap-4 hover:brightness-95 transition"
      >
        <div className="text-2xl flex-shrink-0">{SPORT_EMOJI[match.sport] ?? '🏟'}</div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-brand-text truncate">{match.display_name}</span>
            {match.has_value_bet && (
              <span className="flex-shrink-0 text-xs px-2 py-0.5 rounded-full bg-yellow-400 text-yellow-900 font-semibold">
                🎯 Value bet
              </span>
            )}
          </div>
          <div className="text-xs text-brand-muted mt-0.5">{time}</div>
        </div>

        <svg
          className={`flex-shrink-0 w-4 h-4 text-brand-muted transition-transform ${selected ? 'rotate-90' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
      </button>

      {selected && (
        <div className="border-t border-yellow-300/30 bg-white">
          {/* Opportunità di valore */}
          {opps && opps.length > 0 && (
            <div className="p-4 space-y-2">
              <div className="text-xs font-semibold text-brand-muted uppercase tracking-wide mb-3">
                Giocate identificate ({opps.length})
              </div>
              {opps.map(opp => (
                <div key={opp.id} className="flex items-center gap-3 p-3 bg-green-50 rounded-xl">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-brand-text">
                      {opp.outcome} <span className="text-brand-muted font-normal">@</span>{' '}
                      <span className="font-bold text-brand-blue">{opp.best_odds.toFixed(2)}</span>
                    </div>
                    <div className="text-xs text-brand-muted mt-0.5">
                      {opp.market} · {BOOKMAKER_LABEL[opp.bookmaker] ?? opp.bookmaker}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1 flex-shrink-0">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${CONFIDENCE_COLOR[opp.confidence_level] ?? 'bg-gray-100'}`}>
                      {CONFIDENCE_ICON[opp.confidence_level]} {opp.confidence_level}
                    </span>
                    <span className="text-xs text-brand-muted">
                      EV {opp.expected_value >= 0 ? '+' : ''}{(opp.expected_value * 100).toFixed(1)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {opps && opps.length === 0 && (
            <div className="p-4 text-sm text-brand-muted text-center">
              Nessuna quota di valore identificata — analisi automatica alle 11:45 e 19:00
            </div>
          )}

          {/* Quote per bookmaker */}
          {Object.keys(oddsByBookmaker).length > 0 && (
            <div className="p-4 border-t border-brand-border space-y-4">
              <div className="text-xs font-semibold text-brand-muted uppercase tracking-wide">
                Quote disponibili
              </div>
              {Object.entries(oddsByBookmaker).map(([bk, odds]) => (
                <div key={bk}>
                  <div className="text-xs font-medium text-brand-text mb-2">
                    {BOOKMAKER_LABEL[bk] ?? bk}
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {odds.slice(0, 30).map((o, i) => (
                      <div key={i} className="flex items-center justify-between p-2.5 bg-brand-bg rounded-xl">
                        <div className="text-xs min-w-0">
                          <div className="font-medium text-brand-text truncate">{o.outcome}</div>
                          <div className="text-brand-muted truncate">{o.market}</div>
                        </div>
                        <div className="text-sm font-bold text-brand-text ml-2 flex-shrink-0">
                          {o.odds.toFixed(2)}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {Object.keys(oddsByBookmaker).length === 0 && opps !== undefined && (
            <div className="p-4 border-t border-brand-border text-sm text-brand-muted text-center">
              Quote aggiornate automaticamente alle 09:30 — ricontrolla più tardi
            </div>
          )}
        </div>
      )}
    </div>
  );
}
