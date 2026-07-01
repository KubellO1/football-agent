"""DTOs for the Claude reasoning agent.

``ReasoningContext`` is the evidence packet assembled from the quantitative
models (Poisson/Elo/xG/Monte-Carlo ensembles) plus qualitative signals. It is
the *input* to the LLM.

``ReasoningOutput`` is the *structured* result Claude is constrained to produce.
By design it contains no probabilities, edges, or stakes — those are owned by
the models. Claude may only assess (keep/reduce/discard), rate confidence,
explain, and flag risks.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Input: evidence packet (numbers come from the quantitative models)
# ---------------------------------------------------------------------------


class OutcomeProbability(BaseModel):
    """Model vs. market probability for one 1X2 outcome."""

    outcome: str
    model_probability: float
    implied_probability: float | None = None
    decimal_odds: float | None = None


class CandidateBet(BaseModel):
    """A model-flagged value bet awaiting qualitative review.

    Every numeric field here is produced by the quantitative layer and is
    authoritative — Claude reviews it, it does not recompute it.
    """

    selection_label: str
    decimal_odds: float
    model_probability: float
    edge: float
    expected_value: float
    kelly_fraction: float
    recommended_stake: float | None = None
    bookmaker: str | None = None


class MarketMovementNote(BaseModel):
    selection_label: str
    opening_odds: float
    current_odds: float
    direction: str  # shortening | drifting | stable


class InjuryNote(BaseModel):
    player: str
    team: str
    status: str
    note: str | None = None


class LineupSummary(BaseModel):
    team: str
    formation: str | None = None
    is_confirmed: bool = False
    key_absences: list[str] = Field(default_factory=list)


class TeamForm(BaseModel):
    team: str
    matches_played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    xg_for: float = 0.0
    xg_against: float = 0.0


class ReasoningContext(BaseModel):
    """Full evidence packet handed to the reasoning agent for one fixture."""

    fixture_summary: str
    kickoff_iso: str
    competition: str

    outcome_probabilities: list[OutcomeProbability] = Field(default_factory=list)
    expected_goals_home: float | None = None
    expected_goals_away: float | None = None
    elo_home: float | None = None
    elo_away: float | None = None

    candidate_bets: list[CandidateBet] = Field(default_factory=list)
    market_movements: list[MarketMovementNote] = Field(default_factory=list)
    injuries: list[InjuryNote] = Field(default_factory=list)
    lineups: list[LineupSummary] = Field(default_factory=list)
    team_form: list[TeamForm] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Output: Claude's constrained assessment (no numbers it could invent)
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    KEEP = "keep"
    REDUCE = "reduce"
    DISCARD = "discard"


class SelectionAssessment(BaseModel):
    selection_label: str = Field(description="Must match a candidate bet's label.")
    verdict: Verdict = Field(
        description="Whether to keep, reduce, or discard the model's recommendation."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Qualitative confidence in the recommendation, 0-1. Not a probability.",
    )
    rationale: str = Field(description="Why, grounded in the supplied evidence only.")
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Concrete risks (e.g. 'key striker doubtful', 'sharp money against').",
    )


class ReasoningOutput(BaseModel):
    """Claude's structured review of a fixture's candidate bets."""

    overall_assessment: str = Field(description="Concise narrative synthesis of the fixture.")
    selection_assessments: list[SelectionAssessment] = Field(
        description="One entry per candidate bet reviewed."
    )
    key_factors: list[str] = Field(
        default_factory=list, description="The 2-5 factors that most drove the assessment."
    )
    caveats: list[str] = Field(default_factory=list)
    data_quality_concerns: list[str] = Field(
        default_factory=list,
        description="Missing/stale/contradictory inputs that weaken confidence.",
    )
