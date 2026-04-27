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
                    match_date, sport, status, external_id, raw_stats (JSONB)
match_odds          id, match_id, bookmaker, market, outcome, odds, fetched_at
betting_opportunities  id, match_id, market, outcome, bookmaker, best_odds,
                       model_probability, consensus_votes (JSONB), uncertainty_score,
                       expected_value, tier (S/A/B/C), edge, bet_type, confidence_level,
                       scalata_id, composite_bet_id, reference_source, status,
                       expires_at, uncertainty_blocked
bets                id, opportunity_id, stake, actual_odds, status, result, profit_loss
composite_bets      id, tipo (doppia/multipla), combined_odds, status
scalate             id, nome, stato, step_corrente, quota_totale
agent_runs          id, match_id, agent_name, status, output_data (JSONB), tokens_used
bankroll            id, balance, total_staked, total_profit
users               id, email, hashed_password, is_active, is_admin
players             NBA player props
news                notizie partite
context             SystemHealth
runs                log esecuzioni pipeline
```

---

## Pipeline AI (flusso principale)

```
1. fetch_all_odds()        → scarica quote da The Odds API (2x/giorno)
2. fetch_upcoming_stats()  → scarica stats, ELO, infortuni, meteo
3. run_daily_pipeline()    → per ogni match:
   a. compute_no_vig()     → probabilità vere da Pinnacle (matematica pura)
   b. find_value_opportunities() → EV > 3% vs bookmaker soft
   c. Tutti gli agenti in parallelo (condizionali ai dati disponibili):
        Stats, Odds, Form, H2H, Injury, News, Weather → agent_signal 0-1
        UncertaintyAgent → gate qualitativo (blocca se score ≥ 0.70)
      Se agent_signal < 0.30 con ≥2 agenti → blocca (forte disaccordo)
   d. classify_tier()      → S/A/B/C + bet_type
   e. compute_reliability() → affidabilità 0-100%
      modulata da: EV × uncertainty × bookmaker_agreement × ELO × timing × agent_signal
   f. save BettingOpportunity + send Telegram alert
```

---

## Celery Tasks

### On-Demand (Telegram Triggered)
| Task | Comando | Finestra | Cosa fa |
|------|---------|----------|---------|
| `fetch_complete_sport_data` | `/ricerca*` | **20h** | Fetch odds + stats per sport |
| `run_daily_pipeline` | `/ricerca*` | **20h** | AI analysis + alert Telegram |

**Nota:** Tutte le ricerche sport-specific (`/ricerca_calcio`, `/ricerca_nba`, `/ricerca_tennis`, `/ricerca`) usano finestra di **20 ore** per evitare intasamento e mantener sistema focused.

### Automated Scheduled (Celery Beat)
| Task | Frequenza | Cosa fa |
|------|-----------|---------|
| `sync_competitions` | giornaliero | aggiorna lista competizioni |
| `settle_finished_bets` | ogni ora | registra risultati scommesse |
| `run_health_check` | ogni 30min | stato sistema |
| `update_clv` | giornaliero | aggiorna CLV per bookmaker |
| `expire_waiting_opportunities` | ogni ora | scade opportunità vecchie |
| `monitor_odds_movement` | ogni 2h | monitora movimenti quote |
| `calibrate_clv` | settimanale | calibra blacklist bookmaker |

---

## API Routers

| Router | Path base | Funzione |
|--------|-----------|----------|
| auth | `/api/auth` | login, JWT |
| matches | `/api/matches` | lista partite, quote |
| opportunities | `/api/opportunities` | value bet trovate |
| bets | `/api/bets` | scommesse piazzate |
| competitions | `/api/competitions` | leghe/competizioni |
| settings | `/api/settings` | configurazione sistema |
| scalate | `/api/scalate` | gestione scalate |
| telegram_webhook | `/api/telegram` | comandi bot Telegram |
| intelligence | `/api/intelligence` | agenti AI on-demand |
| analytics | `/api/analytics` | statistiche e report |
| bankroll | `/api/bankroll` | gestione bankroll |

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

## Stato Attuale (2026-04-27 07:40 UTC)

**✅ DEPLOY COMPLETATO — Sistema Live**

**Backend & Architettura:**
- ✅ Architettura on-demand completamente implementata
- ✅ Zero automatic Celery Beat schedules (fetch solo via Telegram)
- ✅ Sport-specific commands: /ricerca_calcio (35/mese), /ricerca_nba (35/mese), /ricerca_tennis (35/mese)
- ✅ Player props abilitati per football e basketball
- ✅ NTP sync configurato (previene clock drift)
- ✅ API quota sotto controllo (~1200 req/mese vs 2000 budget, 40% margine)

**Telegram Bot UI (2026-04-27):**
- ✅ Keyboard con tasti pronti per comandi (/help → mostra pulsanti)
- ✅ Comando /pulisci aggiunto (per chat cleanup manuale)
- ✅ Interfaccia minimale senza separatori
- ✅ Logging dettagliato nei task di ricerca (diagnostica)
- ✅ Bot commands registrati: 13 comandi

**Deployment:**
- ✅ Live su Hetzner 204.168.227.86 (Ubuntu 22.04, CX23)
- ✅ Docker Compose con tutti i servizi (backend, worker, beat, redis, postgres, frontend)
- ✅ Health check: tutti i servizi operativi
- ✅ File inutili eliminati (test, script vecchi, doc obsoleta)
- ✅ Test webhook /ricerca_calcio eseguito con successo
- ✅ Celery worker elaborando task
- ✅ No 429 rate limit errors

**Prossimi Step:**
- ✓ Test operazionale /ricerca_calcio completato
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
