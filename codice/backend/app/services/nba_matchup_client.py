"""
NBA Matchup Client — analisi difensiva avversario per posizione.

Usa nba_api (già installato) per:
  - Ranking difensivo per squadra (opponent pts/reb/ast per game)
  - Difesa per zona: pittura vs perimetro (leaguedashptdefend)
  - Classificazione matchup per ruolo: "exploitable" | "neutral" | "tough"

Logica di matchup per posizione:
  - PG/SG:  peso maggiore su difesa perimetrale (3pt allowed, opp assists allowed)
  - SF:     mix perimetro + pittura
  - PF/C:   peso maggiore su difesa pittura (paint pts allowed, opp reb allowed)

Output per ogni coppia (team_avversario, posizione):
  {
    "team": str,
    "position": str,
    "matchup_rating": "exploitable" | "neutral" | "tough",
    "opp_pts_rank": int,          # 1=worst defense (allow most pts), 30=best
    "paint_rank": int,
    "perimeter_rank": int,
    "details": dict,
  }
"""
from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# ── Posizioni giocatori NBA ────────────────────────────────────────────────────
# Slug normalizzato → posizione (PG, SG, SF, PF, C)
PLAYER_POSITIONS: dict[str, str] = {
    # Point Guards
    "stephen-curry":          "PG",
    "luka-doncic":            "PG",
    "damian-lillard":         "PG",
    "trae-young":             "PG",
    "tyrese-haliburton":      "PG",
    "ja-morant":              "PG",
    "jalen-brunson":          "PG",
    "shai-gilgeous-alexander": "PG",
    "cade-cunningham":        "PG",
    "dejounte-murray":        "PG",
    "fred-vanvleet":          "PG",
    "chris-paul":             "PG",
    "mike-conley":            "PG",
    "lonzo-ball":             "PG",
    "josh-giddey":            "PG",
    "immanuel-quickley":      "PG",
    "lamelo-ball":            "PG",
    "anthony-edwards":        "SG",  # listed PG but plays SG
    # Shooting Guards / Wings
    "devin-booker":           "SG",
    "donovan-mitchell":       "SG",
    "bradley-beal":           "SG",
    "klay-thompson":          "SG",
    "zach-lavine":            "SG",
    "de-aaron-fox":           "SG",
    "jaylen-brown":           "SG",
    "malik-beasley":          "SG",
    "bogdan-bogdanovic":      "SG",
    "fred-vanvleet":          "SG",
    "jalen-green":            "SG",
    "desmond-bane":           "SG",
    "jordan-poole":           "SG",
    "max-strus":              "SG",
    # Small Forwards
    "lebron-james":           "SF",
    "kawhi-leonard":          "SF",
    "paul-george":            "SF",
    "jayson-tatum":           "SF",
    "jimmy-butler":           "SF",
    "khris-middleton":        "SF",
    "og-anunoby":             "SF",
    "mikal-bridges":          "SF",
    "miles-bridges":          "SF",
    "franz-wagner":           "SF",
    "scottie-barnes":         "SF",
    "andrew-wiggins":         "SF",
    "dillon-brooks":          "SF",
    "herbert-jones":          "SF",
    "herb-jones":             "SF",
    "royce-oconnell":         "SF",
    "paolo-banchero":         "SF",
    # Power Forwards
    "giannis-antetokounmpo":  "PF",
    "kevin-durant":           "PF",
    "pascal-siakam":          "PF",
    "draymond-green":         "PF",
    "gordon-hayward":         "PF",
    "julius-randle":          "PF",
    "john-collins":           "PF",
    "jarrett-allen":          "C",   # sometimes PF/C
    "evan-mobley":            "PF",
    "jabari-smith-jr":        "PF",
    "nic-claxton":            "C",
    "keegan-murray":          "PF",
    "thaddeus-young":         "PF",
    "darius-garland":         "PG",
    # Centers
    "nikola-jokic":           "C",
    "joel-embiid":            "C",
    "bam-adebayo":            "C",
    "karl-anthony-towns":     "C",
    "rudy-gobert":            "C",
    "myles-turner":           "C",
    "chet-holmgren":          "C",
    "victor-wembanyama":      "C",
    "deandre-ayton":          "C",
    "brook-lopez":            "C",
    "steven-adams":           "C",
    "jonas-valanciunas":      "C",
    "nikola-vucevic":         "C",
    "clint-capela":           "C",
    "alperen-sengun":         "C",
    "donovan-clingan":        "C",
    "walker-kessler":         "C",
    "alexandre-sarr":         "C",
    "daniel-gafford":         "C",
}

# ── Team name → NBA API Team ID ───────────────────────────────────────────────
NBA_TEAM_IDS: dict[str, int] = {
    "atlanta-hawks":           1610612737,
    "boston-celtics":          1610612738,
    "brooklyn-nets":           1610612751,
    "charlotte-hornets":       1610612766,
    "chicago-bulls":           1610612741,
    "cleveland-cavaliers":     1610612739,
    "dallas-mavericks":        1610612742,
    "denver-nuggets":          1610612743,
    "detroit-pistons":         1610612765,
    "golden-state-warriors":   1610612744,
    "houston-rockets":         1610612745,
    "indiana-pacers":          1610612754,
    "los-angeles-clippers":    1610612746,
    "los-angeles-lakers":      1610612747,
    "memphis-grizzlies":       1610612763,
    "miami-heat":              1610612748,
    "milwaukee-bucks":         1610612749,
    "minnesota-timberwolves":  1610612750,
    "new-orleans-pelicans":    1610612740,
    "new-york-knicks":         1610612752,
    "oklahoma-city-thunder":   1610612760,
    "orlando-magic":           1610612753,
    "philadelphia-76ers":      1610612755,
    "phoenix-suns":            1610612756,
    "portland-trail-blazers":  1610612757,
    "sacramento-kings":        1610612758,
    "san-antonio-spurs":       1610612759,
    "toronto-raptors":         1610612761,
    "utah-jazz":               1610612762,
    "washington-wizards":      1610612764,
}

# ── Team abbreviation → slug ──────────────────────────────────────────────────
_ABBR_TO_SLUG: dict[str, str] = {
    "ATL": "atlanta-hawks",          "BOS": "boston-celtics",
    "BKN": "brooklyn-nets",          "CHA": "charlotte-hornets",
    "CHI": "chicago-bulls",          "CLE": "cleveland-cavaliers",
    "DAL": "dallas-mavericks",       "DEN": "denver-nuggets",
    "DET": "detroit-pistons",        "GSW": "golden-state-warriors",
    "HOU": "houston-rockets",        "IND": "indiana-pacers",
    "LAC": "los-angeles-clippers",   "LAL": "los-angeles-lakers",
    "MEM": "memphis-grizzlies",      "MIA": "miami-heat",
    "MIL": "milwaukee-bucks",        "MIN": "minnesota-timberwolves",
    "NOP": "new-orleans-pelicans",   "NYK": "new-york-knicks",
    "OKC": "oklahoma-city-thunder",  "ORL": "orlando-magic",
    "PHI": "philadelphia-76ers",     "PHX": "phoenix-suns",
    "POR": "portland-trail-blazers", "SAC": "sacramento-kings",
    "SAS": "san-antonio-spurs",      "TOR": "toronto-raptors",
    "UTA": "utah-jazz",              "WAS": "washington-wizards",
}

# ── Position grouping for matchup analysis ────────────────────────────────────
# Perimeter positions: weight 3PT allowed, assist ratio
_PERIMETER_POSITIONS = {"PG", "SG"}
# Interior positions: weight paint pts, reb allowed
_INTERIOR_POSITIONS  = {"PF", "C"}
# Wing: mixed
_WING_POSITIONS      = {"SF"}


def _slug(name: str) -> str:
    """Normalizza nome in slug per lookup dizionari."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    lower = ascii_str.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s\-]", "", lower)
    return re.sub(r"\s+", "-", cleaned.strip())


def _team_slug(team_name: str) -> str:
    """Cerca il team slug dai name fragments."""
    from app.services.nba_news_client import _TEAM_FRAGMENTS
    lower = team_name.lower()
    for fragment, slug in _TEAM_FRAGMENTS.items():
        if fragment in lower:
            return slug
    # Abbreviation lookup
    upper = team_name.upper().strip()
    if upper in _ABBR_TO_SLUG:
        return _ABBR_TO_SLUG[upper]
    return _slug(team_name)


def get_player_position(player_name: str) -> str:
    """
    Ritorna la posizione NBA di un giocatore (PG/SG/SF/PF/C).
    Fallback: "SF" (posizione più comune per forward generici).
    """
    s = _slug(player_name)
    return PLAYER_POSITIONS.get(s, "SF")


class NBAMatchupClient:
    """
    Analisi matchup difensivo avversario per posizione.
    Usa nba_api (già in pyproject.toml) per dati ufficiali NBA Stats.
    """

    SEASON = "2024-25"

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}  # team_slug → defense_data

    async def get_league_defense_rankings(self) -> dict[str, dict]:
        """
        Carica i ranking difensivi di tutte le 30 squadre.
        Ritorna: {team_slug → {opp_pts_rank, opp_reb_rank, opp_ast_rank,
                               paint_rank, perimeter_rank, ...}}

        Usa nba_api.stats.endpoints.LeagueDashTeamStats con MeasureType=Opponent.
        """
        if self._cache:
            return self._cache

        try:
            data = await self._fetch_opponent_stats()
            self._cache = data
            return data
        except Exception as exc:
            logger.warning("NBA matchup fetch failed: %s", exc)
            return {}

    async def _fetch_opponent_stats(self) -> dict[str, dict]:
        """Fetch opponent stats from NBA Stats API."""
        import asyncio

        # nba_api è sync — esegui in thread pool
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, self._fetch_sync)
        return raw

    def _fetch_sync(self) -> dict[str, dict]:
        """Sync NBA API call — da eseguire in executor."""
        try:
            from nba_api.stats.endpoints import LeagueDashTeamStats
        except ImportError:
            logger.warning("nba_api not installed — matchup data unavailable")
            return {}

        try:
            response = LeagueDashTeamStats(
                measure_type_detailed_defense="Opponent",
                per_mode_simple="PerGame",
                season=self.SEASON,
                season_type_all_star="Regular Season",
                timeout=15,
            )
            df = response.get_data_frames()[0]
        except Exception as exc:
            logger.warning("LeagueDashTeamStats fetch failed: %s", exc)
            return {}

        # Colonne attese: TEAM_NAME, OPP_PTS, OPP_REB, OPP_AST, OPP_FG3M, ...
        result: dict[str, dict] = {}
        n = len(df)
        if n == 0:
            return {}

        # Calcola rank per ogni metrica (1 = peggiore difesa = permette di più)
        # "peggiore" per i difensori = migliore per gli attaccanti
        for col in ["OPP_PTS", "OPP_REB", "OPP_AST", "OPP_FG3M", "OPP_FGA"]:
            if col in df.columns:
                # rank ascending=False → 1 = team che permette il massimo
                df[f"{col}_RANK"] = df[col].rank(ascending=False, method="min").astype(int)

        for _, row in df.iterrows():
            team_name = str(row.get("TEAM_NAME", ""))
            slug = _team_slug(team_name)

            result[slug] = {
                "team_name":      team_name,
                "opp_pts":        float(row.get("OPP_PTS", 0)),
                "opp_reb":        float(row.get("OPP_REB", 0)),
                "opp_ast":        float(row.get("OPP_AST", 0)),
                "opp_fg3m":       float(row.get("OPP_FG3M", 0)),
                "opp_pts_rank":   int(row.get("OPP_PTS_RANK", 15)),
                "opp_reb_rank":   int(row.get("OPP_REB_RANK", 15)),
                "opp_ast_rank":   int(row.get("OPP_AST_RANK", 15)),
                "opp_fg3m_rank":  int(row.get("OPP_FG3M_RANK", 15)),
            }

        logger.info("NBA matchup: loaded defense data for %d teams", len(result))
        return result

    def classify_matchup(
        self,
        opposing_team: str,
        player_position: str,
        defense_data: dict[str, dict],
    ) -> dict:
        """
        Classifica il matchup difensivo per un giocatore contro una squadra.

        Args:
            opposing_team:  nome della squadra avversaria
            player_position: posizione del giocatore (PG/SG/SF/PF/C)
            defense_data:   output di get_league_defense_rankings()

        Returns:
            {
              "rating": "exploitable" | "neutral" | "tough",
              "score": float [0,1],   # 1 = più exploitable
              "reasoning": str,
              "opp_pts_rank": int,
            }
        """
        slug = _team_slug(opposing_team)
        team_data = defense_data.get(slug)

        if not team_data:
            # Fallback: cerca partial match
            for ts, td in defense_data.items():
                if any(w in slug for w in ts.split("-") if len(w) > 4):
                    team_data = td
                    break

        if not team_data:
            return {
                "rating":      "neutral",
                "score":       0.5,
                "reasoning":   f"No data for {opposing_team}",
                "opp_pts_rank": 15,
            }

        n_teams = 30
        opp_pts_rank  = team_data.get("opp_pts_rank", 15)
        opp_reb_rank  = team_data.get("opp_reb_rank", 15)
        opp_ast_rank  = team_data.get("opp_ast_rank", 15)
        opp_fg3m_rank = team_data.get("opp_fg3m_rank", 15)

        # Score basato su posizione
        # rank 1 (worst defense) → score 1.0 (exploitable)
        # rank 30 (best defense) → score 0.0 (tough)
        def rank_to_score(rank: int) -> float:
            return (n_teams - rank) / (n_teams - 1) if n_teams > 1 else 0.5

        if player_position in _PERIMETER_POSITIONS:
            # PG/SG: pesa pts (50%), assist (30%), 3pm (20%)
            score = (
                0.50 * rank_to_score(opp_pts_rank)
                + 0.30 * rank_to_score(opp_ast_rank)
                + 0.20 * rank_to_score(opp_fg3m_rank)
            )
            focus = f"perimeter defense rank pts={opp_pts_rank} 3pm={opp_fg3m_rank}"

        elif player_position in _INTERIOR_POSITIONS:
            # PF/C: pesa pts (50%), reb (40%), ast (10%)
            score = (
                0.50 * rank_to_score(opp_pts_rank)
                + 0.40 * rank_to_score(opp_reb_rank)
                + 0.10 * rank_to_score(opp_ast_rank)
            )
            focus = f"interior defense rank pts={opp_pts_rank} reb={opp_reb_rank}"

        else:
            # SF / generico: mix
            score = (
                0.40 * rank_to_score(opp_pts_rank)
                + 0.30 * rank_to_score(opp_reb_rank)
                + 0.30 * rank_to_score(opp_fg3m_rank)
            )
            focus = f"wing defense rank pts={opp_pts_rank} reb={opp_reb_rank}"

        # Classificazione
        if score >= 0.65:
            rating = "exploitable"
            reasoning = (
                f"{opposing_team} è vulnerabile per {player_position} "
                f"({focus}) — matchup favorevole"
            )
        elif score <= 0.35:
            rating = "tough"
            reasoning = (
                f"{opposing_team} difende bene contro {player_position} "
                f"({focus}) — matchup difficile"
            )
        else:
            rating = "neutral"
            reasoning = f"{opposing_team} vs {player_position}: matchup neutro ({focus})"

        return {
            "rating":       rating,
            "score":        round(score, 3),
            "reasoning":    reasoning,
            "opp_pts_rank": opp_pts_rank,
            "opp_reb_rank": opp_reb_rank,
            "opp_fg3m_rank": opp_fg3m_rank,
            "team_data":    team_data,
        }

    def matchup_ev_modifier(self, rating: str) -> float:
        """
        Modificatore EV basato sul matchup difensivo.
        Exploitable → aumenta EV stimato del 10%
        Tough → riduce del 10%
        """
        return {"exploitable": 1.10, "neutral": 1.0, "tough": 0.90}.get(rating, 1.0)

    async def get_player_game_log_nba_api(
        self,
        player_id_nba: int,
        last_n: int = 20,
    ) -> list[dict]:
        """
        Fetch game log via nba_api.PlayerGameLog.
        Ritorna partite con avversario, home/away, stats complete.

        Campi: GAME_DATE, MATCHUP (es. "LAL vs. BOS" o "LAL @ BOS"),
               WL, MIN, PTS, REB, AST, STL, BLK, FG3M.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, self._fetch_game_log_sync, player_id_nba, last_n
            )
        except Exception as exc:
            logger.warning("NBA API game log failed for player %d: %s", player_id_nba, exc)
            return []

    def _fetch_game_log_sync(self, player_id_nba: int, last_n: int) -> list[dict]:
        try:
            from nba_api.stats.endpoints import PlayerGameLog
        except ImportError:
            return []
        try:
            response = PlayerGameLog(
                player_id=player_id_nba,
                season=self.SEASON,
                season_type_all_star="Regular Season",
                timeout=15,
            )
            df = response.get_data_frames()[0]
            if df.empty:
                return []
            records = df.head(last_n).to_dict("records")
            # Normalizza nomi colonne in minuscolo per coerenza
            return [{k.upper(): v for k, v in r.items()} for r in records]
        except Exception as exc:
            logger.warning("PlayerGameLog sync failed player %d: %s", player_id_nba, exc)
            return []

    async def get_game_opponent_scores(
        self,
        player_id_nba: int,
        defense_data: dict[str, dict],
        last_n: int = 20,
    ) -> dict[str, float]:
        """
        Per ogni partita del game log, trova la qualità difensiva dell'avversario.
        Ritorna {date_str → defense_score [0,1]}.
          0 = difesa top (avversario forte)
          1 = difesa scarsa (avversario debole)

        Utile per pesare la hit rate storica per qualità avversario.
        """
        game_log = await self.get_player_game_log_nba_api(player_id_nba, last_n)
        if not game_log:
            return {}

        scores: dict[str, float] = {}
        n_teams = 30

        for game in game_log:
            matchup = game.get("MATCHUP", "")
            date    = game.get("GAME_DATE", "")[:10]

            # Estrai avversario da matchup: "LAL vs. BOS" → "BOS", "LAL @ BOS" → "BOS"
            parts = matchup.replace("vs.", "@").split("@")
            if len(parts) >= 2:
                opp_abbr = parts[-1].strip().split()[-1].upper()
            else:
                continue

            # Risolvi abbr → slug
            opp_slug = _ABBR_TO_SLUG.get(opp_abbr, "")
            if not opp_slug:
                # Prova match parziale
                for abbr, slug in _ABBR_TO_SLUG.items():
                    if abbr in matchup.upper():
                        opp_slug = slug
                        break

            opp_data = defense_data.get(opp_slug, {})
            pts_rank = opp_data.get("opp_pts_rank", 15)

            # Score: rank 1 (worst defense) → 1.0, rank 30 (best) → 0.0
            score = (pts_rank - 1) / (n_teams - 1)
            if date:
                scores[date] = round(score, 3)

        return scores

    async def get_player_defender_matchup(
        self,
        player_name: str,
        opposing_team_slug: str,
        defense_data: dict[str, dict],
    ) -> dict:
        """
        Trova il difensore principale del giocatore nella squadra avversaria.
        Usa nba_api MatchupsRollup → coppie (attaccante, difensore) con frequenza.

        Returns:
            {
              "primary_defender": str,
              "defender_frequency": float,  # % di possessi difeso da lui
              "pts_per_possession": float,  # quanto segna l'attaccante quando difeso
              "defender_rating": str,       # "lockdown" | "average" | "poor"
              "matchup_note": str,
            }
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, self._fetch_matchups_sync, player_name, opposing_team_slug
            )
            return result
        except Exception as exc:
            logger.warning("Matchup defender fetch failed: %s", exc)
            return {"primary_defender": None, "defender_rating": "unknown", "matchup_note": ""}

    def _fetch_matchups_sync(self, player_name: str, opposing_team_slug: str) -> dict:
        """Fetch matchup data dalla NBA Stats API — sync."""
        try:
            from nba_api.stats.endpoints import MatchupsRollup
            from nba_api.stats.static import players as nba_players, teams as nba_teams
        except ImportError:
            return {"primary_defender": None, "defender_rating": "unknown", "matchup_note": ""}

        # Trova player_id NBA
        player_results = nba_players.find_players_by_full_name(player_name)
        if not player_results:
            # Prova con cognome
            last = player_name.split()[-1] if " " in player_name else player_name
            player_results = nba_players.find_players_by_full_name(last)
        if not player_results:
            return {"primary_defender": None, "defender_rating": "unknown", "matchup_note": ""}

        off_player_id = player_results[0]["id"]

        # Trova team ID avversario
        opp_team_id = None
        team_abbr = None
        for abbr, slug in _ABBR_TO_SLUG.items():
            if slug == opposing_team_slug:
                team_abbr = abbr
                break
        if team_abbr:
            teams_results = nba_teams.find_teams_by_abbreviation(team_abbr)
            if teams_results:
                opp_team_id = teams_results[0]["id"]

        try:
            rollup = MatchupsRollup(
                off_player_id_nullable=off_player_id,
                def_team_id_nullable=opp_team_id or "",
                season=self.SEASON,
                timeout=15,
            )
            df = rollup.get_data_frames()[0]
        except Exception as exc:
            logger.debug("MatchupsRollup failed: %s", exc)
            return {"primary_defender": None, "defender_rating": "unknown", "matchup_note": ""}

        if df.empty:
            return {"primary_defender": None, "defender_rating": "unknown", "matchup_note": ""}

        # Trova il difensore con più possessi
        best_row = df.sort_values("PARTIAL_POSS", ascending=False).iloc[0]
        defender_name = str(best_row.get("DEF_PLAYER_NAME", ""))
        frequency     = float(best_row.get("PARTIAL_POSS", 0))
        pts_per_poss  = float(best_row.get("PLAYER_PTS", 0)) / max(frequency, 1)

        # Classifica il difensore
        if pts_per_poss <= 0.6:
            rating = "lockdown"
            note = f"{defender_name} è un difensore di élite — attenzione agli over"
        elif pts_per_poss >= 1.0:
            rating = "poor"
            note = f"{defender_name} difende male questo giocatore — over favorito"
        else:
            rating = "average"
            note = f"{defender_name} è il difensore primario — matchup neutro"

        return {
            "primary_defender":  defender_name,
            "defender_frequency": round(frequency, 1),
            "pts_per_possession": round(pts_per_poss, 3),
            "defender_rating":   rating,
            "matchup_note":      note,
        }

    def defender_ev_modifier(self, defender_rating: str) -> float:
        """
        Modificatore EV basato sul difensore specifico.
        lockdown → -12%, poor → +12%, average → neutro
        """
        return {"lockdown": 0.88, "average": 1.0, "poor": 1.12, "unknown": 1.0}.get(
            defender_rating, 1.0
        )

    async def get_team_pace_data(self) -> dict[str, float]:
        """
        Ritorna il ritmo di gioco (possessi/48 min) di ogni squadra NBA.
        Usa nba_api LeagueDashTeamStats con MeasureType=Advanced.

        Returns: {team_slug → pace_float}
        League average pace ≈ 100.0
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._fetch_pace_sync)
        except Exception as exc:
            logger.warning("Pace data fetch failed: %s", exc)
            return {}

    def _fetch_pace_sync(self) -> dict[str, float]:
        try:
            from nba_api.stats.endpoints import LeagueDashTeamStats
        except ImportError:
            return {}
        try:
            response = LeagueDashTeamStats(
                measure_type_detailed_defense="Advanced",
                per_mode_simple="PerGame",
                season=self.SEASON,
                season_type_all_star="Regular Season",
                timeout=15,
            )
            df = response.get_data_frames()[0]
        except Exception as exc:
            logger.warning("Pace fetch failed: %s", exc)
            return {}

        result: dict[str, float] = {}
        for _, row in df.iterrows():
            team_name = str(row.get("TEAM_NAME", ""))
            slug = _team_slug(team_name)
            pace = float(row.get("PACE", 100.0) or 100.0)
            result[slug] = round(pace, 2)
        return result

    def compute_pace_modifier(
        self,
        player_team_slug: str,
        opposing_team_slug: str,
        pace_data: dict[str, float],
        stat_type: str = "pts",
    ) -> float:
        """
        Calcola il modificatore di pace per una prop.

        Logica: se la combinazione di team gioca più veloce della media
        (più possessi) → l'over è più probabile.

        Il pace modifier si applica come aggiustamento alla hit rate:
          - Pace combo 5% sopra media → modifier 1.04 (boost over)
          - Pace combo 5% sotto media → modifier 0.96 (penalità over)

        Solo per mercati scoring (pts, ast, tpm). Non per reb.

        Returns: float [0.90, 1.10]
        """
        if stat_type in ("reb", "blk", "stl"):
            return 1.0  # rimbalzi/stoppate meno influenzati dal pace

        league_avg = 100.0
        p1 = pace_data.get(player_team_slug, league_avg)
        p2 = pace_data.get(opposing_team_slug, league_avg)
        # Pace della partita = media dei due team
        game_pace = (p1 + p2) / 2.0

        deviation = (game_pace - league_avg) / league_avg  # es. +0.05 = 5% più veloce
        # Clamp: max ±10% deviation → max ±4% modifier
        modifier = 1.0 + (deviation * 0.8)
        return round(max(0.90, min(1.10, modifier)), 3)

    async def get_player_vs_team_stats(
        self,
        player_id_nba: int,
        opposing_team_id: int,
        last_seasons: int = 2,
    ) -> dict:
        """
        Recupera lo storico di un giocatore contro una squadra specifica.
        Usa nba_api PlayerGameLog filtrato per avversario.

        Returns:
            {
              "games": int,
              "avg_pts": float,
              "avg_reb": float,
              "avg_ast": float,
              "avg_min": float,
              "has_history": bool,
            }
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, self._fetch_vs_team_sync, player_id_nba, opposing_team_id
            )
        except Exception as exc:
            logger.warning("Player vs team stats failed: %s", exc)
            return {"has_history": False, "games": 0}

    def _fetch_vs_team_sync(self, player_id_nba: int, opposing_team_id: int) -> dict:
        try:
            from nba_api.stats.endpoints import PlayerGameLog
        except ImportError:
            return {"has_history": False, "games": 0}
        try:
            # Fetch current + last season
            all_games = []
            for season in [self.SEASON, "2023-24"]:
                try:
                    resp = PlayerGameLog(
                        player_id=player_id_nba,
                        season=season,
                        season_type_all_star="Regular Season",
                        vs_team_id_nullable=opposing_team_id,
                        timeout=12,
                    )
                    df = resp.get_data_frames()[0]
                    if not df.empty:
                        all_games.extend(df.to_dict("records"))
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("PlayerGameLog vs team failed: %s", exc)
            return {"has_history": False, "games": 0}

        if not all_games:
            return {"has_history": False, "games": 0}

        import statistics
        pts_list = [float(g.get("PTS", 0) or 0) for g in all_games]
        reb_list  = [float(g.get("REB", 0) or 0) for g in all_games]
        ast_list  = [float(g.get("AST", 0) or 0) for g in all_games]
        min_list  = [float(g.get("MIN", 0) or 0) for g in all_games]

        return {
            "has_history": True,
            "games":    len(all_games),
            "avg_pts":  round(sum(pts_list) / len(pts_list), 1),
            "avg_reb":  round(sum(reb_list) / len(reb_list), 1),
            "avg_ast":  round(sum(ast_list) / len(ast_list), 1),
            "avg_min":  round(sum(min_list) / len(min_list), 1),
        }
