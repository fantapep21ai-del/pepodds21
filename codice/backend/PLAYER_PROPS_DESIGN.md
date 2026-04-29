# Player Props — Architettura Sport-Specifica

**Status:** Design phase (Ready to implement)  
**Date:** 2026-04-30

---

## 1️⃣ FOOTBALL (Calcio) — Giocatori Calcio

### Mercati Disponibili (da The Odds API)

| Mercato | Descrizione | Esempio | Min EV |
|---------|-------------|---------|--------|
| `player_goals` | Gol segnati | Over/Under 0.5 | 3.5% |
| `player_shots_on_target` | Tiri in porta | Over/Under 2.5 | 3.5% |
| `player_shots` | Tiri totali | Over/Under 3.5 | 3.5% |
| `player_assists` | Assist | Over/Under 0.5 | 8.0% |

**Note:**
- Shots e shots_on_target sono rari nelle API, ma disponibili su Bet365/Eplay24
- Gol è il più liquid (tante quote disponibili)
- Assist ha fewer quote, soglia EV più alta (8%)

### Parametri di Analisi per Agente

Crea nuovo agente: **PlayerStatsAgentFootball**

**Dati da raccogliere:**

```python
player_stats = {
    "nome": str,
    "squadra": str,
    "ruolo": str,  # FW, MF, DF
    "ultimi_5_match": {
        "gol": float,  # media gol ultimi 5
        "tiri_in_porta": float,  # media tiri in porta
        "tiri_totali": float,  # media tiri totali
        "assist": float,  # media assist
        "xG": float,  # expected goals (dunkest / api-football)
        "presenze": int  # quanti match ha giocato
    },
    "stagionale": {
        "gol": int,
        "assist": int,
        "minuti_giocati": int,
        "quote_medio_gol": float
    },
    "forma_recente": "ottima|buona|media|scarsa",  # ultimi 3 match
    "infortuni": bool,  # è infortunato?
    "sospensioni": bool,  # rischia squalifica?
    "avversario_difesa": "debole|media|forte",  # ranking difesa avversario
}
```

**Fonti dati:**
- xG: Dunkest API (già in codice per football_client)
- Presenze/minuti: API-Football
- Forma: Calcolo da ultimi match (win/draw/loss)
- Difesa avversario: Rating Elo stagionale

**Segnale agente (0-1):**
```
IF ultimi_5_gol_media > quote_target AND forma_recente != "scarsa":
    signal = 0.8 (forte agreement)
ELIF xG > quote_target * 0.8:
    signal = 0.6 (moderate agreement)
ELIF avversario_difesa == "debole":
    signal = 0.5 (weak agreement)
ELSE:
    signal = 0.2 (disagreement)
```

### Soglie EV Dinamiche per Calcio

```python
# Over 0.5 gol: 3.5% (quote 1.4-3.0)
#              8.0% (quote > 3.0)

# Tiri in porta: 3.5% (quote 1.4-3.0)
#               8.0% (quote > 3.0)

# Assist: 8.0% (sempre, perché raro)
```

---

## 2️⃣ BASKETBALL (NBA) — Giocatori NBA

### Mercati Disponibili

| Mercato | Descrizione | Esempio | Min EV |
|---------|-------------|---------|--------|
| `player_points` | Punti segnati | Over/Under 22.5 | 3.5% |
| `player_rebounds` | Rimbalzi | Over/Under 8.5 | 3.5% |
| `player_assists` | Assist | Over/Under 6.5 | 3.5% |
| `player_threes` | Triple segnate | Over/Under 2.5 | 8.0% |

**Note:**
- Punti, rimbalzi, assist molto liquid
- Triple sono rari, soglia EV più alta
- Stats disponibili daily da Dunkest (già integrato)

### Parametri di Analisi per Agente

Agente già parzialmente implementato, riuso **StatsAgent** con focus su:

```python
player_nba_stats = {
    "nome": str,
    "squadra": str,
    "posizione": str,  # PG, SG, SF, PF, C
    "ultimi_10_match": {
        "ppg": float,  # punti per game (media)
        "rpg": float,  # rimbalzi per game
        "apg": float,  # assist per game
        "fg_percent": float,  # field goal %
        "3p_percent": float,  # 3-point %
    },
    "season_avg": {
        "ppg": float,
        "rpg": float,
        "apg": float,
        "minuti": float,  # minuti per game
    },
    "matchup_vs_opponent": {
        "opponent_rank_defense": int,  # 1-30 (1=migliore difesa)
        "player_home_away": "home|away",  # boost/malus ~2-3%
    },
    "status": "healthy|questionable|out",  # injury status
    "b2b_games": bool,  # back-to-back → fatigue factor
}
```

**Segnale agente (0-1):**
```
IF ultimi_10_ppg > market_over_line:
    signal = 0.8 (strong)
ELIF ultimi_10_ppg within 5% of line:
    signal = 0.6 (moderate)
ELIF opponent_defense_rank > 20:  # weak defense
    signal = 0.55
ELSE:
    signal = 0.3
```

---

## 3️⃣ TENNIS — Giocatori Tennis

### Mercati Proposti

| Mercato | Descrizione | Esempio | Min EV |
|---------|-------------|---------|--------|
| `match_winner` | Vince match (già esiste) | Player A vs Player B | 3.5% |
| `games_over_under` | Over/Under game vinti | Over 22.5 games | 3.5% |
| `sets_over_under` | Over/Under set vinti | Over 2.5 sets | 8.0% |
| `exact_result` | Risultato esatto | 2-0, 2-1, 1-2, 0-2 | 8.0% |
| `first_set_winner` | Vince primo set | Player A / Player B | 3.5% |

**Status:** The Odds API non offre game/set over-under direttamente  
**Soluzione:** Implementare calcolo interno basato su:
- History H2H
- Velocità di gioco (games per minuto)
- Superficie (clay = longer games, grass = shorter)

### Parametri di Analisi per Agente

Crea nuovo agente: **PlayerStatsAgentTennis**

```python
player_tennis_stats = {
    "nome": str,
    "ranking": int,  # ATP/WTA rank
    "superficie": "clay|hard|grass",  # torneo attuale
    "ultimi_10_match": {
        "win_rate": float,  # % vittorie
        "avg_games_won": float,  # media game vinti
        "avg_sets_won": float,  # media set vinti
        "game_per_minuto": float,  # velocità di gioco
    },
    "h2h_vs_opponent": {
        "head_to_head_win_rate": float,
        "avg_games_to_opponent": float,
    },
    "recent_form": "ottima|buona|media|scarsa",  # ultimi 3 match
    "injury_status": "healthy|concerns|out",
    "grass_clay_hard_avg": {
        "games_won_on_surface": float,  # per questa superficie
    },
}
```

**Segnale agente (0-1):**
```
IF h2h_win_rate > 55%:
    signal = 0.75 (moderate-strong)
ELIF ranking_diff > 50:
    signal = 0.65 (underdog factor)
ELIF game_per_minuto is high (clay game) AND target is "over":
    signal = 0.6
ELSE:
    signal = 0.4
```

**Calcolo Internal Over/Under:**

```python
def calculate_games_over_under(player_a, player_b, target_games=22.5):
    """
    Stima game totali basato su:
    - H2H history games
    - Superficie speed
    - Recent form
    """
    h2h_avg_games = (player_a.avg_games_vs_opponent + player_b.avg_games_vs_opponent) / 2
    surface_factor = 1.1 if surface == "clay" else 0.95 if surface == "grass" else 1.0
    form_factor = 1.05 if (player_a.form + player_b.form) / 2 == "ottima" else 1.0
    
    estimated_games = h2h_avg_games * surface_factor * form_factor
    
    # Decision: Over/Under
    return "over" if estimated_games > target_games else "under", estimated_games
```

---

## 4️⃣ Implementazione Timeline

### Phase 7a: Football Player Props (1 week)
1. Create `PlayerStatsAgentFootball` agente
2. Integrate Dunkest API for xG per giocatore
3. Add player stats fetch to pipeline
4. Test con mercati: gol, tiri in porta
5. Deploy & monitor

### Phase 7b: NBA Player Props (3 days)
1. Enhance existing StatsAgent for NBA matchup focus
2. Add injury status + B2B fatigue factor
3. Test con markets: PPG, rebounds, assists
4. Deploy

### Phase 7c: Tennis Player Props (1 week)
1. Create `PlayerStatsAgentTennis` agente
2. Implement internal games/sets over-under calculator
3. H2H history integration
4. Test con exact results + set over/under
5. Deploy

---

## 5️⃣ Database Schema Changes

No new tables needed. Use existing:
- `BettingOpportunity` → stores player prop quotes
- `MatchOdds` → market = "player_goals", "player_ppg", etc.

New fields in Match (optional):
```python
class Match(Base):
    # ... existing fields ...
    player_props_included: bool = False  # flag if player props were fetched
```

---

## 6️⃣ Quote Freshness & Bookmakers

**Player Props Bookmakers:**
- Bet365 (best coverage)
- Eplay24 (European alternative)
- DraftKings (US, NBA focus)
- FanDuel (US, NBA focus)

**Current:** Only Bet365 + Eplay24 are fetched  
**Improve:** Add DraftKings/FanDuel for NBA when expanding

---

## 7️⃣ Risk & Mitigation

| Risk | Mitigation |
|------|-----------|
| Insufficient quotes per giocatore | Start con top 10 players per squadra |
| Stats API gaps (injuries, form) | Fallback a news scraping se API fails |
| High uncertainty per giocatori rari | Increase min EV threshold (8-10%) |
| Tennis games calculation inaccurate | Backtest con historical results |

---

## 8️⃣ Success Metrics

✅ **Per sport, dopo 2 settimane di dati:**
- Min 5 player props identified per match
- ROI > 0% (break-even acceptable)
- CLV >= -2% (vs Pinnacle as reference)
- Player stats agent agreement > 60%

---

**Prossimo Step:** Chiedi conferma su questa architettura, poi implementiamo Phase 7a (Football).
