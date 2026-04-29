"""
Settlement service — resolves open bets once a match finishes.

Logic:
  1. Fetch match scores from The Odds API
  2. Compare bet outcome against actual result
  3. Mark bet won / lost / void, compute P&L
  4. Update AgentScore (Brier score recalculation)
  5. Send Telegram notification
"""
from __future__ import annotations

import logging
import json
import unicodedata
import re
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from rapidfuzz import fuzz

from app.db.models.bet import Bet
from app.db.models.match import Match
from app.db.models.opportunity import BettingOpportunity
from app.db.models.agent import AgentVote, AgentScore
from app.db.models.scalata import Scalata
from app.services.odds_fetcher import OddsAPIClient

logger = logging.getLogger(__name__)

# [BUG #2 FIX] Fuzzy matching settings
FUZZY_THRESHOLD_SHORT = 90  # Nomi corti (<8 char) → threshold più alto
FUZZY_THRESHOLD_LONG = 85   # Nomi lunghi → threshold più permissivo

# [BUG #3 FIX] Retry settings
MAX_RETRIES = 5  # Cap massimo di retry prima di marcare come settlement_failed


def _normalize_team(name: str) -> str:
    """
    [BUG #2 FIX] Normalizzazione robusta per team matching.
    Gestisce accenti, punteggiatura, case-insensitivity.
    """
    # Decompone accenti (NFKD) e converte a ASCII
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    # Lowercaser
    name = name.lower()
    # Rimuovi tutto tranne alphanumeric e spazi
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    # Collassa spazi multipli
    return re.sub(r"\s+", " ", name).strip()


class SettlementService:

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.odds_client = OddsAPIClient()

    async def settle_bet(self, bet: Bet) -> bool:
        """
        Settle a single open bet. Returns True if successfully settled.
        """
        # Load opportunity → match
        opp_result = await self.db.execute(
            select(BettingOpportunity).where(BettingOpportunity.id == bet.opportunity_id)
        )
        opp = opp_result.scalar_one_or_none()
        if not opp:
            logger.error("Opportunity not found for bet %s", bet.id)
            return False

        from sqlalchemy.orm import selectinload
        match_result = await self.db.execute(
            select(Match)
            .options(selectinload(Match.competition))
            .where(Match.id == opp.match_id)
        )
        match = match_result.scalar_one_or_none()
        if not match:
            return False

        # Idempotency: se bet è già stata liquidata, skip
        if bet.status != "open":
            return False

        # [TIMING FIX] Wait 2:30h from match start before attempting settlement
        now_utc = datetime.now(timezone.utc)
        from datetime import timedelta
        if match.match_date and (now_utc - match.match_date) < timedelta(hours=2, minutes=30):
            logger.debug(
                "⏳ Skipping settlement for %s — only %.1f hours elapsed (need 2.5h)",
                match.display_name(),
                (now_utc - match.match_date).total_seconds() / 3600
            )
            return False

        # Try to get scores
        scores = await self._get_match_scores(match)
        if scores is None:
            # [BUG #3 HARDENED] Retry con MAX_RETRIES cap e exponential backoff
            import asyncio
            if match.match_date and (now_utc - match.match_date) > timedelta(hours=2, minutes=30):
                try:
                    pending_meta = json.loads(bet.notes) if bet.notes else {}
                except (json.JSONDecodeError, TypeError):
                    pending_meta = {}
                retry_count = pending_meta.get("settlement_retries", 0) + 1

                # [BUG #3 HARDENED] Verifica cap PRIMA di incrementare
                if retry_count >= MAX_RETRIES:
                    logger.error(
                        "❌ Settlement FAILED dopo %d retry per %s — mark come settlement_failed",
                        MAX_RETRIES, match.display_name()
                    )
                    pending_meta["settlement_retries"] = retry_count
                    pending_meta["settlement_failed_at"] = now_utc.isoformat()
                    await self.db.execute(
                        update(Bet).where(Bet.id == bet.id).values(
                            status="settlement_failed",
                            notes=json.dumps(pending_meta)
                        )
                    )
                    await self.db.commit()
                    return False

                # Exponential backoff: 2^retry_count secondi (1s, 2s, 4s, 8s, 16s)
                backoff_seconds = 2 ** retry_count
                logger.warning(
                    "⏳ Settlement pending per %s (retry %d/%d) — score non disponibile. "
                    "Backoff %ds prima di retry",
                    match.display_name(), retry_count, MAX_RETRIES, backoff_seconds
                )

                pending_meta["settlement_retries"] = retry_count
                pending_meta["settlement_pending_since"] = now_utc.isoformat()
                pending_meta["next_retry_after"] = (now_utc + timedelta(seconds=backoff_seconds)).isoformat()
                await self.db.execute(
                    update(Bet).where(Bet.id == bet.id).values(notes=json.dumps(pending_meta))
                )
                await self.db.commit()

                # Exponential backoff per evitare hammering l'API
                await asyncio.sleep(backoff_seconds)
            else:
                logger.info("Match %s has no score yet — skipping", match.display_name())
            return False

        home_score, away_score = scores

        # Stringa risultato per h2h
        if home_score > away_score:
            h2h_winner = match.home_team
        elif away_score > home_score:
            h2h_winner = match.away_team
        else:
            h2h_winner = "Draw"

        # Stringa risultato per totals
        totals_result = f"total:{home_score}:{away_score}"

        # Marca la partita come "finished" se ancora non lo è
        if match.status != "finished":
            await self.db.execute(
                update(Match)
                .where(Match.id == match.id)
                .values(status="finished")
            )

        # Determine outcome — usa totals_result per mercati totals, h2h per il resto
        if bet.market == "totals":
            won = self._did_bet_win(bet, totals_result)
            actual_winner = totals_result
        else:
            won = self._did_bet_win(bet, h2h_winner)
            actual_winner = h2h_winner

        pnl = (float(bet.odds) - 1.0) * float(bet.stake) if won else -float(bet.stake)
        status = "won" if won else "lost"

        await self.db.execute(
            update(Bet)
            .where(Bet.id == bet.id)
            .values(
                status=status,
                result=actual_winner,
                pnl=pnl,
                settled_at=datetime.now(timezone.utc),
            )
        )

        # Update Brier scores for all agents that voted on this match
        await self._update_agent_scores(opp, won)

        await self.db.commit()
        logger.info(
            "Settled bet %s — %s — P&L: %.2f",
            bet.id, status, pnl,
        )

        # Telegram notification
        await self._notify(bet, match, status, pnl)

        return True

    async def _get_match_scores(self, match: Match) -> tuple[int, int] | None:
        """
        Fetch scores from The Odds API.
        Returns (home_score, away_score) or None if not yet available.
        """
        if not match.competition:
            return None

        sport_key = match.competition.odds_api_key
        if not sport_key:
            return None

        try:
            scores = await self.odds_client.fetch_scores(sport_key, days_from=3)
        except Exception as exc:
            logger.warning("Could not fetch scores for %s: %s", match.display_name(), exc)
            return None

        for score_event in scores:
            if score_event.get("id") != match.external_id:
                continue
            if not score_event.get("completed"):
                return None
            raw_scores = score_event.get("scores") or []
            parsed = {s["name"]: s["score"] for s in raw_scores}
            try:
                home_score = int(parsed.get(match.home_team, -1))
                away_score = int(parsed.get(match.away_team, -1))
            except (ValueError, TypeError):
                return None
            if home_score == -1 or away_score == -1:
                return None
            return (home_score, away_score)

        return None

    async def _get_match_winner(self, match: Match) -> str | None:
        """Backward-compat wrapper: ritorna stringa per mercato h2h."""
        result = await self._get_match_scores(match)
        if result is None:
            return None
        home_score, away_score = result
        if home_score > away_score:
            return match.home_team
        if away_score > home_score:
            return match.away_team
        return "Draw"

    @staticmethod
    def _did_bet_win(bet: Bet, actual_winner: str) -> bool:
        """
        Determina se la scommessa è vinta.
        Per h2h: confronto WRatio (hardened contro varianti, accenti, abbreviazioni).
        Per totals: confronto numerico con la linea.

        [BUG #2 HARDENED] Usa WRatio con normalizzazione robusta.
        Threshold dinamico: 90% per nomi corti (<8 char), 85% per nomi lunghi.
        """
        outcome = bet.outcome  # es. "Over 2.5", "Under 220.5", "Manchester City"

        # Prova a parsare come risultato per totals
        if actual_winner.startswith("total:"):
            parts = actual_winner.split(":")
            if len(parts) == 3:
                try:
                    total_goals = int(parts[1]) + int(parts[2])
                    return SettlementService._resolve_totals(outcome, total_goals)
                except (ValueError, TypeError):
                    pass
            return False

        # Fallback per "Draw" — confronto esatto (prima di normalizzazione)
        if outcome.lower() == "draw" or actual_winner.lower() == "draw":
            return outcome.lower() == actual_winner.lower()

        # H2H: normalizza e confronta con WRatio
        bet_norm = _normalize_team(outcome)
        winner_norm = _normalize_team(actual_winner)

        if not bet_norm or not winner_norm:
            logger.warning("Empty team name after normalization: '%s' vs '%s'", outcome, actual_winner)
            return False

        # WRatio: combina substring matching + token matching (più robusto di token_set_ratio)
        similarity = fuzz.WRatio(bet_norm, winner_norm)

        # Threshold dinamico: nomi corti → threshold più alto (meno false positives)
        threshold = FUZZY_THRESHOLD_SHORT if len(bet_norm) < 8 else FUZZY_THRESHOLD_LONG

        if similarity >= threshold:
            logger.debug(
                "H2H MATCH (WRatio %.0f%% >= threshold %d): '%s' → '%s' (normalized: '%s' vs '%s')",
                similarity, threshold, outcome, actual_winner, bet_norm, winner_norm
            )
            return True

        logger.debug(
            "H2H NO MATCH (WRatio %.0f%% < threshold %d): '%s' vs '%s' (normalized: '%s' vs '%s')",
            similarity, threshold, outcome, actual_winner, bet_norm, winner_norm
        )
        return False

    @staticmethod
    def _resolve_totals(outcome: str, total: int) -> bool:
        """
        Risolve un mercato totals dato il risultato finale.
        Gestisce linee standard (2.5), asiatiche intere (3.0), e quarter (2.25, 2.75).
        """
        import re
        m = re.match(r"(over|under)\s+([\d.]+)", outcome.lower())
        if not m:
            return False

        direction = m.group(1)  # "over" | "under"
        line = float(m.group(2))
        decimal = line - int(line)  # parte decimale per classificare la linea

        if decimal in (0.25, 0.75):
            # Quarter line (.25 o .75): split stake su due linee adiacenti.
            # Per settlement approssimato (no half-stake nel DB):
            #   Over 2.25 ≈ vince pieno con 3+, perde con ≤ 1, push con 2
            #   Under 2.75 ≈ vince pieno con ≤ 2, perde con 4+, push con 3
            lower_line = line - 0.25
            upper_line = line + 0.25
            if direction == "over":
                return total > lower_line   # vince almeno la metà
            else:
                return total < upper_line

        if decimal == 0.0:
            # Linea intera (es. 3.0): push se total == line → rimborso.
            # Per settlement binario: over vince con N+1, under vince con N-1.
            # Il push (esattamente N) viene trattato come perdita (caso raro, senza cashout DB).
            if direction == "over":
                return total > line          # 3.0: over vince con 4+
            else:
                return total < line          # 3.0: under vince con ≤ 2

        # Linea con .5 (es. 2.5): nessun push possibile
        if direction == "over":
            return total > line
        else:
            return total < line

    async def _update_agent_scores(self, opp: BettingOpportunity, bet_won: bool) -> None:
        """
        Recalculate Brier score for each agent that cast a vote on this opportunity.
        Brier score formula: BS = (p - o)² where p=predicted prob, o=actual outcome (1/0)
        Lower Brier score = better calibration.
        """
        actual = 1.0 if bet_won else 0.0

        votes_result = await self.db.execute(
            select(AgentVote)
            .where(AgentVote.match_id == opp.match_id)
            .where(AgentVote.market == opp.market)
            .where(AgentVote.outcome == opp.outcome)
        )
        votes = votes_result.scalars().all()

        for vote in votes:
            # Load agent run to get agent_name
            from app.db.models.agent import AgentRun
            run_result = await self.db.execute(
                select(AgentRun.agent_name).where(AgentRun.id == vote.agent_run_id)
            )
            agent_name = run_result.scalar_one_or_none()
            if not agent_name:
                continue

            # Brier score for this prediction
            brier_contrib = (float(vote.probability) - actual) ** 2

            # Load or create agent score record
            score_result = await self.db.execute(
                select(AgentScore).where(AgentScore.agent_name == agent_name)
            )
            agent_score = score_result.scalar_one_or_none()
            if not agent_score:
                agent_score = AgentScore(agent_name=agent_name)
                self.db.add(agent_score)
                await self.db.flush()

            # Rolling average Brier score
            n = agent_score.total_predictions
            current_bs = float(agent_score.brier_score)
            new_bs = (current_bs * n + brier_contrib) / (n + 1)
            # Weight inversely proportional to Brier score (lower BS = higher weight)
            new_weight = max(0.1, 1.0 - new_bs)

            await self.db.execute(
                update(AgentScore)
                .where(AgentScore.agent_name == agent_name)
                .values(
                    brier_score=new_bs,
                    total_predictions=n + 1,
                    correct_predictions=agent_score.correct_predictions + int(bet_won),
                    weight=new_weight,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def _notify(self, bet: Bet, match: Match, status: str, pnl: float) -> None:
        try:
            from app.services.telegram_service import send_settlement_notification
            await send_settlement_notification(bet, match, status, pnl)
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)

    async def _settle_scalata_step(self, scalata_id, won: bool, profit_loss: float) -> None:
        """
        Aggior la scalata quando uno step viene risolto.

        Se vinto:
          - Calcola stake per step successivo
          - Prepara step successivo
        Se perso:
          - Chiude la scalata come "persa"
        """
        from app.db.models.scalata import Scalata, ScalataStep
        from app.services.scalata_service import ScalataService

        scalata_result = await self.db.execute(
            select(Scalata).where(Scalata.id == scalata_id)
        )
        scalata = scalata_result.scalar_one_or_none()
        if not scalata:
            logger.warning("Scalata %s not found", scalata_id)
            return

        svc = ScalataService(self.db)
        result = await svc.settle_step(
            scalata_id=scalata_id,
            step_num=scalata.current_step,
            won=won,
            profit_loss=profit_loss
        )

        logger.info("Scalata %s step %d settled: %s", scalata_id, scalata.current_step, result)
