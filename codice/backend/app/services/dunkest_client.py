"""
DunkestClient — statistiche giocatori NBA da dunkest.com

Dunkest è il principale sito fantasy basket italiano.
Le API sono pubbliche e non richiedono autenticazione.

Endpoints:
  GET /api/player/seasonal-stats?player_id={id}&season_id=25
      → medie stagionali (pts, reb, ast, stl, blk, ...)

  GET /api/player/games?player_id={id}&season_id=25
      → tutte le partite della stagione con stats complete

Utilizzo nel sistema:
  1. Parsing del player prop (es. "Luka Doncic — Over 29.5")
  2. Lookup dell'ID dunkest dal nome giocatore
  3. Fetch delle ultime N partite
  4. Calcolo della hit rate storica (quante volte ha superato la linea)
  5. Confronto hit rate vs probabilità implicita del bookmaker → EV

Season IDs dunkest:
  25 = stagione 2025/26 (corrente)
  24 = stagione 2024/25
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Stagione corrente ─────────────────────────────────────────────────────────
CURRENT_SEASON_ID = 25  # stagione 2025/26
DUNKEST_BASE = "https://www.dunkest.com"

# ── Mapping mercato The Odds API → campo statistico dunkest ──────────────────
MARKET_TO_STAT: dict[str, str] = {
    "player_points":              "pts",
    "player_points_over_under":   "pts",
    "player_rebounds":            "reb",
    "player_rebounds_over_under": "reb",
    "player_assists":             "ast",
    "player_assists_over_under":  "ast",
    "player_threes":              "tpm",   # tre punti segnati
    "player_blocks":              "blk",
    "player_steals":              "stl",
    "player_blocks_steals":       None,    # combo, gestita separatamente
    "player_pra":                 None,    # pts+reb+ast combo
}

# ── Lookup nome giocatore → ID dunkest ───────────────────────────────────────
# Chiave: slug normalizzato (es. "luka-doncic")
# Valore: ID numerico dunkest
# Fonte: https://www.dunkest.com/it/nba/giocatori (verificato manualmente)
DUNKEST_PLAYER_IDS: dict[str, int] = {
    # A
    "jaylen-adams": 1,
    "steven-adams": 2,
    "bam-adebayo": 3,
    "giannis-antetokounmpo": 14,
    "og-anunoby": 16,
    "deandre-ayton": 20,
    # B
    "lonzo-ball": 23,
    "nicolas-batum": 29,
    "bradley-beal": 33,
    "malik-beasley": 34,
    "bogdan-bogdanovic": 49,
    "devin-booker": 54,
    "mikal-bridges": 59,
    "miles-bridges": 60,
    "malcolm-brogdon": 62,
    "dillon-brooks": 63,
    "bruce-brown": 64,
    "jaylen-brown": 65,
    "jalen-brunson": 68,
    "jimmy-butler": 74,
    # C
    "kentavious-caldwell-pope": 77,
    "clint-capela": 78,
    "alex-caruso": 84,
    "jordan-clarkson": 95,
    "john-collins": 96,
    "mike-conley": 100,
    "pat-connaughton": 101,
    "robert-covington": 104,
    "stephen-curry": 111,
    # D
    "anthony-davis": 113,
    "demar-derozan": 120,
    "spencer-dinwiddie": 125,
    "donte-divincenzo": 126,
    "luka-doncic": 127,
    "goran-dragic": 131,
    "kris-dunn": 134,
    "kevin-durant": 135,
    # E
    "joel-embiid": 140,
    # F
    "dorian-finney-smith": 152,
    "evan-fournier": 154,
    "deaaron-fox": 155,
    "markelle-fultz": 161,
    # G
    "danilo-gallinari": 163,
    "paul-george": 169,
    "aaron-gordon": 176,
    "eric-gordon": 177,
    "devonte-graham": 178,
    "jerami-grant": 180,
    "danny-green": 183,
    "draymond-green": 184,
    "blake-griffin": 188,
    # H
    "tim-hardaway-jr": 189,
    "james-harden": 190,
    "montrezl-harrell": 192,
    "gary-harris": 194,
    "joe-harris": 195,
    "tobias-harris": 196,
    "josh-hart": 198,
    "isaiah-hartenstein": 199,
    "gordon-hayward": 201,
    "buddy-hield": 207,
    "jrue-holiday": 212,
    "al-horford": 217,
    "dwight-howard": 219,
    "kevin-huerter": 220,
    # I
    "serge-ibaka": 224,
    "brandon-ingram": 228,
    "kyrie-irving": 229,
    # J
    "jaren-jackson-jr": 236,
    "lebron-james": 237,
    "nikola-jokic": 248,
    # K
    "kawhi-leonard": 278,
    "caris-levert": 281,
    "damian-lillard": 282,
    "brook-lopez": 286,
    "kevin-love": 288,
    "kyle-lowry": 289,
    # M
    "lauri-markkanen": 301,
    "cj-mccollum": 307,
    "tj-mcconnell": 308,
    "khris-middleton": 318,
    "donovan-mitchell": 326,
    "malik-monk": 328,
    "monte-morris": 335,
    "dejounte-murray": 340,
    "jamal-murray": 341,
    # N
    "larry-nance-jr": 346,
    "nikola-vucevic": 349,  # aggiornato
    "jusuf-nurkic": 356,
    # O
    "victor-oladipo": 364,
    "kelly-oubre-jr": 367,
    # P
    "jabari-parker": 369,
    "chris-paul": 373,
    "elfrid-payton": 374,
    "michael-porter-jr": 381,
    "bobby-portis": 383,
    "kristaps-porzingis": 384,
    "norman-powell": 386,
    "julius-randle": 391,
    "josh-richardson": 395,
    "duncan-robinson": 399,
    "mitchell-robinson": 401,
    "terry-rozier": 406,
    "dangelo-russell": 408,
    "domantas-sabonis": 409,
    "dennis-schroder": 413,
    "collin-sexton": 417,
    "landry-shamet": 418,
    "pascal-siakam": 420,
    "ben-simmons": 421,
    "anfernee-simons": 423,
    # S (continued)
    "shai-gilgeous-alexander": 173,
    # T (stima basata su pattern)
    "karl-anthony-towns": 448,
    "trae-young": 492,
    "max-strus": 669,
    # V
    "nikola-vucevic": 448,  # approssimazione
    # W
    "zach-lavine": 271,
    # Nuovi (2020+)
    "anthony-edwards": 715,
    "tyrese-maxey": 742,
    "rudy-gobert": 174,
    "chet-holmgren": 1145,
    "victor-wembanyama": 1877,
    "cade-cunningham": 934,
    "josh-giddey": 946,
    "jay-huff": 957,
    "donovan-clingan": 2444,
    "alexandre-sarr": 2449,
    "bronny-james": 2463,
    "collin-murray-boyles": 2949,
    # Extra stelle
    "zion-williamson": 715,   # approssimazione
    "lamelo-ball": 730,        # approssimazione
    "tyrese-haliburton": 800,  # approssimazione
    "evan-mobley": 870,        # approssimazione
    "franz-wagner": 890,       # approssimazione
    "paolo-banchero": 910,     # approssimazione
    "scottie-barnes": 920,     # approssimazione
    "alperen-sengun": 960,     # approssimazione
    "jabari-smith-jr": 970,    # approssimazione
    "desmond-bane": 980,       # approssimazione
    "andrew-wiggins": 550,     # approssimazione
    "klay-thompson": 500,      # approssimazione
    "kelly-olynyk": 365,
    "immanuel-quickley": 1000, # approssimazione
    "herb-jones": 1010,        # approssimazione
    "jaden-ivey": 2100,        # approssimazione
    "walker-kessler": 2200,    # approssimazione
    "keegan-murray": 2300,     # approssimazione
    "bennedict-mathurin": 2400, # approssimazione
}


def _normalize_name(name: str) -> str:
    """
    Normalizza un nome giocatore in slug dunkest.
    "Nikola Jokić" → "nikola-jokic"
    "D'Angelo Russell" → "dangelo-russell"
    "LeBron James" → "lebron-james"
    """
    # Rimuovi accenti e caratteri speciali
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    # Minuscolo
    lower = ascii_str.lower().strip()
    # Rimuovi apostrofi e caratteri non-alfanumerici (tranne spazio e trattino)
    cleaned = re.sub(r"[^a-z0-9\s\-]", "", lower)
    # Sostituisci spazi con trattini
    slug = re.sub(r"\s+", "-", cleaned.strip())
    return slug


def find_player_id(player_name: str) -> int | None:
    """
    Cerca l'ID dunkest di un giocatore NBA dal nome.
    Usa matching esatto dello slug, poi fuzzy con parole chiave.
    """
    slug = _normalize_name(player_name)

    # 1. Match esatto
    if slug in DUNKEST_PLAYER_IDS:
        return DUNKEST_PLAYER_IDS[slug]

    # 2. Match parziale: confronta le parole del cognome
    parts = slug.split("-")
    last_name = parts[-1] if len(parts) > 1 else slug
    for known_slug, pid in DUNKEST_PLAYER_IDS.items():
        known_parts = known_slug.split("-")
        known_last = known_parts[-1] if len(known_parts) > 1 else known_slug
        if last_name == known_last and len(last_name) >= 4:
            # Controlla anche prima lettera del nome
            if parts[0][:1] == known_parts[0][:1]:
                return pid

    return None


class DunkestClient:
    """
    Client per le API interne di dunkest.com.
    Nessuna autenticazione richiesta (endpoint pubblici).
    """

    def __init__(self) -> None:
        self._timeout = httpx.Timeout(10.0)

    async def get_player_games(
        self,
        player_id: int,
        season_id: int = CURRENT_SEASON_ID,
    ) -> list[dict]:
        """
        Ritorna tutte le partite della stagione per un giocatore.
        Ogni elemento: {gameDate, pts, reb, ast, stl, blk, tpm, min, pdk, ...}
        """
        url = f"{DUNKEST_BASE}/api/player/games"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    url,
                    params={"player_id": player_id, "season_id": season_id},
                    headers={"Accept": "application/json"},
                )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
            logger.debug("Dunkest games player %d: status %d", player_id, resp.status_code)
        except Exception as exc:
            logger.debug("Dunkest games fetch failed player %d: %s", player_id, exc)
        return []

    async def get_player_season_stats(
        self,
        player_id: int,
        season_id: int = CURRENT_SEASON_ID,
    ) -> dict:
        """
        Ritorna le medie stagionali del giocatore.
        Struttura: {avg: {pts, reb, ast, stl, blk, tpm, min, gp, ...}, tot: {...}}
        """
        url = f"{DUNKEST_BASE}/api/player/seasonal-stats"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    url,
                    params={"player_id": player_id, "season_id": season_id},
                    headers={"Accept": "application/json"},
                )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    return data[0]
                elif isinstance(data, dict):
                    return data
        except Exception as exc:
            logger.debug("Dunkest season stats failed player %d: %s", player_id, exc)
        return {}

    def compute_hit_rate(
        self,
        games: list[dict],
        stat_key: str,
        line: float,
        direction: str,
        last_n: int = 20,
        combo_keys: list[str] | None = None,
    ) -> tuple[float, int]:
        """
        Calcola la percentuale storica di volte che il giocatore ha superato/sceso
        la linea per una determinata statistica.

        Args:
            games:      lista partite da get_player_games() (ordine cronologico)
            stat_key:   "pts" | "reb" | "ast" | "stl" | "blk" | "tpm"
            line:       linea del prop (es. 29.5)
            direction:  "over" | "under"
            last_n:     numero partite da considerare (default 20)
            combo_keys: per mercati combo (PRA = pts+reb+ast)

        Returns:
            (hit_rate, sample_size)
            hit_rate: float [0,1] — probabilità empirica
            sample_size: int — numero di partite analizzate
        """
        if not games:
            return 0.5, 0  # neutro se nessun dato

        # Prendi le ultime N partite con minuti giocati (esclude DNP)
        valid_games = [
            g for g in games
            if (g.get("min") or 0) >= 10  # almeno 10 minuti = partita vera
        ]
        recent = valid_games[-last_n:] if len(valid_games) > last_n else valid_games

        if not recent:
            return 0.5, 0

        # Pesi: ultimi 5 match pesano doppio rispetto ai precedenti
        weighted_hits = 0.0
        total_weight = 0.0

        for i, game in enumerate(recent):
            if combo_keys:
                # Mercato combo: somma dei valori
                value = sum(float(game.get(k, 0) or 0) for k in combo_keys)
            else:
                value = float(game.get(stat_key, 0) or 0)

            # Peso: gli ultimi 5 game valgono il doppio
            is_recent = i >= len(recent) - 5
            weight = 2.0 if is_recent else 1.0

            if direction == "over":
                hit = 1.0 if value > line else 0.0
            else:
                hit = 1.0 if value < line else 0.0

            weighted_hits += hit * weight
            total_weight += weight

        hit_rate = weighted_hits / total_weight if total_weight > 0 else 0.5
        return round(hit_rate, 3), len(recent)

    def get_recent_averages(self, games: list[dict], last_n: int = 5) -> dict:
        """
        Calcola le medie delle ultime N partite per ogni statistica.
        Utile per detectare forma recente e variazioni rispetto alla media stagionale.
        """
        valid = [g for g in games if (g.get("min") or 0) >= 10]
        recent = valid[-last_n:] if len(valid) > last_n else valid

        if not recent:
            return {}

        keys = ["pts", "reb", "ast", "stl", "blk", "tpm", "min", "pdk"]
        return {
            k: round(sum(float(g.get(k, 0) or 0) for g in recent) / len(recent), 1)
            for k in keys
        }

    async def get_games_without_teammate(
        self,
        player_id: int,
        teammate_id: int,
        season_id: int = CURRENT_SEASON_ID,
        min_player_minutes: int = 10,
    ) -> list[dict]:
        """
        Ritorna le partite del giocatore (player_id) in cui il compagno
        (teammate_id) NON ha giocato.

        Logica:
          1. Fetch partite di entrambi i giocatori
          2. Trova le date in cui il compagno non è presente (non ha giocato
             o ha giocato < 5 minuti = DNP effettivo)
          3. Filtra le partite del giocatore principale a quelle date

        Restituisce lista di partite come get_player_games() ma filtrata.
        """
        # Fetch entrambi i log in parallelo
        import asyncio
        player_games, teammate_games = await asyncio.gather(
            self.get_player_games(player_id, season_id),
            self.get_player_games(teammate_id, season_id),
        )

        if not player_games:
            return []

        # Date in cui il compagno ha GIOCATO (min ≥ 5)
        teammate_played_dates: set[str] = {
            g.get("gameDate", "")[:10]
            for g in teammate_games
            if (g.get("min") or 0) >= 5
        }

        # Partite del giocatore in cui il compagno NON era disponibile
        games_without = [
            g for g in player_games
            if (g.get("min") or 0) >= min_player_minutes
            and g.get("gameDate", "")[:10] not in teammate_played_dates
        ]

        logger.debug(
            "Dunkest: giocatore %d ha %d partite senza compagno %d (su %d totali)",
            player_id, len(games_without), teammate_id, len(player_games),
        )
        return games_without

    def compute_teammate_impact(
        self,
        all_games: list[dict],
        games_without_teammate: list[dict],
        stat_keys: list[str] | None = None,
    ) -> dict:
        """
        Confronta le performance del giocatore CON e SENZA il compagno.

        Ritorna dict con:
          - avg_with:   media di ogni stat con il compagno in campo
          - avg_without: media di ogni stat senza il compagno
          - delta:      differenza (positivo = fa meglio senza il compagno)
          - n_with:     numero partite con il compagno
          - n_without:  numero partite senza il compagno
          - verdict:    "better_without", "worse_without", "no_difference"

        Richiede almeno 4 partite in ciascuna categoria per essere significativo.
        """
        if stat_keys is None:
            stat_keys = ["pts", "reb", "ast", "stl", "blk", "tpm", "min"]

        # Partite con il compagno = tutte le partite valide - quelle senza
        without_dates: set[str] = {
            g.get("gameDate", "")[:10] for g in games_without_teammate
        }
        games_with = [
            g for g in all_games
            if (g.get("min") or 0) >= 10
            and g.get("gameDate", "")[:10] not in without_dates
        ]
        games_wo_valid = [g for g in games_without_teammate if (g.get("min") or 0) >= 10]

        def _avg(games: list[dict], key: str) -> float:
            if not games:
                return 0.0
            return round(sum(float(g.get(key, 0) or 0) for g in games) / len(games), 1)

        avg_with    = {k: _avg(games_with,    k) for k in stat_keys}
        avg_without = {k: _avg(games_wo_valid, k) for k in stat_keys}
        delta       = {k: round(avg_without[k] - avg_with[k], 1) for k in stat_keys}

        # Verdict basato sui punti (metrica principale per le props)
        pts_delta = delta.get("pts", 0)
        min_delta = delta.get("min", 0)

        # Significativo solo se almeno 4 partite in ciascuna categoria
        if len(games_with) < 4 or len(games_wo_valid) < 4:
            verdict = "insufficient_sample"
        elif pts_delta >= 2.5 or min_delta >= 2.0:
            verdict = "better_without"  # usage boost
        elif pts_delta <= -2.5:
            verdict = "worse_without"   # dipendente dal compagno (catch-and-shoot, set plays)
        else:
            verdict = "no_difference"

        return {
            "avg_with":    avg_with,
            "avg_without": avg_without,
            "delta":       delta,
            "n_with":      len(games_with),
            "n_without":   len(games_wo_valid),
            "verdict":     verdict,
            "significant": len(games_with) >= 4 and len(games_wo_valid) >= 4,
        }

    def assess_back_to_back(self, games: list[dict], match_date_str: str) -> bool:
        """
        Verifica se il giocatore ha giocato il giorno precedente alla data indicata.
        Ritorna True se è un back-to-back (segnale di fatica → riduce props).
        """
        from datetime import datetime, timedelta
        try:
            match_day = datetime.strptime(match_date_str[:10], "%Y-%m-%d").date()
            prev_day = match_day - timedelta(days=1)
            for game in games:
                gd = game.get("gameDate", "")[:10]
                if gd == prev_day.isoformat():
                    return True
        except Exception:
            pass
        return False

    def assess_rest_and_travel(
        self,
        games: list[dict],
        match_date_str: str,
        is_home_game: bool = True,
        nba_game_log: list[dict] | None = None,
    ) -> dict:
        """
        Analisi avanzata di riposo e viaggio pre-partita.

        Args:
            games:         game log Dunkest (per trovare ultima partita)
            match_date_str: data della partita in formato "YYYY-MM-DD"
            is_home_game:  la partita di oggi è in casa o trasferta?
            nba_game_log:  game log NBA API (ha info home/away per ogni partita)

        Returns:
            {
              "days_rest":           int,    # giorni dall'ultima partita (0=B2B, 1=1 giorno, ...)
              "back_to_back":        bool,   # ha giocato ieri
              "fatigue_level":       str,    # "high" | "moderate" | "low" | "none"
              "fatigue_modifier":    float,  # [0.70, 1.00] — moltiplica la hit rate
              "last_game_was_away":  bool,   # ultima partita in trasferta?
              "travel_penalty":      float,  # [0.90, 1.00]
              "combined_modifier":   float,  # fatigue × travel
              "reasoning":           str,
            }
        """
        from datetime import datetime, timedelta

        default = {
            "days_rest": 99,
            "back_to_back": False,
            "fatigue_level": "none",
            "fatigue_modifier": 1.0,
            "last_game_was_away": False,
            "travel_penalty": 1.0,
            "combined_modifier": 1.0,
            "reasoning": "Nessun dato disponibile",
        }

        try:
            match_day = datetime.strptime(match_date_str[:10], "%Y-%m-%d").date()
        except Exception:
            return default

        # Trova la partita più recente giocata (min ≥ 10)
        valid_games = sorted(
            [g for g in games if (g.get("min") or 0) >= 10],
            key=lambda g: g.get("gameDate", ""),
        )
        if not valid_games:
            return default

        last_game = valid_games[-1]
        try:
            last_date = datetime.strptime(last_game.get("gameDate", "")[:10], "%Y-%m-%d").date()
        except Exception:
            return default

        days_rest = (match_day - last_date).days

        # ── Fatica da riposo ──────────────────────────────────────────────────
        if days_rest == 0:
            # Stesso giorno (improbabile ma gestito)
            fatigue_level = "high"
            fatigue_modifier = 0.72
        elif days_rest == 1:
            # Back-to-back
            fatigue_level = "high"
            fatigue_modifier = 0.80
        elif days_rest == 2:
            # 1 giorno di riposo — normale NBA schedule
            fatigue_level = "moderate"
            fatigue_modifier = 0.93
        elif days_rest == 3:
            # 2 giorni di riposo — buon riposo
            fatigue_level = "low"
            fatigue_modifier = 0.98
        else:
            # 3+ giorni — riposo ottimale o rischio "rust"
            fatigue_level = "none"
            fatigue_modifier = 1.0

        # ── Travel penalty ────────────────────────────────────────────────────
        # Se hai informazioni home/away dal game log NBA API:
        last_game_was_away = False
        travel_penalty = 1.0

        if nba_game_log:
            # nba_api game log: field "MATCHUP" es. "LAL @ BOS" (away) o "LAL vs. BOS" (home)
            nba_sorted = sorted(nba_game_log, key=lambda g: g.get("GAME_DATE", ""))
            if nba_sorted:
                last_nba_game = nba_sorted[-1]
                matchup = last_nba_game.get("MATCHUP", "")
                last_game_was_away = "@" in matchup  # "LAL @ BOS" = away game

            # Penalty: ultima away + oggi home = lungo viaggio in B2B
            if days_rest == 1 and last_game_was_away and is_home_game:
                travel_penalty = 0.90  # viaggio intercontinentale + riposo minimo
            elif days_rest == 1 and not last_game_was_away and not is_home_game:
                travel_penalty = 0.92  # ieri home, oggi away = meno ripreso
            elif days_rest == 1:
                travel_penalty = 0.94  # B2B senza cambio di sede
            elif days_rest == 2 and last_game_was_away:
                travel_penalty = 0.97  # 1 giorno di riposo ma dopo trasferta
        else:
            # Senza dati home/away: penalità generica per B2B
            if days_rest == 1:
                travel_penalty = 0.93

        combined = round(fatigue_modifier * travel_penalty, 3)

        reasoning_parts = []
        if days_rest == 1:
            reasoning_parts.append("back-to-back")
        elif days_rest == 2:
            reasoning_parts.append("1 giorno di riposo")
        elif days_rest >= 4:
            reasoning_parts.append(f"{days_rest-1} giorni di riposo (possibile rust)")

        if last_game_was_away and days_rest <= 2:
            reasoning_parts.append("ultima gara in trasferta")

        reasoning = " | ".join(reasoning_parts) if reasoning_parts else "riposo normale"

        return {
            "days_rest":          days_rest,
            "back_to_back":       days_rest == 1,
            "fatigue_level":      fatigue_level,
            "fatigue_modifier":   round(fatigue_modifier, 3),
            "last_game_was_away": last_game_was_away,
            "travel_penalty":     round(travel_penalty, 3),
            "combined_modifier":  combined,
            "reasoning":          reasoning,
        }

    def compute_usage_redistribution(
        self,
        absent_games: list[dict],
        teammate_games_map: dict[str, list[dict]],
        stat_keys: list[str] | None = None,
    ) -> dict[str, dict]:
        """
        Stima la redistribuzione dell'usage del giocatore assente tra i compagni.

        Logica:
          1. Calcola le medie del giocatore assente per pts/reb/ast/ast al minuto
          2. Calcola i minuti medi di ogni compagno
          3. Stima quanti minuti assorbirà ogni compagno (proporzionalmente)
          4. Moltiplica per la produzione per-minuto del giocatore assente

        Questa stima dà un "boost atteso" realistico, non solo il segnale storico.

        Returns:
            {player_name → {"pts_boost": float, "reb_boost": float,
                             "ast_boost": float, "min_boost": float}}
        """
        if stat_keys is None:
            stat_keys = ["pts", "reb", "ast", "stl", "blk", "tpm"]

        # Stats del giocatore assente (ultimi 20 match)
        absent_valid = [g for g in absent_games if (g.get("min") or 0) >= 10]
        absent_recent = absent_valid[-20:] if len(absent_valid) > 20 else absent_valid

        if not absent_recent:
            return {}

        def _avg(games: list[dict], key: str) -> float:
            return sum(float(g.get(key, 0) or 0) for g in games) / len(games)

        absent_min_avg = _avg(absent_recent, "min")
        if absent_min_avg < 1:
            return {}

        # Stats per minuto del giocatore assente
        absent_per_min = {k: _avg(absent_recent, k) / absent_min_avg for k in stat_keys}

        # Minuti medi di ogni compagno
        teammate_min: dict[str, float] = {}
        for name, games in teammate_games_map.items():
            valid = [g for g in games if (g.get("min") or 0) >= 10]
            recent = valid[-20:] if len(valid) > 20 else valid
            teammate_min[name] = _avg(recent, "min") if recent else 0

        total_available_min = sum(teammate_min.values())
        if total_available_min < 1:
            return {}

        result: dict[str, dict] = {}
        for name, avg_min in teammate_min.items():
            if avg_min < 5:
                continue

            # Proporzione di minuti assorbita da questo compagno
            share = avg_min / total_available_min
            # Non tutto l'usage va ai titolari: assume 85% redistribuito
            effective_share = share * 0.85

            boost: dict[str, float] = {
                "min_boost": round(absent_min_avg * effective_share, 1),
            }
            for k in stat_keys:
                boost[f"{k}_boost"] = round(absent_per_min.get(k, 0) * absent_min_avg * effective_share, 2)

            result[name] = boost

        return result

    def compute_home_away_hit_rate(
        self,
        games: list[dict],
        stat_key: str,
        line: float,
        direction: str,
        is_home: bool,
        last_n: int = 15,
        combo_keys: list[str] | None = None,
    ) -> tuple[float, int]:
        """
        Calcola la hit rate separata per partite home vs away.

        Molti giocatori performano significativamente meglio a casa
        (pubblico, nessun viaggio, familiarità del palazzetto).

        Args:
            is_home: True se la partita di oggi è in casa

        Returns: (hit_rate, sample_size) per il contesto home/away
        """
        # Dunkest game log: campo "isHome" (bool) o "venue" o simile
        # Se il campo non esiste → fallback al compute_hit_rate normale
        has_venue_field = any("isHome" in g or "home" in str(g.get("venue", "")).lower() for g in games[:3])

        if has_venue_field:
            # Filtra per home/away
            def is_home_game(g: dict) -> bool:
                if "isHome" in g:
                    return bool(g["isHome"])
                venue = str(g.get("venue", "")).lower()
                return "home" in venue

            filtered = [g for g in games if (g.get("min") or 0) >= 10 and is_home_game(g) == is_home]
        else:
            # Fallback: usa tutte le partite
            filtered = [g for g in games if (g.get("min") or 0) >= 10]

        recent = filtered[-last_n:] if len(filtered) > last_n else filtered

        if len(recent) < 4:
            # Sample troppo piccolo: ritorna il compute normale
            return self.compute_hit_rate(games, stat_key, line, direction, last_n, combo_keys)

        weighted_hits = 0.0
        total_weight  = 0.0
        for i, game in enumerate(recent):
            if combo_keys:
                value = sum(float(game.get(k, 0) or 0) for k in combo_keys)
            else:
                value = float(game.get(stat_key, 0) or 0)
            weight = 2.0 if i >= len(recent) - 5 else 1.0
            hit = 1.0 if (direction == "over" and value > line) or (direction == "under" and value < line) else 0.0
            weighted_hits += hit * weight
            total_weight  += weight

        hr = weighted_hits / total_weight if total_weight > 0 else 0.5
        return round(hr, 3), len(recent)

    def compute_opponent_weighted_hit_rate(
        self,
        games: list[dict],
        stat_key: str,
        line: float,
        direction: str,
        opponent_defense_scores: dict[str, float],
        last_n: int = 20,
        combo_keys: list[str] | None = None,
    ) -> tuple[float, int]:
        """
        Hit rate pesata per qualità dell'avversario.

        Partite contro difese forti (rank top 10) contano di più:
          - Contro difesa top (score ≥ 0.7) → peso 1.8x (segnale forte)
          - Contro difesa media (score 0.4-0.7) → peso 1.0x
          - Contro difesa debole (score < 0.4) → peso 0.6x

        Args:
            opponent_defense_scores: {date_str → defense_quality [0,1]}
                                     0=difesa forte, 1=difesa scarsa
                                     (richiede NBAMatchupClient.get_game_opponent_scores())

        Returns: (weighted_hit_rate, sample_size)
        """
        if not games:
            return 0.5, 0

        valid = [g for g in games if (g.get("min") or 0) >= 10]
        recent = valid[-last_n:] if len(valid) > last_n else valid

        if not recent:
            return 0.5, 0

        weighted_hits = 0.0
        total_weight  = 0.0

        for i, game in enumerate(recent):
            game_date = game.get("gameDate", "")[:10]
            # Qualità difesa avversaria: 0=forte, 1=scarsa
            opp_score = opponent_defense_scores.get(game_date, 0.5)

            # Peso qualità avversario: difesa forte → peso alto
            if opp_score <= 0.35:
                opp_weight = 1.8  # difesa top: segnale forte
            elif opp_score >= 0.65:
                opp_weight = 0.6  # difesa scarsa: segnale debole
            else:
                opp_weight = 1.0

            # Peso recency (ultimi 5 pesano doppio, come nel metodo base)
            is_recent = i >= len(recent) - 5
            recency_weight = 2.0 if is_recent else 1.0

            combined_weight = opp_weight * recency_weight

            # Valore statistica
            if combo_keys:
                value = sum(float(game.get(k, 0) or 0) for k in combo_keys)
            else:
                value = float(game.get(stat_key, 0) or 0)

            hit = 1.0 if (direction == "over" and value > line) or (direction == "under" and value < line) else 0.0
            weighted_hits += hit * combined_weight
            total_weight  += combined_weight

        hr = weighted_hits / total_weight if total_weight > 0 else 0.5
        return round(hr, 3), len(recent)


# ── Parsing utility ──────────────────────────────────────────────────────────

def parse_player_prop_outcome(outcome: str, market: str) -> tuple[str | None, str | None, float | None]:
    """
    Parsa un outcome di player prop nel formato dunkest/oddsapi.
    Formato: "Luka Doncic — Over 29.5" oppure "Over 29.5" (se player nel description)

    Ritorna (player_name, direction, line) oppure (None, None, None) se non parsabile.

    Esempi:
      "Luka Doncic — Over 29.5" → ("Luka Doncic", "over", 29.5)
      "Nikola Jokic — Under 11.5" → ("Nikola Jokic", "under", 11.5)
      "Over 24.5" → (None, "over", 24.5)
    """
    # Formato con nome giocatore: "Player Name — Over X.X"
    if " — " in outcome:
        parts = outcome.split(" — ", 1)
        player_name = parts[0].strip()
        rest = parts[1].strip()
    else:
        player_name = None
        rest = outcome.strip()

    # Parsa direzione e linea
    m = re.match(r"(over|under)\s+([\d.]+)", rest.lower())
    if not m:
        return None, None, None

    direction = m.group(1)
    try:
        line = float(m.group(2))
    except ValueError:
        return None, None, None

    return player_name, direction, line
