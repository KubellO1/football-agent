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


# ---------------------------------------------------------------------------
# DB 驱动的单场分析响应（数据来自数据库，数值来自数学模型；无外部 API）
# ---------------------------------------------------------------------------


class ProbabilitiesOut(BaseModel):
    """胜平负(1X2)模型概率。"""

    home: float
    draw: float
    away: float


class SelectionAnalysisOut(BaseModel):
    """单个 1X2 选项的分析输出。"""

    code: str
    selection_label: str
    decimal_odds: float
    model_probability: float
    implied_probability: float
    edge: float
    expected_value: float
    kelly_fraction: float
    kelly_stake: float
    currency: str
    recommended: bool
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    explanation: str


class FixtureAnalysisResponse(BaseModel):
    """单场比赛分析响应。数据不足时 probabilities 为空并由 message 说明。"""

    fixture_id: UUID
    probabilities: ProbabilitiesOut | None = None
    expected_goals_home: float | None = None
    expected_goals_away: float | None = None
    selections: list[SelectionAnalysisOut] = Field(default_factory=list)
    data_completeness: float
    message: str | None = None
