from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.db.models.user import User

router = APIRouter(prefix="/settings", tags=["settings"])


@router.post("/fetch-odds-now")
async def fetch_odds_now(_: User = Depends(require_admin)):
    """Trigger manuale fetch quote — usa 10 richieste API."""
    from app.workers.tasks import fetch_all_odds
    fetch_all_odds.delay()
    return {"status": "avviato", "note": "Le quote saranno aggiornate in 1-2 minuti"}
