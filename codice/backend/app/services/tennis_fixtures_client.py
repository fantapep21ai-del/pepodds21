"""
Tennis fixtures client — fetcha partite tennis ATP/WTA (gratuito).
Usa tennis-live-data.p.rapidapi.com o fonti pubbliche.
"""
from __future__ import annotations

import logging
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class TennisFixturesClient:
    """Client per partite tennis ATP/WTA — Masters 1000 + Grand Slams."""

    # Tennis Live Data API (public endpoint, no key required for basic data)
    BASE_URL = "https://tennis-live-data.p.rapidapi.com"

    # Master 1000 tournaments (principali tornei)
    MAIN_TOURNAMENTS = [
        "Australian Open",
        "Roland Garros",
        "Wimbledon",
        "US Open",
        "Miami Open",
        "Monte Carlo",
        "Madrid Open",
        "Rome Masters",
        "Cincinnati Masters",
        "Shanghai Masters",
        "Paris Masters",
        "Indian Wells",
    ]

    async def fetch_upcoming_matches(self, hours_lookahead: int = 18) -> list[dict]:
        """
        Fetcha partite tennis nei prossimi giorni.

        Returns:
        [
            {
                "id": "match_123",
                "player_a": "Novak Djokovic",
                "player_b": "Rafael Nadal",
                "match_date": datetime,
                "tournament": "Roland Garros",
                "status": "scheduled",
            },
            ...
        ]
        """
        matches = []
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_lookahead)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Endpoint: upcoming matches (public, no auth needed)
                resp = await client.get(
                    f"{self.BASE_URL}/matches",
                    params={
                        "status": "scheduled",
                        "limit": 50,
                    },
                    headers={"Accept": "application/json"},
                )

                if not resp.is_success:
                    logger.warning("Tennis API: request failed (status %d)", resp.status_code)
                    # Fallback: ritorna lista vuota, continua con altre fonti
                    return []

                data = resp.json()
                for match in data.get("results", []) or data.get("data", []) or []:
                    try:
                        # Parse match date
                        date_str = match.get("date") or match.get("match_date")
                        if not date_str:
                            continue

                        match_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

                        # Filtra per timeframe
                        if now <= match_date <= cutoff:
                            tournament = match.get("tournament") or match.get("event", "")
                            player_a = match.get("player_1") or match.get("home_player", "?")
                            player_b = match.get("player_2") or match.get("away_player", "?")

                            # Filter: solo Grand Slams + Masters 1000
                            is_major = any(t in tournament for t in self.MAIN_TOURNAMENTS)
                            if not is_major:
                                continue

                            matches.append({
                                "id": match.get("id", ""),
                                "player_a": player_a,
                                "player_b": player_b,
                                "match_date": match_date,
                                "tournament": tournament,
                                "status": "scheduled",
                            })
                    except Exception as e:
                        logger.debug("Tennis match parse error: %s", e)
                        continue

                logger.info("Tennis API: found %d matches in next %dh", len(matches), hours_lookahead)
        except Exception as e:
            logger.error("Tennis client error: %s", e)
            return []

        return matches
