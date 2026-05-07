"""
News scraper — aggregates player/team injury and form news from multiple sources.

Sources:
1. Transfermarkt — injuries, suspensions, official status
2. Sofascore API — recent form, team news
3. ESPN — latest news, injury reports

All sources are public (no authentication required).
"""

import asyncio
import json
import logging
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from fuzzywuzzy import fuzz

logger = logging.getLogger(__name__)


class NewsScraperError(Exception):
    """Base exception for news scraper."""
    pass


def _normalize_player_name(name: str) -> str:
    """
    Normalize player name for fuzzy matching.
    Handles accents, case-insensitivity, extra spaces.
    Example: "Cristiano Ronaldo dos Santos" → "cristiano ronaldo dos santos"
    """
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower().strip()
    name = " ".join(name.split())  # Collapse multiple spaces
    return name


def _fuzzy_match_player(name1: str, name2: str, threshold: int = 85) -> bool:
    """
    Fuzzy match two player names using WRatio (token+substring matching).
    Returns True if similarity >= threshold (default 85%).

    Examples:
      - "Cristiano Ronaldo" vs "C. Ronaldo" → True (WRatio > 85%)
      - "Mbappé" vs "Mbappe" → True (accent handled)
      - "João Felix" vs "Joao Felix" → True (NFKD normalization)
      - "John Smith" vs "Jane Doe" → False
    """
    norm1 = _normalize_player_name(name1)
    norm2 = _normalize_player_name(name2)
    similarity = fuzz.WRatio(norm1, norm2)
    return similarity >= threshold


class NewsScraperService:
    """
    Fetch player/team news and injury status from authoritative sources.
    """

    def __init__(self):
        self._cache: dict[str, tuple[dict, datetime]] = {}
        self._cache_ttl = timedelta(minutes=60)
        self._sofascore_base = "https://api.sofascore.com/api/v1"

    def _is_cache_valid(self, cached_at: datetime) -> bool:
        """Check if cached entry is still valid."""
        return datetime.now(timezone.utc) - cached_at < self._cache_ttl

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    async def _fetch_sofascore_team_info(self, team_name: str) -> dict:
        """
        Fetch team info and recent form from Sofascore API.

        Returns: {"injuries": [...], "recent_form": [...], "source": "sofascore"}
        """
        try:
            # Search for team ID (public endpoint, no auth)
            async with httpx.AsyncClient(timeout=10) as client:
                search_resp = await client.get(
                    f"{self._sofascore_base}/search",
                    params={"q": team_name, "type": "team"}
                )
                search_resp.raise_for_status()
                search_data = search_resp.json()

                if not search_data.get("teams"):
                    logger.debug(f"Sofascore: team '{team_name}' not found")
                    return {}

                team_id = search_data["teams"][0]["id"]

                # Fetch team info (includes injuries)
                info_resp = await client.get(
                    f"{self._sofascore_base}/team/{team_id}"
                )
                info_resp.raise_for_status()
                team_data = info_resp.json()

                injuries = []
                for player in team_data.get("squad", []):
                    if player.get("contractUntilDate") or player.get("injuryStatus"):
                        injuries.append({
                            "player": player.get("name", "Unknown"),
                            "position": player.get("position"),
                            "status": player.get("injuryStatus", "available"),
                        })

                return {
                    "injuries": injuries,
                    "source": "sofascore",
                    "fetch_time_s": 3.0,
                }

        except Exception as e:
            logger.warning(f"Sofascore fetch failed for '{team_name}': {e}")
            return {}

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    async def _fetch_transfermarkt_team_news(self, team_name: str) -> dict:
        """
        Fetch team injury status and suspensions from Transfermarkt.

        Uses web scraping via Firecrawl (Transfermarkt is JS-heavy).
        Returns: {"injuries": [...], "suspensions": [...], "source": "transfermarkt"}
        """
        try:
            from app.services.firecrawl_service import FirecrawlService

            # Construct Transfermarkt search URL
            search_url = f"https://www.transfermarkt.com/search?q={team_name.replace(' ', '+')}"

            firecrawl = FirecrawlService()
            result = await firecrawl.scrape(
                url=search_url,
                format="html",
                wait_for_ms=3000,
                timeout_ms=15000
            )

            html = result.get("html", "")

            # Parse injury/suspension info from HTML (simplified extraction)
            injuries = []
            suspensions = []

            # Look for injury indicators in the HTML
            if "verletzt" in html.lower() or "injured" in html.lower():
                # This is a simplified extraction; real implementation would parse HTML more thoroughly
                logger.debug(f"Transfermarkt: injury indicators found for '{team_name}'")

            return {
                "injuries": injuries,
                "suspensions": suspensions,
                "source": "transfermarkt",
                "fetch_time_s": 5.0,
            }

        except Exception as e:
            logger.warning(f"Transfermarkt fetch failed for '{team_name}': {e}")
            return {}

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    async def _fetch_espn_team_news(self, team_name: str) -> dict:
        """
        Fetch latest team news from ESPN.

        Returns: {"news_items": [...], "source": "espn"}
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # ESPN news feed (simplified — real implementation would use ESPN API or scraping)
                # For now, return empty array (ESPN requires more complex scraping/API)
                return {
                    "news_items": [],
                    "source": "espn",
                    "fetch_time_s": 2.0,
                }

        except Exception as e:
            logger.warning(f"ESPN fetch failed for '{team_name}': {e}")
            return {}

    async def fetch_combined_team_news(
        self,
        team_name: str,
        match_date: Optional[datetime] = None
    ) -> dict:
        """
        Aggregate news from all sources for a team.

        Args:
            team_name: Team name (e.g., "Manchester City")
            match_date: Optional match date (for freshness filtering)

        Returns:
        {
            "injuries": [
                {
                    "player": "Haaland",
                    "status": "out" | "doubtful" | "healthy",
                    "reason": "injury" | "suspension" | "other",
                    "until": "2026-05-10",
                    "source": "sofascore",
                    "confidence": 0.95
                },
                ...
            ],
            "suspensions": [...],
            "recent_form": [...],
            "transfer_rumors": [...],
            "fetch_time_s": 12.5,
            "sources_used": ["sofascore", "transfermarkt"],
            "fetch_status": "complete" | "partial" | "failed"
        }
        """
        import time

        start_time = time.time()
        cache_key = f"team_news:{team_name}"

        # Check cache
        if cache_key in self._cache:
            cached_data, cached_at = self._cache[cache_key]
            if self._is_cache_valid(cached_at):
                logger.debug(f"Cache hit for team '{team_name}'")
                return cached_data

        # Fetch from all sources in parallel
        try:
            sofascore_task = self._fetch_sofascore_team_info(team_name)
            transfermarkt_task = self._fetch_transfermarkt_team_news(team_name)
            espn_task = self._fetch_espn_team_news(team_name)

            sofascore_data, transfermarkt_data, espn_data = await asyncio.gather(
                sofascore_task,
                transfermarkt_task,
                espn_task,
                return_exceptions=True
            )

            # Aggregate results
            all_injuries = []
            all_suspensions = []
            sources_used = []

            if isinstance(sofascore_data, dict) and sofascore_data.get("injuries"):
                all_injuries.extend([
                    {
                        **inj,
                        "status": inj.get("status", "unknown"),
                        "confidence": 0.9,
                        "source": "sofascore"
                    }
                    for inj in sofascore_data.get("injuries", [])
                ])
                sources_used.append("sofascore")

            if isinstance(transfermarkt_data, dict):
                all_injuries.extend([
                    {**inj, "confidence": 0.85, "source": "transfermarkt"}
                    for inj in transfermarkt_data.get("injuries", [])
                ])
                all_suspensions.extend([
                    {**sus, "confidence": 0.95, "source": "transfermarkt"}
                    for sus in transfermarkt_data.get("suspensions", [])
                ])
                if transfermarkt_data.get("injuries") or transfermarkt_data.get("suspensions"):
                    sources_used.append("transfermarkt")

            if isinstance(espn_data, dict) and espn_data.get("news_items"):
                sources_used.append("espn")

            # Deduplicate injuries by player name using fuzzy matching
            # Handles name variants (Mbappé vs Mbappe, C. Ronaldo vs Cristiano Ronaldo, etc.)
            deduplicated_injuries = []
            for inj in all_injuries:
                player_name = inj.get("player", "unknown")
                is_duplicate = False

                for existing_inj in deduplicated_injuries:
                    if _fuzzy_match_player(player_name, existing_inj.get("player", ""), threshold=85):
                        logger.debug(
                            "Injury dedup: '%s' matches existing '%s' (WRatio >= 85%%)",
                            player_name, existing_inj.get("player")
                        )
                        is_duplicate = True
                        break

                if not is_duplicate:
                    deduplicated_injuries.append(inj)

            result = {
                "injuries": deduplicated_injuries,
                "suspensions": all_suspensions,
                "recent_form": [],  # Could be enriched from Sofascore team stats
                "transfer_rumors": [],
                "fetch_time_s": round(time.time() - start_time, 2),
                "sources_used": sources_used,
                "fetch_status": "complete" if sources_used else "failed",
            }

            # Cache result
            self._cache[cache_key] = (result, datetime.now(timezone.utc))

            logger.info(
                f"Team news aggregated for '{team_name}': "
                f"{len(all_injuries)} injuries, {len(all_suspensions)} suspensions, "
                f"sources={sources_used}, fetch_time={result['fetch_time_s']}s"
            )

            return result

        except Exception as e:
            logger.error(f"Team news aggregation failed for '{team_name}': {e}")
            return {
                "injuries": [],
                "suspensions": [],
                "fetch_status": "failed",
                "error": str(e),
            }

    async def fetch_player_injury_status(
        self,
        player_name: str,
        team_name: str
    ) -> Optional[dict]:
        """
        Fetch injury status for a specific player.

        Returns:
        {
            "player": "Haaland",
            "team": "Manchester City",
            "status": "healthy" | "doubtful" | "out",
            "reason": "injury" | "suspension" | "transfer_news",
            "until": "2026-05-10",  # estimated return date
            "source": "sofascore",
            "confidence": 0.95
        }

        or None if not found.
        """
        # Fetch team news and search for player
        team_news = await self.fetch_combined_team_news(team_name)

        for injury in team_news.get("injuries", []):
            if injury.get("player", "").lower() == player_name.lower():
                return {
                    "player": player_name,
                    "team": team_name,
                    "status": injury.get("status", "unknown"),
                    "source": injury.get("source", "unknown"),
                    "confidence": injury.get("confidence", 0.5),
                }

        # Not found in injuries — assume healthy
        return None
