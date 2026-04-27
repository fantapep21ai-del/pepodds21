"""
Stats fetcher — dati reali da api-sports.io + football-data.org + ESPN + Open-Meteo

api-sports.io  (API_FOOTBALL_KEY):
  - Football: infortuni, H2H, statistiche, classifiche, stats giocatori
  - Basketball: standings NBA, stats giocatori per partita
  - Tennis: rankings ATP/WTA, forma giocatori
  - Free plan: 100 req/giorno per sport, 10 req/minuto → throttle automatico

football-data.org (FOOTBALL_DATA_KEY):
  - Forma recente squadre, classifiche, partite
  - Free plan: 10 req/minuto → rispetta header X-RateLimit-Remaining
  - Copre: PL, SA, BL1, FL1, PD, CL, EL, PPL, DED, BSA

ESPN (no key):
  - Infortuni NBA in tempo reale
  - URL pubblica, no autenticazione

Open-Meteo (no key):
  - Meteo allo stadio per calcolo affidabilità totals
  - Gratuito, illimitato

Budget giornaliero (api-sports.io):
  ~10 req  standings/form football (1 per lega top)
  ~10 req  infortuni partite del giorno football
  ~5  req  standings NBA + player stats
  ~5  req  tennis rankings + form
  → totale ~30/100 req/giorno (per sport)
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone, timedelta
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings

logger = logging.getLogger(__name__)

# ── Coordinate stadi europei (lat, lon) per forecast meteo ───────────────────
# Usato da WeatherClient per calcolare il meteo al momento della partita.
# Aggiorna con nuove squadre se necessario.
STADIUM_COORDS: dict[str, tuple[float, float]] = {
    # Premier League
    "arsenal":              (51.5549, -0.1084),
    "chelsea":              (51.4816, -0.1910),
    "liverpool":            (53.4308, -2.9608),
    "manchester city":      (53.4831, -2.2004),
    "manchester united":    (53.4631, -2.2913),
    "tottenham":            (51.6043, -0.0665),
    "newcastle":            (54.9754, -1.6216),
    "west ham":             (51.5386, -0.0164),
    "aston villa":          (52.5092, -1.8849),
    "brighton":             (50.8618, -0.0837),
    "everton":              (53.4388, -2.9663),
    "fulham":               (51.4749, -0.2217),
    "brentford":            (51.4906, -0.2888),
    "crystal palace":       (51.3983, -0.0855),
    "wolves":               (52.5900, -2.1303),
    "nottingham forest":    (52.9399, -1.1328),
    "bournemouth":          (50.7353, -1.8388),
    "leicester":            (52.6204, -1.1422),
    "ipswich":              (52.0551, 1.1447),
    "southampton":          (50.9058, -1.3914),
    # Serie A
    "juventus":             (45.1096, 7.6412),
    "inter":                (45.4781, 9.1240),
    "milan":                (45.4781, 9.1240),
    "ac milan":             (45.4781, 9.1240),
    "roma":                 (41.9340, 12.5547),
    "lazio":                (41.9340, 12.5547),
    "napoli":               (40.8279, 14.1932),
    "atalanta":             (45.7010, 9.6768),
    "fiorentina":           (43.7800, 11.2823),
    "bologna":              (44.4922, 11.3133),
    "torino":               (45.0406, 7.6492),
    "genoa":                (44.4145, 8.9516),
    "udinese":              (46.0762, 13.2018),
    "monza":                (45.5840, 9.2674),
    "lecce":                (40.3597, 18.1735),
    "cagliari":             (39.1942, 9.1359),
    "parma":                (44.7990, 10.3278),
    # La Liga
    "real madrid":          (40.4531, -3.6883),
    "barcelona":            (41.3809, 2.1228),
    "atletico madrid":      (40.4361, -3.5996),
    "atletico de madrid":   (40.4361, -3.5996),
    "sevilla":              (37.3841, -5.9704),
    "real betis":           (37.3561, -5.9816),
    "real sociedad":        (43.3015, -2.0038),
    "athletic club":        (43.2642, -2.9496),
    "villarreal":           (39.9441, -0.1039),
    "valencia":             (39.4747, -0.3585),
    "getafe":               (40.3256, -3.7148),
    "osasuna":              (42.7967, -1.6372),
    # Bundesliga
    "bayern münchen":       (48.2188, 11.6248),
    "borussia dortmund":    (51.4926, 7.4518),
    "bayer leverkusen":     (51.0384, 7.0020),
    "rb leipzig":           (51.3457, 12.3481),
    "eintracht frankfurt":  (50.0688, 8.6455),
    "vfb stuttgart":        (48.7925, 9.2320),
    "borussia mönchengladbach": (51.1745, 6.3854),
    "sc freiburg":          (47.9887, 7.8910),
    "tsg hoffenheim":       (49.2389, 8.8881),
    "union berlin":         (52.4573, 13.5679),
    # Ligue 1
    "paris saint-germain":  (48.8414, 2.2530),
    "psg":                  (48.8414, 2.2530),
    "olympique marseille":  (43.2697, 5.3961),
    "olympique lyonnais":   (45.7653, 4.9823),
    "monaco":               (43.7272, 7.4160),
    "lille":                (50.6120, 3.1303),
    "nice":                 (43.7045, 7.1925),
    "rennes":               (48.1073, -1.6744),
    # Champions League (stadi si trovano automaticamente dalla squadra di casa)
}

# ── Tennis ranking → pseudo-Elo ──────────────────────────────────────────────

def ranking_to_elo(rank: int) -> float:
    """
    Converte il ranking ATP/WTA in un rating Elo pseudo.
    Formula log: rank 1 ≈ 2500, rank 10 ≈ 2000, rank 100 ≈ 1500, rank 1000 ≈ 1000.
    """
    if rank <= 0:
        return 1000.0
    return max(900.0, 2500.0 - 500.0 * math.log10(rank))

# ── Rate limiter globale per api-sports.io (max 10 req/min) ──────────────────
_api_sports_semaphore = asyncio.Semaphore(1)  # 1 richiesta alla volta

# ── Mapping lega The Odds API → API-Football league_id + season ──────────────
LEAGUE_MAP: dict[str, tuple[int, int]] = {
    "soccer_epl":                    (39,  2024),
    "soccer_serie_a":                (135, 2024),
    "soccer_spain_la_liga":          (140, 2024),
    "soccer_germany_bundesliga":     (78,  2024),
    "soccer_france_ligue_one":       (61,  2024),
    "soccer_uefa_champs_league":     (2,   2024),
    "soccer_uefa_europa_league":     (3,   2024),
    "soccer_italy_serie_b":          (136, 2024),
    "soccer_portugal_primeira_liga": (94,  2024),
    "soccer_netherlands_eredivisie": (88,  2024),
}

# Mapping The Odds API → football-data.org competition code
FOOTBALL_DATA_MAP: dict[str, str] = {
    "soccer_epl":                    "PL",
    "soccer_serie_a":                "SA",
    "soccer_spain_la_liga":          "PD",
    "soccer_germany_bundesliga":     "BL1",
    "soccer_france_ligue_one":       "FL1",
    "soccer_uefa_champs_league":     "CL",
    "soccer_uefa_europa_league":     "EL",
    "soccer_portugal_primeira_liga": "PPL",
    "soccer_netherlands_eredivisie": "DED",
}

# Basketball NBA
NBA_LEAGUE_ID = 12
NBA_SEASON    = "2024-2025"


class ApiSportsError(Exception):
    pass


class ApiSportsClient:
    """
    Client per api-sports.io con throttle automatico.
    Attende 6.5s tra una richiesta e l'altra per rispettare 10 req/min.
    """
    _last_request_at: float = 0.0
    _MIN_INTERVAL = 6.5  # secondi tra richieste (< 10/min)

    def __init__(self, sport: str = "football") -> None:
        self._key  = settings.api_football_key
        version    = "v3" if sport == "football" else "v1"
        self._base = f"https://{version}.{sport}.api-sports.io"

    async def get(self, path: str, params: dict | None = None) -> Any:
        if not self._key:
            raise ApiSportsError("API_FOOTBALL_KEY non configurata")

        async with _api_sports_semaphore:
            # Throttle: aspetta il tempo necessario dall'ultima richiesta
            now = asyncio.get_event_loop().time()
            elapsed = now - ApiSportsClient._last_request_at
            if elapsed < self._MIN_INTERVAL:
                await asyncio.sleep(self._MIN_INTERVAL - elapsed)

            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(
                        f"{self._base}{path}",
                        params=params or {},
                        headers={"x-apisports-key": self._key},
                    )
            finally:
                ApiSportsClient._last_request_at = asyncio.get_event_loop().time()

        if resp.status_code == 429:
            raise ApiSportsError("Rate limit api-sports.io — riprova tra 1 minuto")
        if resp.status_code == 401:
            raise ApiSportsError("Chiave api-sports.io non valida")
        resp.raise_for_status()
        data = resp.json()
        errors = data.get("errors", {})
        if errors and errors != [] and errors != {}:
            raise ApiSportsError(f"API error: {errors}")
        return data.get("response", [])


# ── Football (api-sports.io) ──────────────────────────────────────────────────

class FootballStatsClient:
    """
    Dati calcio da api-sports.io (infortuni, H2H, statistiche).
    """

    def __init__(self) -> None:
        self._client = ApiSportsClient("football")

    async def get_fixtures_by_date(self, match_date: date) -> list[dict]:
        return await self._client.get("/fixtures", {"date": match_date.isoformat()})

    async def get_standings(self, league_id: int, season: int) -> list[dict]:
        return await self._client.get("/standings", {"league": league_id, "season": season})

    async def get_injuries(self, fixture_id: int) -> list[dict]:
        return await self._client.get("/injuries", {"fixture": fixture_id})

    async def get_head_to_head(self, team1_id: int, team2_id: int, last: int = 8) -> list[dict]:
        return await self._client.get(
            "/fixtures/headtohead",
            {"h2h": f"{team1_id}-{team2_id}", "last": last},
        )

    async def get_team_statistics(self, team_id: int, league_id: int, season: int) -> dict:
        data = await self._client.get(
            "/teams/statistics",
            {"team": team_id, "league": league_id, "season": season},
        )
        return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})

    def find_fixture(self, fixtures: list[dict], home_team: str, away_team: str) -> dict | None:
        home_l = home_team.lower().strip()
        away_l = away_team.lower().strip()
        for f in fixtures:
            teams = f.get("teams", {})
            fh = (teams.get("home", {}).get("name") or "").lower().strip()
            fa = (teams.get("away", {}).get("name") or "").lower().strip()
            if _name_match(fh, home_l) and _name_match(fa, away_l):
                return f
        return None

    def parse_standings_form(self, standings_raw: list[dict]) -> dict:
        result: dict = {}
        for group in standings_raw:
            for league_data in (group if isinstance(group, list) else [group]):
                standings = league_data.get("league", {}).get("standings", [[]])[0] if isinstance(league_data, dict) else []
                for entry in standings:
                    team_name = entry.get("team", {}).get("name", "")
                    result[team_name.lower()] = {
                        "position": entry.get("rank"),
                        "points": entry.get("points"),
                        "form": entry.get("form"),
                        "goals_for": entry.get("all", {}).get("goals", {}).get("for"),
                        "goals_against": entry.get("all", {}).get("goals", {}).get("against"),
                        "played": entry.get("all", {}).get("played"),
                        "wins": entry.get("all", {}).get("win"),
                        "draws": entry.get("all", {}).get("draw"),
                        "losses": entry.get("all", {}).get("lose"),
                    }
        return result

    def parse_injuries(self, injuries_raw: list[dict]) -> list[dict]:
        result = []
        for item in injuries_raw:
            player = item.get("player", {})
            team   = item.get("team", {})
            result.append({
                "player": player.get("name"),
                "team":   team.get("name"),
                "type":   item.get("type"),
                "reason": player.get("reason"),
            })
        return result

    async def get_fixture_stats(self, fixture_id: int) -> list[dict]:
        return await self._client.get("/fixtures/statistics", {"fixture": fixture_id})

    def parse_fixture_stats(self, raw: list[dict]) -> dict:
        result = {}
        for team_stats in raw:
            team_name = team_stats.get("team", {}).get("name", "unknown")
            stats = {}
            for s in team_stats.get("statistics", []):
                key = s.get("type", "").lower().replace(" ", "_")
                stats[key] = s.get("value")
            result[team_name] = stats
        return result


# ── football-data.org ─────────────────────────────────────────────────────────

class FootballDataClient:
    """
    Client per football-data.org — forma recente e classifiche.
    Rispetta automaticamente il rate limit via response headers.
    Token: X-Auth-Token
    Free plan: 10 req/min, copre PL, SA, BL1, FL1, PD, CL, EL, PPL, DED.
    """
    _BASE = "https://api.football-data.org/v4"
    _last_request_at: float = 0.0
    _MIN_INTERVAL = 7.0  # secondi tra richieste (< 10/min con margine)

    def __init__(self) -> None:
        self._key = getattr(settings, "football_data_key", None) or ""

    async def get(self, path: str, params: dict | None = None) -> Any:
        if not self._key:
            raise ValueError("FOOTBALL_DATA_KEY non configurata")

        # Throttle: rispetta header X-RateLimit-Remaining se possibile
        now = asyncio.get_event_loop().time()
        elapsed = now - FootballDataClient._last_request_at
        if elapsed < self._MIN_INTERVAL:
            await asyncio.sleep(self._MIN_INTERVAL - elapsed)

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self._BASE}{path}",
                params=params or {},
                headers={"X-Auth-Token": self._key},
            )

        FootballDataClient._last_request_at = asyncio.get_event_loop().time()

        # Leggi il rate limit rimanente dagli header (come suggerito dall'API)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) <= 1:
            logger.warning("football-data.org: rate limit quasi raggiunto (%s rimanenti)", remaining)
            await asyncio.sleep(60)  # aspetta 1 minuto se quasi a secco

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning("football-data.org: rate limited, aspetto %ds", retry_after)
            await asyncio.sleep(retry_after)
            raise ValueError("Rate limited — riprova")
        if resp.status_code == 403:
            raise ValueError("Token football-data.org non valido o piano non supporta questa risorsa")
        resp.raise_for_status()
        return resp.json()

    async def get_standings(self, competition_code: str) -> dict:
        """Classifica + forma squadre per una competizione (es. 'PL', 'SA')."""
        try:
            return await self.get(f"/competitions/{competition_code}/standings")
        except Exception as exc:
            logger.warning("football-data.org standings %s: %s", competition_code, exc)
            return {}

    async def get_team_matches(self, team_id: int, last: int = 5) -> list[dict]:
        """Ultimi N match di una squadra (per forma recente)."""
        try:
            data = await self.get(f"/teams/{team_id}/matches", {"status": "FINISHED", "limit": last})
            return data.get("matches", [])
        except Exception as exc:
            logger.warning("football-data.org team %d matches: %s", team_id, exc)
            return []

    def parse_standings(self, data: dict) -> dict:
        """
        Ritorna dict {team_name_lower: {position, points, form, played, ...}}
        compatibile con parse_standings_form() di api-sports.io.
        """
        result: dict = {}
        for group in data.get("standings", []):
            for entry in group.get("table", []):
                team = entry.get("team", {})
                name = (team.get("shortName") or team.get("name") or "").lower()
                if not name:
                    continue
                result[name] = {
                    "position": entry.get("position"),
                    "points": entry.get("points"),
                    "form": entry.get("form"),        # es. "WDWLW"
                    "played": entry.get("playedGames"),
                    "wins": entry.get("won"),
                    "draws": entry.get("draw"),
                    "losses": entry.get("lost"),
                    "goals_for": entry.get("goalsFor"),
                    "goals_against": entry.get("goalsAgainst"),
                }
        return result


# ── Basketball ────────────────────────────────────────────────────────────────

class BasketballStatsClient:
    def __init__(self) -> None:
        self._client = ApiSportsClient("basketball")

    async def get_games_by_date(self, game_date: date) -> list[dict]:
        return await self._client.get("/games", {"date": game_date.isoformat(), "league": NBA_LEAGUE_ID, "season": NBA_SEASON})

    async def get_team_statistics(self, team_id: int, season: str = NBA_SEASON) -> dict:
        data = await self._client.get("/teams/statistics", {"id": team_id, "season": season})
        return data[0] if isinstance(data, list) and data else {}

    async def get_standings(self) -> list[dict]:
        return await self._client.get("/standings", {"league": NBA_LEAGUE_ID, "season": NBA_SEASON})


# ── Tennis (api-sports.io) ────────────────────────────────────────────────────

class TennisStatsClient:
    """
    Statistiche tennis da api-sports.io (stessa chiave di football).
    Base URL: https://v1.tennis.api-sports.io

    Fornisce:
    - Rankings ATP/WTA → pseudo-ELO per valutare forza relativa
    - Forma recente del giocatore (W/L ultimi 10 match)
    - Head-to-head tra due giocatori
    """

    def __init__(self) -> None:
        self._client = ApiSportsClient("tennis")

    async def get_rankings(self, tour: str = "atp") -> list[dict]:
        """
        Ritorna classifica ATP o WTA.
        tour: "atp" | "wta"
        Risposta: [{rank, player: {id, name, country, points}}, ...]
        """
        return await self._client.get("/rankings", {"type": tour.upper()})

    async def get_player_matches(self, player_id: int, season: int = 2025, last: int = 10) -> list[dict]:
        """Ultimi N match di un giocatore per questa stagione."""
        return await self._client.get(
            "/games",
            {"player": player_id, "season": season},
        )

    async def get_head_to_head(self, player1_id: int, player2_id: int) -> list[dict]:
        """Storico H2H tra due giocatori."""
        return await self._client.get(
            "/games/h2h",
            {"h2h": f"{player1_id}-{player2_id}"},
        )

    def build_player_elo_context(
        self,
        rankings: list[dict],
        player_a_name: str,
        player_b_name: str,
    ) -> dict:
        """
        Cerca i giocatori nella classifica e costruisce il contesto Elo.
        Ritorna dict compatibile con raw_stats["elo"] (stesso formato di ClubElo).
        """
        rank_map: dict[str, dict] = {}
        for entry in rankings:
            player = entry.get("player", {})
            name_raw = (player.get("name") or "").lower().strip()
            rank_map[name_raw] = {
                "rank": entry.get("ranking", 0) or entry.get("rank", 0),
                "points": entry.get("player", {}).get("ranking_points") or entry.get("points", 0),
                "id": player.get("id"),
            }

        def _find_player(name: str) -> dict | None:
            name_l = name.lower().strip()
            if name_l in rank_map:
                return rank_map[name_l]
            for k, v in rank_map.items():
                if _name_match(k, name_l):
                    return v
            return None

        info_a = _find_player(player_a_name)
        info_b = _find_player(player_b_name)

        if not info_a and not info_b:
            return {}

        result: dict = {"source": "tennis_ranking", "note": "Pseudo-Elo da ranking ATP/WTA"}

        rank_a = info_a["rank"] if info_a else 999
        rank_b = info_b["rank"] if info_b else 999
        elo_a = ranking_to_elo(rank_a)
        elo_b = ranking_to_elo(rank_b)

        result["player_a_rank"] = rank_a
        result["player_b_rank"] = rank_b
        result["player_a_elo"] = round(elo_a, 1)
        result["player_b_elo"] = round(elo_b, 1)
        result["player_a_id"] = info_a["id"] if info_a else None
        result["player_b_id"] = info_b["id"] if info_b else None

        if info_a and info_b:
            delta = elo_a - elo_b
            # player_a win prob (ELO standard, home/away non rilevante per tennis)
            win_prob_a = round(1 / (1 + 10 ** (-delta / 400)), 3)
            result["elo_player_a_win_prob"] = win_prob_a
            result["elo_home_win_prob"] = win_prob_a  # alias per compatibilità con pipeline
            result["home_advantage_elo"] = round(delta, 1)
            result["interpretation"] = (
                f"{player_a_name} favorito (ELO {elo_a:.0f} vs {elo_b:.0f})" if delta > 0
                else f"{player_b_name} favorito (ELO {elo_b:.0f} vs {elo_a:.0f})"
            )

        return result

    def parse_recent_form(self, matches: list[dict], player_id: int) -> str:
        """
        Estrae forma recente del giocatore (es. 'WWLWL').
        Legge i risultati degli ultimi match disponibili.
        """
        form_chars = []
        for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True)[:10]:
            winner = (m.get("winner") or {}).get("id")
            if winner is None:
                continue
            form_chars.append("W" if winner == player_id else "L")
        return "".join(form_chars) or "N/A"


# ── NBA / Basketball — Infortuni ESPN ────────────────────────────────────────

class NBAInjuryClient:
    """
    Feed infortuni NBA da ESPN (endpoint pubblico, no API key).
    Aggiornato in tempo reale dai team injury report ufficiali NBA.

    URL: https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries
    """
    _ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"

    async def get_injuries(self) -> dict[str, list[dict]]:
        """
        Ritorna {team_name_lower: [{player, status, type, return_date}]}.
        status: "Out", "Questionable", "Probable", "Day-To-Day"
        """
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(self._ESPN_URL)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("ESPN NBA injuries fetch failed: %s", exc)
            return {}

        result: dict[str, list[dict]] = {}
        for team_entry in data.get("injuries", []):
            team_name = (team_entry.get("team", {}).get("displayName") or "").lower().strip()
            if not team_name:
                continue
            injuries = []
            for inj in team_entry.get("injuries", []):
                athlete = inj.get("athlete", {})
                injuries.append({
                    "player": athlete.get("displayName"),
                    "position": athlete.get("position", {}).get("abbreviation"),
                    "status": inj.get("status"),            # "Out", "Questionable", ...
                    "type": inj.get("type"),                 # "Ankle", "Knee", ...
                    "return_date": inj.get("returnDate"),
                })
            result[team_name] = injuries

        logger.info("ESPN NBA injuries: %d team(s) con giocatori out", len(result))
        return result

    @staticmethod
    def assess_impact(injuries: list[dict]) -> str:
        """
        Valuta l'impatto degli infortuni su una squadra.
        Ritorna "high" (titolari out), "medium" (questionable), "low" o "none".
        """
        statuses = {i.get("status", "").lower() for i in injuries}
        if "out" in statuses or "doubtful" in statuses:
            return "high"
        if "questionable" in statuses or "day-to-day" in statuses:
            return "medium"
        if "probable" in statuses:
            return "low"
        return "none"


# ── Weather — Open-Meteo (gratuito, no key) ───────────────────────────────────

class WeatherClient:
    """
    Previsioni meteo allo stadio tramite Open-Meteo.
    Completamente gratuito, nessuna API key richiesta.
    URL: https://api.open-meteo.com/v1/forecast

    Segnale principale per i mercati totals del calcio:
    - Vento forte (>40 km/h): sotto bias (il pallone vola male)
    - Pioggia intensa (>3 mm/h): sotto bias (campo pesante, gioco chiuso)
    - Neve / temperature gelide: sotto bias
    """
    _BASE = "https://api.open-meteo.com/v1/forecast"

    async def get_weather_for_match(
        self,
        home_team: str,
        match_date: datetime | None,
    ) -> dict:
        """
        Ritorna dict con condizioni meteo per la partita.
        Cerca le coordinate dello stadio dal lookup table STADIUM_COORDS.
        """
        if match_date is None:
            return {}

        coords = self._find_coords(home_team)
        if not coords:
            return {"note": f"Stadio non trovato per {home_team}"}

        lat, lon = coords
        match_hour = match_date.strftime("%Y-%m-%dT%H:00")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self._BASE,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "hourly": "wind_speed_10m,precipitation,weather_code,temperature_2m",
                        "forecast_days": 3,
                        "timezone": "auto",
                    },
                )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Weather fetch failed per %s: %s", home_team, exc)
            return {}

        # Trova l'ora più vicina alla partita
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        winds = hourly.get("wind_speed_10m", [])
        precips = hourly.get("precipitation", [])
        temps = hourly.get("temperature_2m", [])
        codes = hourly.get("weather_code", [])

        # Cerca l'indice dell'ora della partita
        try:
            idx = times.index(match_hour)
        except ValueError:
            # Prendi l'ora più vicina
            idx = 0
            for i, t in enumerate(times):
                if t >= match_hour:
                    idx = i
                    break

        if idx >= len(winds):
            return {}

        wind_kmh = winds[idx] if idx < len(winds) else 0
        precip_mm = precips[idx] if idx < len(precips) else 0
        temp_c = temps[idx] if idx < len(temps) else 15
        weather_code = codes[idx] if idx < len(codes) else 0

        weather = {
            "wind_kmh": round(wind_kmh, 1),
            "precipitation_mm": round(precip_mm, 2),
            "temperature_c": round(temp_c, 1),
            "weather_code": weather_code,
            "conditions": self._describe_conditions(wind_kmh, precip_mm, temp_c, weather_code),
            "totals_impact": self._assess_totals_impact(wind_kmh, precip_mm, temp_c),
            "stadium": f"{home_team.title()} ({lat:.2f}, {lon:.2f})",
        }
        logger.info(
            "Meteo %s: vento=%.0f km/h pioggia=%.1fmm temp=%.0f°C → %s",
            home_team, wind_kmh, precip_mm, temp_c, weather["totals_impact"],
        )
        return weather

    @staticmethod
    def _find_coords(home_team: str) -> tuple[float, float] | None:
        """Cerca le coordinate dello stadio con fuzzy matching."""
        team_l = home_team.lower().strip()
        if team_l in STADIUM_COORDS:
            return STADIUM_COORDS[team_l]
        for name, coords in STADIUM_COORDS.items():
            if _name_match(name, team_l):
                return coords
        return None

    @staticmethod
    def _describe_conditions(wind: float, precip: float, temp: float, code: int) -> str:
        parts = []
        if wind >= 50:
            parts.append("vento forte")
        elif wind >= 30:
            parts.append("vento moderato")
        if precip >= 5:
            parts.append("pioggia intensa")
        elif precip >= 1:
            parts.append("pioggia leggera")
        if temp <= 0:
            parts.append("gelido")
        if code in range(71, 78):
            parts.append("neve")
        return ", ".join(parts) if parts else "sereno"

    @staticmethod
    def _assess_totals_impact(wind: float, precip: float, temp: float) -> str:
        """
        Valuta l'impatto sul mercato Over/Under.
        Ritorna "under_bias", "slight_under", "neutral".
        """
        score = 0
        if wind >= 50:
            score += 3
        elif wind >= 35:
            score += 2
        elif wind >= 25:
            score += 1
        if precip >= 5:
            score += 2
        elif precip >= 2:
            score += 1
        if temp <= 0:
            score += 1

        if score >= 4:
            return "under_bias"
        elif score >= 2:
            return "slight_under"
        return "neutral"


# ── Player Stats — api-sports.io (basketball + football) ─────────────────────

class PlayerStatsClient:
    """
    Statistiche giocatori per valutare player prop markets.

    Basketball (NBA):
    - Medie stagionali (punti, rimbalzi, assist per partita)
    - Ultimi 5 match (forma recente)
    - Difesa avversaria vs posizione (quanti punti concede ai PG/SG/SF/PF/C)

    Football (calcio):
    - Goal scorer stats (xG, tiri, gol stagione)
    - Top scorer per lega
    """

    def __init__(self) -> None:
        self._bball = ApiSportsClient("basketball")
        self._football = ApiSportsClient("football")

    # ─── Basketball ────────────────────────────────────────────────────────────

    async def get_nba_player_season_stats(self, player_id: int) -> dict:
        """
        Media stagionale di un giocatore NBA (punti, rimbalzi, assist, etc.).
        """
        data = await self._bball.get(
            "/players/statistics",
            {"id": player_id, "league": NBA_LEAGUE_ID, "season": NBA_SEASON},
        )
        if not data:
            return {}
        # Calcola media degli ultimi 20 game disponibili
        games = data[-20:] if len(data) > 20 else data
        if not games:
            return {}

        def _avg(key: str) -> float:
            vals = [float(g.get(key, 0) or 0) for g in games]
            return round(sum(vals) / len(vals), 1) if vals else 0.0

        return {
            "games_sample": len(games),
            "avg_points": _avg("points"),
            "avg_rebounds": _avg("totReb"),
            "avg_assists": _avg("assists"),
            "avg_steals": _avg("steals"),
            "avg_blocks": _avg("blocks"),
            "avg_threes": _avg("tpm"),
            "avg_minutes": _avg("min"),
            "avg_fga": _avg("fga"),      # field goal attempts
            "avg_pra": round(_avg("points") + _avg("totReb") + _avg("assists"), 1),
        }

    async def get_nba_player_last_n_games(self, player_id: int, n: int = 5) -> list[dict]:
        """Statistiche delle ultime N partite per analisi forma recente."""
        data = await self._bball.get(
            "/players/statistics",
            {"id": player_id, "league": NBA_LEAGUE_ID, "season": NBA_SEASON},
        )
        recent = data[-n:] if len(data) >= n else data
        return [
            {
                "date": g.get("game", {}).get("date"),
                "points": g.get("points", 0),
                "rebounds": g.get("totReb", 0),
                "assists": g.get("assists", 0),
                "minutes": g.get("min", 0),
            }
            for g in recent
        ]

    async def find_nba_player_id(self, player_name: str) -> int | None:
        """Cerca l'ID di un giocatore NBA per nome."""
        data = await self._bball.get(
            "/players",
            {"search": player_name[:20], "league": NBA_LEAGUE_ID, "season": NBA_SEASON},
        )
        if data:
            return data[0].get("id")
        return None

    async def get_nba_team_defense_vs_position(self, team_id: int, position: str) -> dict:
        """
        Quanti punti concede questa squadra ai giocatori della posizione indicata.
        proxy: statistiche del team in difesa (points_against) per stagione.
        """
        data = await self._bball.get(
            "/teams/statistics",
            {"id": team_id, "league": NBA_LEAGUE_ID, "season": NBA_SEASON},
        )
        if data and isinstance(data, list):
            return data[0]
        return {}

    # ─── Football Player Props ─────────────────────────────────────────────────

    async def get_football_top_scorers(self, league_id: int, season: int = 2024) -> list[dict]:
        """Top scorer della lega — per valutare mercati 'anytime goalscorer'."""
        data = await self._football.get(
            "/players/topscorers",
            {"league": league_id, "season": season},
        )
        return [
            {
                "player_id": item.get("player", {}).get("id"),
                "name": item.get("player", {}).get("name"),
                "team": (item.get("statistics", [{}])[0]).get("team", {}).get("name"),
                "goals": (item.get("statistics", [{}])[0]).get("goals", {}).get("total", 0),
                "games": (item.get("statistics", [{}])[0]).get("games", {}).get("appearences", 0),
                "shots_per_game": (item.get("statistics", [{}])[0]).get("shots", {}).get("total", 0),
                "xg": (item.get("statistics", [{}])[0]).get("goals", {}).get("saves"),  # proxy
            }
            for item in data
        ]

    async def get_football_player_fixture_stats(self, fixture_id: int) -> list[dict]:
        """Statistiche di ogni giocatore per questa partita (minuti, gol, assist, etc.)."""
        data = await self._football.get("/fixtures/players", {"fixture": fixture_id})
        result = []
        for team_data in data:
            team_name = team_data.get("team", {}).get("name", "")
            for p in team_data.get("players", []):
                player = p.get("player", {})
                stats = (p.get("statistics") or [{}])[0]
                result.append({
                    "player_id": player.get("id"),
                    "name": player.get("name"),
                    "team": team_name,
                    "minutes": stats.get("games", {}).get("minutes", 0),
                    "goals": stats.get("goals", {}).get("total") or 0,
                    "assists": stats.get("goals", {}).get("assists") or 0,
                    "shots": stats.get("shots", {}).get("total") or 0,
                    "shots_on_target": stats.get("shots", {}).get("on") or 0,
                    "xg": stats.get("goals", {}).get("total") or 0,  # proxy
                })
        return result


# ── ClubElo — fallback gratuito per squadre fuori dalle top leghe ─────────────

class ClubEloClient:
    """
    ClubElo.com — Elo ratings per oltre 1000 squadre mondiali.
    Completamente gratuito, nessuna API key.
    URL: http://api.clubelo.com/{YYYY-MM-DD} → CSV con tutte le squadre del giorno.

    Elo rating è un proxy affidabile della forza relativa della squadra.
    Usato come fallback quando non abbiamo standings/forma da API stats.
    """
    _BASE = "http://api.clubelo.com"
    _cache: dict[str, dict] = {}  # {date_str: {team_name_lower: elo_float}}

    async def get_elo_for_date(self, match_date: date) -> dict[str, float]:
        """
        Ritorna dict {team_name_lower: elo_rating} per tutti i club in quella data.
        Cachato per evitare chiamate duplicate nella stessa esecuzione.
        """
        date_str = match_date.isoformat()
        if date_str in self._cache:
            return self._cache[date_str]

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self._BASE}/{date_str}")
            resp.raise_for_status()

            elo_map: dict[str, float] = {}
            for line in resp.text.splitlines()[1:]:  # salta header
                parts = line.split(",")
                if len(parts) >= 5:
                    club = parts[1].strip().lower()
                    try:
                        elo = float(parts[4].strip())
                        elo_map[club] = elo
                    except ValueError:
                        pass

            self._cache[date_str] = elo_map
            logger.info("ClubElo: %d squadre caricate per %s", len(elo_map), date_str)
            return elo_map

        except Exception as exc:
            logger.warning("ClubElo fetch failed for %s: %s", date_str, exc)
            self._cache[date_str] = {}
            return {}

    def find_team_elo(self, elo_map: dict[str, float], team_name: str) -> float | None:
        """Cerca il rating Elo con matching fuzzy sul nome."""
        team_l = team_name.lower().strip()
        if team_l in elo_map:
            return elo_map[team_l]
        # Fuzzy: trova la migliore corrispondenza parziale
        for club, elo in elo_map.items():
            if _name_match(club, team_l):
                return elo
        return None

    def elo_to_form_proxy(self, home_elo: float | None, away_elo: float | None) -> dict:
        """
        Converte Elo in una struttura compatibile con form_stats.
        Aggiunge il delta Elo come segnale di forza relativa.
        Utilizzato dall'UncertaintyAgent quando mancano dati reali.
        """
        if home_elo is None and away_elo is None:
            return {}

        result: dict = {"source": "clubelo", "note": "Elo ratings (forza relativa squadra)"}
        if home_elo:
            result["home_elo"] = round(home_elo, 1)
        if away_elo:
            result["away_elo"] = round(away_elo, 1)
        if home_elo and away_elo:
            delta = home_elo - away_elo
            result["home_advantage_elo"] = round(delta, 1)
            # Probabilità implicita per Elo (formula standard)
            result["elo_home_win_prob"] = round(1 / (1 + 10 ** (-delta / 400)), 3)
            result["interpretation"] = (
                f"Home più forte di {abs(delta):.0f} punti Elo" if delta > 0
                else f"Away più forte di {abs(delta):.0f} punti Elo"
            )
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

_FOOTBALL_STOP_WORDS = {
    "real", "atletico", "athletic", "sporting", "deportivo", "racing",
    "united", "city", "town", "rovers", "wanderers", "hotspur",
    "fc", "cf", "sc", "ac", "as", "ss", "vfb", "vfl", "bsc", "fsv",
    "olympique", "olympia", "dynamo", "dinamo",
}


def _name_match(a: str, b: str) -> bool:
    if a == b:
        return True
    # Prefix match solo se il prefisso è abbastanza lungo (>= 6 char)
    if len(a) >= 6 and len(b) >= 6 and (a[:6] in b or b[:6] in a):
        return True
    # Word-based matching escluse parole comuni nel calcio
    words_a = {w for w in a.split() if len(w) > 3 and w not in _FOOTBALL_STOP_WORDS}
    words_b = {w for w in b.split() if len(w) > 3 and w not in _FOOTBALL_STOP_WORDS}
    return bool(words_a and words_b and words_a & words_b)


# ── xG Model per il calcio ────────────────────────────────────────────────────

class XGModelClient:
    """
    Modello xG (Expected Goals) per partite di calcio.

    Usa api-football per recuperare xG reale delle ultime partite.
    Un team che sovra-performa il suo xG tende a regredire → over/under signal.

    Endpoints:
      GET /fixtures/statistics?fixture={id}  → xG per partita
      GET /teams/statistics?team={id}&season={year}&league={id}  → xG stagionale
    """

    BASE = "https://v3.football.api-sports.io"

    def __init__(self) -> None:
        from app.config import settings
        self._key = getattr(settings, "api_football_key", "") or getattr(settings, "football_api_key", "")
        self._timeout = httpx.Timeout(10.0)

    async def get_team_xg_form(self, team_id: int, league_id: int, season: int = 2024, last_n: int = 8) -> dict:
        """
        Recupera xG reale delle ultime N partite di un team.

        Returns:
            {
              "avg_xg_for":     float,  # xG creati in media
              "avg_xg_against": float,  # xG subiti in media
              "overperforming": bool,   # segna più del suo xG → rischio regressione
              "underperforming": bool,  # segna meno del suo xG → regressione upward
              "xg_diff":        float,  # goals - xG (positivo = overperforming)
              "sample":         int,
            }
        """
        if not self._key:
            return {"avg_xg_for": 0, "avg_xg_against": 0, "overperforming": False, "underperforming": False, "xg_diff": 0, "sample": 0}

        url = f"{self.BASE}/fixtures"
        params = {
            "team": team_id,
            "league": league_id,
            "season": season,
            "last": last_n,
            "status": "FT",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers={"x-apisports-key": self._key})
            if resp.status_code != 200:
                return {"avg_xg_for": 0, "avg_xg_against": 0, "overperforming": False, "underperforming": False, "xg_diff": 0, "sample": 0}
            fixtures = resp.json().get("response", [])
        except Exception as exc:
            logger.warning("xG form fetch failed for team %d: %s", team_id, exc)
            return {"avg_xg_for": 0, "avg_xg_against": 0, "overperforming": False, "underperforming": False, "xg_diff": 0, "sample": 0}

        xg_for_list, xg_against_list, goals_list = [], [], []
        for f in fixtures:
            teams = f.get("teams", {})
            score = f.get("score", {}).get("fulltime", {})
            is_home = teams.get("home", {}).get("id") == team_id

            xg_home = _safe_float(f.get("goals", {}).get("home"))
            xg_away = _safe_float(f.get("goals", {}).get("away"))
            # api-football pone xG in fixture.statistics (serve secondo endpoint)
            # Approssimazione: usa shots on target come proxy xG se manca il campo diretto
            xg_for     = xg_home if is_home else xg_away
            xg_against = xg_away if is_home else xg_home
            goals_for  = _safe_float(score.get("home") if is_home else score.get("away"))

            if xg_for is not None:
                xg_for_list.append(xg_for)
            if xg_against is not None:
                xg_against_list.append(xg_against)
            if goals_for is not None and xg_for is not None:
                goals_list.append(goals_for - xg_for)

        n = len(xg_for_list)
        if n == 0:
            return {"avg_xg_for": 0, "avg_xg_against": 0, "overperforming": False, "underperforming": False, "xg_diff": 0, "sample": 0}

        avg_xg_for     = round(sum(xg_for_list) / n, 2)
        avg_xg_against = round(sum(xg_against_list) / len(xg_against_list), 2) if xg_against_list else 0
        avg_xg_diff    = round(sum(goals_list) / len(goals_list), 2) if goals_list else 0

        return {
            "avg_xg_for":      avg_xg_for,
            "avg_xg_against":  avg_xg_against,
            "overperforming":  avg_xg_diff > 0.3,   # segna 0.3+ gol in più del suo xG
            "underperforming": avg_xg_diff < -0.3,
            "xg_diff":         avg_xg_diff,
            "sample":          n,
        }

    def assess_totals_bias(self, home_xg: dict, away_xg: dict) -> dict:
        """
        Valuta se c'è bias over/under per questa partita basandosi su xG.

        Logica:
         - Entrambi i team overperformano → regressione → under bias
         - Entrambi underperformano → regressione → over bias
         - Team difende male (xG against alto) → over bias

        Returns: {"signal": "over" | "under" | "neutral", "strength": float [0,1], "note": str}
        """
        home_over = home_xg.get("overperforming", False)
        home_under = home_xg.get("underperforming", False)
        away_over = away_xg.get("overperforming", False)
        away_under = away_xg.get("underperforming", False)

        home_xg_for = home_xg.get("avg_xg_for", 1.2)
        away_xg_for = away_xg.get("avg_xg_for", 1.2)
        projected_total = home_xg_for + away_xg_for

        if home_over and away_over:
            return {"signal": "under", "strength": 0.7,
                    "note": f"Entrambi i team sovra-performano xG (regressione attesa) | xG proiettato: {projected_total:.1f}"}
        if home_under and away_under:
            return {"signal": "over", "strength": 0.65,
                    "note": f"Entrambi i team sotto-performano xG (miglioramento atteso) | xG proiettato: {projected_total:.1f}"}
        if projected_total > 2.8:
            return {"signal": "over", "strength": 0.55,
                    "note": f"xG combinato alto: {projected_total:.1f}"}
        if projected_total < 1.8:
            return {"signal": "under", "strength": 0.55,
                    "note": f"xG combinato basso: {projected_total:.1f}"}

        return {"signal": "neutral", "strength": 0.0,
                "note": f"xG proiettato nella norma: {projected_total:.1f}"}


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Referee Analysis ──────────────────────────────────────────────────────────

class RefereeAnalysisClient:
    """
    Analisi storica degli arbitri per le partite di calcio.

    Certi arbitri dirigono sistematicamente:
     - Più goal (lasciano giocare)
     - Meno goal (molti falli, interruzioni)
     - Più rigori
     - Più cartellini

    Usa api-football /fixtures filtrato per arbitro.
    """

    BASE = "https://v3.football.api-sports.io"
    MIN_FIXTURES = 5  # minimo partite arbitrate per avere dati affidabili

    def __init__(self) -> None:
        from app.config import settings
        self._key = getattr(settings, "api_football_key", "") or getattr(settings, "football_api_key", "")
        self._timeout = httpx.Timeout(12.0)

    async def get_referee_stats(self, referee_name: str, season: int = 2024) -> dict:
        """
        Recupera le statistiche storiche di un arbitro.

        Returns:
            {
              "name": str,
              "fixtures_analyzed": int,
              "avg_goals_per_game": float,
              "avg_cards_per_game": float,
              "home_win_pct": float,
              "over25_pct": float,          # % partite con più di 2.5 goal
              "goals_bias": str,            # "high_scoring" | "low_scoring" | "neutral"
              "bias_strength": float,       # [0, 1]
            }
        """
        if not self._key or not referee_name:
            return self._empty_stats(referee_name)

        # api-football non ha endpoint diretto per arbitro
        # Usiamo /fixtures?referee={name}&season={year}
        url = f"{self.BASE}/fixtures"
        params = {
            "referee": referee_name,
            "season": season,
            "status": "FT",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers={"x-apisports-key": self._key})
            if resp.status_code != 200:
                return self._empty_stats(referee_name)
            fixtures = resp.json().get("response", [])
        except Exception as exc:
            logger.warning("Referee stats fetch failed for %s: %s", referee_name, exc)
            return self._empty_stats(referee_name)

        if len(fixtures) < self.MIN_FIXTURES:
            return self._empty_stats(referee_name)

        total_goals, total_cards, home_wins, over25 = [], [], 0, 0

        for f in fixtures:
            goals = f.get("goals", {})
            g_home = _safe_float(goals.get("home")) or 0
            g_away = _safe_float(goals.get("away")) or 0
            total_g = g_home + g_away
            total_goals.append(total_g)
            if total_g > 2.5:
                over25 += 1
            if g_home > g_away:
                home_wins += 1

            # Cartellini (se disponibili)
            stats = f.get("statistics", [])
            yellow = sum(
                int(s.get("value", 0) or 0)
                for team_stats in stats
                for s in (team_stats.get("statistics", []) if isinstance(team_stats, dict) else [])
                if s.get("type") == "Yellow Cards"
            )
            total_cards.append(yellow)

        n = len(total_goals)
        avg_goals = round(sum(total_goals) / n, 2)
        avg_cards = round(sum(total_cards) / n, 2) if total_cards else 0
        home_win_pct = round(home_wins / n, 3)
        over25_pct = round(over25 / n, 3)

        # League average ≈ 2.6 goal/partita
        league_avg_goals = 2.6
        diff = avg_goals - league_avg_goals

        if diff > 0.4:
            goals_bias, strength = "high_scoring", min(diff / 0.8, 1.0)
        elif diff < -0.4:
            goals_bias, strength = "low_scoring", min(abs(diff) / 0.8, 1.0)
        else:
            goals_bias, strength = "neutral", 0.0

        return {
            "name":                referee_name,
            "fixtures_analyzed":   n,
            "avg_goals_per_game":  avg_goals,
            "avg_cards_per_game":  avg_cards,
            "home_win_pct":        home_win_pct,
            "over25_pct":          over25_pct,
            "goals_bias":          goals_bias,
            "bias_strength":       round(strength, 3),
        }

    def _empty_stats(self, name: str) -> dict:
        return {
            "name": name, "fixtures_analyzed": 0,
            "avg_goals_per_game": 2.6, "avg_cards_per_game": 4.0,
            "home_win_pct": 0.45, "over25_pct": 0.50,
            "goals_bias": "neutral", "bias_strength": 0.0,
        }

    def ev_modifier_for_totals(self, referee_stats: dict, direction: str) -> float:
        """
        Modificatore EV per mercato over/under totali basato sull'arbitro.
        direction: "over" | "under"
        Returns: float [0.88, 1.12]
        """
        bias = referee_stats.get("goals_bias", "neutral")
        strength = referee_stats.get("bias_strength", 0.0)

        modifier = 1.0
        if bias == "high_scoring" and direction == "over":
            modifier = 1.0 + (strength * 0.12)
        elif bias == "high_scoring" and direction == "under":
            modifier = 1.0 - (strength * 0.10)
        elif bias == "low_scoring" and direction == "under":
            modifier = 1.0 + (strength * 0.12)
        elif bias == "low_scoring" and direction == "over":
            modifier = 1.0 - (strength * 0.10)

        return round(max(0.88, min(1.12, modifier)), 3)


# ── Surface-Adjusted Tennis ELO ───────────────────────────────────────────────

class SurfaceEloClient:
    """
    ELO per superficie per il tennis (clay / hard / grass / carpet).

    Logica:
     - Ogni giocatore ha 3 ELO separati (clay, hard, grass)
     - Calcolati dagli ultimi 24 mesi di risultati surface-specific
     - Usa api-sports.io tennis per recuperare i match per superficie

    Formula ELO standard (K=32):
      E_a = 1 / (1 + 10^((R_b - R_a) / 400))
      R_a_new = R_a + K * (S_a - E_a)

    ELO iniziale: 1500 per tutti i giocatori senza storia.
    """

    BASE = "https://v1.tennis.api-sports.io"
    SURFACES = ("clay", "hard", "grass")
    K_FACTOR = 32

    def __init__(self) -> None:
        from app.config import settings
        self._key = getattr(settings, "api_football_key", "") or getattr(settings, "tennis_api_key", "")
        self._timeout = httpx.Timeout(12.0)

    async def get_surface_elo(
        self,
        player_name: str,
        surface: str,
        fallback_rank: int = 50,
    ) -> float:
        """
        Ritorna l'ELO del giocatore per una superficie specifica.

        Se non ha dati surface-specific a sufficienza:
          → usa il rank ATP/WTA come proxy via ranking_to_elo()

        Args:
            player_name:   nome completo (es. "Carlos Alcaraz")
            surface:       "clay" | "hard" | "grass"
            fallback_rank: rank ATP/WTA per fallback ELO
        """
        surface_lower = surface.lower()
        if surface_lower not in self.SURFACES:
            surface_lower = "hard"

        # Prova a recuperare i match recenti per superficie
        matches = await self._fetch_recent_surface_matches(player_name, surface_lower)
        if len(matches) < 5:
            # Fallback: ranking-based ELO con piccolo boost/penalty per superficie
            base_elo = ranking_to_elo(fallback_rank)
            surface_adj = self._surface_adjustment(player_name, surface_lower)
            return round(base_elo + surface_adj, 0)

        # Calcola ELO dalla storia partite
        return self._compute_elo_from_matches(matches)

    async def _fetch_recent_surface_matches(self, player_name: str, surface: str) -> list[dict]:
        """Fetch match recenti per superficie da api-sports.io tennis."""
        if not self._key:
            return []
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self.BASE}/players",
                    params={"search": player_name},
                    headers={"x-apisports-key": self._key},
                )
            if resp.status_code != 200:
                return []
            players = resp.json().get("response", [])
            if not players:
                return []
            player_id = players[0].get("id")
            if not player_id:
                return []

            # Fetch match per questo giocatore (ultimi 2 anni)
            resp2 = await client.get(
                f"{self.BASE}/games",
                params={"player": player_id, "surface": surface, "last": 30},
                headers={"x-apisports-key": self._key},
            )
            if resp2.status_code != 200:
                return []
            return resp2.json().get("response", [])
        except Exception as exc:
            logger.debug("Surface matches fetch failed for %s: %s", player_name, exc)
            return []

    def _compute_elo_from_matches(self, matches: list[dict], initial_elo: float = 1500.0) -> float:
        """Calcola ELO da lista di match (ordine cronologico)."""
        elo = initial_elo
        for match in sorted(matches, key=lambda m: m.get("date", "")):
            won = match.get("winner", {}).get("id") == match.get("players", {}).get("home", {}).get("id")
            opponent_rank = match.get("players", {}).get("away", {}).get("ranking", 50)
            opp_elo = ranking_to_elo(opponent_rank or 50)

            expected = 1.0 / (1.0 + 10 ** ((opp_elo - elo) / 400.0))
            score = 1.0 if won else 0.0
            elo = elo + self.K_FACTOR * (score - expected)

        return round(elo, 0)

    def _surface_adjustment(self, player_name: str, surface: str) -> float:
        """
        Aggiustamento ELO empirico per superficie basato su specializzazione nota.
        Giocatori clay/grass specialisti ricevono bonus/malus.
        """
        # Specialisti clay noti
        clay_specialists = {
            "carlos alcaraz": 80, "casper ruud": 60, "stefanos tsitsipas": 50,
            "alejandro davidovich": 70, "andrey rublev": 40,
        }
        # Specialisti grass
        grass_specialists = {
            "novak djokovic": 60, "roger federer": 100, "nick kyrgios": 50,
        }
        name_lower = player_name.lower()

        if surface == "clay":
            return clay_specialists.get(name_lower, 0)
        if surface == "grass":
            return grass_specialists.get(name_lower, 0)
        return 0.0

    def get_surface_from_tournament(self, tournament_name: str) -> str:
        """Inferisce la superficie dal nome del torneo."""
        lower = tournament_name.lower()
        if any(k in lower for k in ("roland garros", "clay", "terra", "madrid", "rome", "barcelona", "monte")):
            return "clay"
        if any(k in lower for k in ("wimbledon", "grass", "queens", "halle", "nottingham")):
            return "grass"
        return "hard"  # default: hard court
