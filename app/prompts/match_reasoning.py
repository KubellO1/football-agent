"""Prompt template for the match reasoning agent.

The system prompt is stable across fixtures (it is cached), and hard-codes the
central rule: the quantitative models are the source of truth, and the LLM's
job is qualitative review — not prediction.
"""

from __future__ import annotations

from app.schemas.reasoning import ReasoningContext

PROMPT_VERSION = "match-reasoning/v1"

SYSTEM_PROMPT = """\
You are a senior football betting analyst reviewing machine-generated betting \
recommendations for professional bettors.

CRITICAL RULES — these are non-negotiable:
1. The probabilities, edges, expected values, Elo ratings, expected goals (xG), \
and Kelly stakes provided to you are computed by validated quantitative models. \
They are the SOURCE OF TRUTH. You must NOT recompute, adjust, or invent any of \
these numbers, and you must NEVER output a probability, edge, or stake of your own.
2. Your role is qualitative review only. For each candidate bet, decide whether \
the qualitative evidence (injuries, lineups, market movement, recent form) \
CORROBORATES or CONTRADICTS the model, and issue a verdict: keep, reduce, or \
discard. Reducing/discarding is a judgment about confidence, not a new probability.
3. Ground every statement in the supplied evidence. Do not use outside knowledge \
about specific players or matches — you may not have current information, and \
fabricated claims are worse than none. If evidence is thin, say so and lean toward \
'reduce' or 'discard'.
4. Weigh disconfirming signals seriously: confirmed absences of key players, \
lineups that contradict the model's assumptions, and sharp market moves against \
the selection are all reasons to lower confidence.
5. Be concise, specific, and honest about uncertainty. Surface data-quality \
concerns explicitly.

You will respond in the required structured format.\
"""


def build_user_prompt(context: ReasoningContext) -> str:
    """Render the evidence packet into a readable prompt body."""
    lines: list[str] = []
    lines.append(f"# Fixture\n{context.fixture_summary}")
    lines.append(f"Competition: {context.competition}")
    lines.append(f"Kickoff (UTC): {context.kickoff_iso}")

    if context.expected_goals_home is not None or context.expected_goals_away is not None:
        lines.append(
            f"\n# Expected goals (model)\n"
            f"Home xG: {context.expected_goals_home}  |  Away xG: {context.expected_goals_away}"
        )
    if context.elo_home is not None or context.elo_away is not None:
        lines.append(f"\n# Elo ratings\nHome: {context.elo_home}  |  Away: {context.elo_away}")

    if context.outcome_probabilities:
        lines.append("\n# Outcome probabilities (model vs. market)")
        for op in context.outcome_probabilities:
            lines.append(
                f"- {op.outcome}: model={op.model_probability:.3f}"
                + (f", implied={op.implied_probability:.3f}" if op.implied_probability else "")
                + (f", odds={op.decimal_odds}" if op.decimal_odds else "")
            )

    if context.candidate_bets:
        lines.append("\n# Candidate value bets (model-flagged — numbers are authoritative)")
        for b in context.candidate_bets:
            stake = f", stake={b.recommended_stake}" if b.recommended_stake is not None else ""
            book = f", {b.bookmaker}" if b.bookmaker else ""
            lines.append(
                f"- {b.selection_label}: odds={b.decimal_odds}, "
                f"model_prob={b.model_probability:.3f}, edge={b.edge:.3f}, "
                f"EV={b.expected_value:.3f}, kelly={b.kelly_fraction:.3f}{stake}{book}"
            )

    if context.market_movements:
        lines.append("\n# Market movement")
        for m in context.market_movements:
            lines.append(
                f"- {m.selection_label}: {m.opening_odds} -> {m.current_odds} ({m.direction})"
            )

    if context.team_form:
        lines.append("\n# Recent form")
        for f in context.team_form:
            lines.append(
                f"- {f.team}: {f.wins}W-{f.draws}D-{f.losses}L in {f.matches_played}, "
                f"GF {f.goals_for}/GA {f.goals_against}, xGF {f.xg_for}/xGA {f.xg_against}"
            )

    if context.injuries:
        lines.append("\n# Injuries / availability")
        for i in context.injuries:
            note = f" — {i.note}" if i.note else ""
            lines.append(f"- {i.team}: {i.player} ({i.status}){note}")

    if context.lineups:
        lines.append("\n# Lineups")
        for lu in context.lineups:
            status = "confirmed" if lu.is_confirmed else "predicted"
            absences = f"; missing: {', '.join(lu.key_absences)}" if lu.key_absences else ""
            lines.append(f"- {lu.team} ({status}) {lu.formation or ''}{absences}")

    lines.append(
        "\n# Task\nReview each candidate bet. Do not change any numbers. "
        "For each, give a verdict (keep/reduce/discard) with confidence and a "
        "rationale grounded only in the evidence above, then summarize."
    )
    return "\n".join(lines)
