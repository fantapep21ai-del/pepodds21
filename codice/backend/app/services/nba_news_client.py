"""
NBA Injury News Aggregator — fonti tempo reale per assenze NBA.

Fonti (in ordine di velocità/affidabilità):
  1. Telegram public channel (t.me/s/nbanews24italia) — news live in italiano
  2. Rotowire NBA Injuries — aggiornamenti pre-gara strutturati (EN)
  3. ESPN NBA Injuries API — endpoint pubblico (già usato nel sistema)

Output unificato per ogni giocatore:
  {
    "player_name": str,
    "team": str,
    "position": str,
    "status": "out" | "doubtful" | "questionable" | "probable" | "available",
    "reason": str,
    "source": str,
    "fetched_at": datetime,
  }

Logica di priorità:
  - "out" da qualsiasi fonte → confermato assente
  - "doubtful" → considerato out (>75% probabilità assenza)
  - "questionable" → potrebbe non giocare (watch closely)
  - "probable" / "available" → gioca
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Timeout HTTP ─────────────────────────────────────────────────────────────
_TIMEOUT = httpx.Timeout(12.0)

# ── Status normalisation ──────────────────────────────────────────────────────
_STATUS_MAP: dict[str, str] = {
    # English
    "out":          "out",
    "o":            "out",
    "doubtful":     "doubtful",
    "dtd":          "doubtful",   # day-to-day
    "questionable": "questionable",
    "q":            "questionable",
    "probable":     "probable",
    "p":            "probable",
    "available":    "available",
    "active":       "available",
    "game time decision": "questionable",
    "gtd":          "questionable",
    # Italian
    "fuori":        "out",
    "assente":      "out",
    "non gioca":    "out",
    "non ci sarà":  "out",
    "in dubbio":    "questionable",
    "da valutare":  "questionable",
    "confermato":   "available",
    "disponibile":  "available",
    "ok":           "available",
}

# ── Regex patterns for injury messages (EN + IT) ──────────────────────────────
# Matches: "Player Name - OUT", "Player Name: Questionable", "OUT: Player Name"
_INJURY_PATTERNS = [
    # "Player Name — OUT - reason" or "Player Name: OUT"
    re.compile(
        r"([A-Z][a-z]+ (?:[A-Z][a-z'.-]+ )*[A-Z][a-z'.-]+)"  # full name
        r"\s*[-–:]\s*(out|doubtful|questionable|probable|gtd|dtd|day-to-day|available)",
        re.IGNORECASE,
    ),
    # "OUT: Player Name" or "OUT → Player Name"
    re.compile(
        r"\b(out|doubtful|questionable|probable|available)\b\s*[-–:→]\s*"
        r"([A-Z][a-z]+ [A-Z][a-z'.-]+)",
        re.IGNORECASE,
    ),
    # Italian: "Player Name fuori / assente / in dubbio"
    re.compile(
        r"([A-Z][a-z]+ [A-Z][a-z'.-]+)"
        r"\s+(?:è\s*)?(fuori|assente|in dubbio|da valutare|confermato|disponibile|non gioca)",
        re.IGNORECASE,
    ),
]

# ── Known team name fragments → canonical slug ───────────────────────────────
_TEAM_FRAGMENTS: dict[str, str] = {
    "lakers":       "los-angeles-lakers",
    "warriors":     "golden-state-warriors",
    "celtics":      "boston-celtics",
    "bucks":        "milwaukee-bucks",
    "nuggets":      "denver-nuggets",
    "heat":         "miami-heat",
    "76ers":        "philadelphia-76ers",
    "sixers":       "philadelphia-76ers",
    "suns":         "phoenix-suns",
    "clippers":     "los-angeles-clippers",
    "nets":         "brooklyn-nets",
    "knicks":       "new-york-knicks",
    "bulls":        "chicago-bulls",
    "mavs":         "dallas-mavericks",
    "mavericks":    "dallas-mavericks",
    "thunder":      "oklahoma-city-thunder",
    "jazz":         "utah-jazz",
    "hawks":        "atlanta-hawks",
    "cavaliers":    "cleveland-cavaliers",
    "cavs":         "cleveland-cavaliers",
    "raptors":      "toronto-raptors",
    "pacers":       "indiana-pacers",
    "magic":        "orlando-magic",
    "hornets":      "charlotte-hornets",
    "pistons":      "detroit-pistons",
    "wizards":      "washington-wizards",
    "kings":        "sacramento-kings",
    "blazers":      "portland-trail-blazers",
    "grizzlies":    "memphis-grizzlies",
    "pelicans":     "new-orleans-pelicans",
    "spurs":        "san-antonio-spurs",
    "rockets":      "houston-rockets",
    "wolves":       "minnesota-timberwolves",
    "timberwolves": "minnesota-timberwolves",
}


def _normalise_status(raw: str) -> str:
    key = raw.lower().strip()
    return _STATUS_MAP.get(key, "questionable")


def _extract_injuries_from_text(text: str, source: str) -> list[dict]:
    """
    Estrae informazioni su assenze/dubbi da testo libero (messaggi Telegram o simili).
    Ritorna lista di record injury.
    """
    results: list[dict] = []
    now = datetime.now(timezone.utc)

    for pattern in _INJURY_PATTERNS:
        for m in pattern.finditer(text):
            groups = m.groups()
            # Ordine dipende dal pattern: player+status o status+player
            if re.match(r"out|doubtful|questionable|probable|gtd|dtd|fuori|assente|in dubbio|da valutare|confermato|disponibile", groups[0], re.IGNORECASE):
                status_raw, player_name = groups[0], groups[1]
            else:
                player_name, status_raw = groups[0], groups[1]

            status = _normalise_status(status_raw)
            results.append({
                "player_name": player_name.strip(),
                "team": "",
                "position": "",
                "status": status,
                "reason": "",
                "source": source,
                "fetched_at": now,
            })

    return results


# ── Source 1: Telegram public channel ─────────────────────────────────────────

class TelegramChannelClient:
    """
    Legge gli ultimi messaggi di un canale Telegram pubblico via HTML preview.
    t.me/s/{channel} serve i messaggi recenti come HTML senza autenticazione.
    """

    def __init__(self, channel: str = "nbanews24italia") -> None:
        self.url = f"https://t.me/s/{channel}"

    async def fetch_recent_messages(self, limit: int = 30) -> list[dict]:
        """
        Ritorna i messaggi recenti del canale come lista di {text, date}.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    self.url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; NBABot/1.0)"},
                    follow_redirects=True,
                )
            if resp.status_code != 200:
                logger.warning("Telegram channel %s: status %d", self.url, resp.status_code)
                return []

            return self._parse_html(resp.text, limit)
        except Exception as exc:
            logger.warning("Telegram channel fetch failed: %s", exc)
            return []

    def _parse_html(self, html: str, limit: int) -> list[dict]:
        """
        Parsa HTML di t.me/s per estrarre messaggi e timestamp.
        """
        messages: list[dict] = []

        # Estrai blocchi messaggio
        # Struttura: <div class="tgme_widget_message_bubble"> ... testo ... timestamp
        bubble_pattern = re.compile(
            r'tgme_widget_message_text[^>]*>(.*?)</div>.*?'
            r'datetime="([^"]+)"',
            re.DOTALL,
        )
        for m in bubble_pattern.finditer(html):
            raw_text = m.group(1)
            dt_str   = m.group(2)

            # Rimuovi tag HTML
            clean_text = re.sub(r"<[^>]+>", " ", raw_text)
            clean_text = re.sub(r"&amp;", "&", clean_text)
            clean_text = re.sub(r"&#\d+;", "", clean_text)
            clean_text = clean_text.strip()

            try:
                dt = datetime.fromisoformat(dt_str)
            except Exception:
                dt = datetime.now(timezone.utc)

            if clean_text:
                messages.append({"text": clean_text, "date": dt})

            if len(messages) >= limit:
                break

        logger.debug("Telegram: parsed %d messages", len(messages))
        return messages

    async def get_injury_updates(self) -> list[dict]:
        """
        Legge il canale e ritorna i record injury estratti dai messaggi.
        Solo messaggi delle ultime 6 ore.
        """
        msgs = await self.fetch_recent_messages(limit=50)
        now = datetime.now(timezone.utc)
        results: list[dict] = []

        for msg in msgs:
            # Considera solo messaggi recenti (ultime 6h)
            age_hours = (now - msg["date"].replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if age_hours > 6:
                continue

            injuries = _extract_injuries_from_text(msg["text"], source="telegram_nbanews24italia")
            results.extend(injuries)

        logger.info("Telegram NBA channel: %d injury records estratti", len(results))
        return results


# ── Source 2: Rotowire ─────────────────────────────────────────────────────────

class RotowireNBAClient:
    """
    Scrapa la pagina infortuni NBA di Rotowire.
    URL: https://www.rotowire.com/basketball/nba-injuries.php
    Tabella HTML con colonne: Player | Team | Pos | Updated | Type | Status | Notes
    """

    URL = "https://www.rotowire.com/basketball/nba-injuries.php"

    async def get_injuries(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    self.URL,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                        "Referer": "https://www.rotowire.com/",
                    },
                    follow_redirects=True,
                )
            if resp.status_code != 200:
                logger.warning("Rotowire: status %d", resp.status_code)
                return []
            return self._parse(resp.text)
        except Exception as exc:
            logger.warning("Rotowire fetch failed: %s", exc)
            return []

    def _parse(self, html: str) -> list[dict]:
        """
        Parsa la tabella HTML degli infortuni di Rotowire.
        Struttura: righe con classe 'injury-row' o simile.
        """
        now = datetime.now(timezone.utc)
        results: list[dict] = []

        # Rotowire usa una struttura: <ul class="injury-report">
        # Ogni giocatore ha attributi data-* o è in una lista strutturata
        # Fallback: cerca pattern nome - status nel testo

        # Primo tentativo: cerca il JSON embedded (Rotowire a volte lo include)
        json_match = re.search(r'injuries\s*=\s*(\[.*?\]);', html, re.DOTALL)
        if json_match:
            try:
                import json
                data = json.loads(json_match.group(1))
                for item in data:
                    results.append({
                        "player_name": item.get("name", ""),
                        "team": item.get("team", ""),
                        "position": item.get("position", ""),
                        "status": _normalise_status(item.get("status", "")),
                        "reason": item.get("injury", ""),
                        "source": "rotowire",
                        "fetched_at": now,
                    })
                logger.debug("Rotowire JSON: %d records", len(results))
                return results
            except Exception:
                pass

        # Secondo tentativo: regex HTML
        # Cerca blocchi tipo: <td>Player Name</td><td>LAL</td><td>PF</td>...<td>Out</td>
        row_pattern = re.compile(
            r'player-name[^>]*>([^<]+)<.*?'       # player name
            r'team[^>]*>([A-Z]{2,3})<.*?'         # team abbreviation
            r'pos(?:ition)?[^>]*>([A-Z/]+)<.*?'   # position
            r'(?:injury|type)[^>]*>([^<]+)<.*?'   # injury type
            r'status[^>]*>([^<]+)<',               # status
            re.DOTALL | re.IGNORECASE,
        )
        for m in row_pattern.finditer(html):
            status_raw = m.group(5).strip()
            results.append({
                "player_name": m.group(1).strip(),
                "team": m.group(2).strip(),
                "position": m.group(3).strip(),
                "status": _normalise_status(status_raw),
                "reason": m.group(4).strip(),
                "source": "rotowire",
                "fetched_at": now,
            })

        # Terzo tentativo: testo libero con patterns di injury
        if not results:
            text_injuries = _extract_injuries_from_text(html, source="rotowire")
            results.extend(text_injuries)

        logger.info("Rotowire: %d injury records", len(results))
        return [r for r in results if r["player_name"]]


# ── Source 3: ESPN API (già usata nel sistema) ────────────────────────────────

class ESPNInjuryClient:
    """
    Wrapper per l'endpoint pubblico ESPN NBA injuries.
    Già usato in stats_fetcher.NBAInjuryClient ma qui con output normalizzato.
    """

    URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"

    async def get_injuries(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(self.URL)
            if resp.status_code != 200:
                return []

            data = resp.json()
            results: list[dict] = []

            for team_block in data:
                team_name = team_block.get("team", {}).get("displayName", "")
                for injury in team_block.get("injuries", []):
                    athlete = injury.get("athlete", {})
                    status_raw = injury.get("status", "")
                    position = athlete.get("position", {}).get("abbreviation", "")
                    results.append({
                        "player_name": athlete.get("displayName", ""),
                        "team": team_name,
                        "position": position,
                        "status": _normalise_status(status_raw),
                        "reason": injury.get("shortComment", ""),
                        "source": "espn",
                        "fetched_at": now,
                    })

            return [r for r in results if r["player_name"]]
        except Exception as exc:
            logger.warning("ESPN injury fetch failed: %s", exc)
            return []


# ── Aggregator ────────────────────────────────────────────────────────────────

class NBAInjuryAggregator:
    """
    Combina tutte le fonti e deduplica per player_name + status.
    La fonte con lo status "peggiore" (più conservativo) ha precedenza.
    Ordine di severità: out > doubtful > questionable > probable > available
    """

    STATUS_SEVERITY: dict[str, int] = {
        "out":          5,
        "doubtful":     4,
        "questionable": 3,
        "probable":     2,
        "available":    1,
    }

    def __init__(self) -> None:
        self._telegram  = TelegramChannelClient()
        self._rotowire  = RotowireNBAClient()
        self._espn      = ESPNInjuryClient()

    async def fetch_all(self) -> list[dict]:
        """
        Fetch da tutte le fonti in parallelo e deduplica.
        """
        import asyncio
        telegram_res, rotowire_res, espn_res = await asyncio.gather(
            self._telegram.get_injury_updates(),
            self._rotowire.get_injuries(),
            self._espn.get_injuries(),
            return_exceptions=True,
        )

        all_records: list[dict] = []
        for res in (telegram_res, rotowire_res, espn_res):
            if isinstance(res, list):
                all_records.extend(res)

        return self._deduplicate(all_records)

    def _deduplicate(self, records: list[dict]) -> list[dict]:
        """
        Deduplica per player_name. Tieni il record con status più severo.
        """
        best: dict[str, dict] = {}  # player_slug → record

        for rec in records:
            name = rec.get("player_name", "").strip()
            if not name:
                continue

            slug = name.lower().replace(" ", "-")
            severity = self.STATUS_SEVERITY.get(rec.get("status", "available"), 1)

            existing = best.get(slug)
            if existing is None:
                best[slug] = rec
            else:
                existing_severity = self.STATUS_SEVERITY.get(existing.get("status", "available"), 1)
                if severity > existing_severity:
                    # Mantieni info di team/position da chi ce l'ha
                    merged = {**existing, **rec}
                    if not rec.get("team") and existing.get("team"):
                        merged["team"] = existing["team"]
                    if not rec.get("position") and existing.get("position"):
                        merged["position"] = existing["position"]
                    best[slug] = merged

        return list(best.values())

    def filter_confirmed_out(self, records: list[dict]) -> list[dict]:
        """
        Ritorna solo i giocatori confermati OUT o Doubtful (trattati come OUT).
        """
        return [r for r in records if r.get("status") in ("out", "doubtful")]

    def find_player(self, player_name: str, records: list[dict]) -> Optional[dict]:
        """
        Cerca un giocatore nei records injury per nome (fuzzy).
        """
        target = player_name.lower().strip()
        # Exact match
        for r in records:
            if r["player_name"].lower() == target:
                return r
        # Last name match
        target_last = target.split()[-1] if " " in target else target
        for r in records:
            if r["player_name"].lower().split()[-1] == target_last:
                return r
        return None
