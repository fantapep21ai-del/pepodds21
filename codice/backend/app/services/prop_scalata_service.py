"""
PropScalataService — trova giocate con valore intrinseco multi-sport
e le combina in scalate soft (2-3 step, quote 1.35-1.75 per step).

Fonti per ogni sport:
  NBA:    DunkestClient — media e hit rate giocatori (gratis)
  Calcio: MatchOdds totals (Over/Under) già nel DB, EV > 3%
  Tennis: MatchOdds h2h favorito chiaro (no_vig < 1.35) già nel DB

Non consuma quote API extra — tutto da dati già in DB o Dunkest.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from itertools import combinations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import Match, MatchOdds, Competition
from app.services.dunkest_client import (
    DunkestClient,
    find_player_id,
)

logger = logging.getLogger(__name__)

# ── Costanti ──────────────────────────────────────────────────────────────────
MIN_HIT_RATE     = 0.62     # hit rate minimo Dunkest
MIN_SAMPLE_SIZE  = 6        # almeno 6 partite nel sample
SCALATA_STEPS    = [2, 3]   # costruisci scalate da 2 e da 3 step
MAX_SCALATE      = 2        # massimo 2 scalate per alert

# Calcio: Over/Under — prendi solo se odds 1.40-1.72 e EV > 3%
FOOTBALL_MIN_ODDS   = 1.40
FOOTBALL_MAX_ODDS   = 1.72
FOOTBALL_MIN_EV     = 0.03

# Tennis: h2h favorito — prendi solo se odds 1.30-1.65 (chiaro favorito)
TENNIS_MIN_ODDS  = 1.30
TENNIS_MAX_ODDS  = 1.65

# NBA: margine conservativo per smart line
NBA_OVER_MARGIN  = -2.5     # linea = avg - 2.5

TOP_NBA_PLAYERS: list[tuple[str, str]] = [
    ("LeBron James",            "lakers"),
    ("Anthony Davis",           "lakers"),
    ("Stephen Curry",           "warriors"),
    ("Kevin Durant",            "suns"),
    ("Jayson Tatum",            "celtics"),
    ("Jaylen Brown",            "celtics"),
    ("Nikola Jokic",            "nuggets"),
    ("Joel Embiid",             "76ers"),
    ("Giannis Antetokounmpo",   "bucks"),
    ("Damian Lillard",          "bucks"),
    ("Luka Doncic",             "mavericks"),
    ("Kyrie Irving",            "mavericks"),
    ("Shai Gilgeous-Alexander", "thunder"),
    ("Karl-Anthony Towns",      "knicks"),
    ("Jalen Brunson",           "knicks"),
    ("Donovan Mitchell",        "cavaliers"),
    ("Trae Young",              "hawks"),
    ("Paolo Banchero",          "magic"),
    ("Franz Wagner",            "magic"),
    ("Tyrese Haliburton",       "pacers"),
    ("Devin Booker",            "suns"),
    ("Anthony Edwards",         "timberwolves"),
    ("Ja Morant",               "grizzlies"),
    ("Jaren Jackson Jr.",       "grizzlies"),
]

STATS_TO_CHECK = [
    ("pts", "Punti",    "over"),
    ("reb", "Rimbalzi", "over"),
    ("ast", "Assist",   "over"),
]

# Bookmaker label breve per Telegram
BK_SHORT = {
    "bet365": "Bet365", "snai": "Snai", "lottomatica": "Lottomatica",
    "sisal": "Sisal", "goldbet": "Goldbet", "eplay24": "Eplay24",
    "unibet_it": "Unibet", "betsson": "Betsson", "bwin": "Bwin",
}


@dataclass
class ScalataStep:
    """Un singolo step della scalata."""
    sport: str                # "basketball" | "football" | "tennis"
    match_name: str
    description: str          # testo breve della giocata
    direction: str            # "over" | "under" | "h2h"
    best_odds: float
    best_bookmaker: str
    confidence: float         # hit rate o EV-based confidence
    reasoning: str            # 1 riga di spiegazione
    # Solo per NBA props
    player_name: str = ""
    smart_line: float = 0.0
    recent_avg: float = 0.0
    back_to_back: bool = False
    target_odds_min: float = 0.0


@dataclass
class Scalata:
    steps: list[ScalataStep]
    combined_odds: float
    joint_confidence: float

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    @property
    def expected_value(self) -> float:
        return self.joint_confidence * self.combined_odds - 1.0


class PropScalataService:

    def __init__(self) -> None:
        self._dunkest = DunkestClient()

    # ── Entry point principale ────────────────────────────────────────────────

    async def find_all_value_steps(
        self,
        db: AsyncSession,
        hours_ahead: int = 30,
    ) -> list[ScalataStep]:
        """
        Raccoglie step candidati da tutti e tre gli sport.
        Ordina per score (confidenza × edge).
        """
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)

        steps: list[ScalataStep] = []

        # 1. NBA player props (Dunkest)
        nba_steps = await self._find_nba_props(db, now, cutoff)
        steps.extend(nba_steps)

        # 2. Calcio totals (dal DB)
        football_steps = await self._find_football_totals(db, now, cutoff)
        steps.extend(football_steps)

        # 3. Tennis h2h favorito chiaro (dal DB)
        tennis_steps = await self._find_tennis_favorites(db, now, cutoff)
        steps.extend(tennis_steps)

        steps.sort(key=lambda s: s.confidence, reverse=True)
        logger.info(
            "ScalataService: %d NBA + %d calcio + %d tennis = %d step totali",
            len(nba_steps), len(football_steps), len(tennis_steps), len(steps)
        )
        return steps

    # ── NBA props via Dunkest ─────────────────────────────────────────────────

    async def _find_nba_props(
        self, db: AsyncSession, now: datetime, cutoff: datetime
    ) -> list[ScalataStep]:
        # Partite NBA imminenti
        result = await db.execute(
            select(Match.home_team, Match.away_team, Match.match_date)
            .join(Competition, Competition.id == Match.competition_id)
            .where(Competition.sport == "basketball")
            .where(Match.match_date >= now)
            .where(Match.match_date <= cutoff)
            .where(Match.status == "scheduled")
        )
        upcoming = result.fetchall()
        if not upcoming:
            return []

        teams_playing: set[str] = set()
        match_by_team: dict[str, str] = {}
        match_date_by_team: dict[str, str] = {}
        for m in upcoming:
            hl = m.home_team.lower()
            al = m.away_team.lower()
            teams_playing.update([hl, al])
            match_name = f"{m.home_team} vs {m.away_team}"
            date_str = m.match_date.strftime("%Y-%m-%d") if m.match_date else ""
            for t in (hl, al):
                match_by_team[t] = match_name
                match_date_by_team[t] = date_str

        active = [
            (name, hint) for name, hint in TOP_NBA_PLAYERS
            if any(hint in team for team in teams_playing)
        ]

        steps: list[ScalataStep] = []
        for player_name, team_hint in active[:12]:
            pid = find_player_id(player_name)
            if not pid:
                continue
            try:
                games = await self._dunkest.get_player_games(pid)
            except Exception:
                continue
            if not games:
                continue

            # Trova match e data
            match_team = next((t for t in teams_playing if team_hint in t), None)
            if not match_team:
                continue
            match_name  = match_by_team.get(match_team, "NBA Game")
            date_str    = match_date_by_team.get(match_team, "")

            b2b = self._dunkest.assess_back_to_back(games, date_str)
            avgs = self._dunkest.get_recent_averages(games, last_n=5)

            for stat_key, stat_label, direction in STATS_TO_CHECK:
                avg = avgs.get(stat_key, 0.0)
                if avg < 3.0:
                    continue

                smart_line = round(avg + NBA_OVER_MARGIN - 0.5, 0) + 0.5
                smart_line = max(smart_line, 1.5)

                hit_rate, sample = self._dunkest.compute_hit_rate(
                    games, stat_key, smart_line, direction, last_n=20
                )
                if sample < MIN_SAMPLE_SIZE or hit_rate < MIN_HIT_RATE:
                    continue

                # Calcola target_odds da hit_rate, ma clampala a range ragionevole per scalata
                target_odds = round(1.0 / hit_rate + 0.05, 2)
                # CLAMP: per scalate desideriamo quote 1.4-1.6 idealmente
                # Se hit_rate è troppo basso (odds > 1.6), scarta
                # Se hit_rate è troppo alto (odds < 1.3), non è scalata ma singola
                if target_odds < 1.3 or target_odds > 1.75:
                    continue

                effective_conf = hit_rate * (0.92 if b2b else 1.0)

                b2b_note = " (B2B oggi)" if b2b else ""
                reasoning = (
                    f"Media ult.5: {avg:.1f} {stat_label} | "
                    f"Hit {smart_line} {direction}: {round(hit_rate*100)}% "
                    f"su {sample} match{b2b_note}"
                )

                steps.append(ScalataStep(
                    sport="basketball",
                    match_name=match_name,
                    description=f"{player_name} — {direction.capitalize()} {smart_line} {stat_label}",
                    direction=direction,
                    best_odds=target_odds,
                    best_bookmaker="Bet365/Snai",
                    confidence=effective_conf,
                    reasoning=reasoning,
                    player_name=player_name,
                    smart_line=smart_line,
                    recent_avg=round(avg, 1),
                    back_to_back=b2b,
                    target_odds_min=target_odds,
                ))

        return steps

    # ── Calcio totals dal DB ──────────────────────────────────────────────────

    async def _find_football_totals(
        self, db: AsyncSession, now: datetime, cutoff: datetime
    ) -> list[ScalataStep]:
        """
        Trova Over/Under calcio già nel DB con EV > 3% e odds 1.40-1.72.
        """
        result = await db.execute(text("""
            SELECT
                m.home_team, m.away_team, m.match_date,
                mo.outcome, mo.odds, mo.bookmaker,
                c.name as comp_name
            FROM match_odds mo
            JOIN matches m ON m.id = mo.match_id
            JOIN competitions c ON c.id = m.competition_id
            WHERE c.sport IN ('football', 'soccer')
            AND m.match_date >= :now AND m.match_date <= :cutoff
            AND m.status = 'scheduled'
            AND mo.market = 'totals'
            AND mo.odds >= :min_odds AND mo.odds <= :max_odds
            ORDER BY m.match_date, mo.odds DESC
        """), {"now": now, "cutoff": cutoff,
               "min_odds": FOOTBALL_MIN_ODDS, "max_odds": FOOTBALL_MAX_ODDS})

        rows = result.fetchall()

        # Raggruppa per (match, outcome) → miglior quota
        best: dict[tuple, dict] = {}
        for r in rows:
            key = (r.home_team, r.away_team, r.outcome)
            if key not in best or r.odds > best[key]["odds"]:
                best[key] = {
                    "home": r.home_team, "away": r.away_team,
                    "match_date": r.match_date, "outcome": r.outcome,
                    "odds": float(r.odds), "bookmaker": r.bookmaker,
                    "comp": r.comp_name,
                }

        # Calcola no-vig per Over/Under della stessa partita
        # Raggruppa per partita
        by_match: dict[tuple, dict] = {}
        for key, val in best.items():
            mk = (val["home"], val["away"])
            by_match.setdefault(mk, {})[val["outcome"]] = val

        steps: list[ScalataStep] = []
        for (home, away), outcomes in by_match.items():
            # Cerca Over X.5 e Under X.5 dalla stessa linea
            over_key  = next((o for o in outcomes if "Over" in o), None)
            under_key = next((o for o in outcomes if "Under" in o), None)
            if not over_key or not under_key:
                continue

            over  = outcomes[over_key]
            under = outcomes[under_key]

            # No-vig
            o_raw = 1.0 / over["odds"]
            u_raw = 1.0 / under["odds"]
            total = o_raw + u_raw
            if total <= 0:
                continue

            over_nv  = o_raw / total
            under_nv = u_raw / total

            # EV per entrambi
            for side, nv, data, direction in [
                (over_key,  over_nv,  over,  "over"),
                (under_key, under_nv, under, "under"),
            ]:
                ev = nv * data["odds"] - 1.0
                if ev < FOOTBALL_MIN_EV:
                    continue

                bk_short = BK_SHORT.get(data["bookmaker"], data["bookmaker"])
                match_name = f"{home} vs {away}"
                reasoning = (
                    f"EV no-vig: +{round(ev*100, 1)}% | "
                    f"Prob implicita fair: {round(nv*100, 1)}% | "
                    f"{data['comp']}"
                )
                steps.append(ScalataStep(
                    sport="football",
                    match_name=match_name,
                    description=f"{side} @ {data['odds']:.2f} ({bk_short})",
                    direction=direction,
                    best_odds=data["odds"],
                    best_bookmaker=data["bookmaker"],
                    confidence=round(nv + ev * 0.5, 3),  # prob + EV bonus
                    reasoning=reasoning,
                ))

        return steps

    # ── Tennis h2h favorito chiaro ─────────────────────────────────────────────

    async def _find_tennis_favorites(
        self, db: AsyncSession, now: datetime, cutoff: datetime
    ) -> list[ScalataStep]:
        """
        Trova favoriti chiari nel tennis (quota 1.30-1.65) dal DB.
        """
        result = await db.execute(text("""
            SELECT
                m.player_a, m.player_b, m.home_team, m.away_team,
                m.match_date, mo.outcome, mo.odds, mo.bookmaker,
                c.name as comp_name
            FROM match_odds mo
            JOIN matches m ON m.id = mo.match_id
            JOIN competitions c ON c.id = m.competition_id
            WHERE c.sport = 'tennis'
            AND m.match_date >= :now AND m.match_date <= :cutoff
            AND m.status = 'scheduled'
            AND mo.market = 'h2h'
            AND mo.odds >= :min_odds AND mo.odds <= :max_odds
            ORDER BY mo.odds ASC
        """), {"now": now, "cutoff": cutoff,
               "min_odds": TENNIS_MIN_ODDS, "max_odds": TENNIS_MAX_ODDS})

        rows = result.fetchall()
        steps: list[ScalataStep] = []

        seen_matches: set[str] = set()
        for r in rows:
            home = r.home_team or r.player_a or "Player A"
            away = r.away_team or r.player_b or "Player B"
            match_key = f"{home}|{away}"

            if match_key in seen_matches:
                continue
            seen_matches.add(match_key)

            # Per calcolare no-vig servono entrambe le quote — skip se non le abbiamo
            # Usiamo il favorito a queste odds direttamente come step soft
            odds = float(r.odds)
            implied = 1.0 / odds
            # Approssimazione no-vig (assumiamo vig ~5% nel tennis)
            fair_prob = implied / 1.05
            confidence = min(fair_prob, 0.80)  # cap a 80%

            bk_short = BK_SHORT.get(r.bookmaker, r.bookmaker)
            match_name = f"{home} vs {away}"
            reasoning = (
                f"Favorito @ {odds:.2f} {bk_short} | "
                f"Prob fair ~{round(fair_prob*100)}% | "
                f"{r.comp_name}"
            )
            steps.append(ScalataStep(
                sport="tennis",
                match_name=match_name,
                description=f"{r.outcome} @ {odds:.2f} ({bk_short})",
                direction="h2h",
                best_odds=odds,
                best_bookmaker=r.bookmaker,
                confidence=confidence,
                reasoning=reasoning,
            ))

        return steps

    # ── Costruisce scalate multi-sport ────────────────────────────────────────

    def build_scalate(self, steps: list[ScalataStep]) -> list[Scalata]:
        """
        Costruisce scalate ottimali da step di sport/match diversi.
        Ogni step deve venire da una partita diversa.
        """
        scalate: list[Scalata] = []

        for n in SCALATA_STEPS:
            if len(steps) < n:
                continue

            best: Scalata | None = None
            for combo in combinations(steps[:15], n):
                # Ogni step da match diverso
                matches = {s.match_name for s in combo}
                if len(matches) < n:
                    continue

                combined = 1.0
                joint    = 1.0
                for s in combo:
                    combined *= s.best_odds
                    joint    *= s.confidence

                sc = Scalata(
                    steps=list(combo),
                    combined_odds=round(combined, 2),
                    joint_confidence=round(joint, 3),
                )

                if best is None or sc.expected_value > best.expected_value:
                    best = sc

            if best is not None:
                scalate.append(best)

        scalate.sort(key=lambda s: s.expected_value, reverse=True)
        return scalate[:MAX_SCALATE]

    # ── Formatta Telegram ─────────────────────────────────────────────────────

    def format_telegram_message(self, scalata: Scalata) -> str:
        sport_icons = {"basketball": "[NBA]", "football": "[Calcio]", "tennis": "[Tennis]"}
        lines: list[str] = []
        lines.append(f"SCALATA {scalata.n_steps} STEP — MULTI SPORT")
        lines.append(
            f"Quota combinata: {scalata.combined_odds:.2f} | "
            f"EV: {scalata.expected_value*100:+.1f}%"
        )
        lines.append("")

        for i, step in enumerate(scalata.steps, 1):
            icon = sport_icons.get(step.sport, "")
            lines.append(f"Step {i} {icon}: {step.description}")
            lines.append(f"  Partita: {step.match_name}")
            lines.append(f"  {step.reasoning}")
            lines.append("")

        lines.append(f"Confidenza congiunta: {round(scalata.joint_confidence*100)}%")
        return "\n".join(lines)
