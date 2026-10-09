"""Deterministic semantic identity for replay-safe value-bet decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from app.services.prediction_decision_identity import (
    PredictionDecisionContext,
    analysis_input_fingerprint,
    stable_identity_fingerprint,
)

if TYPE_CHECKING:
    from app.models.entities.value_bet import ValueBet
    from app.services.fixture_analysis import DetailedAnalysis

_VALUE_BET_NAMESPACE = UUID("baf5d020-9e6f-449e-baa4-90f42ae8539e")


def build_value_bet_identity(
    *,
    value_bet: ValueBet,
    detailed: DetailedAnalysis,
    context: PredictionDecisionContext,
    model_version: str,
) -> UUID:
    """Identify one approved decision without wall-clock or LLM prose fields."""

    stake = value_bet.stake
    payload = {
        "version": 1,
        "fixture_id": value_bet.fixture_id,
        "context": context.as_dict(),
        "model_version": model_version,
        "analysis_input": analysis_input_fingerprint(detailed),
        "selection": value_bet.selection,
        "bookmaker_id": value_bet.bookmaker_id,
        "odds": value_bet.odds.decimal,
        "model_probability": value_bet.model_probability.value,
        "stake": (
            {
                "amount": stake.amount.amount,
                "currency": stake.amount.currency,
                "fraction": stake.fraction_of_bankroll,
            }
            if stake is not None
            else None
        ),
        "confidence": value_bet.confidence,
    }
    return uuid5(_VALUE_BET_NAMESPACE, stable_identity_fingerprint(payload))
