"""
Sofascore API client — fetcha quote tennis ATP/WTA (gratuito, pubblico).
Sofascore ha copertura globale di tennis con odds da vari bookmaker.
"""
from __future__ import annotations

import logging
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class SofascoreClient:
    """Client per Sofascore API — quote tennis ATP/WTA."""

    BASE_URL = "https://api.sofascore.com/api/v1"

    async def fetch_tennis_odds(self, match_id: str | None = None, hours_lookahead: int = 18) -> list[dict]:
        """
        Fetcha quote tennis da Sofascore per i prossimi giorni.

        Returns:
        [
            {
                "match_id": "sofascore_123",
                "player_a": "Jannik Sinner",
                "player_b": "Alexander Zverev",
                "match_date": datetime,
                "tournament": "ATP Italian Open",
                "odds": [
                    {
                        "bookmaker": "Bet365",
                        "player_a_odds": 1.85,
                        "player_b_odds": 2.10,
                        "market": "h2h"
                    },
                    ...
                ],
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
                # Endpoint: tennis matches (non-live, upcoming)
                resp = await client.get(
                    f"{self.BASE_URL}/sport/tennis/events",
                    params={
                        "limit": 50,
                        "offset": 0,
                    },
                    headers={"Accept": "application/json"},
                )

                if not resp.is_success:
                    logger.warning("Sofascore API: request failed (status %d)", resp.status_code)
                    return []

                data = resp.json()
                events = data.get("events", [])
                logger.debug("Sofascore API returned %d events", len(events))

                for event in events:
                    try:
                        # Parse match date
                        start_timestamp = event.get("startTimestamp")
                        if not start_timestamp:
                            continue

                        match_date = datetime.fromtimestamp(start_timestamp, tz=timezone.utc)

                        # Filtra per timeframe
                        if not (now <= match_date <= cutoff):
                            continue

                        # Parse teams/players
                        home_data = event.get("homeTeam", {})
                        away_data = event.get("awayTeam", {})
                        player_a = home_data.get("name", "?")
                        player_b = away_data.get("name", "?")

                        # Get tournament
                        tournament_data = event.get("tournament", {})
                        tournament = tournament_data.get("name", "Unknown")

                        # Get status
                        status_data = event.get("status", {})
                        status_type = status_data.get("type", "scheduled").lower()
                        if status_type == "notstarted":
                            status = "scheduled"
                        elif status_type == "inprogress":
                            status = "live"
                        elif status_type == "finished":
                            status = "final"
                        else:
                            status = status_type

                        # Fetch odds per questo match
                        match_id = event.get("id", "")
                        odds_list = await self._fetch_match_odds(client, match_id)

                        matches.append({
                            "id": f"sofascore_{match_id}",
                            "player_a": player_a,
                            "player_b": player_b,
                            "match_date": match_date,
                            "tournament": tournament,
                            "odds": odds_list,
                            "status": status,
                        })
                    except Exception as e:
                        logger.debug("Sofascore match parse error: %s", e)
                        continue

                logger.info("Sofascore API: found %d tennis matches in next %dh", len(matches), hours_lookahead)
        except Exception as e:
            logger.error("Sofascore client error: %s", e)
            return []

        return matches

    async def _fetch_match_odds(self, client: httpx.AsyncClient, match_id: str) -> list[dict]:
        """Fetcha le quote per un singolo match da Sofascore."""
        try:
            resp = await client.get(
                f"{self.BASE_URL}/event/{match_id}/odds",
                headers={"Accept": "application/json"},
            )

            if not resp.is_success:
                logger.debug("Sofascore odds fetch failed for match %s (status %d)", match_id, resp.status_code)
                return []

            data = resp.json()
            odds_list = []

            # Parse bookmakers and odds
            markets = data.get("odds", [])
            for market in markets:
                try:
                    bookmaker = market.get("bookmaker", {}).get("name", "Unknown")
                    choices = market.get("choices", [])

                    if len(choices) >= 2:
                        odds_list.append({
                            "bookmaker": bookmaker,
                            "player_a_odds": float(choices[0].get("fractionalValue", "1.0")),
                            "player_b_odds": float(choices[1].get("fractionalValue", "1.0")),
                            "market": "h2h",
                        })
                except Exception as e:
                    logger.debug("Sofascore odds parse error for match %s: %s", match_id, e)
                    continue

            return odds_list
        except Exception as e:
            logger.debug("Sofascore _fetch_match_odds error: %s", e)
            return []
