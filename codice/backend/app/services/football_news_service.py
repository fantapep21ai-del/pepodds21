"""
FootballNewsService — RSS feeds per news di calcio.

Fonti gratuite (no API key):
  - BBC Sport Football RSS
  - Sky Sport Italia RSS
  - Gazzetta dello Sport RSS
  - TuttoSport RSS
  - UEFA.com news RSS

Cerca per nome squadra nel titolo/descrizione dell'articolo.
Ritorna le ultime 5 notizie rilevanti per una partita specifica.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    # Sport internazionale (inglese)
    "https://feeds.bbci.co.uk/sport/football/rss.xml",
    # Sky Sport Italia
    "https://sport.sky.it/sport/calcio.rss",
    # Gazzetta dello Sport
    "https://www.gazzetta.it/rss/home.xml",
    # TuttoSport
    "https://www.tuttosport.com/rss/home.xml",
    # Corriere dello Sport
    "https://www.corrieredellosport.it/rss/home.xml",
    # ESPN Soccer
    "https://www.espn.com/espn/rss/soccer/news",
]

# Parole chiave che indicano notizie rilevanti per le quote
RELEVANT_KEYWORDS = [
    "infortun", "injury", "injured", "squalific", "suspended",
    "titolare", "starter", "formazione", "lineup", "team news",
    "out", "dubbio", "doubt", "conferenza", "press conference",
    "allenamento", "training", "recupero", "return",
]


class FootballNewsService:
    """Aggrega notizie RSS per squadre di calcio."""

    def __init__(self, timeout: float = 8.0) -> None:
        self._timeout = httpx.Timeout(timeout)

    async def get_match_news(
        self,
        home_team: str,
        away_team: str,
        max_articles: int = 5,
        hours_back: int = 48,
    ) -> str:
        """
        Cerca notizie recenti per home_team e away_team.
        Ritorna un testo riassuntivo per l'UncertaintyAgent.
        """
        articles = await self._fetch_all_feeds(hours_back)
        relevant = self._filter_for_match(articles, home_team, away_team)

        if not relevant:
            return ""

        # Deduplica per titolo simile e prendi i più recenti
        seen_titles: set[str] = set()
        unique = []
        for a in sorted(relevant, key=lambda x: x["published"], reverse=True):
            title_key = a["title"][:40].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique.append(a)
            if len(unique) >= max_articles:
                break

        lines = []
        for a in unique:
            pub_str = a["published"].strftime("%d/%m %H:%M") if a.get("published") else ""
            lines.append(f"- [{pub_str}] {a['title']}")
            if a.get("summary"):
                lines.append(f"  {a['summary'][:120]}")

        return "\n".join(lines)

    async def _fetch_all_feeds(self, hours_back: int) -> list[dict]:
        """Scarica e parsa tutti i feed RSS in parallelo."""
        import asyncio
        tasks = [self._fetch_feed(url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        articles: list[dict] = []
        for result in results:
            if isinstance(result, Exception) or not result:
                continue
            for art in result:
                pub = art.get("published")
                if pub and isinstance(pub, datetime):
                    if pub >= cutoff:
                        articles.append(art)
                else:
                    articles.append(art)  # include se non riusciamo a parsare la data

        return articles

    async def _fetch_feed(self, url: str) -> list[dict]:
        """Scarica un singolo feed RSS e parsa gli articoli."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                return []
            return self._parse_rss(resp.text)
        except Exception as exc:
            logger.debug("RSS fetch failed for %s: %s", url, exc)
            return []

    def _parse_rss(self, xml_text: str) -> list[dict]:
        """Parsa XML RSS senza librerie esterne (regex semplice)."""
        articles = []

        # Estrai ogni <item>
        items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
        for item in items:
            title   = self._extract_tag(item, "title")
            summary = self._extract_tag(item, "description") or self._extract_tag(item, "summary")
            pub_str = self._extract_tag(item, "pubDate") or self._extract_tag(item, "dc:date")

            published: Optional[datetime] = None
            if pub_str:
                published = self._parse_date(pub_str)

            # Rimuovi HTML dai campi
            if summary:
                summary = re.sub(r"<[^>]+>", "", summary).strip()[:200]
            if title:
                title = re.sub(r"<[^>]+>", "", title).strip()

            if title:
                articles.append({
                    "title": title,
                    "summary": summary or "",
                    "published": published,
                })

        return articles

    def _extract_tag(self, text: str, tag: str) -> Optional[str]:
        m = re.search(rf"<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>", text, re.DOTALL)
        if m:
            val = m.group(1).strip()
            # CDATA
            val = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", val, flags=re.DOTALL)
            return val.strip()
        return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parsa date RSS in formato RFC 2822 o ISO 8601."""
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    def _filter_for_match(
        self,
        articles: list[dict],
        home_team: str,
        away_team: str,
    ) -> list[dict]:
        """Filtra articoli rilevanti per una partita specifica."""
        home_words = self._team_keywords(home_team)
        away_words = self._team_keywords(away_team)

        relevant = []
        for art in articles:
            text = (art.get("title", "") + " " + art.get("summary", "")).lower()

            home_match = any(w in text for w in home_words)
            away_match = any(w in text for w in away_words)

            if not (home_match or away_match):
                continue

            # Bonus: l'articolo menziona parole chiave rilevanti (infortuni, formazione)
            has_keyword = any(kw in text for kw in RELEVANT_KEYWORDS)
            if home_match or away_match:
                art["relevance"] = (2 if (home_match and away_match) else 1) + (1 if has_keyword else 0)
                relevant.append(art)

        relevant.sort(key=lambda a: a.get("relevance", 0), reverse=True)
        return relevant

    def _team_keywords(self, team_name: str) -> list[str]:
        """Genera keyword di ricerca per una squadra."""
        name_lower = team_name.lower()
        words = [w for w in name_lower.split() if len(w) > 3]

        keywords = [name_lower]
        keywords.extend(words)

        # Abbreviazioni comuni
        abbrevs = {
            "juventus": ["juve"],
            "inter": ["inter milan", "internazionale"],
            "manchester united": ["man utd", "man united"],
            "manchester city": ["man city"],
            "paris saint germain": ["psg", "paris sg"],
            "atletico madrid": ["atletico"],
            "real madrid": ["real"],
            "borussia dortmund": ["dortmund", "bvb"],
            "bayer leverkusen": ["leverkusen"],
            "rb leipzig": ["leipzig"],
        }
        for full, abbrs in abbrevs.items():
            if full in name_lower or name_lower in full:
                keywords.extend(abbrs)

        return list(set(keywords))
