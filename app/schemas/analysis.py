"""分析 API 的请求/响应 DTO。

因数据抓取（providers）尚未接入，分析所需数据经请求体传入，由 endpoint 组装
成领域 ModelInput。响应只暴露必要字段。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class TeamStatsInput(BaseModel):
    """球队统计输入。"""

    matches_played: int = Field(gt=0)
    wins: int = Field(ge=0)
    draws: int = Field(ge=0)
    losses: int = Field(ge=0)
    goals_for: int = Field(ge=0)
    goals_against: int = Field(ge=0)
    xg_for: float = 0.0
    xg_against: float = 0.0


class QuoteInput(BaseModel):
    """一个市场报价输入。"""

    market: str  # MarketType 的值，例如 "1x2"
    code: str  # 选项代码，例如 "home"
    line: float | None = None
    odds_decimal: float = Field(gt=1.0)
    bookmaker_id: UUID | None = None


class AnalyzeRequest(BaseModel):
    """单场比赛分析请求。"""

    home_stats: TeamStatsInput
    away_stats: TeamStatsInput
    league_goals_per_game: float = Field(gt=0)
    bankroll: float = Field(gt=0)
    currency: str = "EUR"
    data_completeness: float = Field(ge=0, le=100)
    evidence_level: str  # A-E
    home_elo: float | None = None
    away_elo: float | None = None
    quotes: list[QuoteInput] = Field(default_factory=list)


class ValueBetOut(BaseModel):
    """推荐输出（数值来自模型，confidence/rationale 来自 Claude）。"""

    selection_label: str
    decimal_odds: float
    model_probability: float
    edge: float
    stake_fraction: float | None = None
    confidence: float | None = None
    rationale: str | None = None


class AnalyzeResponse(BaseModel):
    """分析响应。无价值时 message 给出说明、selected 为空。"""

    message: str | None = None
    selected: list[ValueBetOut] = Field(default_factory=list)
    chief_summary: str | None = None
    key_factors: list[str] = Field(default_factory=list)
