"""
ESPN NBA API client — fetcha partite NBA in real-time (gratuito, pubblico).
"""
from __future__ import annotations

import logging
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class NBAFixturesClient:
    """Client per ESPN NBA API — partite NBA USA."""

    BASE_URL = "https://www.espn.com/apis/site/v2/sports/basketball/nba"

    async def fetch_upcoming_matches(self, hours_lookahead: int = 18) -> list[dict]:
        """
        Fetcha partite NBA nelle prossime 18 ore.

        Returns:
        [
            {
                "id": "401547123",
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "match_date": datetime,
                "status": "scheduled",  # o "live", "final"
            },
            ...
        ]
        """
        matches = []
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_lookahead)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self.BASE_URL}/events")
                if not resp.is_success:
                    logger.warning("ESPN NBA: request failed (status %d)", resp.status_code)
                    return []

                data = resp.json()
                for event in data.get("events", []):
                    # Parse date
                    date_str = event.get("date", "")
                    try:
                        match_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    except Exception:
                        continue

                    # Filtra per timeframe
                    if now <= match_date <= cutoff:
                        competitions = event.get("competitions", [])
                        if not competitions:
                            continue

                        comp = competitions[0]
                        competitors = comp.get("competitors", [])
                        if len(competitors) < 2:
                            continue

                        home = competitors[0].get("team", {}).get("displayName", "?")
                        away = competitors[1].get("team", {}).get("displayName", "?")

                        status = event.get("status", {}).get("type", "scheduled")
                        if status == "pre":
                            status = "scheduled"
                        elif status == "in":
                            status = "live"
                        elif status == "post":
                            status = "final"

                        matches.append({
                            "id": event.get("id", ""),
                            "home_team": home,
                            "away_team": away,
                            "match_date": match_date,
                            "status": status,
                        })

                logger.info("ESPN NBA: found %d matches in next %dh", len(matches), hours_lookahead)
        except Exception as e:
            logger.error("ESPN NBA client error: %s", e)
            return []

        return matches
