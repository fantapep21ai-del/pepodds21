# CLAUDE.md — pepodds21

## Project Overview

Sistema AI per identificare value bet sportive tramite analisi no-vig su quote Pinnacle.
Pipeline: fetch quote → calcolo matematico EV → gate qualitativo con Claude → alert Telegram.
Utente finale: Giuseppe, che decide se piazzare la scommessa dopo aver ricevuto l'alert.

Priorità assoluta: affidabilità della pipeline e costo token minimo.

## Tech Stack

- Backend: FastAPI (Python, async) — porta 8000
- Database: PostgreSQL 16 (async via SQLAlchemy)
- Cache/Broker: Redis 7 (con password — usare sempre `redis_url_with_auth`)
- Task queue: Celery worker + beat
- Frontend: Next.js + Tailwind CSS — porta 3000
- Deploy: Docker Compose
- Package manager: pip / pnpm
- AI: Anthropic Claude (haiku per agenti analisi, sonnet riservato)

## Coding Conventions

- Tutto async/await — mai funzioni sincrone nei router FastAPI
- UUID come primary key su tutte le tabelle
- JSONB per dati flessibili (raw_stats, consensus_votes, output_data)
- Import relativi dentro `app/` — mai path assoluti
- Nomi file snake_case, classi PascalCase
- Router: schema Pydantic sopra gli endpoint, nello stesso file
- Commenti in italiano per logica di business, inglese per commenti tecnici
- Nessun print() — solo logger = logging.getLogger(__name__)
- Alembic per tutte le migrazioni schema — mai modificare tabelle direttamente

## Never Do This

- Mai usare `settings.redis_url` — usare sempre `settings.redis_url_with_auth`
- Mai esporre porte Redis (6379) o PostgreSQL (5432) nel docker-compose
- Mai modificare la pipeline principale in `agents/pipeline.py` senza mostrare il piano prima
- Mai aggiungere dipendenze Python senza chiedere
- Mai usare `--reload` in produzione (solo in dev)
- Mai hardcodare API key o password nel codice
- Mai toccare i file in `alembic/versions/` — creare sempre nuova migration
- Mai usare `db.execute()` con SQL raw dove esiste già un query builder
- Mai aggiungere console.log nel frontend

## File Structure

```
codice/
  backend/
    app/
      agents/       ← pipeline AI, agenti, consensus, tiers
      api/routers/  ← endpoint REST (un file per dominio)
      db/models/    ← modelli SQLAlchemy (un file per entità)
      services/     ← client API esterni, Kelly, settlement, Telegram
      workers/      ← Celery tasks e celery_app.py
      config.py     ← tutte le variabili env (Settings)
      main.py       ← FastAPI app, lifespan, middleware
    alembic/        ← migrazioni DB
  frontend/
    src/
      app/          ← pagine Next.js (App Router)
      components/   ← componenti riutilizzabili
      lib/          ← utility e client API
      types/        ← TypeScript types condivisi
  docker-compose.yml
  .env              ← NON committare mai
```

Non creare nuove cartelle top-level senza chiedere.

## Current Goals

✅ **DEPLOY COMPLETATO (2026-04-27)**
- BUG #1: UNIQUE INDEX parziale (race condition fix) ✅
- BUG #2: Fuzzy matching WRatio + NFKD (team settlement) ✅
- BUG #3: Retry cap MAX_RETRIES=5 + exponential backoff ✅
- Tutte i Celery task girono correttamente ✅
- Redis password-protected, porte non esposte ✅

**Prossimo:** Monitorare logs per team matching success ("H2H MATCH WRatio") e retry caps.

## Important Context

- Redis era esposto pubblicamente (porta 6379 aperta) → segnalazione BSI/CERT-Bund → server cancellato
- Fix applicato: `--requirepass ${REDIS_PASSWORD}`, porte DB/Redis non più esposte, `redis_url_with_auth` ovunque
- `REDIS_PASSWORD` deve essere aggiunto al `.env` prima del deploy (`openssl rand -hex 32`)
- Il sistema usa Pinnacle come riferimento "sharp" — le sue quote sono la verità matematica
- `UncertaintyAgent` è l'unico agente che gira in automatico — gli altri 7 sono on-demand
- CLV blacklist: bookmaker con performance negative vengono esclusi automaticamente via Redis
- Freshness quote: solo quote < 6h vengono usate nella pipeline

**BUG FIXES (2026-04-27):**
- **BUG #1:** Migration 005 applica CREATE UNIQUE INDEX parziale su (match_id, market, outcome) con WHERE status IN ('pending', 'in_attesa', 'bet_placed'). Impedisce race condition — due worker non creano duplicati. Indice parziale permette opportunità vecchie duplicate.
- **BUG #2:** settlement_service.py usa WRatio fuzzy matching con NFKD normalization per team name settlement. Threshold dinamico: 90% nomi corti (<8 char), 85% lunghi. Gestisce accenti, abbreviazioni, case-insensitivity.
- **BUG #3:** settlement_service.py ha retry logic con MAX_RETRIES=5 cap e exponential backoff (2^retry_count secondi). Evita hammer su The Odds API se score non disponibile. Fallisce gracefully dopo 5 tentativi.
- **Bonus Fix:** PnL calculation (linea 170) converte float(bet.odds) per evitare Decimal multiplication TypeError.

## Communication Style

- Risposte brevi — se faccio una modifica, dico cosa ho cambiato e perché in una riga
- Se vedo un bug o problema architetturale non richiesto, lo segnalo prima di procedere
- Mostro sempre il piano prima di modificare `pipeline.py`, modelli DB o docker-compose
