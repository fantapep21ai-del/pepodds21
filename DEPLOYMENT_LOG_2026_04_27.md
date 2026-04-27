# Deployment Log — 2026-04-27

## Deploy Completato ✅

**Data/Ora:** 2026-04-27 07:39 UTC  
**Server:** Hetzner 204.168.227.86 (Ubuntu 22.04, CX23)  
**Versione:** pepodds21 v0.1.0 (On-Demand Architecture)

---

## Status Finale

### Servizi Operativi ✅
- Backend (FastAPI) — Porta 8000 — **Healthy**
- Celery Worker — Task processing — **Online**
- Celery Beat — Scheduler — **Online**
- PostgreSQL 16 — Database — **Healthy**
- Redis 7 — Cache/Broker — **Healthy**
- Frontend (Next.js) — Porta 3000 — **Online**

### Health Check
```
GET http://204.168.227.86:8000/health
Response: {"status":"healthy","services":{"db":"ok","redis":"ok"},"env":"production"}
```

### Test Eseguiti
✅ Webhook Telegram `/ricerca_calcio` — Task lanciato correttamente  
✅ Redis counter tracking — `ricerche:basketball:2026-04 = 2`  
✅ Celery worker — Elaborando task di fetch_complete_sport_data  
✅ No 429 rate limit errors  
✅ Telegram polling — Attivo e rispondente  

---

## Architettura On-Demand

### Comandi Telegram Disponibili
| Comando | Funzione | Quota |
|---------|----------|-------|
| `/ricerca` | Ricerca tutti gli sport | 80/mese |
| `/ricerca_calcio` | Ricerca calcio | 35/mese |
| `/ricerca_nba` | Ricerca NBA | 35/mese |
| `/ricerca_tennis` | Ricerca tennis | 35/mese |
| `/oggi` | Partite di oggi | N/A |
| `/opportunita` | Opportunità pendenti | N/A |
| `/bilancio` | P&L statistics | N/A |
| `/help` | Mostra keyboard | N/A |

### Flusso Ricerca Sport-Specific (Esempio: /ricerca_calcio)
1. Webhook riceve `/ricerca_calcio`
2. Task: `fetch_complete_sport_data(sport="football")`
   - sync_competitions (football)
   - fetch_all_odds (football)
   - fetch_upcoming_stats (football)
3. Attesa 60 secondi
4. Task: `run_daily_pipeline(sport="football")`
   - AI agents analysis
   - Opportunity detection
   - Telegram alert con risultati

---

## Configurazione Affidabilità

### Rate Limiting & API Quota
- **Budget mensile:** 2000 req
- **Utilizzo attuale:** ~1200 req/mese (60% utilizzo)
- **Margine disponibile:** 40%
- **Strategy:** On-demand fetch — nessun fetch automatico schedulato

### Prevenzione 429 Errors
✅ NTP sync — Clock drift prevention  
✅ Odds staleness — 300s (quote < 6h)  
✅ 60s delay tra fetch e pipeline  
✅ API key rotation logging  
✅ Retry backoff: 5s → 10s → 20s (max 2 retry)

### Security
✅ Redis password protected  
✅ DB not exposed (5432 internal)  
✅ Redis not exposed (6379 internal)  
✅ JWT auth su tutti i router  
✅ Telegram bot token in env  

---

## Prossimi Step

1. **Monitoraggio** — Verificare alert Telegram ricevuti
2. **API Quota** — Controllare settimanalmente utilizzo req/mese
3. **Player Props** — Verificare che vengano fetchiati per football/basketball
4. **Alert Quality** — Assicurare che gli alert includano probabilità agenti

---

## File Modificati (Ultima sessione)

| File | Modifica |
|------|----------|
| `backend/app/main.py` | Commentato import/router `results` (modulo non esiste) |
| `backend/app/api/routers/telegram_webhook.py` | Aggiunto `/help` keyboard, `/pulisci` command |
| `CONTEXT.md` | Aggiornato stato finale on-demand architecture |
| `docker-compose.yml` | Worker + beat avviati |

---

## Deploy Verificato ✅

Tutti i test completati. Sistema pronto per uso produttivo.

**Comando finale per rideploy futuro:**
```bash
cd /opt/pepodds21
docker-compose down
docker-compose build --no-cache
docker-compose up -d
curl http://localhost:8000/health
```
