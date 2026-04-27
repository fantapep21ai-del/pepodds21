"""
Player Props Pipeline — analisi EV per scommesse su singoli giocatori.

Supporta:
  - NBA: punti, rimbalzi, assist, triple, stoppate, recuperi, PRA combo
  - Calcio: marcatore (anytime/first goalscorer) — probabilità da xG stagionale

Flusso:
  1. Legge MatchOdds con market in PLAYER_PROP_MARKETS
  2. Per ogni prop: estrae giocatore, direzione (over/under), linea
  3. Cerca statistiche storiche su Dunkest (NBA) o api-sports.io (football)
  4. Calcola hit rate storica (quante volte ha superato la linea)
  5. EV = (hit_rate × best_odds) - 1
  6. Se EV > MIN_EV_PROPS: crea BettingOpportunity
  7. Skip se:
     - Giocatore non trovato in Dunkest
     - Meno di 8 partite nel campione
     - Giocatore potenzialmente infortunato (in lista ESPN)
     - Back-to-back game

Soglie più conservative del mercato principale (h2h/totals):
  - EV minimo: 6% (il mercato props è meno efficiente ma anche meno liquido)
  - Hit rate minima: 55% per "over", 55% per "under"
  - Sample minimo: 8 partite
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import Match, MatchOdds
from app.db.models.opportunity import BettingOpportunity
from app.services.dunkest_client import (
    DunkestClient,
    find_player_id,
    parse_player_prop_outcome,
    MARKET_TO_STAT,
)
from app.services.nba_matchup_client import NBAMatchupClient, get_player_position, _team_slug

logger = logging.getLogger(__name__)

# ── Mercati player props da analizzare ───────────────────────────────────────
PLAYER_PROP_MARKETS = {
    "player_points",
    "player_points_over_under",
    "player_rebounds",
    "player_rebounds_over_under",
    "player_assists",
    "player_assists_over_under",
    "player_threes",
    "player_blocks",
    "player_steals",
    "player_pra",
    "player_goal_scorer_anytime",
}

# Mercati combo (summa più statistiche)
COMBO_MARKETS: dict[str, list[str]] = {
    "player_pra": ["pts", "reb", "ast"],
    "player_blocks_steals": ["blk", "stl"],
}

# Soglie
MIN_EV_PROPS    = 0.06    # 6% EV minimo per player props
MIN_HIT_RATE    = 0.55    # almeno 55% storico per andare avanti
MIN_SAMPLE_SIZE = 8       # almeno 8 partite nel campione
MAX_ODDS        = 2.50    # quota massima per props (evita longshot impliciti)
MIN_ODDS        = 1.40    # quota minima (troppo bassa = margine già compresso)

# Bookmaker sharp per player props
SHARP_PROP_BOOKMAKERS = {"pinnacle", "betfair_ex_eu"}


async def analyse_player_props(match: Match, db: AsyncSession) -> int:
    """
    Pipeline completa per i player props di una partita.
    Ritorna il numero di opportunità trovate e salvate.
    """
    if match.sport not in ("basketball", "football", "soccer"):
        return 0

    logger.info("Player props: analysing %s", match.display_name())

    # ── 1. Carica quote player props per questa partita ───────────────────────
    now = datetime.now(timezone.utc)
    freshness_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)  # oggi

    props_result = await db.execute(
        select(MatchOdds)
        .where(MatchOdds.match_id == match.id)
        .where(MatchOdds.market.in_(PLAYER_PROP_MARKETS))
        .where(MatchOdds.fetched_at >= freshness_cutoff)
        .order_by(MatchOdds.fetched_at.desc())
        .limit(500)
    )
    props_odds: list[MatchOdds] = props_result.scalars().all()

    if not props_odds:
        logger.debug("Player props: nessuna quota per %s", match.display_name())
        return 0

    logger.info("Player props: %d quote trovate per %s", len(props_odds), match.display_name())

    # ── 2. Raggruppa per (player_name, market, direction, line) ──────────────
    # Struttura: {(player_name, market, direction, line): {bookmaker: odds}}
    prop_groups: dict[tuple, dict[str, float]] = {}

    for row in props_odds:
        player_name, direction, line = parse_player_prop_outcome(row.outcome, row.market)
        if not player_name or not direction or line is None:
            continue

        key = (player_name, row.market, direction, line)
        if key not in prop_groups:
            prop_groups[key] = {}
        # Tieni la migliore quota per ogni bookmaker
        existing = prop_groups[key].get(row.bookmaker, 0.0)
        prop_groups[key][row.bookmaker] = max(existing, float(row.odds))

    if not prop_groups:
        return 0

    logger.info(
        "Player props: %d prop distinti per %s",
        len(prop_groups), match.display_name(),
    )

    # ── 3. Setup client e cache per questa partita ───────────────────────────
    dunkest          = DunkestClient()
    matchup_client   = NBAMatchupClient()
    found            = 0

    # Cache per questa partita (evita fetch duplicati)
    player_games_cache: dict[int, list[dict]] = {}
    nba_gamelog_cache:  dict[int, list[dict]] = {}   # NBA API game log con avversari
    opp_scores_cache:   dict[int, dict]       = {}   # defense scores per giocatore
    # Usare dict mutabile come "box" per il lazy load (evita nonlocal)
    _state: dict = {"defense_data": {}, "defense_loaded": False, "pace_data": {}, "pace_loaded": False, "correlated_props": []}

    # Squadra avversaria per il matchup (dedotta dalla partita)
    _home_team  = (match.home_team or "").lower()
    _away_team  = (match.away_team or "").lower()

    # Redis per line movement (lazy init)
    _redis = None
    try:
        import redis as _redis_mod
        from app.config import settings as _settings
        _redis = _redis_mod.from_url(_settings.redis_url_with_auth, decode_responses=True)
    except Exception:
        pass

    for (player_name, market, direction, line), bk_odds in prop_groups.items():

        # Trova la migliore quota tra i bookmaker soft
        soft_odds_list = [
            (bk, odds) for bk, odds in bk_odds.items()
            if bk not in SHARP_PROP_BOOKMAKERS
        ]
        if not soft_odds_list:
            continue

        best_bk, best_odds = max(soft_odds_list, key=lambda x: x[1])

        # Filtro quote fuori range
        if best_odds < MIN_ODDS or best_odds > MAX_ODDS:
            continue

        # ── Cerca la probabilità sharp (Pinnacle) come benchmark ─────────────
        pinnacle_odds = None
        for sharp_bk in SHARP_PROP_BOOKMAKERS:
            if sharp_bk in bk_odds:
                pinnacle_odds = bk_odds[sharp_bk]
                break

        implied_prob_soft = 1.0 / best_odds
        pinnacle_prob = (1.0 / pinnacle_odds) if pinnacle_odds else None

        # Se Pinnacle è disponibile, usa la sua prob come benchmark
        # (stesso metodo del no-vig per game lines, ma senza rimuovere il vig
        # poiché abbiamo solo 1 outcome props at a time)
        if pinnacle_prob:
            # EV rispetto a Pinnacle (più preciso)
            ev = (pinnacle_prob * best_odds) - 1.0
            reference_prob = pinnacle_prob
        else:
            # Fallback: usa la hit rate dunkest come stima
            ev = None
            reference_prob = None

        # ── Fetch statistiche dunkest (solo NBA) ──────────────────────────────
        dunkest_hit_rate: float | None = None
        opp_weighted_hit_rate: float | None = None
        home_away_hr: float | None = None
        sample_size: int = 0
        back_to_back = False
        rest_data: dict = {}
        teammate_impact: dict | None = None
        matchup_info: dict = {}
        defender_info: dict = {}
        line_movement: dict = {}
        pace_modifier: float = 1.0

        if match.sport == "basketball":
            player_id = find_player_id(player_name)
            if player_id:
                # Usa cache se già fetchato
                if player_id not in player_games_cache:
                    games = await dunkest.get_player_games(player_id)
                    player_games_cache[player_id] = games
                else:
                    games = player_games_cache[player_id]

                if games:
                    # Mercato: trova la chiave statistica
                    if market in COMBO_MARKETS:
                        combo_keys = COMBO_MARKETS[market]
                        stat_key = None
                    else:
                        stat_key = MARKET_TO_STAT.get(market)
                        combo_keys = None

                    if stat_key or combo_keys:
                        dunkest_hit_rate, sample_size = dunkest.compute_hit_rate(
                            games=games,
                            stat_key=stat_key or "",
                            line=line,
                            direction=direction,
                            last_n=20,
                            combo_keys=combo_keys,
                        )

                    # ── [5] Rest & travel avanzato (sostituisce B2B semplice) ──
                    if match.match_date:
                        nba_log = nba_gamelog_cache.get(player_id)
                        is_home = bool(match.home_team and
                                       any(w in _home_team for w in player_name.lower().split() if len(w)>3))
                        rest_data = dunkest.assess_rest_and_travel(
                            games=games,
                            match_date_str=match.match_date.strftime("%Y-%m-%d"),
                            is_home_game=is_home,
                            nba_game_log=nba_log,
                        )
                        back_to_back = rest_data.get("back_to_back", False)

                        # Skip B2B con fatica alta (come prima, ma ora con più contesto)
                        if rest_data.get("fatigue_level") == "high":
                            logger.info(
                                "Props skip %s — %s (combined_modifier=%.2f)",
                                player_name,
                                rest_data.get("reasoning", "fatica alta"),
                                rest_data.get("combined_modifier", 1.0),
                            )

                    # ── [3] Opponent-weighted hit rate ────────────────────────
                    # Carica defense data una sola volta per tutte le props
                    if not _state["defense_loaded"]:
                        _state["defense_data"]   = await matchup_client.get_league_defense_rankings()
                        _state["defense_loaded"] = True
                    defense_data = _state["defense_data"]

                    if defense_data and (stat_key or combo_keys):
                        # Fetch NBA game log con avversari (cache per giocatore)
                        if player_id not in opp_scores_cache:
                            opp_scores = await matchup_client.get_game_opponent_scores(
                                player_id_nba=player_id,
                                defense_data=defense_data,
                                last_n=20,
                            )
                            opp_scores_cache[player_id] = opp_scores
                        else:
                            opp_scores = opp_scores_cache[player_id]

                        if opp_scores:
                            opp_weighted_hit_rate, _ = dunkest.compute_opponent_weighted_hit_rate(
                                games=games,
                                stat_key=stat_key or "",
                                line=line,
                                direction=direction,
                                opponent_defense_scores=opp_scores,
                                last_n=20,
                                combo_keys=combo_keys,
                            )

                    # ── [PACE] Pace of play modifier ─────────────────────────
                    pace_modifier = 1.0
                    if not _state.get("pace_loaded"):
                        _state["pace_data"]   = await matchup_client.get_team_pace_data()
                        _state["pace_loaded"] = True
                    pace_data = _state.get("pace_data", {})
                    if pace_data:
                        from app.services.nba_matchup_client import _team_slug as _ts
                        home_slug = _ts(match.home_team or "")
                        away_slug = _ts(match.away_team or "")
                        stat_type_for_pace = stat_key or "pts"
                        pace_modifier = matchup_client.compute_pace_modifier(
                            home_slug, away_slug, pace_data, stat_type_for_pace
                        )

                    # ── [HOME/AWAY] Split hit rate per contesto ───────────────
                    home_away_hr: float | None = None
                    is_home_for_player = bool(
                        match.home_team and player_name.lower().split()[-1] in (match.home_team or "").lower()
                    )
                    if stat_key and (stat_key or combo_keys):
                        ha_hr, ha_n = dunkest.compute_home_away_hit_rate(
                            games=games,
                            stat_key=stat_key or "",
                            line=line,
                            direction=direction,
                            is_home=is_home_for_player,
                            last_n=15,
                            combo_keys=combo_keys,
                        )
                        if ha_n >= 6:
                            home_away_hr = ha_hr

                    # ── [2] Matchup difensivo + difensore specifico ───────────
                    if defense_data:
                        position = get_player_position(player_name)
                        # Determina squadra avversaria
                        player_slug = player_name.lower().replace(" ", "-")
                        # Approssima: se il giocatore è nella home team → avversario è away
                        opp_team = match.away_team or ""
                        matchup_info = matchup_client.classify_matchup(opp_team, position, defense_data)

                        # Difensore specifico (lazy, solo se matchup non è already neutral)
                        if matchup_info.get("rating") != "neutral":
                            opp_slug = _team_slug(opp_team)
                            defender_info = await matchup_client.get_player_defender_matchup(
                                player_name=player_name,
                                opposing_team_slug=opp_slug,
                                defense_data=defense_data,
                            )

                    # ── [4] Line movement tracking ────────────────────────────
                    if _redis:
                        line_movement = _track_line_movement(
                            _redis, match.id, player_name, market, direction, line
                        )

                    # ── Analisi impatto compagni infortunati (pre-esistente) ──
                    teammate_impact = await _analyze_teammate_impact(
                        dunkest=dunkest,
                        player_id=player_id,
                        player_games=games,
                        match=match,
                        stat_key=stat_key if not (market in COMBO_MARKETS) else None,
                        combo_keys=combo_keys if market in COMBO_MARKETS else None,
                        line=line,
                        direction=direction,
                    )
                    if teammate_impact:
                        verdict = teammate_impact.get("verdict", "")
                        if verdict == "better_without":
                            without_hr = teammate_impact.get("without_hit_rate")
                            without_n  = teammate_impact.get("without_sample", 0)
                            if without_hr is not None and without_n >= MIN_SAMPLE_SIZE and without_hr > (dunkest_hit_rate or 0):
                                logger.info(
                                    "Teammate boost %s: senza %s → hit rate %.0f%% (normale %.0f%%, n=%d)",
                                    player_name,
                                    teammate_impact.get("teammate_name", "?"),
                                    without_hr * 100,
                                    (dunkest_hit_rate or 0) * 100,
                                    without_n,
                                )
                                dunkest_hit_rate = without_hr
                                sample_size = without_n

        # ── Calcola EV finale ─────────────────────────────────────────────────
        # Usa opponent-weighted hit rate se disponibile e ha più sample
        best_hr = dunkest_hit_rate
        if opp_weighted_hit_rate is not None and sample_size >= MIN_SAMPLE_SIZE:
            # Media pesata: 60% opponent-weighted, 40% normale (più robusto)
            best_hr = round(0.60 * opp_weighted_hit_rate + 0.40 * (dunkest_hit_rate or opp_weighted_hit_rate), 3)

        if best_hr is not None and sample_size >= MIN_SAMPLE_SIZE:
            # Applica modificatori matchup e difensore
            matchup_mod  = matchup_client.matchup_ev_modifier(matchup_info.get("rating", "neutral"))
            defender_mod = matchup_client.defender_ev_modifier(defender_info.get("defender_rating", "unknown"))
            rest_mod     = rest_data.get("combined_modifier", 1.0) if rest_data else 1.0
            # Applica modificatori alla probabilità (caps a 0.98)
            adjusted_prob = min(0.98, best_hr * matchup_mod * defender_mod * rest_mod)
            ev = (adjusted_prob * best_odds) - 1.0
            final_prob = adjusted_prob
        elif reference_prob is not None:
            final_prob = reference_prob
            # ev già calcolato sopra con Pinnacle
        else:
            logger.debug(
                "Props skip %s %s %s %.1f — nessun dato probabilità",
                player_name, market, direction, line,
            )
            continue

        # ── [4] Line movement: boost EV se la linea si muove a favore ────────
        line_moved_favorable = line_movement.get("favorable", False)
        if line_moved_favorable:
            ev = ev * 1.05  # segnale di conferma: libro vede lo stesso edge
            logger.debug("Line movement boost per %s %s %s", player_name, direction, line)

        # ── Filtri qualità ────────────────────────────────────────────────────
        if ev < MIN_EV_PROPS:
            continue

        if best_hr is not None and best_hr < MIN_HIT_RATE:
            logger.debug(
                "Props skip %s: hit_rate %.1f%% < %.0f%%",
                player_name, best_hr * 100, MIN_HIT_RATE * 100,
            )
            continue

        # [5] Skip per fatica alta (B2B + viaggio) — più preciso del semplice B2B
        if rest_data.get("fatigue_level") == "high" and rest_data.get("combined_modifier", 1.0) < 0.84:
            logger.info(
                "Props skip %s — fatica alta: %s (mod=%.2f)",
                player_name, rest_data.get("reasoning", ""),
                rest_data.get("combined_modifier", 1.0),
            )
            continue

        # ── Anti-duplicati ────────────────────────────────────────────────────
        outcome_label = f"{player_name} — {direction.capitalize()} {line}"
        existing = await db.scalar(
            select(func.count(BettingOpportunity.id)).where(
                BettingOpportunity.match_id == match.id,
                BettingOpportunity.market == market,
                BettingOpportunity.outcome == outcome_label,
                BettingOpportunity.status.in_(["pending", "in_attesa", "bet_placed"]),
            )
        )
        if existing:
            continue

        # ── Calcola affidabilità ──────────────────────────────────────────────
        teammate_verdict = teammate_impact.get("verdict") if teammate_impact else None
        reliability = _compute_prop_reliability(
            ev=ev,
            hit_rate=best_hr,
            sample_size=sample_size,
            has_pinnacle=pinnacle_odds is not None,
            back_to_back=back_to_back,
            teammate_verdict=teammate_verdict,
            matchup_rating=matchup_info.get("rating"),
            defender_rating=defender_info.get("defender_rating"),
            rest_modifier=rest_data.get("combined_modifier", 1.0) if rest_data else 1.0,
            line_movement_favorable=line_moved_favorable,
        )

        # ── Prepara metadata per storage ─────────────────────────────────────
        teammate_meta: dict | None = None
        if teammate_impact and teammate_impact.get("significant"):
            teammate_meta = {
                "teammate":        teammate_impact.get("teammate_name"),
                "verdict":         teammate_impact.get("verdict"),
                "n_without":       teammate_impact.get("n_without"),
                "pts_delta":       teammate_impact.get("delta", {}).get("pts"),
                "ast_delta":       teammate_impact.get("delta", {}).get("ast"),
                "min_delta":       teammate_impact.get("delta", {}).get("min"),
                "without_hit_rate": teammate_impact.get("without_hit_rate"),
            }

        # ── Persisti opportunità ──────────────────────────────────────────────
        opportunity = BettingOpportunity(
            match_id=match.id,
            market=market,
            outcome=outcome_label,
            bookmaker=best_bk,
            best_odds=best_odds,
            model_probability=final_prob,
            consensus_votes={
                "source":               "player_props",
                "player":               player_name,
                "stat":                 MARKET_TO_STAT.get(market, market),
                "line":                 line,
                "direction":            direction,
                "dunkest_hit_rate":     round(dunkest_hit_rate, 3) if dunkest_hit_rate else None,
                "opp_weighted_hit_rate": round(opp_weighted_hit_rate, 3) if opp_weighted_hit_rate else None,
                "sample_size":          sample_size,
                "pinnacle_prob":        round(pinnacle_prob, 4) if pinnacle_prob else None,
                "ev":                   round(ev, 4),
                "reliability":          round(reliability, 4),
                "back_to_back":         back_to_back,
                "rest_info":            rest_data or None,
                "matchup":              matchup_info or None,
                "defender":             defender_info or None,
                "line_movement":        line_movement or None,
                "teammate_impact":      teammate_meta,
            },
            uncertainty_score=0.35,
            expected_value=ev,
            tier=_ev_to_tier(ev),
            edge=round(ev * 100, 2),
            bet_type="singola",
            confidence_level="medium" if reliability >= 0.50 else "low",
            status="pending",
            reference_source="player_props_dunkest",
            expires_at=match.match_date,
        )
        db.add(opportunity)
        await db.flush()
        found += 1

        logger.info(
            "PLAYER PROP VALUE: %s | %s %s %.1f @ %.2f | EV %+.1f%% hit=%.0f%% (%d gare) aff=%.0f%%",
            match.display_name(),
            player_name, direction.upper(), line, best_odds,
            ev * 100,
            (dunkest_hit_rate or final_prob) * 100,
            sample_size,
            reliability * 100,
        )

        # Notifica Telegram
        await _send_prop_alert(
            opportunity, match, reliability,
            player_name, direction, line,
            sample_size, dunkest_hit_rate,
            teammate_impact=teammate_meta,
        )

    if found:
        await db.commit()

    logger.info(
        "Player props done for %s — %d opportunità trovate",
        match.display_name(), found,
    )
    return found


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _analyze_teammate_impact(
    dunkest: "DunkestClient",
    player_id: int,
    player_games: list[dict],
    match: "Match",
    stat_key: str | None,
    combo_keys: list[str] | None,
    line: float,
    direction: str,
) -> dict | None:
    """
    Cerca compagni di squadra confermati OUT (infortuni/riposo) e analizza
    come cambiano le stats del giocatore analizzato in loro assenza.

    Logica:
      1. Legge match.raw_stats["injuries"]["home"/"away"] per trovare giocatori OUT
      2. Per ogni OUT player, cerca il suo ID dunkest
      3. Fetch del game log del compagno (solo lui, il giocatore principale è già in cache)
      4. Filtra le partite del giocatore dove il compagno era assente
      5. Calcola hit rate e impatto sulle medie per quelle partite
      6. Ritorna il compagno con l'impatto più significativo, o None

    Ritorna dict con:
      teammate_name, verdict, n_without, pts_delta, without_hit_rate, without_sample, ...
    """
    raw_stats = getattr(match, "raw_stats", None) or {}
    injuries  = raw_stats.get("injuries", {})

    # Raccogli tutti i giocatori OUT (sia home che away)
    all_out: list[str] = []
    for side in ("home", "away"):
        side_injuries = injuries.get(side, [])
        if isinstance(side_injuries, list):
            all_out.extend(side_injuries)

    if not all_out:
        return None

    best_impact: dict | None = None
    best_abs_delta: float = 0.0

    for out_name in all_out:
        teammate_id = find_player_id(out_name)
        if not teammate_id or teammate_id == player_id:
            continue

        try:
            # Fetch solo le partite del compagno infortunato
            teammate_games = await dunkest.get_player_games(teammate_id)

            # Date in cui il compagno ha giocato (min ≥ 5)
            teammate_played: set[str] = {
                g.get("gameDate", "")[:10]
                for g in teammate_games
                if (g.get("min") or 0) >= 5
            }

            # Partite del giocatore analizzato senza il compagno
            games_without = [
                g for g in player_games
                if (g.get("min") or 0) >= 10
                and g.get("gameDate", "")[:10] not in teammate_played
            ]

            # Calcola impatto sulle medie
            impact = dunkest.compute_teammate_impact(player_games, games_without)
            if not impact.get("significant"):
                continue

            # Calcola hit rate specifica sulle partite senza il compagno
            without_hit_rate: float | None = None
            without_sample: int = 0
            if games_without and (stat_key or combo_keys):
                without_hit_rate, without_sample = dunkest.compute_hit_rate(
                    games=games_without,
                    stat_key=stat_key or "",
                    line=line,
                    direction=direction,
                    last_n=20,
                    combo_keys=combo_keys,
                )

            # Tieni l'impatto più significativo in termini di punti
            abs_delta = abs(impact["delta"].get("pts", 0))
            if abs_delta > best_abs_delta:
                best_abs_delta = abs_delta
                best_impact = {
                    **impact,
                    "teammate_name":   out_name,
                    "without_hit_rate": without_hit_rate,
                    "without_sample":  without_sample,
                }

        except Exception as exc:
            logger.debug("Teammate impact analysis failed for %s: %s", out_name, exc)

    return best_impact


def _compute_prop_reliability(
    ev: float,
    hit_rate: float | None,
    sample_size: int,
    has_pinnacle: bool,
    back_to_back: bool,
    teammate_verdict: str | None = None,
    matchup_rating: str | None = None,
    defender_rating: str | None = None,
    rest_modifier: float = 1.0,
    line_movement_favorable: bool = False,
) -> float:
    """
    Affidabilità [0, 0.85] per player props.

    Componenti core (geometrica pesata):
      - EV quality (peso 35%)
      - Hit rate evidence (peso 30%)
      - Sample size (peso 20%)
      - Pinnacle benchmark (peso 15%)

    Modificatori moltiplicativi:
      - Fatica/riposo (rest_modifier):        [0.70, 1.00]
      - Teammate absence:                     +8% / -12%
      - Matchup difensivo (exploitable/tough): +6% / -8%
      - Difensore specifico (lockdown/poor):  +6% / -8%
      - Line movement favorevole:             +5%
    """
    # ── Componenti core ───────────────────────────────────────────────────────
    ev_factor     = min(max(ev / 0.15, 0.0), 1.0)
    hr_factor     = max(0.0, (hit_rate - 0.55) / 0.20) if hit_rate is not None else 0.4
    sample_factor = min((sample_size - MIN_SAMPLE_SIZE) / 12.0, 1.0) if sample_size >= MIN_SAMPLE_SIZE else 0.1
    pinnacle_factor = 1.0 if has_pinnacle else 0.6

    raw = (
        (ev_factor      ** 0.35)
        * (hr_factor    ** 0.30)
        * (sample_factor ** 0.20)
        * (pinnacle_factor ** 0.15)
    )

    # ── Modificatori moltiplicativi ───────────────────────────────────────────
    # [5] Riposo e viaggio
    raw *= rest_modifier  # già in [0.70, 1.00]

    # Teammate absence
    if teammate_verdict == "better_without":
        raw *= 1.08
    elif teammate_verdict == "worse_without":
        raw *= 0.88

    # [2] Matchup difensivo squadra
    if matchup_rating == "exploitable":
        raw *= 1.06
    elif matchup_rating == "tough":
        raw *= 0.92

    # [2] Difensore specifico
    if defender_rating == "poor":
        raw *= 1.06
    elif defender_rating == "lockdown":
        raw *= 0.92

    # [4] Line movement favorevole
    if line_movement_favorable:
        raw *= 1.05

    return max(0.05, min(0.85, raw))


def _track_line_movement(
    redis_client,
    match_id,
    player_name: str,
    market: str,
    direction: str,
    line: float,
) -> dict:
    """
    [Miglioramento 4] Traccia il movimento della linea di prop nel tempo.

    Salva la linea corrente in Redis e confronta con quella precedente.
    Se la linea si muove a nostro favore → segnale di conferma che il
    mercato vede lo stesso edge (es. stiamo bet OVER e la linea scende).

    Redis key: prop:line:{match_id}:{player_slug}:{market}:{direction}
    TTL: 24 ore (reset giornaliero)

    Returns:
        {
          "previous_line": float | None,
          "current_line":  float,
          "movement":      float,        # current - previous (negativo = linea scesa)
          "favorable":     bool,         # True se movimento conferma la nostra tesi
          "signal":        str,          # "confirmed" | "neutral" | "against"
        }
    """
    import re
    slug = re.sub(r"[^a-z0-9]", "-", player_name.lower())
    key  = f"prop:line:{str(match_id)[:8]}:{slug}:{market}:{direction}"

    try:
        prev_raw = redis_client.get(key)
        prev_line = float(prev_raw) if prev_raw else None
    except Exception:
        prev_line = None

    # Salva linea corrente (TTL 24h)
    try:
        redis_client.setex(key, 86400, str(line))
    except Exception:
        pass

    if prev_line is None:
        return {"previous_line": None, "current_line": line,
                "movement": 0.0, "favorable": False, "signal": "neutral"}

    movement = line - prev_line  # negativo = linea scesa

    # Favorevole:
    # - Stiamo bet OVER e la linea è scesa (mercato ci viene incontro)
    # - Stiamo bet UNDER e la linea è salita (stesso ragionamento)
    if direction == "over":
        favorable = movement < -0.25   # linea scesa di almeno 0.25 punti
        signal = "confirmed" if favorable else ("against" if movement > 0.25 else "neutral")
    else:
        favorable = movement > 0.25    # linea salita
        signal = "confirmed" if favorable else ("against" if movement < -0.25 else "neutral")

    return {
        "previous_line": prev_line,
        "current_line":  line,
        "movement":      round(movement, 2),
        "favorable":     favorable,
        "signal":        signal,
    }


def _ev_to_tier(ev: float) -> str:
    if ev >= 0.12:
        return "S"
    elif ev >= 0.09:
        return "A"
    elif ev >= 0.06:
        return "B"
    return "C"


async def _send_prop_alert(
    opp: BettingOpportunity,
    match: Match,
    reliability: float,
    player: str,
    direction: str,
    line: float,
    sample: int,
    hit_rate: float | None,
    teammate_impact: dict | None = None,
) -> None:
    """Invia notifica Telegram per player prop con valore."""
    try:
        from app.services.telegram_service import _send

        sport_emoji = "🏀" if match.sport == "basketball" else "⚽"
        dir_emoji   = "📈" if direction == "over" else "📉"
        tier_emoji  = {"S": "🔥", "A": "⭐", "B": "✅", "C": "📋"}.get(opp.tier, "✅")

        stats_line = ""
        if hit_rate is not None and sample >= MIN_SAMPLE_SIZE:
            stats_line = f"\n📊 Storico: {hit_rate*100:.0f}% hit rate ({sample} partite)"

        teammate_line = ""
        if teammate_impact:
            verdict   = teammate_impact.get("verdict", "")
            tname     = teammate_impact.get("teammate", "?")
            pts_delta = teammate_impact.get("pts_delta", 0) or 0
            n_wo      = teammate_impact.get("n_without", 0) or 0
            wo_hr     = teammate_impact.get("without_hit_rate")

            if verdict == "better_without":
                wo_hr_str = f" | hit {wo_hr*100:.0f}%" if wo_hr else ""
                teammate_line = (
                    f"\n🟢 Senza <b>{tname}</b> ({n_wo} gare): "
                    f"<b>{pts_delta:+.1f} pts</b>{wo_hr_str} — usage boost"
                )
            elif verdict == "worse_without":
                teammate_line = (
                    f"\n🔴 Senza <b>{tname}</b> ({n_wo} gare): "
                    f"<b>{pts_delta:+.1f} pts</b> — dipendente, attenzione"
                )

        # Matchup info
        matchup_line = ""
        consensus_data = opp.consensus_votes or {}
        matchup_d  = consensus_data.get("matchup") or {}
        defender_d = consensus_data.get("defender") or {}
        rest_d     = consensus_data.get("rest_info") or {}
        line_mov_d = consensus_data.get("line_movement") or {}

        matchup_rating = matchup_d.get("rating", "")
        if matchup_rating == "exploitable":
            matchup_line += f"\n🟢 Matchup favorevole (rank difesa opp. #{matchup_d.get('opp_pts_rank','')})"
        elif matchup_rating == "tough":
            matchup_line += f"\n🔴 Matchup difficile (rank difesa opp. #{matchup_d.get('opp_pts_rank','')})"

        defender_name = defender_d.get("primary_defender")
        defender_rtg  = defender_d.get("defender_rating", "")
        if defender_name and defender_rtg == "lockdown":
            matchup_line += f"\n🔒 Difensore: <b>{defender_name}</b> (lockdown)"
        elif defender_name and defender_rtg == "poor":
            matchup_line += f"\n💧 Difensore: <b>{defender_name}</b> (weak defender)"

        rest_line = ""
        if rest_d.get("reasoning") and rest_d.get("fatigue_level") not in ("none", ""):
            fatigue = rest_d.get("fatigue_level", "")
            emoji_map = {"high": "😴", "moderate": "⚡", "low": "✅"}
            rest_line = f"\n{emoji_map.get(fatigue,'⚡')} Riposo: {rest_d.get('reasoning','')} (mod {rest_d.get('combined_modifier',1.0):.2f})"

        line_mov_line = ""
        if line_mov_d.get("signal") == "confirmed":
            line_mov_line = (
                f"\n📐 Linea: {line_mov_d.get('previous_line',line)} → {line_mov_d.get('current_line',line)} "
                f"({line_mov_d.get('movement',0):+.1f}) — movimento confermante"
            )

        msg = (
            f"{sport_emoji} <b>Player Prop: {tier_emoji} Tier {opp.tier}</b>\n"
            f"{match.display_name()}\n\n"
            f"👤 <b>{player}</b>\n"
            f"{dir_emoji} <b>{direction.upper()} {line}</b> @ <b>{opp.best_odds:.2f}</b> ({opp.bookmaker})\n"
            f"💹 EV: <b>{opp.expected_value * 100:+.1f}%</b> | Aff: {reliability * 100:.0f}%"
            f"{stats_line}"
            f"{teammate_line}"
            f"{matchup_line}"
            f"{rest_line}"
            f"{line_mov_line}\n\n"
            f"Rispondi <b>/accetta {str(opp.id)[:8]}</b> per piazzare questa scommessa."
        )
        await _send(msg)
    except Exception as exc:
        logger.warning("Player prop Telegram alert failed: %s", exc)
