"""
8 specialized agents — one file for clarity.

Tutti e 8 girano in parallelo nella pipeline principale (pipeline.py).
Ogni agente si concentra su un segnale specifico e ritorna stime di probabilità
per h2h e/o totals. Il loro segnale combinato modula l'affidabilità della giocata.

  1-7. Specialist (condizionali ai dati disponibili):
       Stats, Odds, Form, H2H, Injury, News, Weather
  8.   UncertaintyAgent — gate qualitativo finale (blocca se score ≥ 0.70)
"""
from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent


# ─────────────────────────────────────────────────────────────────────────────
# 1. StatsAgent — quantitative stats (xG, possession, shots, SoT)
# ─────────────────────────────────────────────────────────────────────────────

class StatsAgent(BaseAgent):
    name = "stats"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a quantitative sports analyst specialising in match statistics. "
            "You evaluate teams based on advanced metrics: xG (expected goals), "
            "shots on target, possession, pressing intensity, defensive lines. "
            "You convert statistical edges into calibrated win probabilities. "
            "Be rigorous — don't overfit to small samples."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        return f"""Analyse this match and estimate win probabilities.

Match: {ctx.get('match_name')}
Competition: {ctx.get('competition')}
Date: {ctx.get('match_date')}

MATCH STATISTICS:
{self._format_stats(ctx.get('stats'))}

CURRENT ODDS (market implied probabilities for calibration):
{self._format_odds(ctx.get('odds', []))}

Based on the statistical evidence, submit your probability estimates for:
- h2h: home win, away win, draw
- totals: over 2.5, under 2.5 (if data supports it)

Be conservative. If stats are missing or unreliable, lower your confidence."""


# ─────────────────────────────────────────────────────────────────────────────
# 2. OddsAgent — odds movement and market efficiency analysis
# ─────────────────────────────────────────────────────────────────────────────

class OddsAgent(BaseAgent):
    name = "odds"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a professional odds analyst and market efficiency expert. "
            "You identify value by comparing bookmaker odds across markets, "
            "detecting line movement, steam moves, and market inefficiencies. "
            "You convert decimal odds to implied probabilities and adjust for the "
            "bookmaker's overround (vig). Sharp money moves lines — you look for divergence."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        # Build best-of-market per outcome
        odds = ctx.get('odds', [])
        h2h_odds = [o for o in odds if o.get('market') == 'h2h']
        totals_odds = [o for o in odds if o.get('market') == 'totals']

        return f"""Analyse the odds markets for this match.

Match: {ctx.get('match_name')}
Competition: {ctx.get('competition')}

H2H ODDS (all bookmakers):
{self._format_odds(h2h_odds)}

TOTALS ODDS:
{self._format_odds(totals_odds)}

Tasks:
1. Remove the vig and compute true implied probabilities per outcome.
2. Identify the best available odds and whether they represent value.
3. Flag any significant line discrepancies between bookmakers (soft lines).

Submit your probability estimates reflecting your market analysis."""


# ─────────────────────────────────────────────────────────────────────────────
# 3. FormAgent — recent form, momentum, streaks
# ─────────────────────────────────────────────────────────────────────────────

class FormAgent(BaseAgent):
    name = "form"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a sports analyst specialising in team form and momentum. "
            "You evaluate last 5-10 results, goal differences, home/away splits, "
            "winning/losing streaks, fatigue from congested fixtures, and "
            "psychological momentum. Form is more predictive than reputation."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        return f"""Evaluate the recent form of both teams.

Match: {ctx.get('match_name')}
Competition: {ctx.get('competition')}
Home advantage factor: {'yes — home stadium' if ctx.get('is_home_game', True) else 'neutral venue'}

RECENT FORM DATA:
{self._format_stats(ctx.get('form_stats'))}

STANDINGS / LEAGUE TABLE:
{self._format_stats(ctx.get('standings'))}

Consider:
- Last 5 results for each team (home/away split)
- Goals scored/conceded trend
- Any fixture congestion or rotation risk

Submit probability estimates for h2h outcomes."""


# ─────────────────────────────────────────────────────────────────────────────
# 4. H2HAgent — head-to-head history
# ─────────────────────────────────────────────────────────────────────────────

class H2HAgent(BaseAgent):
    name = "h2h"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a sports analyst specialising in head-to-head (H2H) analysis. "
            "You identify psychological and tactical patterns between specific opponents. "
            "Some teams consistently underperform or overperform against certain opponents "
            "regardless of form. Recent H2H (last 3 years) is weighted more than older results. "
            "Be aware of squad changes that might make old H2H less relevant."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        return f"""Analyse the head-to-head history for this fixture.

Match: {ctx.get('match_name')}

HEAD TO HEAD HISTORY (last 10 meetings):
{self._format_stats(ctx.get('h2h'))}

Consider:
- Win/draw/loss rates for each team in this specific matchup
- Home vs away patterns in H2H
- How recent are these meetings? (Older than 2 years = lower weight)
- Have the squads/managers changed significantly?

Submit h2h probability estimates. Reduce confidence if H2H history is sparse (<5 games)."""


# ─────────────────────────────────────────────────────────────────────────────
# 5. InjuryAgent — injury and suspension analysis
# ─────────────────────────────────────────────────────────────────────────────

class InjuryAgent(BaseAgent):
    name = "injury"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a sports injury analyst. You assess the impact of player "
            "absences (injuries, suspensions, international duty) on match probabilities. "
            "Key players missing (strikers, key playmakers, starting goalkeeper) "
            "have a significant impact. Squad depth matters — top teams absorb absences better. "
            "Be quantitative: a top striker missing shifts win probability by ~5-15%."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        return f"""Assess the injury and suspension situation.

Match: {ctx.get('match_name')}

INJURY / SUSPENSION REPORT:
{self._format_stats(ctx.get('injuries'))}

NEWS (relevant player status updates):
{ctx.get('news_summary', 'No recent news available.')}

Instructions:
1. Identify the most impactful absences for each team.
2. Estimate the probability shift caused by these absences.
3. If no injury data available, set confidence to 0.3.

Submit your adjusted h2h probability estimates."""


# ─────────────────────────────────────────────────────────────────────────────
# 6. NewsAgent — qualitative news, motivation, tactical intel
# ─────────────────────────────────────────────────────────────────────────────

class NewsAgent(BaseAgent):
    name = "news"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a sports intelligence analyst. You evaluate qualitative factors "
            "not captured in statistics: managerial pressure, internal conflicts, "
            "motivation (must-win, dead rubber, cup distraction), derby atmosphere, "
            "referee assignments, pitch conditions, and travel fatigue. "
            "These soft factors rarely move probabilities more than 5-10% but are "
            "systematically under-priced by bookmakers."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        return f"""Analyse qualitative factors for this match.

Match: {ctx.get('match_name')}
Competition: {ctx.get('competition')}
Match date: {ctx.get('match_date')}

RECENT NEWS:
{ctx.get('news_summary', 'No recent news available.')}

CONTEXTUAL FACTORS:
- Is this a high-stakes match (title race, relegation, cup)? {ctx.get('high_stakes', 'unknown')}
- Is either team playing multiple games this week? {ctx.get('fixture_congestion', 'unknown')}

Analyse motivation, psychological state, and any tactical intel from press conferences.
Submit h2h probability estimates. Use confidence ≤ 0.4 if news data is sparse."""


# ─────────────────────────────────────────────────────────────────────────────
# 7. WeatherAgent — weather impact on outdoor sports
# ─────────────────────────────────────────────────────────────────────────────

class WeatherAgent(BaseAgent):
    name = "weather"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        return (
            "You are a sports environmental analyst. You assess how weather conditions "
            "affect match outcomes and goal totals. Heavy rain, strong wind, and extreme "
            "temperatures reduce total goals, favour physical over technical teams, and "
            "help home teams who are acclimatised. Indoor sports (basketball arenas) are "
            "unaffected. Moderate conditions have minimal impact."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        weather = ctx.get('weather')
        sport = ctx.get('sport', 'football')

        if sport in ('basketball',) or not weather:
            return f"""Match: {ctx.get('match_name')}
Sport: {sport}

No relevant weather data or indoor sport. Submit estimates with confidence 0.1 — weather is not a factor here.
Assign equal probability across h2h outcomes (this signal does not differentiate)."""

        return f"""Assess weather impact for this outdoor match.

Match: {ctx.get('match_name')}

WEATHER FORECAST:
{self._format_stats(weather)}

Consider:
- Wind speed > 30 km/h: reduces total goals, wider pitch = more draw likelihood
- Heavy rain: reduces goals, surface slipperiness
- Extreme cold/heat: fatigue factor in second half
- Which team's style is better suited to these conditions?

Submit probability estimates for h2h and totals, adjusted for weather."""


# ─────────────────────────────────────────────────────────────────────────────
# 8. UncertaintyAgent — measures predictability, acts as a gate
# ─────────────────────────────────────────────────────────────────────────────

class UncertaintyAgent(BaseAgent):
    name = "uncertainty"

    def system_prompt(self, ctx: dict[str, Any]) -> str:
        sport = ctx.get("sport", "football")
        if sport in ("basketball", "basket"):
            sport_context = (
                "NBA: valuta infortuni titolari (impatto usage), back-to-back schedule, "
                "rotazioni coach, player props (hit rate storica su punti/rimbalzi/assist), "
                "matchup difensivo avversario. Se un titolare è OUT → compagni hanno più usage → "
                "over sui punti di quei giocatori può avere senso intrinseco."
            )
        elif sport == "tennis":
            sport_context = (
                "Tennis: valuta ranking ATP/WTA, H2H storico, superficie (clay/grass/hard), "
                "forma recente (ultimi 3 tornei), infortuni fisici noti, motivazione (Slam vs 250). "
                "Quote pre-match tennistiche sono spesso inefficienti su superfici specifiche."
            )
        else:
            sport_context = (
                "Calcio: valuta forma recente (ultimi 5), infortuni difensori/portiere, "
                "motivazione (lotta retrocessione/titolo), meteo (pioggia/vento → under totals), "
                "stanchezza coppa europea, H2H recente."
            )
        return (
            f"Sei un analista sportivo professionale specializzato in value betting. "
            f"Il tuo compito è valutare SE una giocata ha senso intrinseco — non solo matematico. "
            f"Pinnacle ha già prezzato le info pubbliche note. Devi trovare:\n"
            f"1. Segnali NON ancora prezzati (notizie last-minute, infortuni confermati oggi)\n"
            f"2. Contesto che SUPPORTA la giocata (form, stats, logica sportiva)\n"
            f"3. Rischi nascosti che annullerebbero il vantaggio matematico\n\n"
            f"Sport corrente — {sport.upper()}:\n{sport_context}\n\n"
            f"Il campo 'reasoning' DEVE essere in italiano, 2-3 frasi specifiche che spiegano "
            f"PERCHÉ la giocata ha o non ha senso intrinseco. Evita frasi generiche."
        )

    def user_prompt(self, ctx: dict[str, Any]) -> str:
        elo = ctx.get('elo') or {}
        elo_str = (
            f"Home Elo: {elo.get('home_elo')} · Away Elo: {elo.get('away_elo')} · "
            f"Win prob implicita: {elo.get('elo_home_win_prob')}"
            if elo else "non disponibile"
        )

        # Costruisci sezione contesto dinamica
        sections = []

        if ctx.get('news_summary'):
            sections.append(f"📰 NOTIZIE/INFORTUNI RECENTI:\n{ctx['news_summary']}")

        if ctx.get('injury_note'):
            sections.append(f"🚑 IMPATTO INFORTUNI: {ctx['injury_note']}")

        if ctx.get('dunkest_note'):
            sections.append(f"📊 STATS GIOCATORI (ultimi 5 match):\n{ctx['dunkest_note']}")

        if ctx.get('weather_note'):
            sections.append(f"🌧️ METEO: {ctx['weather_note']}")

        if ctx.get('form_stats'):
            sections.append(f"📈 FORMA RECENTE: {str(ctx['form_stats'])[:400]}")

        if elo:
            sections.append(f"⚡ ELO RATING: {elo_str}")

        context_block = "\n\n".join(sections) if sections else "Nessun dato contestuale disponibile (normale — valuta solo la logica matematica)."

        # Quote rilevanti (prime 8 per leggibilità)
        odds_preview = []
        for o in (ctx.get('odds') or [])[:8]:
            odds_preview.append(f"  {o.get('bookmaker','?')} | {o.get('market','?')} | {o.get('outcome','?')} @ {o.get('odds','?')}")
        odds_str = "\n".join(odds_preview) if odds_preview else "non disponibili"

        return f"""Analisi a 360° per questa giocata.

PARTITA: {ctx.get('match_name')} — {ctx.get('competition')}
DATA: {ctx.get('match_date')}
SPORT: {ctx.get('sport', '?').upper()}

QUOTE DISPONIBILI:
{odds_str}

CONTESTO:
{context_block}

SEGNALI DISPONIBILI NEL SISTEMA: {ctx.get('available_signals', 'nessuno')}

---
Sulla base di tutto il contesto sopra, valuta:

1. La giocata ha senso intrinseco? (form, infortuni, logica sportiva supportano la quota)
2. C'è qualcosa che i bookmaker potrebbero NON aver ancora prezzato?
3. Ci sono rischi nascosti che annullano il vantaggio matematico?

Rispondi con:
  market: "uncertainty"
  outcome: "score"
  probability: <0.0–1.0>
    0.00–0.35 = FORTE SENSO — segnali positivi concreti supportano la giocata
    0.35–0.55 = SENSO MODERATO — nessun segnale di allarme, gioca con fiducia normale
    0.55–0.70 = DUBBIO — qualche preoccupazione, riduci importo
    0.70–1.00 = BLOCCA — rischio concreto non prezzato, salta questa giocata
  confidence: 1.0
  reasoning: 2-3 frasi IN ITALIANO che spiegano PERCHÉ la giocata ha/non ha senso.
             Cita dati specifici (es. "LeBron ha segnato 30+ punti in 4 delle ultime 5 partite,
             oggi affronta i Pistons 27° per punti concessi ai SF. Back-to-back ma vs difesa debole.")

Soglia blocco: uncertainty >= 0.70 → giocata scartata."""


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

ALL_AGENTS: list[type[BaseAgent]] = [
    StatsAgent,
    OddsAgent,
    FormAgent,
    H2HAgent,
    InjuryAgent,
    NewsAgent,
    WeatherAgent,
    UncertaintyAgent,
]

ANALYSIS_AGENTS: list[type[BaseAgent]] = [a for a in ALL_AGENTS if a.name != "uncertainty"]
