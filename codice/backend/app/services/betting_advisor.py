"""
BettingAdvisor — decide come strutturare ogni giocata.

Ragiona come uno scommettitore professionista:
  - Singola: confidenza > 70%, EV > 5% → massimizza l'edge con bassa varianza
  - Doppia:  2 pick da 62-70% → quota ~2.00-2.50, EV combinato > 3%
  - Tripla:  3 pick da 60-65% → quota ~3.00-4.00, solo se sport/match diversi
  - Scalata: 2-3 pick soft (1.35-1.75 ognuno) → incrementale, bassa varianza

Principio: MAI combinare solo per creare una quota grande.
La combinazione si giustifica solo se l'EV congiunto supera l'EV individuale
E la confidenza è abbastanza alta da reggere la varianza.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Soglie decisionali ────────────────────────────────────────────────────────
SINGOLA_CONFIDENCE_MIN   = 0.68   # > 68% hit rate o fair prob → singola forte
SINGOLA_EV_MIN           = 0.04   # EV > 4% → singola diretta
DOPPIA_CONFIDENCE_MIN    = 0.62   # ogni pick > 62% per doppia
DOPPIA_EV_MIN            = 0.025  # EV congiunto > 2.5% per doppia
TRIPLA_CONFIDENCE_MIN    = 0.60   # ogni pick > 60% per tripla
TRIPLA_EV_MIN            = 0.015  # EV congiunto > 1.5% per tripla
SCALATA_CONFIDENCE_MIN   = 0.58   # ogni step > 58% per scalata soft
SCALATA_EV_MIN           = 0.0    # basta che sia > 0

# Kelly conservativo (frazione kelly / 4)
KELLY_DIVISOR = 4.0
MAX_STAKE_PCT  = 0.025   # mai più del 2.5% del bankroll


@dataclass
class BettingDecision:
    """
    La decisione ottimale di come giocare un insieme di giocate disponibili.
    """
    bet_type: str            # "singola" | "doppia" | "tripla" | "scalata" | "skip"
    steps: list[dict]        # ogni step: {description, match_name, odds, confidence, reasoning, sport}
    combined_odds: float
    joint_confidence: float
    expected_value: float
    recommended_stake_pct: float  # % del bankroll (Kelly conservativo)
    reasoning: str           # spiegazione in italiano del perché questa struttura


class BettingAdvisor:
    """
    Riceve una lista di step candidati (con confidenza e EV) e decide
    la struttura ottimale: singola, doppia, tripla, o scalata.
    """

    def decide(self, candidates: list[dict]) -> BettingDecision:
        """
        Analizza i candidati e ritorna la decisione migliore.

        Args:
            candidates: lista di dict con:
                - description, match_name, sport, direction
                - best_odds (float), confidence (float 0-1), ev (float)
                - reasoning (str)
        """
        if not candidates:
            return self._skip("Nessun candidato disponibile.")

        # Ordina per confidenza × ev (edge ponderato)
        scored = sorted(
            candidates,
            key=lambda c: c.get("confidence", 0) * (c.get("best_odds", 1) - 1),
            reverse=True
        )

        # Prova in ordine di preferenza: singola → doppia → tripla → scalata
        decision = (
            self._try_singola(scored)
            or self._try_doppia(scored)
            or self._try_tripla(scored)
            or self._try_scalata(scored)
            or self._skip("Nessuna struttura soddisfa i requisiti minimi.")
        )

        logger.info(
            "BettingAdvisor: %s | odds %.2f | EV %.1f%% | confidence %.0f%%",
            decision.bet_type, decision.combined_odds,
            decision.expected_value * 100, decision.joint_confidence * 100
        )
        return decision

    # ── Singola ───────────────────────────────────────────────────────────────

    def _try_singola(self, candidates: list[dict]) -> BettingDecision | None:
        """Singola forte: confidenza > 68%, EV > 4%."""
        for c in candidates[:3]:
            conf = c.get("confidence", 0)
            odds = c.get("best_odds", 1.0)
            ev   = c.get("ev", conf * odds - 1)
            if conf >= SINGOLA_CONFIDENCE_MIN and ev >= SINGOLA_EV_MIN:
                stake = self._kelly_stake(conf, odds)
                return BettingDecision(
                    bet_type="singola",
                    steps=[c],
                    combined_odds=round(odds, 2),
                    joint_confidence=round(conf, 3),
                    expected_value=round(ev, 3),
                    recommended_stake_pct=stake,
                    reasoning=(
                        f"Confidenza alta ({round(conf*100)}%) con EV {round(ev*100, 1)}%. "
                        f"Giocata in singola per massimizzare l'edge riducendo la varianza."
                    ),
                )
        return None

    # ── Doppia ────────────────────────────────────────────────────────────────

    def _try_doppia(self, candidates: list[dict]) -> BettingDecision | None:
        """Doppia: 2 pick con confidenza > 62%, match diversi, EV congiunto > 2.5%."""
        eligible = [c for c in candidates if c.get("confidence", 0) >= DOPPIA_CONFIDENCE_MIN]
        if len(eligible) < 2:
            return None

        # Prendi i 2 migliori da match diversi
        pair = self._pick_diverse(eligible, n=2)
        if not pair:
            return None

        combined_odds = pair[0]["best_odds"] * pair[1]["best_odds"]
        joint_conf    = pair[0]["confidence"] * pair[1]["confidence"]
        ev            = joint_conf * combined_odds - 1.0

        if ev < DOPPIA_EV_MIN:
            return None

        stake = self._kelly_stake(joint_conf, combined_odds)
        sport_mix = len({p.get("sport", "?") for p in pair}) > 1
        sport_note = "multi-sport" if sport_mix else pair[0].get("sport", "")

        return BettingDecision(
            bet_type="doppia",
            steps=pair,
            combined_odds=round(combined_odds, 2),
            joint_confidence=round(joint_conf, 3),
            expected_value=round(ev, 3),
            recommended_stake_pct=stake,
            reasoning=(
                f"Due pick con confidenza {round(pair[0]['confidence']*100)}% e "
                f"{round(pair[1]['confidence']*100)}% ({sport_note}). "
                f"Quota combinata {round(combined_odds, 2)} con EV {round(ev*100, 1)}%. "
                f"La combinazione amplifica il margine senza compromettere eccessivamente la prob. di successo."
            ),
        )

    # ── Tripla ────────────────────────────────────────────────────────────────

    def _try_tripla(self, candidates: list[dict]) -> BettingDecision | None:
        """Tripla: 3 pick con confidenza > 60%, match diversi, EV congiunto > 1.5%."""
        eligible = [c for c in candidates if c.get("confidence", 0) >= TRIPLA_CONFIDENCE_MIN]
        if len(eligible) < 3:
            return None

        trio = self._pick_diverse(eligible, n=3)
        if not trio:
            return None

        combined_odds = trio[0]["best_odds"] * trio[1]["best_odds"] * trio[2]["best_odds"]
        joint_conf    = trio[0]["confidence"] * trio[1]["confidence"] * trio[2]["confidence"]
        ev            = joint_conf * combined_odds - 1.0

        if ev < TRIPLA_EV_MIN:
            return None

        stake = self._kelly_stake(joint_conf, combined_odds) * 0.7  # riduzione per tripla

        return BettingDecision(
            bet_type="tripla",
            steps=trio,
            combined_odds=round(combined_odds, 2),
            joint_confidence=round(joint_conf, 3),
            expected_value=round(ev, 3),
            recommended_stake_pct=stake,
            reasoning=(
                f"Tre pick da match diversi ({', '.join(p.get('sport','?') for p in trio)}). "
                f"Quota {round(combined_odds, 2)}, prob congiunta {round(joint_conf*100)}%, "
                f"EV {round(ev*100, 1)}%. Struttura più rischiosa ma giustificata dalla coerenza dei segnali."
            ),
        )

    # ── Scalata soft ──────────────────────────────────────────────────────────

    def _try_scalata(self, candidates: list[dict]) -> BettingDecision | None:
        """Scalata: 2-3 step soft (odds 1.35-1.75 ognuno), confidenza > 58%."""
        soft = [
            c for c in candidates
            if (SCALATA_CONFIDENCE_MIN <= c.get("confidence", 0)
                and 1.30 <= c.get("best_odds", 0) <= 1.80)
        ]
        if len(soft) < 2:
            return None

        steps = self._pick_diverse(soft, n=min(3, len(soft)))
        if len(steps) < 2:
            return None

        combined_odds = 1.0
        joint_conf    = 1.0
        for s in steps:
            combined_odds *= s["best_odds"]
            joint_conf    *= s["confidence"]

        ev = joint_conf * combined_odds - 1.0
        stake = self._kelly_stake(joint_conf, combined_odds) * 0.5  # ultra conservativo

        return BettingDecision(
            bet_type="scalata",
            steps=steps,
            combined_odds=round(combined_odds, 2),
            joint_confidence=round(joint_conf, 3),
            expected_value=round(ev, 3),
            recommended_stake_pct=stake,
            reasoning=(
                f"Scalata {len(steps)} step soft (odds {' × '.join(str(round(s['best_odds'],2)) for s in steps)} "
                f"= {round(combined_odds, 2)}). "
                f"Prob congiunta {round(joint_conf*100)}%, bassa varianza grazie a quote < 1.80. "
                + (f"EV: {round(ev*100, 1)}%." if ev > 0 else "Marginalmente positivo — giocare con importo minimo.")
            ),
        )

    # ── Skip ─────────────────────────────────────────────────────────────────

    def _skip(self, reason: str) -> BettingDecision:
        return BettingDecision(
            bet_type="skip",
            steps=[],
            combined_odds=1.0,
            joint_confidence=0.0,
            expected_value=0.0,
            recommended_stake_pct=0.0,
            reasoning=reason,
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    def _pick_diverse(self, candidates: list[dict], n: int) -> list[dict]:
        """Prendi N candidati assicurandoti che vengano da match diversi."""
        picked: list[dict] = []
        used_matches: set[str] = set()
        for c in candidates:
            match = c.get("match_name", str(id(c)))
            if match not in used_matches:
                picked.append(c)
                used_matches.add(match)
            if len(picked) >= n:
                break
        return picked if len(picked) >= n else []

    def _kelly_stake(self, prob: float, odds: float) -> float:
        """Kelly fraction conservativo (÷ 4), cap al 2.5%."""
        if odds <= 1.0 or prob <= 0:
            return 0.005
        b = odds - 1.0
        q = 1.0 - prob
        kelly = (prob * b - q) / b
        conservative = max(0, kelly / KELLY_DIVISOR)
        return round(min(conservative, MAX_STAKE_PCT), 4)


def format_decision_telegram(decision: BettingDecision) -> str:
    """Formatta una BettingDecision per Telegram."""
    type_emoji = {
        "singola": "[SINGOLA]",
        "doppia":  "[DOPPIA]",
        "tripla":  "[TRIPLA]",
        "scalata": "[SCALATA]",
        "skip":    "[SKIP]",
    }
    sport_icon = {
        "basketball": "[NBA]",
        "football":   "[Calcio]",
        "tennis":     "[Tennis]",
    }
    lines: list[str] = []

    if decision.bet_type == "skip":
        return f"[SKIP] {decision.reasoning}"

    tag = type_emoji.get(decision.bet_type, decision.bet_type.upper())
    lines.append(f"{tag} — Quota {decision.combined_odds:.2f}")
    lines.append(
        f"Prob successo: {round(decision.joint_confidence*100)}% | "
        f"EV: {decision.expected_value*100:+.1f}%"
    )
    lines.append(f"Stake consigliato: {round(decision.recommended_stake_pct*100, 1)}% bankroll")
    lines.append("")

    for i, step in enumerate(decision.steps, 1):
        icon = sport_icon.get(step.get("sport", ""), "")
        lines.append(f"Step {i} {icon}: {step.get('description', '?')}")
        lines.append(f"  Partita: {step.get('match_name', '?')}")
        lines.append(f"  Quota: {step.get('best_odds', '?')} @{step.get('best_bookmaker', '?')}")
        lines.append(f"  {step.get('reasoning', '')}")
        lines.append("")

    lines.append(f"Ragionamento: {decision.reasoning}")
    return "\n".join(lines)
