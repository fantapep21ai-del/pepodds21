"""
Football-Data.org API client — fetcha partite di calcio gratuitamente.
Piano gratuito: 10 req/min, nessuna API key richiesta.
"""
from __future__ import annotations

import logging
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class FootballDataOrgClient:
    """Client per football-data.org — partite di calcio europee principali."""

    BASE_URL = "https://api.football-data.org/v4"

    # Competizioni europee principali (IDs da football-data.org)
    MAIN_COMPETITIONS = {
        "PL": "Premier League",      # Inghilterra
        "SA": "Serie A",              # Italia
        "BL1": "Bundesliga",          # Germania
        "FR1": "Ligue 1",             # Francia
        "ES": "La Liga",              # Spagna
        "PPL": "Primeira Liga",       # Portogallo
        "NL": "Eredivisie",           # Olanda
        "CL": "UEFA Champions League",
        "EL": "UEFA Europa League",
    }

    async def fetch_upcoming_matches(self, hours_lookahead: int = 18) -> list[dict]:
        """
        Fetcha partite di calcio europee nelle prossime 18 ore.

        Returns:
        [
            {
                "id": "123456",
                "home_team": "Manchester United",
                "away_team": "Liverpool",
                "match_date": datetime,
                "competition": "Premier League",
                "status": "SCHEDULED",  o "LIVE", "FINISHED"
            },
            ...
        ]
        """
        matches = []
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_lookahead)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for comp_code, comp_name in self.MAIN_COMPETITIONS.items():
                    try:
                        resp = await client.get(
                            f"{self.BASE_URL}/competitions/{comp_code}/matches",
                            params={
                                "status": "SCHEDULED,LIVE",
                                "limit": 10,
                            },
                        )
                        if not resp.is_success:
                            logger.warning("Football-Data: %s failed (status %d)", comp_code, resp.status_code)
                            continue

                        data = resp.json()
                        for m in data.get("matches", []):
                            match_date = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))

                            # Filtra per timeframe: solo partite nelle prossime 18h
                            if now <= match_date <= cutoff:
                                home = m.get("homeTeam", {}).get("name", "?")
                                away = m.get("awayTeam", {}).get("name", "?")
                                matches.append({
                                    "id": str(m["id"]),
                                    "home_team": home,
                                    "away_team": away,
                                    "match_date": match_date,
                                    "competition": comp_name,
                                    "status": m["status"],
                                })

                        logger.info("Football-Data %s: found %d matches", comp_code, len(data.get("matches", [])))
                    except Exception as e:
                        logger.warning("Football-Data %s error: %s", comp_code, e)
                        continue

        except Exception as e:
            logger.error("Football-Data client error: %s", e)
            return []

        logger.info("Football-Data: total %d matches in next %dh", len(matches), hours_lookahead)
        return matches
