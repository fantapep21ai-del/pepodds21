# pepodds21 — Contesto Backend per Claude

**STATUS:** 🟢 **ACTIVE** — Production live + optimize phase  
**PRIORITY:** Max (pepodds21 is primary project)  
**Budget:** €2.54/mese su Claude (ultra-lean)

Leggi questo file prima di iniziare a lavorare su pepodds21.
Ti dà la topologia completa del sistema senza esplorare il codice.

---

## Stack

| Layer | Tecnologia | Porta |
|-------|-----------|-------|
| Backend API | FastAPI (Python, async) | 8000 |
| Database | PostgreSQL 16 | interno (non esposto) |
| Cache / Broker | Redis 7 (con password) | interno (non esposto) |
| Task queue | Celery worker + beat | — |
| Frontend | Next.js + Tailwind CSS | 3000 |
| Deploy | Docker Compose | — |

**Path progetto:** `/Users/giuseppesiracusa/Desktop/Pepodds21/codice/`

---

## Schema DB — Tabelle principali

```
competitions        id, name, sport, tier, weight, external_id, odds_api_key
matches             id, competition_id, home_team, away_team, player_a, player_b,
                    match_date, sport, status, external_id, raw_stats (JSONB),
                    analysis_status, analysis_reason
match_odds          id, match_id, bookmaker, market, outcome, odds, fetched_at, is_live
betting_opportunities  id, match_id, market, outcome, bookmaker, best_odds,
                       model_probability, consensus_votes (JSONB), uncertainty_score,
                       expected_value, tier (S/A/B/C), edge, bet_type, confidence_level,
                       reference_source, status, expires_at, uncertainty_blocked,
                       rejection_reason
bets                id, opportunity_id, bookmaker, market, outcome, odds, stake,
                    placed_at, status, result, pnl, settled_at, closing_odds, clv,
                    actual_odds, notes
agent_votes         id, match_id, agent_name, prediction, confidence, output_data (JSONB)
agent_scores        id, agent_name, brier_score, accuracy, predictions_count
users               id, email, hashed_password, is_active, is_admin
players             NBA player props
news                notizie partite
context             SystemHealth
pipeline_runs       id, sport, started_at, finished_at, status, matches_processed,
                    opportunities_found
```

**DELETED TABLES (2026-04):**
- `bankroll` — User now decides stake per bet
- `composite_bets` — Only singole (no multi-leg)
- `scalate` — No scalata/accumulator strategy

---

## Pipeline AI (flusso principale)

**Trigger:** User sends `/ricerca_calcio`, `/ricerca_nba`, or `/ricerca_tennis` on Telegram

```
1. Store command_timestamp in Redis
2. fetch_complete_sport_data()   → scarica quote + stats per sport (18h from command)
3. run_daily_pipeline()          → per ogni match nel timeframe:
   a. Load matches with 18h window (from command_timestamp to command_timestamp + 18h)
   b. Filter by allowed leagues (sport-specific)
   c. Per ogni match:
      i.   compute_no_vig()        → probabilità vere da Pinnacle (sharp odds)
      ii.  find_value_opportunities() → EV usando threshold dinamico:
           - Odds 1.4-3.0: min EV = 3.5%
           - Odds > 3.0: min EV = 8.0%
           - Odds < 1.4: EXCLUDED
      iii. Tutti gli agenti in parallelo (if data available):
           Stats, Odds, Form, H2H, Injury, News, Weather → agent_signal 0-1
           UncertaintyAgent → gate qualitativo (blocca se score ≥ 0.70)
      iv.  classify_tier()      → S/A/B/C (quality tiers)
      v.   compute_reliability() → affidabilità per consensus
   d. Collect ALL qualifying singole per match
   e. Send Telegram report showing all matches with analysis status + all singole
4. User uses /opportunita to accept/reject singole + select stake
5. System creates Bet records with status="open"
6. Automatic settlement every 2h via controlla task
```

---

## Celery Tasks

### On-Demand (Telegram Triggered)
| Task | Comando | Finestra | Cosa fa |
|------|---------|----------|---------|
| `fetch_complete_sport_data` | `/ricerca_calcio` | **18h from command** | Fetch odds + stats |
| `run_daily_pipeline` | (auto-triggered) | **18h from command** | AI analysis + alert |

**Nota:** Solo sport-specific: `/ricerca_calcio`, `/ricerca_nba`, `/ricerca_tennis`  
Finestra è **relativa al comando** (non da server boot): NOW() to NOW() + 18h dal momento dell'utente

### Automated Scheduled (Celery Beat)
| Task | Frequenza | Cosa fa |
|------|-----------|---------|
| `controlla` | ogni 2h | Settle bets completati (match finiti) |
| `sync_competitions` | giornaliero | Aggiorna lista competizioni + sync The Odds API |
| `run_health_check` | ogni 30min | Verifica stato Redis + PostgreSQL |
| `update_clv` | giornaliero | Calcola CLV per scommesse liquidate |
| `expire_waiting_opportunities` | ogni ora | Scade opportunità old (match già iniziato) |
| `monitor_uncertainty` | ogni 3h | Monitora agenti incompleti |

---

## API Routers

| Router | Path base | Funzione |
|--------|-----------|----------|
| auth | `/api/auth` | login, JWT |
| matches | `/api/matches` | lista partite, quote |
| opportunities | `/api/opportunities` | value bet trovate |
| bets | `/api/bets` | scommesse piazzate (status, P&L) |
| competitions | `/api/competitions` | leghe/competizioni |
| results | `/api/results` | settlement, results match |
| telegram_webhook | `/api/telegram` | comandi bot Telegram |
| intelligence | `/api/intelligence` | agenti AI on-demand |
| analytics | `/api/analytics` | statistiche (P&L, ROI, CLV) |

**DELETED ROUTERS (2026-04):**
- `/api/scalate` — No multi-leg strategy
- `/api/bankroll` — User decides stake per bet
- `/api/settings` (partially) — No bankroll config

---

## Agenti AI (8) — tutti attivi in parallelo

| Agente | Segnale | Condizione esecuzione |
|--------|---------|----------------------|
| StatsAgent | xG, possession, shots on target | se stats disponibili |
| OddsAgent | line movement, overround, market efficiency | se ≥4 bookmaker |
| FormAgent | ultimi 5 risultati, streak, standings | se form_stats disponibili |
| H2HAgent | storico testa a testa (peso più alto sugli ultimi 3 anni) | se h2h disponibile |
| InjuryAgent | infortuni e sospensioni (-5-15% prob per top player out) | se injuries disponibili |
| NewsAgent | motivazione, pressione tecnico, derby, fattori psicologici | se news disponibili |
| WeatherAgent | vento >30km/h, pioggia → meno gol (solo calcio) | se weather + calcio |
| UncertaintyAgent | gate qualitativo finale — sempre eseguito | sempre |

Tutti girano in parallelo (asyncio.gather). Il loro segnale combinato (agent_signal 0-1)
modula l'affidabilità. Se agent_signal < 0.30 con ≥2 agenti → opportunità bloccata.

---

## Servizi Esterni

| Servizio | Variabile env | Uso |
|----------|--------------|-----|
| The Odds API | `ODDS_API_KEY` (+ _2 _3 _4) | quote principali, rotazione 4 chiavi |
| OddsPapi | `ODDSPAPI_KEY` | include Bet365/Eplay24, 250 req/mese |
| API-Football | `API_FOOTBALL_KEY` | statistiche calcio |
| football-data.org | `FOOTBALL_DATA_KEY` | stats calcio alternativo |
| Anthropic | `ANTHROPIC_API_KEY` | agenti AI |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | alert e comandi |
| ClubElo | gratuito, no key | rating ELO calcio (fallback stats) |

---

## Convenzioni Codice

- Tutto async/await (FastAPI + SQLAlchemy async)
- UUID come primary key su tutte le tabelle
- JSONB per dati flessibili (raw_stats, consensus_votes)
- Redis con autenticazione (`redis_url_with_auth` — NON usare `redis_url` direttamente)
- Migrations con Alembic (`alembic/`)
- Modelli in `app/db/models/`, router in `app/api/routers/`
- I worker Celery usano `_run(coro)` per eseguire codice async

---

## Errori Comuni

| Errore | Causa | Fix |
|--------|-------|-----|
| Redis connection refused | Password mancante in URL | usare `settings.redis_url_with_auth` |
| 401 su Celery task | Token JWT scaduto nel context | rigenerare token |
| `No sharp odds for match` | Pinnacle non ha quote per quella partita | normale, skip automatico |
| `Uncertainty gate` | UncertaintyAgent score ≥ 0.70 | normale, opportunità bloccata |
| Migration error | Schema non aggiornato | `alembic upgrade head` |

---

## Stato Attuale (2026-04-30 09:15 UTC)

**✅ SINGLE VALUE BET FINDER IMPLEMENTATO**

**Phase Completate (2026-04):**
- ✅ Phase 1: Removed bankroll + Kelly parameters
- ✅ Phase 2: Removed scalata + multi-leg architecture
- ✅ Phase 3: Dynamic EV thresholds (3.5% for 1.4-3.0, 8% for >3.0)
- ✅ Phase 4: Simplified to sport-specific commands only
- ✅ Phase 5: Show ALL qualifying singole per match (not just 1 best)
- ✅ Phase 6: Relative timeframe (18h from command, not server boot)

**Architecture Changes:**
- ✅ /ricerca (general) → DELETED
- ✅ Only /ricerca_calcio, /ricerca_nba, /ricerca_tennis remain
- ✅ NBA Playoffs support added (until 2026-06-30)
- ✅ Dynamic EV gating: exclude odds < 1.4, apply thresholds per quote range
- ✅ Report now shows: all matches analyzed + all singole per match
- ✅ Settlement: automatic via controlla task (every 2h)

**Telegram Bot Commands:**
- ✅ /ricerca_calcio — Football analysis
- ✅ /ricerca_nba — Basketball + Playoffs
- ✅ /ricerca_tennis — Tennis Grand Slams + Masters 1000
- ✅ /opportunita — Accept/reject singole with stake selection
- ✅ /bilancio — Show P&L, ROI, win rate, CLV
- ✅ /stats — Show pipeline metrics
- ✅ /oggi — Today's matches
- ✅ /attesa — In-progress opportunities
- ✅ /help — Command list with buttons

**Infrastructure Ready:**
- ✅ User acceptance/rejection interface (/opportunita)
- ✅ Stake selection (€5, €10, €20, €30, €50, €100+)
- ✅ Automatic settlement every 2h (controlla task)
- ✅ P&L tracking + statistics reporting (/bilancio)
- ✅ CLV monitoring for edge validation

**Deferred Decisions (user input needed):**
- ❓ Bookmaker selection strategy (min count, blacklist criteria)
- ❓ Player props integration (same pipeline for giocatori singoli?)
- ❓ Settlement: confirm automatic 2h schedule is preferred
- ✓ Sistema in produzione e monitorando
- ⏳ Verificare alert Telegram per nuove opportunità (si attendono risultati)
- ⏳ Monitorare API quota settimanale (attualmente 1200/2000 req)

## Fix applicati (2026-04-24)

| File | Fix |
|------|-----|
| `config.py` | Aggiunto `scalata_start_amount` (configurabile via env); rimosso `redis_url` per prevenire uso senza auth |
| `pipeline.py` | `_get_clv_blacklist()` ora async via `asyncio.to_thread` — non blocca più l'event loop |
| `celery_app.py` | Aggiunto `sync_competitions` al beat_schedule (mancava — competizioni mai auto-sincronizzate) |
| `tasks.py` | Rimosso ~130 righe dead code `_fetch_oddspapi_async`; `fetch_all_stats` ora copre tutti gli sport; `scalata_start_amount` da settings |
| `settlement_service.py` | Aggiunta normalizzazione nomi squadra (`_normalize_name`) — risolve mismatch "Man City" vs "Manchester City" |
| `consensus.py` | `UNCERTAINTY_GATE` allineato a 0.70 (era 0.55 — incoerente con pipeline) |
| `tiers.py` | Docstring corretta (gate 0.70, non 0.55) |
| `script/deploy.sh` | Riscritto con rsync — include tutti i file, nessun file dimenticato; lancia alembic automaticamente |
| `script/migrate_db.sh` | Usa `alembic upgrade head` invece di SQL raw |
| `script/logs.sh` | IP parametrizzato, accetta servizio come argomento |
| `script/trigger.sh` | IP parametrizzato, supporta più task (pipeline/odds/stats/settle/clv) |
| `script/upload.sh` | Riscritto per hotfix singolo file con istruzioni chiare |
