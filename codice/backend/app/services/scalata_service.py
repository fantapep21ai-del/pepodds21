"""
ScalataService — gestisce scalate (sequenze di scommesse correlate).

Una scalata è:
  - Sequenza di N step (es. 3)
  - Si vince solo se TUTTI gli step vengono vinti
  - Se un step perde, la scalata fallisce
  - Lo stake di ogni step è il guadagno del precedente (all-in)

Responsabilità:
  - Creare scalate da candidati step
  - Tracciare progressione step
  - Calculare stake per ogni step
  - Gestire settlement

Example:
  Step 1: €50 @ 1.40 = guadagno €20 → stake step 2 = €70
  Step 2: €70 @ 1.50 = guadagno €35 → stake step 3 = €105
  Step 3: €105 @ 1.60 = guadagno €63 → TOTALE €168

  Se step 2 perde: scalata fallisce, loss = -€50 (solo step 1)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.scalata import Scalata, ScalataStep
from app.db.models.bet import Bet

if TYPE_CHECKING:
    from app.db.models.opportunity import BettingOpportunity

logger = logging.getLogger(__name__)


class ScalataService:
    """Gestisce scalate multi-step."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_scalata(
        self,
        steps_data: list[dict],
        start_amount: float,
        strategy: str = "accumulator",
    ) -> Scalata:
        """
        Crea una nuova scalata da una lista di step.

        Input (steps_data):
          [
            {
              "opportunity_id": UUID,
              "odds": 1.40,
              "market": "totals",
              "outcome": "Over 2.5",
              "match_name": "Inter vs Milan",
              "match_date": datetime,
              "bookmaker": "Bet365",
            },
            ...
          ]

        Output:
          Scalata con status="attiva" e current_step=0
        """
        if not steps_data or len(steps_data) < 2:
            raise ValueError("Scalata richiede minimo 2 step")

        # Crea record Scalata
        scalata = Scalata(
            id=uuid.uuid4(),
            status="attiva",
            total_steps=len(steps_data),
            current_step=0,
            start_amount=start_amount,
            current_amount=start_amount,
            potential_win=self._calculate_potential_win(steps_data, start_amount),
            created_at=datetime.now(timezone.utc),
            notes=f"Strategy: {strategy}",
        )
        self.db.add(scalata)
        await self.db.flush()

        # Crea ScalataStep per ciascun step
        for i, step_data in enumerate(steps_data, 1):
            scalata_step = ScalataStep(
                id=uuid.uuid4(),
                scalata_id=scalata.id,
                step_number=i,
                opportunity_id=step_data.get("opportunity_id"),
                odds=step_data.get("odds"),
                stake=0.0,  # Settato quando step diventa "attivo"
                status="in_attesa",
                match_name=step_data.get("match_name", "Match"),
                market=step_data.get("market", "h2h"),
                outcome=step_data.get("outcome", ""),
                bookmaker=step_data.get("bookmaker", ""),
                match_date=step_data.get("match_date"),
            )
            self.db.add(scalata_step)

        await self.db.flush()
        logger.info("Created scalata %s with %d steps", scalata.id, len(steps_data))
        return scalata

    async def play_step(
        self,
        scalata_id: uuid.UUID,
        step_num: int,
        stake: float,
        odds: float,
        bookmaker: str,
    ) -> Bet:
        """
        Registra una scommessa su uno step della scalata.

        Input:
          scalata_id: ID della scalata
          step_num: numero dello step (1-based)
          stake: importo piazzato
          odds: quota finale confermata
          bookmaker: bookmaker scelto

        Output:
          Bet con scalata_id linkato

        Side effects:
          - Aggiorna ScalataStep.status = "attivo"
          - Aggiorna ScalataStep.stake = stake
          - Aggiorna Scalata.current_step = step_num
        """
        # Carica scalata
        scalata_result = await self.db.execute(
            select(Scalata).where(Scalata.id == scalata_id)
        )
        scalata = scalata_result.scalar_one_or_none()
        if not scalata:
            raise ValueError(f"Scalata {scalata_id} not found")

        if scalata.status != "attiva":
            raise ValueError(f"Scalata non è attiva (status={scalata.status})")

        if step_num != scalata.current_step + 1:
            raise ValueError(
                f"Prossimo step è {scalata.current_step + 1}, non {step_num}"
            )

        # Carica lo step
        step_result = await self.db.execute(
            select(ScalataStep).where(
                ScalataStep.scalata_id == scalata_id,
                ScalataStep.step_number == step_num,
            )
        )
        step = step_result.scalar_one_or_none()
        if not step:
            raise ValueError(f"Step {step_num} not found in scalata")

        # Aggiorna step
        await self.db.execute(
            update(ScalataStep)
            .where(ScalataStep.id == step.id)
            .values(
                status="attivo",
                stake=stake,
                odds=odds,
                placed_at=datetime.now(timezone.utc),
            )
        )

        # Aggiorna scalata
        await self.db.execute(
            update(Scalata)
            .where(Scalata.id == scalata_id)
            .values(
                current_step=step_num,
                current_amount=stake,
                started_at=datetime.now(timezone.utc) if step_num == 1 else None,
            )
        )

        # Crea Bet
        bet = Bet(
            id=uuid.uuid4(),
            opportunity_id=step.opportunity_id,
            stake=stake,
            actual_odds=odds,
            status="open",
            created_at=datetime.now(timezone.utc),
            # Link alla scalata per tracking
            notes=f"Scalata {scalata_id} Step {step_num}/{scalata.total_steps}",
        )
        self.db.add(bet)
        await self.db.flush()

        logger.info(
            "Played scalata step: %s step %d/%d stake=%.2f",
            scalata_id,
            step_num,
            scalata.total_steps,
            stake,
        )
        return bet

    async def settle_step(
        self,
        scalata_id: uuid.UUID,
        step_num: int,
        won: bool,
        profit_loss: float,
    ) -> dict:
        """
        Registra il risultato di uno step.

        Se vinto:
          - Calcola stake per step successivo (all-in)
          - Ritorna info su step successivo

        Se perso:
          - Chiude scalata come "persa"
          - Calcola PnL totale
        """
        # Carica scalata e step
        scalata_result = await self.db.execute(
            select(Scalata).where(Scalata.id == scalata_id)
        )
        scalata = scalata_result.scalar_one_or_none()
        if not scalata:
            raise ValueError(f"Scalata {scalata_id} not found")

        step_result = await self.db.execute(
            select(ScalataStep).where(
                ScalataStep.scalata_id == scalata_id,
                ScalataStep.step_number == step_num,
            )
        )
        step = step_result.scalar_one_or_none()
        if not step:
            raise ValueError(f"Step {step_num} not found")

        if won:
            # Step vinto — calcola stake per step successivo
            next_stake = (step.odds - 1) * float(step.stake) + float(step.stake)

            # Aggiorna step
            await self.db.execute(
                update(ScalataStep)
                .where(ScalataStep.id == step.id)
                .values(
                    status="vinto",
                    settled_at=datetime.now(timezone.utc),
                )
            )

            # Se è l'ultimo step, chiudi scalata come vinta
            if step_num == scalata.total_steps:
                await self.db.execute(
                    update(Scalata)
                    .where(Scalata.id == scalata_id)
                    .values(
                        status="vinta",
                        completed_at=datetime.now(timezone.utc),
                        total_pnl=profit_loss,
                    )
                )
                logger.info("Scalata %s VINTA | total_pnl=%.2f", scalata_id, profit_loss)
                return {
                    "status": "completed",
                    "result": "vinta",
                    "total_pnl": profit_loss,
                }
            else:
                # Prossimo step in attesa
                return {
                    "status": "next_step_ready",
                    "next_step": step_num + 1,
                    "suggested_stake": round(next_stake, 2),
                }

        else:
            # Step perso — scalata fallisce
            await self.db.execute(
                update(ScalataStep)
                .where(ScalataStep.id == step.id)
                .values(
                    status="perso",
                    settled_at=datetime.now(timezone.utc),
                )
            )

            # Calcola PnL totale (loss su step perso + gains precedenti)
            total_pnl = profit_loss  # Already includes all steps

            await self.db.execute(
                update(Scalata)
                .where(Scalata.id == scalata_id)
                .values(
                    status="persa",
                    completed_at=datetime.now(timezone.utc),
                    total_pnl=total_pnl,
                )
            )

            logger.info(
                "Scalata %s PERSA at step %d/%d | total_pnl=%.2f",
                scalata_id,
                step_num,
                scalata.total_steps,
                total_pnl,
            )
            return {
                "status": "completed",
                "result": "persa",
                "failed_at_step": step_num,
                "total_pnl": total_pnl,
            }

    @staticmethod
    def _calculate_potential_win(
        steps_data: list[dict], start_amount: float
    ) -> float:
        """Calcola guadagno potenziale se tutti gli step vengono vinti."""
        current = start_amount
        for step in steps_data:
            odds = step.get("odds", 1.0)
            current = current * odds
        return current

    async def get_scalata_status(self, scalata_id: uuid.UUID) -> dict:
        """Ritorna stato completo di una scalata."""
        scalata_result = await self.db.execute(
            select(Scalata).where(Scalata.id == scalata_id)
        )
        scalata = scalata_result.scalar_one_or_none()
        if not scalata:
            return {"error": "Scalata not found"}

        steps_result = await self.db.execute(
            select(ScalataStep)
            .where(ScalataStep.scalata_id == scalata_id)
            .order_by(ScalataStep.step_number)
        )
        steps = steps_result.scalars().all()

        return {
            "scalata_id": str(scalata.id),
            "status": scalata.status,
            "progress": f"{scalata.current_step}/{scalata.total_steps}",
            "start_amount": float(scalata.start_amount),
            "potential_win": float(scalata.potential_win),
            "total_pnl": float(scalata.total_pnl) if scalata.total_pnl else None,
            "steps": [
                {
                    "step_number": s.step_number,
                    "status": s.status,
                    "match_name": s.match_name,
                    "odds": float(s.odds),
                    "stake": float(s.stake) if s.stake else None,
                }
                for s in steps
            ],
        }
