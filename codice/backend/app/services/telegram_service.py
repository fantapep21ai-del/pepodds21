"""
Telegram notification service.

Invia notifiche al chat_id configurato via python-telegram-bot.
Stile messaggi: chiaro, conciso, leggibile — ispirato al design Apple.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.db.models.bet import Bet
    from app.db.models.match import Match
    from app.db.models.opportunity import BettingOpportunity

logger = logging.getLogger(__name__)

_TIER_EMOJI = {"S": "🔥", "A": "✅", "B": "⚡", "C": "—"}
_BET_TYPE_LABEL = {
    "singola": "Singola",
}

_SEP = "━━━━━━━━━━━━━━━━━━"


def _format_market_outcome(market: str, outcome: str, sport: str) -> tuple[str, str]:
    """
    Ritorna (market_label, outcome_label) chiari e completi per il messaggio Telegram.

    Esempi:
      football + totals + "Over 3.0" → ("Totale gol", "Over 3.0 (vinci con 4+, 50% rimborso se 3 esatti)")
      football + totals + "Over 2.5" → ("Totale gol", "Over 2.5 (vinci con 3+ gol)")
      basketball + totals + "Over 220.5" → ("Totale punti", "Over 220.5 (vinci con 221+ punti)")
      football + h2h + "Draw" → ("Risultato finale 1X2", "Draw (pareggio)")
    """
    if market == "h2h":
        market_label = "Risultato finale 1X2"
        outcome_label = {"Draw": "Draw (pareggio)"}.get(outcome, outcome)
        return market_label, outcome_label

    if market == "spreads":
        market_label = "Handicap"
        return market_label, outcome

    if market == "totals":
        # Determina l'unità in base allo sport
        sport_lower = (sport or "").lower()
        if "basketball" in sport_lower:
            unit = "punti"
            market_label = "Totale punti"
        elif "tennis" in sport_lower:
            unit = "game"
            market_label = "Totale game"
        else:
            unit = "gol"
            market_label = "Totale gol"

        # Spiega la linea
        import re
        m = re.match(r'^(Over|Under)\s+(\d+(?:\.\d+)?)$', outcome, re.IGNORECASE)
        if m:
            direction = m.group(1).capitalize()
            line_str = m.group(2)
            try:
                line = float(line_str)
            except ValueError:
                return market_label, outcome

            decimal = line - int(line)

            if decimal == 0.0:
                # Linea asiatica esatta (es. 3.0): push con esattamente N
                n = int(line)
                if direction == "Over":
                    outcome_label = (
                        f"Over {line_str} {unit} "
                        f"(vinci con {n+1}+, rimborso 50% se esattamente {n})"
                    )
                else:
                    outcome_label = (
                        f"Under {line_str} {unit} "
                        f"(vinci con {n-1} o meno, rimborso 50% se esattamente {n})"
                    )
            elif decimal == 0.25:
                # Quarto di linea (es. 2.25): metà scommessa su 2.0, metà su 2.5
                n_lo = int(line)
                if direction == "Over":
                    outcome_label = (
                        f"Over {line_str} {unit} "
                        f"(vinci pieno con {n_lo+1}+, metà win con esattamente {n_lo})"
                    )
                else:
                    outcome_label = (
                        f"Under {line_str} {unit} "
                        f"(vinci pieno con {n_lo-1} o meno, metà win con esattamente {n_lo})"
                    )
            elif decimal == 0.5:
                # Linea standard (es. 2.5): nessun push possibile
                n = int(line)
                if direction == "Over":
                    outcome_label = f"Over {line_str} {unit} (vinci con {n+1}+)"
                else:
                    outcome_label = f"Under {line_str} {unit} (vinci con {n} o meno)"
            elif decimal == 0.75:
                # Tre-quarti di linea (es. 2.75)
                n_hi = int(line) + 1
                if direction == "Over":
                    outcome_label = (
                        f"Over {line_str} {unit} "
                        f"(vinci pieno con {n_hi}+, metà win con esattamente {int(line)+1})"
                    )
                else:
                    outcome_label = (
                        f"Under {line_str} {unit} "
                        f"(vinci pieno con {int(line)} o meno)"
                    )
            else:
                outcome_label = f"{outcome} {unit}"

            return market_label, outcome_label

        return market_label, outcome

    # Mercati sconosciuti: mostra as-is
    return market.replace("_", " ").title(), outcome


async def _send(text: str) -> None:
    """Invia un messaggio al chat_id configurato con rate limiting (max 1 msg/500ms)."""
    import asyncio
    from telegram import Bot
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        # Flood control: aspetta e riprova una volta
        err_str = str(exc).lower()
        if "flood" in err_str or "retry" in err_str or "429" in err_str:
            logger.warning("Telegram flood control — attesa 5s e retry")
            await asyncio.sleep(5)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode="HTML",
            )
        else:
            raise
    await asyncio.sleep(0.5)  # rate limiting: max 2 msg/sec


# ─────────────────────────────────────────────────────────────────────────────
# Opportunity alert (con bottoni inline)
# ─────────────────────────────────────────────────────────────────────────────

def _notifications_paused() -> bool:
    """Controlla se le notifiche sono in pausa (flag persistente su Redis)."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url_with_auth, decode_responses=True)
        return r.get("telegram:notifications:paused") == "1"
    except Exception:
        return False


async def send_opportunity_alert(
    opp: BettingOpportunity,
    match_name: str,
    reliability: float = 0.0,
    reasoning: str = "",
    sport: str = "football",
    agent_insights: list[str] | None = None,
    synthesis_narrative: str = "",
) -> None:
    """
    Alert principale — narrativa da SynthesisAgent + insight agenti.
    Ogni alert risponde a: COSA giocare, PERCHÉ ha senso, QUANTO è affidabile.

    La narrativa viene dal SynthesisAgent che integra i 7 specialist in una storia coerente.
    """
    if _notifications_paused():
        logger.info("Notifiche in pausa — skip alert per %s", match_name)
        return

    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    tier      = getattr(opp, "tier", "B") or "B"
    bet_type  = getattr(opp, "bet_type", "singola") or "singola"
    ev        = float(getattr(opp, "expected_value", 0.0) or 0.0)
    opp_id    = str(opp.id)

    market_label, outcome_label = _format_market_outcome(opp.market, opp.outcome, sport)

    rel_pct = int(round(reliability * 100))
    if rel_pct >= 70:
        rel_icon = "🟢"
    elif rel_pct >= 50:
        rel_icon = "🟡"
    else:
        rel_icon = "🔴"

    tier_emoji = _TIER_EMOJI.get(tier, "✅")
    tipo       = _BET_TYPE_LABEL.get(bet_type, bet_type.capitalize())

    # ── SINTESI NARRATIVA da SynthesisAgent ───────────────────────────────────
    synthesis_block = ""
    if synthesis_narrative and len(synthesis_narrative) > 20:
        narrative_display = synthesis_narrative[:500]
        if len(synthesis_narrative) > 500:
            narrative_display += "…"
        synthesis_block = f"\n<b>Analisi</b>\n<i>{narrative_display}</i>\n"

    # ── Sezione fattori principali ────────────────────────────────────────────────
    # Mostra max 3 insight per non appesantire il messaggio
    insights = agent_insights or []
    analysis_lines = "\n".join(f"  {ins}" for ins in insights[:3]) if insights else ""
    analysis_block = (
        f"\n<b>Fattori</b>\n{analysis_lines}\n"
        if analysis_lines else ""
    )

    # ── Verdetto AI (UncertaintyAgent) ────────────────────────────────────────
    verdict_block = ""
    if reasoning and len(reasoning) > 10:
        verdict_block = f"\n<b>Verdetto</b>\n  <i>{reasoning[:220]}</i>\n"

    # ── Edge matematico ───────────────────────────────────────────────────────
    cv = getattr(opp, "consensus_votes", {}) or {}
    n_bk  = cv.get("n_confirming_bookmakers", 0)
    n_ag  = cv.get("n_agents_agreeing", 0)
    n_ag_run = len(cv.get("agents_run", []))
    edge_detail = ""
    if n_bk:
        edge_detail += f"  {n_bk} bookmaker confermano il value\n"
    if n_ag_run:
        edge_detail += f"  {n_ag}/{n_ag_run} agenti d'accordo con Pinnacle\n"

    msg = (
        f"{tier_emoji} <b>{tipo.upper()} — Tier {tier}</b>\n"
        f"{_SEP}\n"
        f"<b>{match_name}</b>\n"
        f"\n"
        f"<b>Gioca:</b> {market_label}\n"
        f"  → <b>{outcome_label}</b> @ <b>{float(opp.best_odds):.2f}</b>\n"
        f"  Bookmaker: {opp.bookmaker.replace('_', ' ').title()}\n"
        f"{synthesis_block}"
        f"<b>Edge matematico</b>\n"
        f"  Vantaggio stimato: <b>{ev:+.1%}</b>\n"
        f"{edge_detail}"
        f"  Affidabilità: {rel_icon} <b>{rel_pct}%</b>\n"
        f"{analysis_block}"
        f"{_SEP}\n"
        f"<i>Quanto vuoi giocare?</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Gioco", callback_data=f"approve:{opp_id}"),
            InlineKeyboardButton("❌ Salto", callback_data=f"reject:{opp_id}"),
            InlineKeyboardButton("⏸ Dopo", callback_data=f"hold:{opp_id}"),
        ],
    ])

    try:
        bot = Bot(token=settings.telegram_bot_token)
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=msg,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logger.warning("Telegram opportunity alert failed: %s", exc)


def _format_analysis_reason(reasons: list[str]) -> str:
    """Convert list of reason codes to human-readable Italian message."""
    reason_map = {
        "no_pinnacle_quotes": "Quote Pinnacle non disponibili (impossibile calcolare EV)",
        "no_fresh_odds": "Nessuna quota fresca (< 6 ore)",
        "insufficient_bookmakers": "Bookmaker insufficienti (< 4 fonti)",
        "stats_incomplete": "Dati statistici incompleti",
        "form_data_missing": "Dati di forma squadra mancanti",
        "injury_data_missing": "Dati infortuni mancanti",
        "weather_unavailable": "Dati meteo non disponibili",
        "no_vig_calculation_failed": "Calcolo no-vig fallito",
    }
    if not reasons:
        return "Motivo sconosciuto"
    readable = [reason_map.get(r, r) for r in reasons[:2]]  # Max 2 reasons
    return " | ".join(readable)


async def send_sport_analysis_report(sport: str, matches_report: list | None = None, duration_s: float = 0.0) -> None:
    """
    Send report with all analyzed matches and all qualifying singole per match.

    Args:
        sport: "football" | "basketball" | "tennis"
        matches_report: [
            {
                "match_name": str,
                "competition": str,
                "analysis_status": "complete" | "incomplete" | "unknown",
                "analysis_reason": str or None,
                "singole": [
                    {
                        "market": str,
                        "outcome": str,
                        "best_odds": float,
                        "expected_value": float,
                        "bookmaker": str,
                    },
                    ...
                ]
            },
            ...
        ] or None/[] if no matches analyzed
        duration_s: time spent on analysis in seconds
    """
    if _notifications_paused():
        return

    from telegram import Bot

    sport_emoji = {"football": "⚽", "calcio": "⚽", "basketball": "🏀", "basket": "🏀", "tennis": "🎾"}.get(sport.lower(), "📊")
    sport_label = {"football": "CALCIO", "calcio": "CALCIO", "basketball": "BASKET", "basket": "BASKET", "tennis": "TENNIS"}.get(sport.lower(), sport.upper())
    command = {"football": "ricerca_calcio", "calcio": "ricerca_calcio", "basketball": "ricerca_nba", "basket": "ricerca_nba", "tennis": "ricerca_tennis"}.get(sport.lower(), "ricerca_calcio")

    # No matches or empty report
    if not matches_report:
        msg = f"{sport_emoji} <b>RICERCA {sport_label}</b>\n{_SEP}\n"
        msg += "❌ Nessuna partita trovata nel timeframe\n"
        msg += f"\nRiprova con /{command}\n"
        try:
            bot = Bot(token=settings.telegram_bot_token)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=msg,
                parse_mode="HTML",
            )
            logger.info("No matches found for sport: %s", sport)
        except Exception as exc:
            logger.warning("Notification failed: %s", exc)
        return

    # Build report header
    msg = f"{sport_emoji} <b>RICERCA {sport_label}</b>\n{_SEP}\n"
    msg += f"<b>Partite analizzate:</b> {len(matches_report)}\n\n"

    # Process each match
    total_singole = 0
    for i, match in enumerate(matches_report, 1):
        msg += f"<b>{i}. {match['match_name']}</b> — {match['competition']}\n"

        # Check analysis status
        if match["analysis_status"] != "complete":
            msg += f"   ⚠️ Analisi incompleta"
            if match["analysis_reason"]:
                msg += f": {match['analysis_reason']}"
            msg += "\n"
        else:
            msg += "   ✓ Analisi completa\n"

        # Show all singole for this match
        singole = match.get("singole", [])
        if singole:
            for j, singola in enumerate(singole, 1):
                market_label, outcome_label = _format_market_outcome(singola["market"], singola["outcome"], sport)
                msg += f"\n   <b>{j}.</b> {market_label} → {outcome_label}\n"
                msg += f"       Quote: {singola['best_odds']:.2f} @ {singola['bookmaker'].replace('_', ' ').title()}\n"
                msg += f"       EV: {singola['expected_value']:+.1%}\n"
                total_singole += 1
        else:
            msg += "   ❌ Nessuna singola di valore trovata\n"

        msg += "\n"

    # Footer
    msg += _SEP + "\n"
    if total_singole > 0:
        msg += f"<b>TOTALE SINGOLE:</b> {total_singole}\n"
    msg += f"<b>TEMPO ANALISI:</b> {duration_s:.1f}s\n"
    msg += f"<b>AZIONE:</b> Accetta o rifiuta proposte con /scommesse\n"

    try:
        bot = Bot(token=settings.telegram_bot_token)
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=msg,
            parse_mode="HTML",
        )
        logger.info("Sport analysis report sent: %s (%d matches, %d singole, %.1fs)", sport, len(matches_report), total_singole, duration_s)
    except Exception as exc:
        logger.warning("Report notification failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Sync: notifica quando l'utente agisce dal sito (non dal bot)
# ─────────────────────────────────────────────────────────────────────────────

async def notify_opportunity_confirmed(opp: BettingOpportunity, match_name: str, stake: float) -> None:
    """Notifica Telegram quando la conferma avviene dal sito web."""
    msg = (
        f"✅ <b>Confermata dal sito</b>\n"
        f"{_SEP}\n"
        f"<b>{match_name}</b>\n"
        f"{opp.market} — <b>{opp.outcome}</b>\n"
        f"Quota: <b>{float(opp.best_odds):.2f}</b> · {opp.bookmaker}\n"
        f"Stake: <b>€{stake:.0f}</b>"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram confirm notify failed: %s", exc)


async def notify_opportunity_rejected(opp: BettingOpportunity, match_name: str, reason: str = "") -> None:
    """Notifica Telegram quando il rifiuto avviene dal sito web."""
    reason_str = f"\n<i>Motivo: {reason}</i>" if reason and reason != "Rifiutata manualmente" else ""
    msg = (
        f"❌ <b>Rifiutata dal sito</b>\n"
        f"{_SEP}\n"
        f"<b>{match_name}</b>\n"
        f"{opp.market} — {opp.outcome} @ {float(opp.best_odds):.2f}"
        f"{reason_str}"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram reject notify failed: %s", exc)


async def notify_opportunity_on_hold(opp: BettingOpportunity, match_name: str) -> None:
    """Notifica Telegram quando una scommessa viene messa in attesa dal sito."""
    msg = (
        f"⏸ <b>Messa in attesa dal sito</b>\n"
        f"{_SEP}\n"
        f"<b>{match_name}</b>\n"
        f"{opp.market} — {opp.outcome} @ {float(opp.best_odds):.2f}\n"
        f"<i>Usa /attesa sul bot per vederla e combinarla con un'altra.</i>"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram hold notify failed: %s", exc)


async def notify_opportunity_modified(opp: BettingOpportunity, match_name: str) -> None:
    """Notifica Telegram quando una scommessa viene modificata dal sito."""
    msg = (
        f"✏️ <b>Modificata dal sito</b>\n"
        f"{_SEP}\n"
        f"<b>{match_name}</b>\n"
        f"{opp.market} — {opp.outcome} @ {float(opp.best_odds):.2f}\n"
        f"Tipo: {(opp.bet_type or 'singola').capitalize()} · EV: {float(opp.expected_value or 0):+.1%}"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram modify notify failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Settlement
# ─────────────────────────────────────────────────────────────────────────────

async def send_settlement_notification(
    bet: Bet, match: Match, status: str, pnl: float
) -> None:
    emoji = "✅" if status == "won" else "❌"
    esito = "Vinta" if status == "won" else "Persa"
    pnl_str = f"+€{pnl:.2f}" if pnl >= 0 else f"-€{abs(pnl):.2f}"
    msg = (
        f"{emoji} <b>Scommessa {esito}</b>\n"
        f"{_SEP}\n"
        f"<b>{match.display_name()}</b>\n"
        f"{bet.market} — {bet.outcome}\n"
        f"Quota: {float(bet.odds):.2f} · Stake: €{float(bet.stake):.0f}\n"
        f"P&amp;L: <b>{pnl_str}</b>"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram settlement notify failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline summary
# ─────────────────────────────────────────────────────────────────────────────

async def send_no_bet_today(matches_analysed: int, duration_s: float) -> None:
    """Notifica quando l'analisi non trova nessuna quota di valore."""
    msg = (
        f"🔍 <b>Analisi completata</b>\n"
        f"{_SEP}\n"
        f"Partite analizzate: {matches_analysed}\n"
        f"Durata: {duration_s:.1f}s\n\n"
        f"Nessuna quota di valore identificata.\n"
        f"<i>Il sistema non scommette se non c'è un edge reale.</i>"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram no-bet notify failed: %s", exc)


async def send_pipeline_summary(
    matches_processed: int,
    opportunities_found: int,
    duration_s: float,
) -> None:
    msg = (
        f"📊 <b>Analisi completata</b>\n"
        f"{_SEP}\n"
        f"Partite analizzate: {matches_processed}\n"
        f"Opportunità trovate: <b>{opportunities_found}</b>\n"
        f"Durata: {duration_s:.1f}s\n\n"
        f"<i>Usa /opportunita per vedere e gestire le quote trovate.</i>"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram pipeline summary failed: %s", exc)


async def send_odds_movement_alert(match_name: str, market: str, outcome: str,
                                   old_odds: float, new_odds: float, pct: float) -> None:
    direction = "▲" if new_odds > old_odds else "▼"
    msg = (
        f"📈 <b>Movimento quota</b>\n"
        f"{_SEP}\n"
        f"<b>{match_name}</b>\n"
        f"{market} — {outcome}\n"
        f"{old_odds:.2f} → <b>{new_odds:.2f}</b> {direction} {abs(pct):.1%}"
    )
    try:
        await _send(msg)
    except Exception as exc:
        logger.warning("Telegram odds movement alert failed: %s", exc)
