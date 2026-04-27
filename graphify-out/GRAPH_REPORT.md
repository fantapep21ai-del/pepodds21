# Graph Report - pepodds21  (2026-04-27)

## Corpus Check
- 99 files · ~68,399 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 956 nodes · 2935 edges · 35 communities detected
- Extraction: 41% EXTRACTED · 59% INFERRED · 0% AMBIGUOUS · INFERRED: 1718 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]

## God Nodes (most connected - your core abstractions)
1. `BettingOpportunity` - 149 edges
2. `Bet` - 138 edges
3. `Match` - 136 edges
4. `MatchOdds` - 80 edges
5. `DunkestClient` - 63 edges
6. `Scalata` - 62 edges
7. `ScalataStep` - 53 edges
8. `AgentScore` - 49 edges
9. `Competition` - 48 edges
10. `OddsAPIClient` - 48 edges

## Surprising Connections (you probably didn't know these)
- `_seed_admin()` --calls--> `hash_password()`  [INFERRED]
  codice/backend/app/main.py → codice/backend/app/core/security.py
- `Consensus engine — Pinnacle no-vig first.  Funzioni esportate:   compute_no_vig(` --uses--> `AgentResult`  [INFERRED]
  codice/backend/app/agents/consensus.py → codice/backend/app/agents/base.py
- `Calcola probabilità no-vig dai bookmaker sharp.     Restituisce {(market, outcom` --uses--> `AgentResult`  [INFERRED]
  codice/backend/app/agents/consensus.py → codice/backend/app/agents/base.py
- `Confronta probabilità no-vig con le migliori quote soft.     Ritorna lista di ca` --uses--> `AgentResult`  [INFERRED]
  codice/backend/app/agents/consensus.py → codice/backend/app/agents/base.py
- `Calcola un indice di affidabilità della giocata tra 0 e 1.      Componenti:` --uses--> `AgentResult`  [INFERRED]
  codice/backend/app/agents/consensus.py → codice/backend/app/agents/base.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (89): ApiError, request(), find_player_id(), _normalize_name(), parse_player_prop_outcome(), DunkestClient — statistiche giocatori NBA da dunkest.com  Dunkest è il principal, Normalizza un nome giocatore in slug dunkest.     "Nikola Jokić" → "nikola-jokic, Cerca l'ID dunkest di un giocatore NBA dal nome.     Usa matching esatto dello s (+81 more)

### Community 1 - "Community 1"
Cohesion: 0.03
Nodes (99): CLVRecord, CLVSummaryOut, get_clv(), get_performance(), PerformanceOut, Analytics API — performance statistics and CLV tracking.  GET /analytics/perform, Closing Line Value analysis.     CLV > 0 means we consistently bet before the ma, System performance breakdown by tier and bet type. (+91 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (79): BettingAdvisor, Riceve una lista di step candidati (con confidenza e EV) e decide     la struttu, DunkestClient, Client per le API interne di dunkest.com.     Nessuna autenticazione richiesta (, FootballNewsService, FootballNewsService — RSS feeds per news di calcio.  Fonti gratuite (no API key), Scarica un singolo feed RSS e parsa gli articoli., Parsa XML RSS senza librerie esterne (regex semplice). (+71 more)

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (68): ABC, AgentRun, AgentScore, AgentVote, One execution of one agent for one match., Probabilistic vote cast by one agent for a specific outcome., Rolling Brier score per agent — updated after each match settles., FormAgent (+60 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (63): Base, Base, MatchContext, Rich contextual data for a match — assembled from multiple sources.     One reco, Archive of every raw API response before any processing.     Guarantees full aud, RawDataStore, DeclarativeBase, get_market_intelligence() (+55 more)

### Community 5 - "Community 5"
Cohesion: 0.05
Nodes (71): BankrollSnapshot, Daily snapshot of bankroll state., System health snapshot — written every 2 minutes., SystemHealth, Exception, System health checker — runs every 2 minutes via Celery. Checks: DB, Redis. Writ, Full health check:       - DB connectivity (simple query)       - Redis ping, run_health_check() (+63 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (36): _did_bet_win(), _normalize_name(), _resolve_totals(), calibrate_clv(), _calibrate_clv_async(), check_nba_injuries(), _check_nba_injuries_async(), _clv_recommendation() (+28 more)

### Community 7 - "Community 7"
Cohesion: 0.13
Nodes (14): ESPNInjuryClient, _extract_injuries_from_text(), _normalise_status(), NBA Injury News Aggregator — fonti tempo reale per assenze NBA.  Fonti (in ordin, Estrae informazioni su assenze/dubbi da testo libero (messaggi Telegram o simili, Legge gli ultimi messaggi di un canale Telegram pubblico via HTML preview.     t, Ritorna i messaggi recenti del canale come lista di {text, date}., Parsa HTML di t.me/s per estrarre messaggi e timestamp. (+6 more)

### Community 8 - "Community 8"
Cohesion: 0.14
Nodes (14): _get_with_key(), _is_allowed_bookmaker(), OddsAPIError, OddsAPIQuotaError, parse_odds_response(), parse_player_props_response(), The Odds API client.  Docs: https://the-odds-api.com/liveapi/guides/v4/ Free tie, Prova le chiavi in ordine — passa alla successiva se la quota è esaurita. (+6 more)

### Community 9 - "Community 9"
Cohesion: 0.14
Nodes (12): BettingDecision, format_decision_telegram(), BettingAdvisor — decide come strutturare ogni giocata.  Ragiona come uno scommet, Doppia: 2 pick con confidenza > 62%, match diversi, EV congiunto > 2.5%., Tripla: 3 pick con confidenza > 60%, match diversi, EV congiunto > 1.5%., Scalata: 2-3 step soft (odds 1.35-1.75 ognuno), confidenza > 58%., Prendi N candidati assicurandoti che vengano da match diversi., Kelly fraction conservativo (÷ 4), cap al 2.5%. (+4 more)

### Community 10 - "Community 10"
Cohesion: 0.15
Nodes (10): ranking_to_elo(), Converte il ranking ATP/WTA in un rating Elo pseudo.     Formula log: rank 1 ≈ 2, ELO per superficie per il tennis (clay / hard / grass / carpet).      Logica:, Ritorna l'ELO del giocatore per una superficie specifica.          Se non ha dat, Fetch match recenti per superficie da api-sports.io tennis., Calcola ELO da lista di match (ordine cronologico)., Aggiustamento ELO empirico per superficie basato su specializzazione nota., Inferisce la superficie dal nome del torneo. (+2 more)

### Community 11 - "Community 11"
Cohesion: 0.17
Nodes (15): BetRecommendation, build_recommendations(), _correlation(), _find_best_doubles(), _kelly_stake(), OpportunityLike, Bet Builder — constructs Singles, Doubles, and Multiples from opportunities.  Pr, Quarter-Kelly with 3% bankroll hard cap. (+7 more)

### Community 12 - "Community 12"
Cohesion: 0.13
Nodes (9): Recupera xG reale delle ultime N partite di un team.          Returns:, Valuta se c'è bias over/under per questa partita basandosi su xG.          Logic, Analisi storica degli arbitri per le partite di calcio.      Certi arbitri dirig, Recupera le statistiche storiche di un arbitro.          Returns:             {, Modificatore EV per mercato over/under totali basato sull'arbitro.         direc, Modello xG (Expected Goals) per partite di calcio.      Usa api-football per rec, RefereeAnalysisClient, _safe_float() (+1 more)

### Community 13 - "Community 13"
Cohesion: 0.2
Nodes (12): BankrollHealth, _calculate_correlation_risk(), _calculate_exposure(), _calculate_max_loss_scenario(), _estimate_risk_of_ruin(), _generate_warnings(), RiskMonitor — monitora la salute del bankroll durante la sessione.  Metriche:, Valutazione della salute del bankroll. (+4 more)

### Community 14 - "Community 14"
Cohesion: 0.21
Nodes (12): compute_no_vig(), compute_reliability(), _extract_uncertainty(), find_value_opportunities(), OpportunityCandidate, Consensus engine — Pinnacle no-vig first.  Funzioni esportate:   compute_no_vig(, Calcola un indice di affidabilità della giocata tra 0 e 1.      Componenti:, Interfaccia backward-compat. Il pipeline principale non la usa più. (+4 more)

### Community 15 - "Community 15"
Cohesion: 0.2
Nodes (8): login(), Token, get_current_user(), create_access_token(), decode_token(), hash_password(), Return the subject (user email) or raise JWTError., verify_password()

### Community 16 - "Community 16"
Cohesion: 0.24
Nodes (7): SynthesisAgent — integra i 7 specialist in una narrativa coerente.  Riceve:   -, Formatta i risultati degli specialist per il prompt., Wrapper async per SynthesisAgent., Sintetizza i 7 specialist in una narrativa narrativa coerente.     Usa Haiku (ec, Sintetizza i segnali dei 7 specialist in una narrativa coerente.          Input:, SynthesisAgent, synthesize_agent_results()

### Community 17 - "Community 17"
Cohesion: 0.24
Nodes (7): _build_reasoning(), DecisionEngine, KellyDecision, DecisionEngine — suggierisce stake usando Kelly Criterion.  Kelly Criterion:   f, Risultato di una decisione Kelly., Suggerisce stake personalizzati basati su Kelly Criterion., Calcola stake con Kelly Criterion.          Input:           bankroll: saldo att

### Community 18 - "Community 18"
Cohesion: 0.25
Nodes (1): Layout()

### Community 19 - "Community 19"
Cohesion: 0.4
Nodes (5): classify(), min_ev_for_tier(), Tier classification engine for betting opportunities.  Tier S: EV >= 8%  → massi, Classifica una opportunità in un tier.      Logica:     - EV >= 8%  → Tier S (ma, TierResult

### Community 20 - "Community 20"
Cohesion: 0.53
Nodes (4): get_url(), run_async_migrations(), run_migrations_offline(), run_migrations_online()

### Community 22 - "Community 22"
Cohesion: 0.6
Nodes (3): fmt(), PipelinePage(), pnlStr()

### Community 23 - "Community 23"
Cohesion: 0.5
Nodes (2): BaseSettings, Settings

### Community 24 - "Community 24"
Cohesion: 0.5
Nodes (1): initial schema  Revision ID: 001 Revises: Create Date: 2026-04-16 00:00:00.00000

### Community 25 - "Community 25"
Cohesion: 0.5
Nodes (1): Add scalata integration to bets and actual_odds field.  Revision ID: 004 Revises

### Community 26 - "Community 26"
Cohesion: 0.5
Nodes (1): Add scalata tables and enhance opportunities + bankroll models.  Revision ID: 00

### Community 27 - "Community 27"
Cohesion: 0.5
Nodes (1): Enhanced schema: players, context, news, raw_data, system_health, composite_bets

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Return the system prompt for this agent given match context.

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Return the user prompt (the actual analysis request).

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Valuta l'impatto degli infortuni su una squadra.         Ritorna "high" (titolar

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Cerca le coordinate dello stadio con fuzzy matching.

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Valuta l'impatto sul mercato Over/Under.         Ritorna "under_bias", "slight_u

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Salva quota residua in Redis.         NON chiama asyncio.run() perché questo met

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): Costruisce spiegazione per l'utente.

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): Stima Risk of Ruin (probabilità di bankrupt) nei prossimi N scommesse.

## Knowledge Gaps
- **146 isolated node(s):** `Return the subject (user email) or raise JWTError.`, `Bet Builder — constructs Singles, Doubles, and Multiples from opportunities.  Pr`, `Duck-type protocol so we can use both ORM objects and test stubs.`, `Build bet recommendations from a list of classified opportunities.      Args:`, `Quarter-Kelly with 3% bankroll hard cap.` (+141 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 18`** (8 nodes): `layout.tsx`, `layout.tsx`, `layout.tsx`, `layout.tsx`, `layout.tsx`, `layout.tsx`, `layout.tsx`, `Layout()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (4 nodes): `config.py`, `BaseSettings`, `redis_url_with_auth()`, `Settings`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (4 nodes): `downgrade()`, `initial schema  Revision ID: 001 Revises: Create Date: 2026-04-16 00:00:00.00000`, `upgrade()`, `001_initial_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (4 nodes): `downgrade()`, `Add scalata integration to bets and actual_odds field.  Revision ID: 004 Revises`, `upgrade()`, `004_scalata_bet_integration.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (4 nodes): `downgrade()`, `Add scalata tables and enhance opportunities + bankroll models.  Revision ID: 00`, `upgrade()`, `002_scalata_and_enhancements.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (4 nodes): `downgrade()`, `Enhanced schema: players, context, news, raw_data, system_health, composite_bets`, `upgrade()`, `003_enhanced_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Return the system prompt for this agent given match context.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Return the user prompt (the actual analysis request).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Valuta l'impatto degli infortuni su una squadra.         Ritorna "high" (titolar`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Cerca le coordinate dello stadio con fuzzy matching.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Valuta l'impatto sul mercato Over/Under.         Ritorna "under_bias", "slight_u`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Salva quota residua in Redis.         NON chiama asyncio.run() perché questo met`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `Costruisce spiegazione per l'utente.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `Stima Risk of Ruin (probabilità di bankrupt) nei prossimi N scommesse.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Bet` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 13`?**
  _High betweenness centrality (0.121) - this node is a cross-community bridge._
- **Why does `BettingOpportunity` connect `Community 3` to `Community 0`, `Community 1`, `Community 2`, `Community 4`, `Community 5`, `Community 13`?**
  _High betweenness centrality (0.118) - this node is a cross-community bridge._
- **Why does `Match` connect `Community 4` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 7`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 146 inferred relationships involving `BettingOpportunity` (e.g. with `Player Props Pipeline — analisi EV per scommesse su singoli giocatori.  Supporta` and `Pipeline completa per i player props di una partita.     Ritorna il numero di op`) actually correct?**
  _`BettingOpportunity` has 146 INFERRED edges - model-reasoned connections that need verification._
- **Are the 135 inferred relationships involving `Bet` (e.g. with `SizingResult` and `Risk engine — sizes bets using fractional Kelly criterion and enforces exposure`) actually correct?**
  _`Bet` has 135 INFERRED edges - model-reasoned connections that need verification._
- **Are the 133 inferred relationships involving `Match` (e.g. with `Player Props Pipeline — analisi EV per scommesse su singoli giocatori.  Supporta` and `Pipeline completa per i player props di una partita.     Ritorna il numero di op`) actually correct?**
  _`Match` has 133 INFERRED edges - model-reasoned connections that need verification._
- **Are the 78 inferred relationships involving `MatchOdds` (e.g. with `Player Props Pipeline — analisi EV per scommesse su singoli giocatori.  Supporta` and `Pipeline completa per i player props di una partita.     Ritorna il numero di op`) actually correct?**
  _`MatchOdds` has 78 INFERRED edges - model-reasoned connections that need verification._