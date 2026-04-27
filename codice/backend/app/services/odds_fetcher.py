"""
The Odds API client.

Docs: https://the-odds-api.com/liveapi/guides/v4/
Free tier: 500 requests/month. Essential: 20,000/month.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Bookmaker whitelist ───────────────────────────────────────────────────────
# Bookmaker disponibili su The Odds API regione EU
# Pinnacle è il riferimento professionale (margini più bassi, quote più accurate)

# Bookmaker sharp — riferimento per la probabilità reale (no-vig)
SHARP_BOOKMAKERS = {"pinnacle", "betfair_ex_eu"}

# Bookmaker soft — dove cerchiamo valore rispetto a Pinnacle
SOFT_BOOKMAKERS = {
    # Italiani prioritari (giocabili da Giuseppe)
    "bet365",
    "snai",
    "lottomatica",
    "sisal",
    "goldbet",
    "eplay24",
    # Europei utili per conferma quota
    "unibet_eu", "unibet_it",
    "williamhill",
    "marathonbet",
    "codere_it",
    "betsson",
}

ALLOWED_BOOKMAKERS = SHARP_BOOKMAKERS | SOFT_BOOKMAKERS

def _is_allowed_bookmaker(key: str) -> bool:
    return key.lower() in ALLOWED_BOOKMAKERS

# ─── Mercati standard (1 chiamata per sport) ───────────────────────────────────
MARKETS_STANDARD = "h2h,totals"

# ─── Mercati player props per sport (1 chiamata per evento) ───────────────────
PLAYER_PROPS_FOOTBALL = ",".join([
    "player_goal_scorer_anytime",
    "player_goal_scorer_first",
    "player_shots_on_target",
    "player_shots_on_target_over_under",
    "player_assists",
    "player_cards",
    "player_pass_completions",
])

PLAYER_PROPS_BASKETBALL = ",".join([
    "player_points",
    "player_points_over_under",
    "player_rebounds",
    "player_rebounds_over_under",
    "player_assists",
    "player_assists_over_under",
    "player_threes",
    "player_blocks",
    "player_steals",
])

PLAYER_PROPS_BY_SPORT: dict[str, str] = {
    "football":   PLAYER_PROPS_FOOTBALL,
    "soccer":     PLAYER_PROPS_FOOTBALL,
    "basketball": PLAYER_PROPS_BASKETBALL,
}

REGIONS = "eu"
ODDS_FORMAT = "decimal"


class OddsAPIError(Exception):
    pass


class OddsAPIQuotaError(OddsAPIError):
    """Quota mensile esaurita per questa chiave."""
    pass


class OddsAPIClient:
    def __init__(self) -> None:
        self._base = settings.odds_api_base_url
        # Carica tutte le chiavi disponibili (salta le vuote)
        self._keys: list[str] = [
            k for k in [
                settings.odds_api_key,
                settings.odds_api_key_2,
                settings.odds_api_key_3,
                settings.odds_api_key_4,
            ] if k
        ]
        logger.info("OddsAPIClient: %d chiave/i disponibili", len(self._keys))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.TransportError),
        reraise=True,
    )
    async def _get_with_key(self, path: str, params: dict, api_key: str) -> Any:
        params = {**params, "apiKey": api_key}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", params=params)

        # 401 = quota esaurita (The Odds API usa 401 per OUT_OF_USAGE_CREDITS)
        # → trattato come quota error per permettere la rotazione alla chiave successiva
        if resp.status_code == 401:
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            error_code = body.get("error_code", "")
            if error_code == "OUT_OF_USAGE_CREDITS":
                raise OddsAPIQuotaError(f"Quota esaurita per chiave ...{api_key[-6:]}")
            raise OddsAPIError(f"Chiave non valida ...{api_key[-6:]}")

        if resp.status_code == 429:
            raise OddsAPIError("Rate limit exceeded")

        # Quota esaurita: The Odds API risponde con JSON + error_code
        if resp.status_code in (402, 422) or (
            resp.status_code == 200 and isinstance(resp.json(), dict)
            and resp.json().get("error_code") == "OUT_OF_USAGE_CREDITS"
        ):
            raise OddsAPIQuotaError(f"Quota esaurita per chiave ...{api_key[-6:]}")

        resp.raise_for_status()

        # Logga e salva quota residua in Redis
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if remaining:
            rem_int = int(remaining)
            logger.info("Odds API [chiave ...%s] — usate: %s  rimanenti: %s", api_key[-6:], used, remaining)
            self._track_quota(api_key, rem_int)
            if rem_int < 100:
                logger.error("CRITICO: Odds API quota quasi esaurita per chiave ...%s: solo %s richieste rimaste", api_key[-6:], remaining)
            elif rem_int < 200:
                logger.warning("Odds API quota bassa per chiave ...%s: %s richieste rimaste", api_key[-6:], remaining)

        return resp.json()

    @staticmethod
    def _track_quota(api_key: str, remaining: int) -> None:
        """
        Salva quota residua in Redis.
        NON chiama asyncio.run() perché questo metodo è invocato da un contesto async —
        gli alert Telegram vengono spediti dal task Celery fetch_all_odds, non qui.
        """
        try:
            import redis as redis_lib
            from app.config import settings
            redis_client = redis_lib.from_url(settings.redis_url_with_auth, decode_responses=True)
            key_tag = api_key[-6:]
            redis_key = f"odds_api:remaining:{key_tag}"
            redis_client.setex(redis_key, 86400 * 35, str(remaining))  # TTL 35 giorni

            # Flag per alert — il task async lo legge e spedisce il Telegram
            if remaining < 100:
                redis_client.set(f"odds_api:needs_alert:{key_tag}", str(remaining))
        except Exception as exc:
            logger.debug("Quota tracking failed (non critico): %s", exc)

    async def _get(self, path: str, params: dict) -> Any:
        """Prova le chiavi in ordine — passa alla successiva se la quota è esaurita."""
        last_exc: Exception = OddsAPIError("Nessuna chiave API configurata")
        total_keys = len(self._keys)
        for attempt_idx, api_key in enumerate(self._keys, start=1):
            try:
                logger.debug("Attempt %d/%d: Trying key ...%s for path=%s", attempt_idx, total_keys, api_key[-6:], path)
                return await self._get_with_key(path, params, api_key)
            except OddsAPIQuotaError as exc:
                logger.warning("Attempt %d/%d: Quota esaurita per key ...%s — rotating to next", attempt_idx, total_keys, api_key[-6:])
                last_exc = exc
                continue
        # Tutte le chiavi esaurite
        logger.error("CRITICAL: Tutte le %d chiavi Odds API sono esaurite per questo mese. Richiedere nuove chiavi.", total_keys)
        raise last_exc

    async def list_sports(self) -> list[dict]:
        return await self._get("/sports", {"all": "false"})

    async def fetch_odds(self, sport_key: str) -> list[dict]:
        """Fetch quote standard per tutti gli eventi di uno sport (1 req)."""
        logger.info("Fetching odds for sport_key=%s", sport_key)
        data = await self._get(
            f"/sports/{sport_key}/odds",
            {
                "regions": REGIONS,
                "markets": MARKETS_STANDARD,
                "oddsFormat": ODDS_FORMAT,
                "dateFormat": "iso",
            },
        )
        logger.info("Got %d events for %s", len(data), sport_key)
        return data

    async def fetch_player_props(self, sport_key: str, event_id: str) -> list[dict]:
        """
        Fetch quote player props per un singolo evento (1 req per evento).
        Ritorna lista bookmaker con mercati props, stesso formato di fetch_odds.
        """
        sport_normalized = sport_key.split("_")[0]  # "soccer_serie_a" → "soccer"
        prop_markets = PLAYER_PROPS_BY_SPORT.get(sport_normalized)
        if not prop_markets:
            return []

        try:
            data = await self._get(
                f"/sports/{sport_key}/events/{event_id}/odds",
                {
                    "regions": REGIONS,
                    "markets": prop_markets,
                    "oddsFormat": ODDS_FORMAT,
                    "dateFormat": "iso",
                },
            )
            # L'endpoint event-level ritorna un singolo oggetto, non una lista
            return [data] if data else []
        except Exception as exc:
            logger.warning("Player props fetch failed for event %s: %s", event_id, exc)
            return []

    async def fetch_scores(self, sport_key: str, days_from: int = 1) -> list[dict]:
        return await self._get(
            f"/sports/{sport_key}/scores",
            {"daysFrom": days_from, "dateFormat": "iso"},
        )


def parse_odds_response(
    raw_events: list[dict],
    competition_id: str,
    sport: str,
) -> tuple[list[dict], list[dict]]:
    """
    Converte risposta The Odds API in (matches_data, odds_data).
    Filtra automaticamente: solo Bet365 e Eplay24.
    """
    matches_data: list[dict] = []
    odds_data: list[dict] = []
    fetched_at = datetime.now(timezone.utc)

    for event in raw_events:
        event_id = event.get("id")
        commence_time = event.get("commence_time")
        home = event.get("home_team")
        away = event.get("away_team")

        if not event_id or not commence_time:
            continue

        match_dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))

        matches_data.append({
            "external_id": event_id,
            "competition_id": competition_id,
            "sport": sport,
            "home_team": home,
            "away_team": away,
            "match_date": match_dt,
            "status": "scheduled",
        })

        for bookmaker in event.get("bookmakers", []):
            bk_key = bookmaker.get("key", "")
            # Filtra: SOLO Bet365 e Eplay24
            if not _is_allowed_bookmaker(bk_key):
                continue
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                for outcome in market.get("outcomes", []):
                    point = outcome.get("point")  # per over/under e handicap
                    outcome_name = outcome.get("name", "")
                    # Aggiunge il punto (es. "Over 0.5") al nome se presente
                    if point is not None and "over" in outcome_name.lower() or (
                        point is not None and "under" in outcome_name.lower()
                    ):
                        outcome_label = f"{outcome_name} {point}"
                    else:
                        outcome_label = outcome_name

                    odds_data.append({
                        "match_external_id": event_id,
                        "bookmaker": bk_key,
                        "market": market_key,
                        "outcome": outcome_label,
                        "odds": float(outcome.get("price", 0)),
                        "fetched_at": fetched_at,
                        "is_live": False,
                    })

    return matches_data, odds_data


def parse_player_props_response(
    raw_events: list[dict],
    match_external_id: str,
) -> list[dict]:
    """
    Converte risposta player props (event-level) in lista odds_data.
    Filtra solo Bet365 e Eplay24.
    """
    odds_data: list[dict] = []
    fetched_at = datetime.now(timezone.utc)

    for event in raw_events:
        for bookmaker in event.get("bookmakers", []):
            bk_key = bookmaker.get("key", "")
            if not _is_allowed_bookmaker(bk_key):
                continue
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                for outcome in market.get("outcomes", []):
                    description = outcome.get("description", "")  # nome giocatore
                    name = outcome.get("name", "")
                    point = outcome.get("point")

                    # Es: "Romelu Lukaku — Over 2.5 Tiri in Porta"
                    if description:
                        label = f"{description} — {name}"
                        if point is not None:
                            label += f" {point}"
                    else:
                        label = name
                        if point is not None:
                            label += f" {point}"

                    odds_data.append({
                        "match_external_id": match_external_id,
                        "bookmaker": bk_key,
                        "market": market_key,
                        "outcome": label,
                        "odds": float(outcome.get("price", 0)),
                        "fetched_at": fetched_at,
                        "is_live": False,
                    })

    return odds_data
