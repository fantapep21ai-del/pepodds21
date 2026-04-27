"""
SynthesisAgent — integra i 7 specialist in una narrativa coerente.

Riceve:
  - Segnali di StatsAgent, FormAgent, H2HAgent, InjuryAgent, NewsAgent, OddsAgent, WeatherAgent
  - Scores di incertezza per ogni agente
  - Match context (squadre, competition, date)

Output:
  - Narrativa unificata (2-3 paragrafi in italiano)
  - Confidence finale (0-1)
  - Key factors (lista di fattori principali)
  - Contradictions (se gli agenti contraddicono)
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from anthropic import Anthropic

if TYPE_CHECKING:
    from app.agents.base import AgentResult

logger = logging.getLogger(__name__)


class SynthesisAgent:
    """
    Sintetizza i 7 specialist in una narrativa narrativa coerente.
    Usa Haiku (economico) per aggregare i segnali.
    """

    def __init__(self):
        self.client = Anthropic()
        self.model = "claude-haiku-4-5-20251001"

    async def synthesize(
        self,
        match_name: str,
        competition: str,
        sport: str,
        specialist_results: dict[str, AgentResult],
        uncertainty_score: float,
        no_vig_prob: float,
        best_odds: float,
        expected_value: float,
    ) -> dict:
        """
        Sintetizza i segnali dei 7 specialist in una narrativa coerente.

        Input:
          specialist_results: {agent_name: AgentResult}
            where AgentResult = {
              "signal": float (0-1),
              "probabilities": dict,
              "confidence": float,
              "reasoning": str,
            }

        Output:
          {
            "narrative": str,  # 2-3 paragrafi
            "confidence": float,  # 0-1
            "key_factors": [str],  # lista di fattori principali
            "contradictions": [
              {"specialist": str, "issue": str}
            ],
            "recommendation": str,  # "Gioca" | "Cauzione" | "Salta"
          }
        """
        # Prepara i dati per il prompt
        specialist_summary = self._format_specialist_results(specialist_results)

        prompt = f"""Tu sei un analista sportivo professionista con 15 anni di esperienza.
Hai davanti 7 analisti specializzati che hanno studiato una partita.
Il tuo compito: integrarli in una conclusione UNICA, onesta, e comprensibile per uno scommettitore intelligente.

MATCH: {match_name}
COMPETIZIONE: {competition}
SPORT: {sport}

ANALISI MATEMATICA (gold standard):
- Probabilità no-vig Pinnacle: {round(no_vig_prob*100, 1)}%
- Miglior quota: {best_odds}
- EV atteso: {expected_value:+.1%}
- Incertezza generale: {uncertainty_score:.2f}/1.00

SEGNALI DEGLI SPECIALISTI:
{specialist_summary}

---

COMPITO TUO (ESSENZIALE):

1. INTEGRA I SEGNALI: se gli specialisti concordano, rinforza. Se contraddicono, spiega il perché
   Esempio: "StatsAgent vede data che supporta over, ma WeatherAgent dice che vento forte riduce gol"

2. DECIDI I PESI: per QUESTO match, quale specialista è più affidabile?
   Esempio: "In derby, H2H è sempre più predittivo di stats puri"

3. CONTRADDIZIONI: se 2+ specialisti contraddicono, SEGNALA. Non nascondere.

4. NARRATIVA: scrivi 2-3 paragrafi che uno scommettitore medio capisce.
   - Paragrafo 1: fattori quantitativi (stats, form)
   - Paragrafo 2: fattori soft (infortuni, notizie, meteo)
   - Paragrafo 3: conclusione + livello di fiducia

5. RECOMMENDATION: basato su tutto
   - "Gioca con fiducia" (EV alto + concordanza agenti)
   - "Gioca con cautela" (EV ok ma contradizioni)
   - "Salta" (contradizioni forti o incertezza alta)

OUTPUT JSON (RIGIDO):
{{
  "narrative": "Paragrafo 1...\n\nParagrafo 2...\n\nParagrafo 3...",
  "confidence": 0.75,
  "key_factors": ["Forma inter solida", "Milan difesa fragile", "Meteo neutro"],
  "contradictions": [
    {{"specialist": "h2h", "issue": "H2H storico favora draw, ma form recente spinge over"}}
  ],
  "recommendation": "Gioca con fiducia"
}}
"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=800,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            # Estrai il JSON dal response
            text = response.content[0].text
            # Prova a parsare JSON — potrebbe essere in un blocco markdown
            json_str = text
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0]

            result = json.loads(json_str.strip())

            # Valida che confidence è 0-1
            conf = result.get("confidence", 0.5)
            result["confidence"] = max(0.0, min(1.0, float(conf)))

            logger.info(
                "SynthesisAgent complete: %s — confidence=%.2f",
                match_name,
                result["confidence"],
            )
            return result

        except json.JSONDecodeError as e:
            logger.error("SynthesisAgent JSON parse error: %s", e)
            # Fallback: ritorna risposta conservativa
            return {
                "narrative": "Analisi incompleta — rivedi manualmente.",
                "confidence": 0.30,
                "key_factors": [],
                "contradictions": [],
                "recommendation": "Cauzione",
            }

    def _format_specialist_results(self, results: dict[str, AgentResult]) -> str:
        """Formatta i risultati degli specialist per il prompt."""
        lines = []
        for agent_name, result in results.items():
            signal = result.get("signal", 0.5)
            confidence = result.get("confidence", 0.5)
            reasoning = result.get("reasoning", "N/A")

            lines.append(
                f"• {agent_name.upper()}: segnale {signal:.2f} | fiducia {confidence:.0%}\n"
                f"  Reasoning: {reasoning}"
            )

        return "\n".join(lines)


# Entry point per uso nella pipeline
async def synthesize_agent_results(
    match_name: str,
    competition: str,
    sport: str,
    specialist_results: dict[str, AgentResult],
    uncertainty_score: float,
    no_vig_prob: float,
    best_odds: float,
    expected_value: float,
) -> dict:
    """Wrapper async per SynthesisAgent."""
    agent = SynthesisAgent()
    return await agent.synthesize(
        match_name=match_name,
        competition=competition,
        sport=sport,
        specialist_results=specialist_results,
        uncertainty_score=uncertainty_score,
        no_vig_prob=no_vig_prob,
        best_odds=best_odds,
        expected_value=expected_value,
    )
