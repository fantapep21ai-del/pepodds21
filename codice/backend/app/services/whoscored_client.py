"""
Whoscored client — player statistics scraping via web scraping.

Fetches match centre data from Whoscored (StatsBomb-owned) for:
- Player xG, xA, shots, passes, pressures, tackles, etc.
- Match-by-match statistics for completed/live matches
- Caching with 30-minute TTL to avoid redundant scrapes
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class WhoscoredError(Exception):
    """Base exception for Whoscored client."""
    pass


class WhoscoredMatchNotFound(WhoscoredError):
    """Match data not available (preview page or no data)."""
    pass


class WhoscoredClient:
    """
    Scrape player statistics from Whoscored match pages.

    Only works on LIVE or COMPLETED matches (preview pages return empty).
    """

    def __init__(self):
        self._cache: dict[str, tuple[dict, datetime]] = {}  # url -> (data, timestamp)
        self._cache_ttl = timedelta(minutes=30)

    def _is_cache_valid(self, cached_at: datetime) -> bool:
        """Check if cached entry is still valid."""
        return datetime.now(timezone.utc) - cached_at < self._cache_ttl

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def _scrape_match_page(self, match_url: str, timeout: int = 20) -> str:
        """
        Scrape Whoscored match page HTML.

        Uses Firecrawl for JS rendering + waits for page load.
        Returns raw HTML with matchCentreData embedded.
        """
        from app.services.firecrawl_service import FirecrawlService

        firecrawl = FirecrawlService()
        try:
            result = await firecrawl.scrape(
                url=match_url,
                format="html",
                wait_for_ms=5000,  # Wait 5s for JS rendering
                timeout_ms=timeout * 1000
            )
            return result.get("html", "")
        except Exception as e:
            logger.warning(f"Firecrawl scrape failed for {match_url}: {e}")
            raise WhoscoredError(f"Failed to scrape {match_url}") from e

    @staticmethod
    def _extract_match_centre_data(html: str) -> dict:
        """
        Extract matchCentreData JSON from HTML <script> tag.

        Pattern: var matchCentreData = {...};
        Returns empty dict if not found (preview page).
        """
        pattern = r'var matchCentreData = ({.*?});'
        match = re.search(pattern, html, re.DOTALL)

        if not match:
            return {}

        try:
            json_str = match.group(1)
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse matchCentreData JSON: {e}")
            return {}

    @staticmethod
    def _extract_player_stats(match_data: dict) -> dict[str, dict]:
        """
        Extract and normalize player statistics from matchCentreData.

        Returns:
        {
            "home_players": {
                "player_name": {
                    "position": "ST",
                    "number": 9,
                    "xG": 0.45,
                    "xA": 0.12,
                    "shotOnTarget": 1,
                    "shotsTotal": 3,
                    "passes": 28,
                    "passesCompleted": 22,
                    "tackles": 2,
                    "interceptions": 1,
                    "pressures": 12,
                    "pressuresSuccessful": 5,
                    "fouls": 1,
                    "wasFouled": 2,
                    ...
                },
                ...
            },
            "away_players": {...},
            "match_id": 1978428,
            "status": "complete" | "live" | "unavailable"
        }
        """
        result = {
            "home_players": {},
            "away_players": {},
            "status": "unavailable",
            "match_id": None
        }

        if not match_data:
            return result

        # Extract match metadata
        match_info = match_data.get("match", {})
        result["match_id"] = match_info.get("id")
        result["status"] = match_info.get("status", "unavailable")

        # Extract home team players
        home_team = match_data.get("home", {})
        for player in home_team.get("players", []):
            player_name = player.get("name", "Unknown")
            stats = player.get("stats", {})

            result["home_players"][player_name] = {
                "position": player.get("position", ""),
                "number": player.get("shirtNumber", 0),
                "player_id": player.get("playerId"),
                "xG": stats.get("expectedGoals", 0),
                "xA": stats.get("expectedAssists", 0),
                "shotOnTarget": stats.get("shotOnTarget", 0),
                "shotsTotal": stats.get("shotsTotal", 0),
                "passes": stats.get("passes", 0),
                "passesCompleted": stats.get("passesCompleted", 0),
                "passSuccessPercentage": stats.get("passSuccessPercentage", 0),
                "tackles": stats.get("tackles", 0),
                "interceptions": stats.get("interceptions", 0),
                "pressures": stats.get("pressures", 0),
                "pressuresSuccessful": stats.get("pressuresSuccessful", 0),
                "fouls": stats.get("foulsCommitted", 0),
                "wasFouled": stats.get("wasFouled", 0),
                "dribbles": stats.get("dribbles", 0),
                "dribblesSuccessful": stats.get("dribblesSuccessful", 0),
                "minutesPlayed": stats.get("minutesPlayed", 0),
                "goals": stats.get("goals", 0),
                "assists": stats.get("assists", 0),
            }

        # Extract away team players (same structure)
        away_team = match_data.get("away", {})
        for player in away_team.get("players", []):
            player_name = player.get("name", "Unknown")
            stats = player.get("stats", {})

            result["away_players"][player_name] = {
                "position": player.get("position", ""),
                "number": player.get("shirtNumber", 0),
                "player_id": player.get("playerId"),
                "xG": stats.get("expectedGoals", 0),
                "xA": stats.get("expectedAssists", 0),
                "shotOnTarget": stats.get("shotOnTarget", 0),
                "shotsTotal": stats.get("shotsTotal", 0),
                "passes": stats.get("passes", 0),
                "passesCompleted": stats.get("passesCompleted", 0),
                "passSuccessPercentage": stats.get("passSuccessPercentage", 0),
                "tackles": stats.get("tackles", 0),
                "interceptions": stats.get("interceptions", 0),
                "pressures": stats.get("pressures", 0),
                "pressuresSuccessful": stats.get("pressuresSuccessful", 0),
                "fouls": stats.get("foulsCommitted", 0),
                "wasFouled": stats.get("wasFouled", 0),
                "dribbles": stats.get("dribbles", 0),
                "dribblesSuccessful": stats.get("dribblesSuccessful", 0),
                "minutesPlayed": stats.get("minutesPlayed", 0),
                "goals": stats.get("goals", 0),
                "assists": stats.get("assists", 0),
            }

        return result

    async def fetch_match_stats(self, match_url: str) -> dict:
        """
        Fetch player statistics for a Whoscored match.

        Args:
            match_url: Whoscored match URL (e.g. https://www.whoscored.com/matches/1978428/)

        Returns:
        {
            "status": "complete" | "partial" | "unavailable",
            "home_players": {...},
            "away_players": {...},
            "fetch_time_s": 8.2,
            "match_id": 1978428,
            "match_status": "complete" | "live" | "unavailable"
        }

        Raises:
            WhoscoredError: If scraping fails after retries
        """
        import time

        start_time = time.time()

        # Check cache
        if match_url in self._cache:
            cached_data, cached_at = self._cache[match_url]
            if self._is_cache_valid(cached_at):
                logger.debug(f"Cache hit for {match_url}")
                return cached_data

        try:
            # Scrape match page
            html = await self._scrape_match_page(match_url)

            # Extract matchCentreData
            match_data = self._extract_match_centre_data(html)

            # Parse player stats
            player_stats = self._extract_player_stats(match_data)

            # Build result
            result = {
                "status": "complete" if player_stats["home_players"] else "unavailable",
                "home_players": player_stats["home_players"],
                "away_players": player_stats["away_players"],
                "fetch_time_s": round(time.time() - start_time, 2),
                "match_id": player_stats["match_id"],
                "match_status": player_stats["status"],
            }

            # Cache result
            self._cache[match_url] = (result, datetime.now(timezone.utc))

            logger.info(
                f"Whoscored fetch successful for match {result['match_id']}: "
                f"{len(result['home_players'])} home + {len(result['away_players'])} away players, "
                f"fetch_time={result['fetch_time_s']}s"
            )

            return result

        except WhoscoredError as e:
            logger.error(f"Whoscored fetch failed for {match_url}: {e}")
            return {
                "status": "unavailable",
                "home_players": {},
                "away_players": {},
                "fetch_time_s": round(time.time() - start_time, 2),
                "error": str(e),
            }
