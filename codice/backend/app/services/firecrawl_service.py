"""
Firecrawl service — async wrapper for web scraping via Firecrawl API.

Handles JavaScript-rendered pages, with retry logic and timeout management.
"""

import logging
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class FirecrawlService:
    """
    Async wrapper for Firecrawl web scraping.

    Firecrawl handles:
    - JavaScript rendering (Chrome headless browser)
    - Dynamic content loading
    - Rate limiting with exponential backoff
    """

    def __init__(self):
        from app.config import settings

        self._api_key = settings.firecrawl_api_key
        self._base_url = "https://api.firecrawl.dev/v1"

        if not self._api_key:
            logger.warning("Firecrawl API key not configured — web scraping unavailable")

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=1, max=10),
        reraise=True
    )
    async def scrape(
        self,
        url: str,
        format: str = "markdown",
        wait_for_ms: int = 0,
        timeout_ms: int = 30000
    ) -> dict:
        """
        Scrape a URL with Firecrawl.

        Args:
            url: URL to scrape
            format: "markdown", "html", "rawHtml", "json", etc.
            wait_for_ms: Wait for JS to render (e.g., 5000 for 5 seconds)
            timeout_ms: Total timeout in milliseconds

        Returns:
        {
            "success": True,
            "data": {
                "html": "...",  # or "markdown" or "json" depending on format
                "metadata": {...}
            }
        }

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self._api_key:
            logger.error("Firecrawl API key not configured")
            return {"success": False, "error": "API key not configured"}

        headers = {"Authorization": f"Bearer {self._api_key}"}

        payload = {
            "url": url,
            "formats": [format],
            "waitFor": wait_for_ms,
            "timeout": timeout_ms // 1000,  # Convert to seconds
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_ms / 1000 + 10) as client:
                resp = await client.post(
                    f"{self._base_url}/scrape",
                    json=payload,
                    headers=headers
                )

                if resp.status_code == 429:
                    logger.warning(f"Firecrawl rate limit for {url}")
                    raise httpx.RequestError("Rate limited")

                resp.raise_for_status()
                return resp.json()

        except httpx.HTTPError as e:
            logger.error(f"Firecrawl scrape failed for {url}: {e}")
            raise

    async def map(
        self,
        url: str,
        include_subdomains: bool = False,
        limit: int = 50
    ) -> dict:
        """
        Map (crawl) a website to discover all URLs.

        Args:
            url: Root URL to crawl
            include_subdomains: Whether to include subdomains
            limit: Max URLs to discover

        Returns:
        {
            "success": True,
            "data": {
                "urls": ["https://...", "https://...", ...]
            }
        }
        """
        if not self._api_key:
            return {"success": False, "error": "API key not configured"}

        headers = {"Authorization": f"Bearer {self._api_key}"}

        payload = {
            "url": url,
            "includeSubdomains": include_subdomains,
            "limit": limit,
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/map",
                    json=payload,
                    headers=headers
                )

                if resp.status_code == 429:
                    logger.warning(f"Firecrawl rate limit for {url}")
                    raise httpx.RequestError("Rate limited")

                resp.raise_for_status()
                return resp.json()

        except httpx.HTTPError as e:
            logger.error(f"Firecrawl map failed for {url}: {e}")
            raise
