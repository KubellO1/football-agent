"""每日推荐（Top Picks）的响应 DTO。"""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003 - Pydantic 会在运行时解析字段注解

from pydantic import BaseModel, Field


class DailyRunResponse(BaseModel):
    """每日批处理运行结果（触发端点返回）。"""

    date: str
    fixtures_analyzed: int = Field(description="跑过确定性数学分析的比赛数（全部当日比赛）。")
    fixtures_qualified: int = Field(description="通过 gate + 阈值、值得 LLM 评审的比赛数。")
    fixtures_reviewed: int = Field(description="本次实际调用 LLM 评审的比赛数（≤ 上限）。")
    fixtures_skipped_existing: int = Field(description="已有评审记录而跳过（不重复花费 LLM）。")
    value_bets_created: int
    reviewed_fixture_ids: list[UUID] = Field(default_factory=list)


class RecommendationBetOut(BaseModel):
    selection_label: str
    decimal_odds: float
    model_probability: float
    edge: float
    expected_value: float
    kelly_fraction: float
    kelly_stake: float
    currency: str
    confidence: float | None = None
    rationale: str | None = None


class FixtureRecommendationOut(BaseModel):
    fixture_id: UUID
    review_summary: str | None = None
    review: dict[str, Any] | None = None
    bets: list[RecommendationBetOut] = Field(default_factory=list)


class RecommendationsTodayResponse(BaseModel):
    """当日推荐读取响应（纯读库，不触发 LLM）。"""

    date: str
    count: int
    recommendations: list[FixtureRecommendationOut] = Field(default_factory=list)
