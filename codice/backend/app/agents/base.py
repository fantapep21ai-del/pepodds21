"""
BaseAgent — every agent inherits from this.

Each agent:
  1. Receives a match + pre-fetched context dict
  2. Builds a sport-specific system prompt
  3. Calls Claude with structured tool use
  4. Returns a list of AgentVote objects

Tool calling pattern:
  We give Claude one tool: `submit_probability_estimate`
  Claude MUST call it to return its answer — no free-text extraction.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Tool that every agent uses to return structured output
SUBMIT_TOOL = {
    "name": "submit_probability_estimate",
    "description": (
        "Submit your probability estimates for match outcomes. "
        "Call this exactly once with all your estimates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "estimates": {
                "type": "array",
                "description": "List of probability estimates",
                "items": {
                    "type": "object",
                    "properties": {
                        "market": {
                            "type": "string",
                            "description": "Market type: h2h | totals | spreads",
                        },
                        "outcome": {
                            "type": "string",
                            "description": "Outcome label (e.g. 'Inter', 'Draw', 'Over 2.5')",
                        },
                        "probability": {
                            "type": "number",
                            "description": "Probability 0.0–1.0 that this outcome occurs",
                        },
                        "confidence": {
                            "type": "number",
                            "description": (
                                "Your confidence in this estimate 0.0–1.0. "
                                "Use low confidence when data is scarce."
                            ),
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "1-2 sentences explaining your reasoning",
                        },
                    },
                    "required": ["market", "outcome", "probability", "confidence", "reasoning"],
                },
            },
        },
        "required": ["estimates"],
    },
}


class AgentResult:
    """Structured output from one agent run."""

    __slots__ = (
        "agent_name", "estimates", "reasoning", "tokens_used",
        "duration_ms", "error",
    )

    def __init__(
        self,
        agent_name: str,
        estimates: list[dict],
        reasoning: str = "",
        tokens_used: int = 0,
        duration_ms: int = 0,
        error: str | None = None,
    ):
        self.agent_name = agent_name
        self.estimates = estimates      # list of {market, outcome, probability, confidence, reasoning}
        self.reasoning = reasoning
        self.tokens_used = tokens_used
        self.duration_ms = duration_ms
        self.error = error

    @property
    def failed(self) -> bool:
        return self.error is not None


class BaseAgent(ABC):
    """
    Abstract base for all sport-analysis agents.

    Subclasses must implement:
      - name: str  (class attribute)
      - system_prompt(match_context) -> str
      - user_prompt(match_context) -> str
    """

    name: str = "base"

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @abstractmethod
    def system_prompt(self, ctx: dict[str, Any]) -> str:
        """Return the system prompt for this agent given match context."""
        ...

    @abstractmethod
    def user_prompt(self, ctx: dict[str, Any]) -> str:
        """Return the user prompt (the actual analysis request)."""
        ...

    async def run(self, ctx: dict[str, Any]) -> AgentResult:
        """
        Execute the agent. Returns AgentResult (never raises — errors captured).
        ctx keys:
          match_name, sport, home_team, away_team, match_date,
          competition, odds, stats, injuries, h2h, news, weather, ...
        """
        t0 = time.monotonic()
        try:
            result = await self._call_claude(ctx)
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            return result
        except Exception as exc:
            logger.error("[%s] Agent run failed: %s", self.name, exc, exc_info=True)
            return AgentResult(
                agent_name=self.name,
                estimates=[],
                error=str(exc),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    async def _call_claude(self, ctx: dict[str, Any]) -> AgentResult:
        import json, re
        system = self.system_prompt(ctx)
        user = self.user_prompt(ctx)

        json_instruction = (
            "\n\nRispondi ESCLUSIVAMENTE con un oggetto JSON valido nel formato:\n"
            '{"estimates": [{"market": "h2h", "outcome": "Home", "probability": 0.55, '
            '"confidence": 0.7, "reasoning": "breve spiegazione"}]}\n'
            "Non aggiungere testo fuori dal JSON."
        )

        response = await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=400,
            system=[
                {
                    "type": "text",
                    "text": system + json_instruction,
                    "cache_control": {"type": "ephemeral"},  # prompt caching: 10% costo sui token già visti
                }
            ],
            messages=[{"role": "user", "content": user}],
        )

        # Con prompt caching attivo, input_tokens copre solo i token non-cached.
        # cache_creation_input_tokens = token scritti in cache (prima chiamata, costo normale)
        # cache_read_input_tokens = token letti da cache (costo 10%) — altrimenti assenti
        cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        tokens = response.usage.input_tokens + response.usage.output_tokens + cache_creation + cache_read
        raw_text = "".join(b.text for b in response.content if hasattr(b, "text"))

        estimates: list[dict] = []
        try:
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                estimates = parsed.get("estimates", [])
        except Exception as e:
            logger.warning("[%s] JSON parse failed: %s", self.name, e)

        for est in estimates:
            est["probability"] = max(0.01, min(0.99, float(est.get("probability", 0.5))))
            est["confidence"] = max(0.0, min(1.0, float(est.get("confidence", 0.5))))

        return AgentResult(
            agent_name=self.name,
            estimates=estimates,
            reasoning=raw_text,
            tokens_used=tokens,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _format_odds(odds_list: list[dict]) -> str:
        if not odds_list:
            return "No odds available."
        lines = []
        for o in odds_list[:20]:   # cap to avoid giant prompts
            lines.append(
                f"  {o['bookmaker']} | {o['market']} | {o['outcome']}: {o['odds']:.2f}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_stats(stats: dict | None) -> str:
        if not stats:
            return "No stats available."
        import json
        return json.dumps(stats, indent=2, default=str)[:2000]  # truncate for token safety
