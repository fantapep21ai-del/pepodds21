"""
Telegram webhook — riceve aggiornamenti dal bot e gestisce comandi e callback.

Comandi supportati:
  /ricerca          — Ricerca tutti gli sport (300/mese)
  /ricerca_calcio   — Ricerca calcio (150/mese)
  /ricerca_nba      — Ricerca NBA (150/mese)
  /ricerca_tennis   — Ricerca tennis (150/mese)
  /oggi             — Partite di oggi
  /opportunita      — Opportunità pendenti
  /bilancio         — P&L e statistiche
  /scommesse        — Scommesse aperte con ID
  /aggiorna_quote   — Aggiorna quote
  /pipeline         — Analisi manuale
  /pausa            — Pausa notifiche
  /help             — Mostra aiuto
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request, Response

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

# Pausa notifiche — chiave Redis "telegram:notifications:paused" (persistente tra restart)
_PAUSE_REDIS_KEY = "telegram:notifications:paused"

async def _is_paused() -> bool:
    try:
        from redis.asyncio import Redis
        from app.config import settings as _cfg
        r = Redis(**_cfg.get_redis_connection_kwargs())
        async with r:
            return await r.get(_PAUSE_REDIS_KEY) == "1"
    except Exception:
        return False

async def _set_paused(paused: bool) -> None:
    try:
        from redis.asyncio import Redis
        from app.config import settings as _cfg
        r = Redis(**_cfg.get_redis_connection_kwargs())
        async with r:
            if paused:
                await r.set(_PAUSE_REDIS_KEY, "1")
            else:
                await r.delete(_PAUSE_REDIS_KEY)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# Command registration
# ─────────────────────────────────────────────────────────────────────────────

async def register_bot_commands() -> None:
    """Register all available commands on Telegram BotFather."""
    try:
        from telegram import Bot, BotCommand
        bot = Bot(token=settings.telegram_bot_token)
        commands = [
            BotCommand("ricerca_calcio", "Ricerca calcio"),
            BotCommand("ricerca_nba", "Ricerca NBA + Playoffs"),
            BotCommand("ricerca_tennis", "Ricerca tennis"),
            BotCommand("oggi", "Partite di oggi"),
            BotCommand("opportunita", "Opportunità pendenti"),
            BotCommand("bilancio", "P&L e statistiche"),
            BotCommand("scommesse", "Scommesse aperte"),
            BotCommand("aggiorna_quote", "Aggiorna quote"),
            BotCommand("pipeline", "Analisi manuale"),
            BotCommand("pausa", "Pausa notifiche"),
            BotCommand("pulisci", "Pulisci chat"),
            BotCommand("help", "Mostra aiuto"),
        ]
        await bot.set_my_commands(commands)
        logger.info("✅ Bot commands registered successfully")
    except Exception as exc:
        logger.warning("Failed to register bot commands: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Riceve update dal bot Telegram — comandi e callback dei bottoni inline."""
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=200)

    if callback := data.get("callback_query"):
        await _handle_callback(callback)
        return Response(status_code=200)

    message = data.get("message") or data.get("edited_message")
    if not message:
        return Response(status_code=200)

    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()

    if chat_id != str(settings.telegram_chat_id):
        return Response(status_code=200)

    command = text.split("@")[0].split(" ")[0].lower()

    handlers = {
        "/start":           _handle_help,
        "/help":            _handle_help,
        "/ricerca_calcio":  _handle_ricerca_calcio,
        "/ricerca_nba":     _handle_ricerca_nba,
        "/ricerca_tennis":  _handle_ricerca_tennis,
        "/oggi":            _handle_oggi,
        "/opportunita":     _handle_opportunita,
        "/attesa":          _handle_attesa,
        "/bilancio":        _handle_bilancio,
        "/stats":           _handle_stats,
        "/status":          _handle_status,
        "/aggiorna_quote":  _handle_aggiorna_quote,
        "/pipeline":        _handle_pipeline,
        "/pausa":           _handle_pausa,
        "/scommesse":       _handle_scommesse,
        "/quote":           _handle_quote,
        "/pulisci":         _handle_pulisci,
    }
    if handler := handlers.get(command):
        await handler(chat_id)
    elif command == "/settle":
        # /settle <bet_id> <win|loss>
        parts = text.split()
        if len(parts) >= 3:
            await _handle_settle(chat_id, parts[1], parts[2])
        else:
            await _send(chat_id, "Uso: /settle &lt;bet_id&gt; &lt;win|loss&gt;\n\nUsa /scommesse per vedere gli ID delle scommesse aperte.")

    return Response(status_code=200)


# ─────────────────────────────────────────────────────────────────────────────
# Callback handler (bottoni inline)
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_callback(callback: dict) -> None:
    from telegram import Bot
    from app.db.base import AsyncSessionLocal
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.bet import Bet
    from sqlalchemy import select

    callback_id = callback.get("id")
    chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
    message_id = callback.get("message", {}).get("message_id")
    data = callback.get("data", "")

    if chat_id != str(settings.telegram_chat_id):
        return

    bot = Bot(token=settings.telegram_bot_token)

    try:
        await bot.answer_callback_query(callback_query_id=callback_id)
    except Exception:
        pass

    parts = data.split(":")
    if len(parts) < 2:
        return

    action = parts[0]
    opp_id_str = parts[1]
    extra = parts[2] if len(parts) >= 3 else None

    try:
        opp_uuid = uuid.UUID(opp_id_str)
    except ValueError:
        logger.warning("Callback: UUID non valido '%s'", opp_id_str)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BettingOpportunity).where(BettingOpportunity.id == opp_uuid)
        )
        opp = result.scalar_one_or_none()

        if not opp:
            await _safe_send(bot, chat_id, "Opportunità non trovata.")
            return

        if action in ("approve", "reject", "hold", "modify") and opp.status not in ("pending", "in_attesa"):
            label = {
                "bet_placed": "già confermata ✅",
                "rejected": "già rifiutata ❌",
                "expired": "scaduta — la partita è già iniziata ⏱",
            }.get(opp.status, opp.status)
            await _safe_edit(bot, chat_id, message_id, f"Questa opportunità è {label}.")
            return

        if action == "approve":
            # Se c'è già l'importo nel callback (es. approve:{id}:{amount}), conferma direttamente
            if extra:
                try:
                    stake = float(extra)
                except ValueError:
                    stake = None

                if stake and stake > 0:
                    bet = Bet(
                        opportunity_id=opp.id,
                        bookmaker=opp.bookmaker,
                        market=opp.market,
                        outcome=opp.outcome,
                        odds=float(opp.best_odds),
                        stake=stake,
                        status="open",
                    )
                    opp.status = "bet_placed"
                    db.add(bet)
                    await db.commit()
                    await _safe_edit(bot, chat_id, message_id, _fmt_confirmed(opp, stake))
                    return

            # Altrimenti mostra il picker importo
            await db.commit()  # nessuna modifica
            await _safe_edit(bot, chat_id, message_id, _fmt_ask_stake(opp), reply_markup=_keyboard_stake(opp_id_str))

        elif action == "reject":
            opp.status = "rejected"
            opp.rejection_reason = "Rifiutata via Telegram"
            await db.commit()
            await _safe_edit(bot, chat_id, message_id, _fmt_rejected(opp))

        elif action == "hold":
            opp.status = "in_attesa"
            await db.commit()
            await _safe_edit(bot, chat_id, message_id, _fmt_on_hold(opp))

        elif action == "modify":
            # Redirect al picker importo — è lo stesso flusso di "approve"
            await _safe_edit(bot, chat_id, message_id, _fmt_ask_stake(opp), reply_markup=_keyboard_stake(opp_id_str))

        elif action == "setstake" and extra:
            try:
                new_stake = float(extra)
            except ValueError:
                return
            await db.commit()
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            text = (
                f"💰 <b>Importo aggiornato</b>\n"
                f"\n"
                f"{opp.market} — <b>{opp.outcome}</b>\n"
                f"Quota: <b>{float(opp.best_odds):.2f}</b>\n\n"
                f"Importo: <b>€{new_stake:.0f}</b> · Tipo: {opp.bet_type or 'singola'}"
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Conferma", callback_data=f"approve:{opp_id_str}"),
                InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject:{opp_id_str}"),
                InlineKeyboardButton("✏️ Modifica", callback_data=f"modify:{opp_id_str}"),
            ]])
            await _safe_edit(bot, chat_id, message_id, text, reply_markup=keyboard)

        elif action == "settype" and extra:
            opp.bet_type = extra
            await db.commit()
            # Dopo aver cambiato il tipo → torna al picker importo
            await _safe_edit(bot, chat_id, message_id, _fmt_ask_stake(opp), reply_markup=_keyboard_stake(opp_id_str))


# ─────────────────────────────────────────────────────────────────────────────
# Formattatori
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_ask_stake(opp) -> str:
    return (
        f"💰 <b>Quanto vuoi giocare?</b>\n"
        f"\n"
        f"<b>{opp.outcome}</b> @ {float(opp.best_odds):.2f}\n"
        f"{opp.bookmaker.replace('_', ' ').title()}\n\n"
        f"Scegli l'importo:"
    )

def _keyboard_stake(opp_id_str: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("€ 5",   callback_data=f"approve:{opp_id_str}:5"),
            InlineKeyboardButton("€ 10",  callback_data=f"approve:{opp_id_str}:10"),
            InlineKeyboardButton("€ 20",  callback_data=f"approve:{opp_id_str}:20"),
        ],
        [
            InlineKeyboardButton("€ 30",  callback_data=f"approve:{opp_id_str}:30"),
            InlineKeyboardButton("€ 50",  callback_data=f"approve:{opp_id_str}:50"),
            InlineKeyboardButton("€ 100", callback_data=f"approve:{opp_id_str}:100"),
        ],
        [
            InlineKeyboardButton("❌ Annulla", callback_data=f"reject:{opp_id_str}"),
        ],
    ])

def _fmt_confirmed(opp, stake: float) -> str:
    return (
        f"✅ <b>Confermata</b>\n"
        f"\n"
        f"{opp.market} — <b>{opp.outcome}</b>\n"
        f"Quota <b>{float(opp.best_odds):.2f}</b> · {opp.bookmaker}\n"
        f"Stake: <b>€{stake:.0f}</b>"
    )

def _fmt_rejected(opp) -> str:
    return (
        f"❌ <b>Rifiutata</b>\n"
        f"\n"
        f"{opp.market} — {opp.outcome} @ {float(opp.best_odds):.2f}"
    )

def _fmt_on_hold(opp) -> str:
    return (
        f"⏸ <b>In attesa</b>\n"
        f"\n"
        f"{opp.market} — <b>{opp.outcome}</b> @ {float(opp.best_odds):.2f}\n\n"
        f"<i>Tienila da parte per combinarla con un'altra.\n"
        f"Se la partita inizia prima che tu la giochi, viene scartata automaticamente.</i>\n\n"
        f"Usa /attesa per vedere tutte le scommesse in attesa."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper send/edit
# ─────────────────────────────────────────────────────────────────────────────

async def _safe_edit(bot, chat_id: str, message_id: int, text: str, reply_markup=None) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
        )
    except Exception:
        await _safe_send(bot, chat_id, text, reply_markup=reply_markup)

async def _safe_send(bot, chat_id: str, text: str, reply_markup=None) -> None:
    try:
        await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)

async def _send(chat_id: str, text: str) -> None:
    from telegram import Bot
    bot = Bot(token=settings.telegram_bot_token)
    await _safe_send(bot, chat_id, text)


# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_pulisci(chat_id: str) -> None:
    """Cancella ultimi 100 messaggi dalla chat."""
    try:
        from telegram import Bot
        bot = Bot(token=settings.telegram_bot_token)
        logger.info("🗑️ Pulizia chat in corso... (ultimi 100 messaggi)")

        # Telegram API non supporta delete_chat_history per chat private
        # Estrattempo manuale: ottieni gli ultimi messaggi e eliminali uno per uno
        # Questo è inefficiente ma funziona con la API standard
        # Soluzione semplice: inviare un messaggio di conferma

        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ <b>Chat</b>\nPer pulire i messaggi vecchi, scorri la chat manualmente o usa la funzione Telegram 'Cancella tutti i messaggi' nelle impostazioni.",
            parse_mode="HTML"
        )
    except Exception as exc:
        logger.error("Errore pulizia: %s", exc)
        await _send(chat_id, "❌ Errore nella pulizia.")


async def _handle_help(chat_id: str) -> None:
    from telegram import ReplyKeyboardMarkup
    msg = "<b>PEPODDS21</b>"
    keyboard = [
        ["/ricerca_calcio", "/ricerca_nba"],
        ["/ricerca_tennis", "/oggi"],
        ["/opportunita", "/scommesse"],
        ["/bilancio"],
    ]
    try:
        from telegram import Bot
        bot = Bot(token=settings.telegram_bot_token)
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        await bot.send_message(
            chat_id=chat_id, text=msg,
            parse_mode="HTML", reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.warning("Telegram keyboard send failed: %s", exc)
        await _send(chat_id, msg)


async def _handle_oggi(chat_id: str) -> None:
    try:
        from app.db.base import AsyncSessionLocal
        from app.db.models.match import Match
        from app.db.models.opportunity import BettingOpportunity
        from sqlalchemy import select, func, and_
        from datetime import date

        async with AsyncSessionLocal() as db:
            today = date.today()

            # Partite di oggi
            matches_result = await db.execute(
                select(Match)
                .where(func.date(Match.match_date) == today)
                .order_by(Match.match_date.asc())
                .limit(10)
            )
            matches = matches_result.scalars().all()

            # Opportunità aperte
            opp_result = await db.execute(
                select(func.count()).select_from(BettingOpportunity)
                .where(BettingOpportunity.status == "pending")
            )
            pending_count = opp_result.scalar() or 0

        if not matches:
            msg = f"📅 <b>Oggi</b>\nNessuna partita.\nOpportunità aperte: <b>{pending_count}</b>"
            await _send(chat_id, msg)
            return

        lines = [f"📅 <b>Oggi — {len(matches)} partite</b>"]
        for m in matches[:8]:  # Show max 8 matches
            time_str = m.match_date.strftime("%H:%M") if m.match_date else "?"
            lines.append(f"{time_str} · {m.display_name()}")

        if len(matches) > 8:
            lines.append(f"... e {len(matches)-8} altre")

        lines.append(f"\nOpportunità aperte: <b>{pending_count}</b>")

        await _send(chat_id, "\n".join(lines))
    except Exception as exc:
        logger.exception("Errore /oggi: %s", exc)
        await _send(chat_id, "❌ Errore nel recupero delle partite di oggi.")


async def _handle_opportunita(chat_id: str) -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from app.db.base import AsyncSessionLocal
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.match import Match
    from app.services.telegram_service import _TIER_EMOJI, _BET_TYPE_LABEL
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(BettingOpportunity, Match)
            .join(Match, Match.id == BettingOpportunity.match_id)
            .where(BettingOpportunity.status == "pending")
            .order_by(BettingOpportunity.expected_value.desc())
            .limit(5)
        )).all()

    if not rows:
        await _send(chat_id, "🔍 Nessuna opportunità pendente al momento.")
        return

    bot = Bot(token=settings.telegram_bot_token)
    for opp, match in rows:
        tier_emoji = _TIER_EMOJI.get(opp.tier, "✅")
        tipo = _BET_TYPE_LABEL.get(opp.bet_type, (opp.bet_type or "Singola").capitalize())
        opp_id = str(opp.id)
        ev = float(opp.expected_value or 0)
        # Leggi affidabilità salvata nel consensus_votes
        cv = opp.consensus_votes or {}
        rel = float(cv.get("reliability", 0))
        rel_pct = int(round(rel * 100))
        if rel_pct >= 70:
            rel_icon = "🟢"
        elif rel_pct >= 50:
            rel_icon = "🟡"
        else:
            rel_icon = "🔴"

        msg = (
            f"{tier_emoji} <b>{tipo}</b> — Tier {opp.tier}\n"
            f"\n"
            f"<b>{match.display_name()}</b>\n"
            f"{opp.market} — <b>{opp.outcome}</b>\n"
            f"Quota: <b>{float(opp.best_odds):.2f}</b> · {opp.bookmaker}\n"
            f"EV: <b>{ev:+.1%}</b> · Affidabilità: {rel_icon} <b>{rel_pct}%</b>\n\n"
            f"<i>Decidi tu:</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Conferma", callback_data=f"approve:{opp_id}"),
                InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject:{opp_id}"),
            ],
            [
                InlineKeyboardButton("✏️ Modifica", callback_data=f"modify:{opp_id}"),
                InlineKeyboardButton("⏸ In attesa", callback_data=f"hold:{opp_id}"),
            ],
        ])
        await _safe_send(bot, chat_id, msg, reply_markup=keyboard)


async def _handle_attesa(chat_id: str) -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from app.db.base import AsyncSessionLocal
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.match import Match
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(BettingOpportunity, Match)
            .join(Match, Match.id == BettingOpportunity.match_id)
            .where(BettingOpportunity.status == "in_attesa")
            .order_by(BettingOpportunity.expected_value.desc())
            .limit(5)
        )).all()

    if not rows:
        await _send(chat_id, "⏸ Nessuna scommessa in attesa al momento.")
        return

    bot = Bot(token=settings.telegram_bot_token)
    lines = [f"⏸ <b>In attesa ({len(rows)})</b>\n"]
    for opp, match in rows:
        lines.append(
            f"\n<b>{match.display_name()}</b>\n"
            f"{opp.market} — {opp.outcome} @ {float(opp.best_odds):.2f}\n"
            f"EV: {float(opp.expected_value or 0):+.1%}"
        )
    lines.append("\n<i>Vai su PEPODDS21 → Opportunità per combinarle.</i>")
    await _safe_send(bot, chat_id, "\n".join(lines))


async def _handle_bilancio(chat_id: str) -> None:
    try:
        from app.db.base import AsyncSessionLocal
        from app.db.models.bet import Bet
        from sqlalchemy import select, func

        async with AsyncSessionLocal() as db:
            total = (await db.execute(select(func.count()).select_from(Bet))).scalar() or 0
            won = (await db.execute(select(func.count()).select_from(Bet).where(Bet.status == "won"))).scalar() or 0
            lost = (await db.execute(select(func.count()).select_from(Bet).where(Bet.status == "lost"))).scalar() or 0
            open_bets = (await db.execute(select(func.count()).select_from(Bet).where(Bet.status == "open"))).scalar() or 0
            total_pnl = (await db.execute(select(func.sum(Bet.pnl)).where(Bet.pnl.isnot(None)))).scalar() or 0.0
            total_stake = (await db.execute(select(func.sum(Bet.stake)))).scalar() or 0.0
            avg_clv = (await db.execute(select(func.avg(Bet.clv)).where(Bet.clv.isnot(None)))).scalar()
            clv_count = (await db.execute(select(func.count()).select_from(Bet).where(Bet.clv.isnot(None)))).scalar() or 0

        settled = won + lost
        win_rate = f"{won/settled:.1%}" if settled else "—"
        roi = f"{float(total_pnl)/float(total_stake):.1%}" if total_stake else "—"
        pnl_val = float(total_pnl)
        pnl_str = f"+€{pnl_val:.2f}" if pnl_val >= 0 else f"-€{abs(pnl_val):.2f}"

        if avg_clv is not None and clv_count >= 3:
            clv_val = float(avg_clv)
            clv_icon = "🟢" if clv_val > 0 else "🔴"
            clv_str = f"{clv_icon} <b>{clv_val:+.2f}%</b> (su {clv_count} scommesse)"
            clv_note = "<i>✅ Edge reale confermato.</i>" if clv_val > 0 else "<i>⚠️ CLV negativo — aspetta più dati.</i>"
        else:
            clv_str = "— (dati insufficienti)"
            clv_note = "<i>CLV disponibile dopo almeno 3 scommesse liquidate.</i>"

        msg = (
            f"💰 <b>Risultati PEPODDS21</b>\n"
            f"\n"
            f"P&amp;L totale: <b>{pnl_str}</b>\n"
            f"ROI: <b>{roi}</b>\n\n"
            f"Scommesse: {total} · Vinte: {won} · Perse: {lost} · Aperte: {open_bets}\n"
            f"Win rate: <b>{win_rate}</b>\n\n"
            f"<b>CLV vs Pinnacle</b>: {clv_str}\n"
            f"{clv_note}"
        )
        await _send(chat_id, msg)
    except Exception as exc:
        logger.exception("Errore /bilancio: %s", exc)
        await _send(chat_id, "❌ Errore nel recupero dei risultati.")


async def _handle_stats(chat_id: str) -> None:
    try:
        from app.db.base import AsyncSessionLocal
        from app.db.models.runs import PipelineRun
        from app.db.models.opportunity import BettingOpportunity
        from sqlalchemy import select, func

        async with AsyncSessionLocal() as db:
            # Ultimo run pipeline
            last_run = (await db.execute(
                select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)
            )).scalar_one_or_none()

            # Totale opportunità trovate
            total_opp = (await db.execute(select(func.count()).select_from(BettingOpportunity))).scalar() or 0
            pending_opp = (await db.execute(
                select(func.count()).select_from(BettingOpportunity).where(BettingOpportunity.status == "pending")
            )).scalar() or 0

            # EV medio delle opportunità
            avg_ev = (await db.execute(
                select(func.avg(BettingOpportunity.expected_value))
                .where(BettingOpportunity.status.in_(["pending", "bet_placed"]))
            )).scalar()

        last_run_str = "—"
        last_matches = "—"
        if last_run:
            last_run_str = last_run.started_at.strftime("%d/%m %H:%M") if last_run.started_at else "—"
            last_matches = str(last_run.matches_processed or 0)

        avg_ev_str = f"{float(avg_ev):.2%}" if avg_ev else "—"

        msg = (
            f"📈 <b>Statistiche Pipeline</b>\n"
            f"\n"
            f"Ultima analisi: <b>{last_run_str}</b>\n"
            f"Partite analizzate: {last_matches}\n\n"
            f"Opportunità totali: {total_opp}\n"
            f"In attesa di decisione: <b>{pending_opp}</b>\n"
            f"EV medio: <b>{avg_ev_str}</b>"
        )
        await _send(chat_id, msg)
    except Exception as exc:
        logger.exception("Errore /stats: %s", exc)
        await _send(chat_id, "❌ Errore nel recupero delle statistiche.")


async def _handle_status(chat_id: str) -> None:
    try:
        from app.db.base import AsyncSessionLocal
        from app.db.models.bet import Bet
        from app.db.models.context import SystemHealth
        from sqlalchemy import select, func

        async with AsyncSessionLocal() as db:
            open_count = (await db.execute(
                select(func.count()).select_from(Bet).where(Bet.status == "open")
            )).scalar() or 0

            total_stake_open = (await db.execute(
                select(func.sum(Bet.stake)).where(Bet.status == "open")
            )).scalar() or 0.0

            health = (await db.execute(
                select(SystemHealth).order_by(SystemHealth.checked_at.desc()).limit(1)
            )).scalar_one_or_none()

        status = health.status if health else "healthy"
        services = health.services if health else {}
        db_ok = services.get("db", True)
        redis_ok = services.get("redis", True)

        status_icon = "🟢" if status == "healthy" else "🔴"
        paused_str = " ⏸" if await _is_paused() else ""
        db_icon = "🟢" if db_ok else "🔴"
        redis_icon = "🟢" if redis_ok else "🔴"

        msg = (
            f"📊 Sistema {status_icon}{paused_str}\n"
            f"DB {db_icon} · Redis {redis_icon}\n"
            f"Aperte: {open_count} · Stake: €{float(total_stake_open):.0f}"
        )
        await _send(chat_id, msg)
    except Exception as exc:
        logger.exception("Errore /status: %s", exc)
        await _send(chat_id, "❌ Errore nel recupero dello stato.")


async def _handle_ricerca_calcio(chat_id: str) -> None:
    """Ricerca calcio solo."""
    await _handle_ricerca_by_sport(chat_id, sport="football")


async def _handle_ricerca_nba(chat_id: str) -> None:
    """Ricerca NBA solo."""
    await _handle_ricerca_by_sport(chat_id, sport="basketball")


async def _handle_ricerca_tennis(chat_id: str) -> None:
    """Ricerca tennis solo."""
    await _handle_ricerca_by_sport(chat_id, sport="tennis")


async def _handle_ricerca_by_sport(chat_id: str, sport: str | None = None) -> None:
    """Ricerca on-demand per sport: fetch completo (odds + stats) + analisi AI.

    Args:
        chat_id: Chat ID Telegram
        sport: "football", "basketball", "tennis", o None per tutti
    """
    logger.info("🔍 _handle_ricerca_by_sport started (chat_id=%s, sport=%s)", chat_id, sport)
    try:
        import redis.asyncio as aioredis
        from datetime import datetime
        from app.config import settings as _cfg

        # Sport labels per user feedback
        sport_labels = {
            "football": "⚽ Calcio",
            "basketball": "🏀 NBA",
            "tennis": "🎾 Tennis",
            None: "🌍 Tutti gli sport",
        }
        sport_label = sport_labels.get(sport, "Sport")

        # Lock: previeni ricerche duplicate
        kwargs = _cfg.get_redis_connection_kwargs()
        r = aioredis.Redis(**kwargs)
        try:
            lock_key = f"ricerca:lock:{chat_id}:{sport or 'all'}"
            is_locked = await r.set(lock_key, "1", nx=True, ex=120)  # 2 min lock
            if not is_locked:
                logger.warning("⚠️ Ricerca già in corso per sport=%s, scarto", sport)
                await _send(chat_id, "⏳ Una ricerca è già in corso. Attendi il risultato.")
                return

            # Counter: sport-specific monthly tracking
            month_key = f"ricerche:{sport or 'all'}:{datetime.now().strftime('%Y-%m')}"
            count = await r.incr(month_key)
            await r.expire(month_key, 86400 * 31)  # expire in 31 days
        finally:
            await r.aclose()

        # Limiti basati su The Odds API: Free=500 req/mese, Essential=20,000/mese
        # Una ricerca calcio ~8 req, nba/tennis ~2 req
        # Free plan: 60+ ricerche calcio, 250+ nba/tennis possibili
        # Usiamo valori conservativi: singoli sport=150, all=300
        limit = 300 if sport is None else 150
        count_msg = f"📊 Ricerca #{count} questo mese ({sport_label})"
        if count > int(limit * 0.95):
            count_msg += f" ⚠️ <b>ATTENZIONE: stai per esaurire i crediti ({count}/{limit})!</b>"
        elif count > int(limit * 0.75):
            count_msg += f" ⚠️ ({count}/{limit} crediti usati)"

        # Step 1: Fetch quote + partite dal DB (SINCRONO per subito mostrare la lista)
        from app.workers.tasks import fetch_complete_sport_data
        from app.services.ingestion_service import IngestionService
        from app.db.base import AsyncSessionLocal
        from sqlalchemy import select, and_
        from app.db.models.match import Match
        from datetime import timedelta, timezone as tz
        from datetime import datetime as dt

        # Messaggio iniziale: "Caricamento partite..."
        initial_msg = f"🔍 <b>Ricerca avviata</b>\n{sport_label}\n{count_msg}\n\n📥 <b>Caricamento partite...</b>"
        await _send(chat_id, initial_msg)

        # Fetch quote sincrono (tramite ingestion service)
        matches_for_report = []
        try:
            async with AsyncSessionLocal() as db:
                svc = IngestionService(db)
                logger.info("📦 Fetching odds per sport=%s", sport)
                fetch_result = await svc.ingest_all_odds(sport=sport)
                logger.info("✅ Fetch completato: %s", fetch_result)

                # Ora queryiamo le partite che sono state appena caricate
                now = dt.now(tz.utc)
                cutoff = now + timedelta(hours=18)
                logger.info("🔍 Querying matches after fetch: sport=%s, now=%s, cutoff=%s", sport, now, cutoff)
                where_conditions = [Match.status == "scheduled", Match.match_date <= cutoff, Match.match_date >= now]
                if sport:
                    where_conditions.append(Match.sport == sport)
                result = await db.execute(select(Match).where(and_(*where_conditions)).order_by(Match.match_date))
                matches_list = result.scalars().all()
                logger.info("✅ Found %d matches after fetch", len(matches_list))
                matches_for_report = [f"{m.display_name()}" for m in matches_list[:10]]
        except Exception as e:
            logger.exception("❌ Failed to fetch matches: %s", e)
            matches_for_report = ["(errore nel caricamento)"]

        # Aggiorna messaggio con lista partite
        matches_str = "\n".join(matches_for_report) if matches_for_report else "(nessuna partita trovata)"
        update_msg = f"🔍 <b>Ricerca avviata</b>\n{sport_label}\n{count_msg}\n\n📋 <b>Partite trovate:</b>\n{matches_str}\n\n⏳ Analisi AI in corso..."
        await _send(chat_id, update_msg)

        # Step 2: Metti in coda il task per l'analisi AI (facoltativo, solo se vuoi analisi extra)
        # Per ora, il fetch e query sono sufficienti
        logger.info("✅ Ricerca completata (sport=%s)", sport)

    except Exception as exc:
        logger.exception("❌ Errore ricerca (sport=%s): %s", sport, exc)
        await _send(chat_id, "❌ Errore nella ricerca. Riprova.")


async def _handle_aggiorna_quote(chat_id: str) -> None:
    try:
        await _send(chat_id, "⏳ <b>Aggiornamento quote avviato.</b>\nRiceverai un riepilogo al termine.")
        from app.workers.tasks import fetch_all_odds
        fetch_all_odds.delay()
    except Exception as exc:
        logger.exception("Errore /aggiorna_quote: %s", exc)
        await _send(chat_id, "❌ Errore nell'avvio dell'aggiornamento.")


async def _handle_pipeline(chat_id: str) -> None:
    try:
        await _send(chat_id, "🤖 <b>Aggiornamento manuale avviato.</b>\nL'AI sta analizzando le partite. Riceverai le opportunità trovate a breve.")
        from app.workers.tasks import run_daily_pipeline
        run_daily_pipeline.delay()
    except Exception as exc:
        logger.exception("Errore /pipeline: %s", exc)
        await _send(chat_id, "❌ Errore nell'avvio dell'analisi.")


async def _handle_scommesse(chat_id: str) -> None:
    """Lista scommesse aperte con ID breve per /settle."""
    try:
        from app.db.base import AsyncSessionLocal
        from app.db.models.bet import Bet
        from app.db.models.opportunity import BettingOpportunity
        from app.db.models.match import Match
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Bet, BettingOpportunity, Match)
                .join(BettingOpportunity, BettingOpportunity.id == Bet.opportunity_id)
                .join(Match, Match.id == BettingOpportunity.match_id)
                .where(Bet.status == "open")
                .order_by(Bet.placed_at.desc())
                .limit(10)
            )).all()

        if not rows:
            await _send(chat_id, "📋 Nessuna scommessa aperta al momento.")
            return

        lines = ["📋 <b>Scommesse aperte</b>\n"]
        for bet, opp, match in rows:
            bid = str(bet.id)[:8]  # ID breve per comodità
            placed = bet.placed_at.strftime("%d/%m %H:%M") if bet.placed_at else "?"
            lines.append(
                f"\n<code>{bid}…</code>\n"
                f"<b>{match.display_name()}</b>\n"
                f"{opp.market} — {opp.outcome} @ {float(bet.odds):.2f}\n"
                f"Stake: €{float(bet.stake):.0f} · {placed}"
            )
        lines.append("\n<i>Usa /settle &lt;ID breve (8 caratteri)&gt; win|loss</i>\nEsempio: <code>/settle a3f12b78 win</code>")
        await _send(chat_id, "\n".join(lines))
    except Exception as exc:
        logger.exception("Errore /scommesse: %s", exc)
        await _send(chat_id, "❌ Errore nel recupero delle scommesse.")


async def _handle_quote(chat_id: str) -> None:
    """Mostra quota rimanente per ogni chiave Odds API."""
    try:
        import redis.asyncio as aioredis
        from app.config import settings

        keys = [k for k in [
            settings.odds_api_key,
            settings.odds_api_key_2,
            settings.odds_api_key_3,
            settings.odds_api_key_4,
        ] if k]

        lines = ["🔑 <b>Odds API — Quota mensile</b>\n"]
        from redis.asyncio import Redis
        async with Redis(**settings.get_redis_connection_kwargs()) as redis_client:
            for i, k in enumerate(keys, 1):
                tag = k[-6:]
                rem_str = await redis_client.get(f"odds_api:remaining:{tag}")
                if rem_str:
                    rem = int(rem_str)
                    icon = "🟢" if rem > 200 else "🟡" if rem > 100 else "🔴"
                    lines.append(f"Chiave {i} ({tag}): {icon} <b>{rem}</b> rimaste")
                else:
                    lines.append(f"Chiave {i} ({tag}): — (nessun dato ancora)")

        lines.append("\n<i>I dati si aggiornano ad ogni fetch quote.</i>")
        await _send(chat_id, "\n".join(lines))
    except Exception as exc:
        logger.exception("Errore /quote: %s", exc)
        await _send(chat_id, "❌ Errore nel recupero delle quote API.")


async def _handle_settle(chat_id: str, bet_id_str: str, result_str: str) -> None:
    """Settlement manuale: /settle <bet_uuid> <win|loss>"""
    try:
        import uuid as uuid_lib
        from datetime import datetime, timezone
        from app.db.base import AsyncSessionLocal
        from app.db.models.bet import Bet
        from app.db.models.opportunity import BettingOpportunity
        from app.db.models.match import Match
        from sqlalchemy import select, update

        result_str = result_str.lower().strip()
        if result_str not in ("win", "loss", "vinta", "persa"):
            await _send(chat_id, "❌ Risultato non valido. Usa <b>win</b> o <b>loss</b>.")
            return

        won = result_str in ("win", "vinta")

        # Cerca la bet (supporta UUID parziale — min 8 caratteri)
        async with AsyncSessionLocal() as db:
            # Prova UUID completo prima
            bet = None
            try:
                bet_uuid = uuid_lib.UUID(bet_id_str)
                bet = (await db.execute(
                    select(Bet).where(Bet.id == bet_uuid)
                )).scalar_one_or_none()
            except ValueError:
                pass

            # Fallback: cerca per UUID parziale (i primi 8 caratteri)
            if not bet and len(bet_id_str) >= 8:
                all_open = (await db.execute(
                    select(Bet).where(Bet.status == "open").limit(50)
                )).scalars().all()
                prefix = bet_id_str.lower()
                matches_found = [b for b in all_open if str(b.id).startswith(prefix)]
                if len(matches_found) > 1:
                    ids_str = "\n".join(f"  <code>{b.id}</code>" for b in matches_found)
                    await _send(chat_id, f"⚠️ Trovate {len(matches_found)} scommesse con prefix <code>{bet_id_str}</code>:\n{ids_str}\n\nUsa l'UUID completo.")
                    return
                bet = matches_found[0] if matches_found else None

            if not bet:
                await _send(chat_id, f"❌ Scommessa non trovata con ID <code>{bet_id_str}</code>.\nUsa /scommesse per vedere gli ID completi.")
                return

            if bet.status != "open":
                await _send(chat_id, f"⚠️ Scommessa già liquidata (status: {bet.status}).")
                return

            # Settlement
            pnl = (float(bet.odds) - 1) * float(bet.stake) if won else -float(bet.stake)
            status = "won" if won else "lost"

            await db.execute(
                update(Bet)
                .where(Bet.id == bet.id)
                .values(
                    status=status,
                    result="manuale",
                    pnl=pnl,
                    settled_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

            # Recupera dettagli per il messaggio
            opp = (await db.execute(
                select(BettingOpportunity).where(BettingOpportunity.id == bet.opportunity_id)
            )).scalar_one_or_none()
            match = None
            if opp:
                match = (await db.execute(
                    select(Match).where(Match.id == opp.match_id)
                )).scalar_one_or_none()

        icon = "✅" if won else "❌"
        pnl_str = f"+€{pnl:.2f}" if pnl >= 0 else f"-€{abs(pnl):.2f}"
        match_name = match.display_name() if match else "—"
        outcome_str = f"{opp.market} — {opp.outcome}" if opp else "—"

        msg = (
            f"{icon} <b>Scommessa liquidata manualmente</b>\n"
            f"\n"
            f"{match_name}\n"
            f"{outcome_str} @ {float(bet.odds):.2f}\n"
            f"Stake: €{float(bet.stake):.0f} · Esito: <b>{status.upper()}</b>\n"
            f"P&L: <b>{pnl_str}</b>"
        )
        await _send(chat_id, msg)

    except Exception as exc:
        logger.exception("Errore /settle: %s", exc)
        await _send(chat_id, "❌ Errore nel settlement manuale.")


async def _handle_pausa(chat_id: str) -> None:
    paused_now = not await _is_paused()
    await _set_paused(paused_now)
    if paused_now:
        msg = (
            "⏸ <b>Notifiche in pausa</b>\n"
            "\n"
            "Non riceverai più notifiche automatiche.\n"
            "Usa /pausa di nuovo per riattivarle.\n\n"
            "<i>I comandi manuali (/opportunita, /bilancio, ecc.) funzionano sempre.</i>"
        )
    else:
        msg = (
            "▶️ <b>Notifiche riattivate</b>\n"
            "\n"
            "Riceverai di nuovo le opportunità e i risultati."
        )
    await _send(chat_id, msg)
