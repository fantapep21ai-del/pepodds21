"""
Unified Odds API client — fetcha partite per calcio, NBA, tennis direttamente da The Odds API.
Usa le 4 API keys configurate su Railway con rotazione automatica.
"""
from __future__ import annotations

import logging
import httpx
from datetime import datetime, timezone, timedelta
from app.config import settings

logger = logging.getLogger(__name__)


class OddsFixturesClient:
    """Client unico per The Odds API — supporta calcio, NBA, tennis."""

    BASE_URL = "https://api.the-odds-api.com/v4"

    # Mapping sport -> sport_key su The Odds API
    SPORT_KEYS = {
        "football": [
            "soccer_epl",           # Premier League
            "soccer_italy",         # Serie A
            "soccer_germany_bundesliga",  # Bundesliga
            "soccer_spain_la_liga", # La Liga
            "soccer_france_ligue_one",  # Ligue 1
            "soccer_portugal_primeira_liga",
            "soccer_netherlands_eredivisie",
            "soccer_uefa_champs_league",  # Champions League
            "soccer_uefa_europa_league",  # Europa League
        ],
        "basketball": [
            "basketball_nba",
            "basketball_nba_playoffs",
        ],
        "tennis": [
            "tennis_atp",
            "tennis_wta",
        ],
    }

    def __init__(self):
        """Inizializza con le API keys da config."""
        self.api_keys = [
            settings.odds_api_key,
            settings.odds_api_key_2,
            settings.odds_api_key_3,
            settings.odds_api_key_4,
        ]
        self.api_keys = [k for k in self.api_keys if k]  # Rimuovi vuote
        self.current_key_index = 0
        logger.info("🔑 OddsFixturesClient initialized with %d API keys", len(self.api_keys))

    def _get_next_key(self) -> str:
        """Rotazione tra le API keys disponibili."""
        if not self.api_keys:
            raise ValueError("No Odds API keys configured")
        key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        return key

    async def fetch_upcoming_matches(self, sport: str, hours_lookahead: int = 18) -> list[dict]:
        """
        Fetcha partite per uno sport dalle prossime 18h usando The Odds API.

        Args:
            sport: "football", "basketball", "tennis"
            hours_lookahead: finestra temporale (default 18h)

        Returns:
        [
            {
                "id": "match_123",
                "home_team": "Team A",
                "away_team": "Team B",
                "match_date": datetime,
                "competition": "Premier League",
                "status": "scheduled",
            },
            ...
        ]
        """
        if sport not in self.SPORT_KEYS:
            logger.warning("Sport %s not supported", sport)
            return []

        matches = []
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_lookahead)
        sport_keys = self.SPORT_KEYS[sport]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for sport_key in sport_keys:
                    try:
                        api_key = self._get_next_key()
                        logger.info("Fetching %s from The Odds API (key rotation)...", sport_key)

                        resp = await client.get(
                            f"{self.BASE_URL}/sports/{sport_key}/events",
                            params={
                                "apiKey": api_key,
                                "regions": "us,eu",
                                "markets": "h2h,spreads,totals",  # Fetch tutte le quote disponibili
                                "oddsFormat": "decimal",
                            },
                        )

                        if not resp.is_success:
                            logger.warning("The Odds API %s failed (status %d). Response: %s", sport_key, resp.status_code, resp.text[:200])
                            if resp.status_code == 401:
                                logger.error("Invalid API key for %s", sport_key)
                            continue

                        data = resp.json()
                        events = data.get("events", [])
                        logger.debug("The Odds API %s returned %d events", sport_key, len(events))
                        for event in events:
                            try:
                                # Parse match date
                                date_str = event.get("commence_time")
                                if not date_str:
                                    continue

                                match_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

                                # Filtra per timeframe
                                if now <= match_date <= cutoff:
                                    home = event.get("home_team", "?")
                                    away = event.get("away_team", "?")
                                    competition = self._extract_competition(sport_key, event)

                                    matches.append({
                                        "id": event.get("id", ""),
                                        "home_team": home,
                                        "away_team": away,
                                        "match_date": match_date,
                                        "competition": competition,
                                        "status": "scheduled",  # The Odds API non dà status in evento
                                    })
                            except Exception as e:
                                logger.debug("Match parse error: %s", e)
                                continue

                        logger.info("The Odds API %s: found %d matches", sport_key, len(data.get("events", [])))
                    except Exception as e:
                        logger.warning("The Odds API %s error: %s", sport_key, e)
                        continue

        except Exception as e:
            logger.error("OddsFixturesClient error: %s", e)
            return []

        logger.info("OddsFixturesClient: total %d matches in next %dh for sport=%s", len(matches), hours_lookahead, sport)
        return matches

    @staticmethod
    def _extract_competition(sport_key: str, event: dict) -> str:
        """Estrae il nome della competizione dal sport_key."""
        mapping = {
            "soccer_epl": "Premier League",
            "soccer_italy": "Serie A",
            "soccer_germany_bundesliga": "Bundesliga",
            "soccer_spain_la_liga": "La Liga",
            "soccer_france_ligue_one": "Ligue 1",
            "soccer_portugal_primeira_liga": "Primeira Liga",
            "soccer_netherlands_eredivisie": "Eredivisie",
            "soccer_uefa_champs_league": "UEFA Champions League",
            "soccer_uefa_europa_league": "UEFA Europa League",
            "basketball_nba": "NBA",
            "basketball_nba_playoffs": "NBA Playoffs",
            "tennis_atp": "ATP",
            "tennis_wta": "WTA",
        }
        return mapping.get(sport_key, "Unknown")
