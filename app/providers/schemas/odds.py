"""Response models for the odds feed.

Provider-normalized DTOs returned by an ``OddsProvider``. Prices are decimal
(European) odds as reported upstream — no conversion to the domain ``Odds`` value
object happens here (that is a service-layer concern, not wired yet).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic 会在运行时解析字段注解
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, Field


class ProviderOddsTarget(BaseModel):
    """已由生产数据库和白名单确认、允许查询的单场比赛。"""

    model_config = {"frozen": True}

    fixture_id: UUID
    home_team: str
    away_team: str
    kickoff: datetime


class OddsOutcome(BaseModel):
    """A single priced outcome within a market."""

    name: str = Field(description="Outcome label (team name, 'Draw', 'Over', ...).")
    price: float = Field(description="Decimal (European) odds, > 1.0.")
    point: float | None = Field(
        default=None,
        description="Handicap / total line, when the market has one (spreads, totals).",
    )


class BookmakerMarket(BaseModel):
    """One market offered by one bookmaker (e.g. head-to-head, totals)."""

    bookmaker_key: str = Field(description="The provider's bookmaker identifier.")
    bookmaker_title: str
    market: str = Field(description="Market key, e.g. 'h2h', 'spreads', 'totals'.")
    last_update: datetime | None = Field(
        default=None, description="When the bookmaker last refreshed this market."
    )
    outcomes: list[OddsOutcome] = Field(default_factory=list)


class ProviderFixtureOdds(BaseModel):
    """All bookmaker markets the odds provider has for a single fixture."""

    provider_id: str = Field(description="The provider's own event identifier.")
    commence_time: datetime = Field(description="Event start, timezone-aware UTC.")
    home_team: str
    away_team: str
    sport_key: str | None = None
    bookmakers: list[BookmakerMarket] = Field(default_factory=list)
    source: str = "unknown"
