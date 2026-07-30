"""在两个决策时点之间构建可验证、可追溯的 1X2 市场变化。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models.value_objects.market_movement import MarketMovement
from app.models.value_objects.odds import Odds

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.value_objects.markets import Selection
    from app.services.verified_market_quote import (
        MarketQuoteRejectionReason,
        VerifiedMarketQuoteResult,
        VerifiedMarketQuoteService,
        VerifiedSelectionQuote,
    )

_MATCH_RESULT_CODES = ("home", "draw", "away")


class MarketMovementVerificationStatus(StrEnum):
    """完整市场变化的验证状态。"""

    VERIFIED = "verified"
    REJECTED = "rejected"


class MarketMovementStage(StrEnum):
    """赔率问题发生的决策阶段。"""

    OPENING = "opening"
    CURRENT = "current"


@dataclass(frozen=True, slots=True)
class MarketMovementIssue:
    """保留阶段信息的赔率验证失败证据。"""

    stage: MarketMovementStage
    reason: MarketQuoteRejectionReason
    selection_code: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedSelectionMovement:
    """单个 1X2 选项的共识赔率变化及两端完整来源。"""

    selection: Selection
    opening_quote: VerifiedSelectionQuote
    current_quote: VerifiedSelectionQuote
    movement: MarketMovement

    def __post_init__(self) -> None:
        opening_selection = self.opening_quote.quote.selection
        current_selection = self.current_quote.quote.selection
        if self.selection != opening_selection or self.selection != current_selection:
            raise ValueError("movement selections must match")
        if self.movement.opening.decimal != self.opening_quote.consensus_decimal_odds:
            raise ValueError("opening movement odds must use opening consensus")
        if self.movement.current.decimal != self.current_quote.consensus_decimal_odds:
            raise ValueError("current movement odds must use current consensus")


@dataclass(frozen=True, slots=True)
class VerifiedMarketMovementResult:
    """完整 1X2 市场变化；任一阶段失败时不暴露部分变化。"""

    status: MarketMovementVerificationStatus
    opening_as_of: datetime
    current_as_of: datetime
    movements: tuple[VerifiedSelectionMovement, ...] = ()
    issues: tuple[MarketMovementIssue, ...] = ()

    def __post_init__(self) -> None:
        _validate_time_range(self.opening_as_of, self.current_as_of)
        if self.status is MarketMovementVerificationStatus.VERIFIED:
            if len(self.movements) != len(_MATCH_RESULT_CODES) or self.issues:
                raise ValueError("verified movement requires three movements and no issues")
            if tuple(item.selection.code for item in self.movements) != _MATCH_RESULT_CODES:
                raise ValueError("verified movements must use deterministic 1X2 order")
        elif self.movements or not self.issues:
            raise ValueError("rejected movement requires issues and no movements")

    @property
    def accepted(self) -> bool:
        return self.status is MarketMovementVerificationStatus.VERIFIED


class VerifiedMarketMovementService:
    """比较两个经过相同质量策略验证的完整 1X2 市场。"""

    def __init__(self, *, market_quotes: VerifiedMarketQuoteService) -> None:
        self._market_quotes = market_quotes

    async def compare(
        self,
        fixture_id: UUID,
        *,
        opening_as_of: datetime,
        current_as_of: datetime,
    ) -> VerifiedMarketMovementResult:
        """比较指定基准时点与当前时点；基准时点不等同于绝对开盘时间。"""
        _validate_time_range(opening_as_of, current_as_of)

        opening = await self._market_quotes.verify(fixture_id, as_of=opening_as_of)
        current = await self._market_quotes.verify(fixture_id, as_of=current_as_of)
        issues = self._stage_issues(MarketMovementStage.OPENING, opening) + self._stage_issues(
            MarketMovementStage.CURRENT, current
        )
        if issues:
            return VerifiedMarketMovementResult(
                status=MarketMovementVerificationStatus.REJECTED,
                opening_as_of=opening_as_of,
                current_as_of=current_as_of,
                issues=issues,
            )

        current_by_code = {item.quote.selection.code: item for item in current.quotes}
        movements = tuple(
            self._build_movement(opening_quote, current_by_code[opening_quote.quote.selection.code])
            for opening_quote in opening.quotes
        )
        return VerifiedMarketMovementResult(
            status=MarketMovementVerificationStatus.VERIFIED,
            opening_as_of=opening_as_of,
            current_as_of=current_as_of,
            movements=movements,
        )

    @staticmethod
    def _stage_issues(
        stage: MarketMovementStage,
        result: VerifiedMarketQuoteResult,
    ) -> tuple[MarketMovementIssue, ...]:
        return tuple(
            MarketMovementIssue(
                stage=stage,
                reason=issue.reason,
                selection_code=issue.selection_code,
            )
            for issue in result.issues
        )

    @staticmethod
    def _build_movement(
        opening_quote: VerifiedSelectionQuote,
        current_quote: VerifiedSelectionQuote,
    ) -> VerifiedSelectionMovement:
        selection = opening_quote.quote.selection
        if current_quote.quote.selection != selection:
            raise ValueError("opening and current selections must match")
        movement = MarketMovement(
            opening=Odds(opening_quote.consensus_decimal_odds),
            current=Odds(current_quote.consensus_decimal_odds),
        )
        return VerifiedSelectionMovement(
            selection=selection,
            opening_quote=opening_quote,
            current_quote=current_quote,
            movement=movement,
        )


def _validate_time_range(opening_as_of: datetime, current_as_of: datetime) -> None:
    for name, value in (
        ("opening_as_of", opening_as_of),
        ("current_as_of", current_as_of),
    ):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if opening_as_of >= current_as_of:
        raise ValueError("opening_as_of must be earlier than current_as_of")
