"""AI 评审委员会的输入上下文与结构化产出 DTO。

上下文里的所有数值均来自数学模型（唯一真相来源）；产出只包含定性文字，
不含任何 LLM 自造的数值——LLM 只解释与批判，不改动数字。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal  # noqa: TC003 - Pydantic 会在运行时解析字段注解
from uuid import UUID  # noqa: TC003 - Pydantic 会在运行时解析字段注解

from pydantic import BaseModel, Field

from app.schemas.analysis import (  # noqa: TC001 - Pydantic 会在运行时解析字段注解
    ProbabilitiesOut,
    SelectionAnalysisOut,
)

# ---------------------------------------------------------------------------
# 输入：交给 LLM 的证据包（数值均来自数学模型 / gate）
# ---------------------------------------------------------------------------


class SelectionContext(BaseModel):
    """单个 1X2 候选的量化事实（权威、不可改动）。"""

    selection_label: str
    decimal_odds: float
    model_probability: float
    implied_probability: float
    edge: float
    expected_value: float
    kelly_fraction: float
    kelly_stake: float
    currency: str
    recommended: bool = Field(description="准入 gate 的确定性推荐结论。")
    model_confidence: float
    gate_reasons: list[str] = Field(default_factory=list)


class TeamFormContext(BaseModel):
    """某队近期战绩（由已完赛比赛汇总）。"""

    side: str  # home | away
    matches_played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int


class CommitteeReviewContext(BaseModel):
    """交给评审委员会的完整证据包（单场比赛）。"""

    fixture_summary: str
    competition: str
    kickoff_iso: str
    probabilities: dict[str, float] = Field(description="胜平负模型概率：home/draw/away。")
    expected_goals_home: float | None = None
    expected_goals_away: float | None = None
    elo_home: float | None = None
    elo_away: float | None = None
    league_baseline_rate: float = Field(gt=0)
    league_baseline_metric: Literal["goals", "xg"]
    home_form: TeamFormContext
    away_form: TeamFormContext
    selections: list[SelectionContext] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 输出：LLM 的结构化评审（纯定性，无数值）
# ---------------------------------------------------------------------------


class SelectionStance(StrEnum):
    """评审对某候选相对模型/gate 结论的立场。"""

    SUPPORT = "support"  # 支持
    NEUTRAL = "neutral"  # 中立
    AGAINST = "against"  # 反对


class SelectionReview(BaseModel):
    """针对单个候选的评审（不含新数值）。"""

    selection_label: str = Field(description="必须与上下文中的候选标签一致。")
    stance: SelectionStance = Field(description="相对模型/gate 结论的立场。")
    agrees_with_model: bool = Field(description="是否认同 gate 对该候选的推荐/拒绝结论。")
    explanation: str = Field(description="为什么模型推荐或拒绝该投注；仅基于所给证据，不含新数值。")


class CommitteeReview(BaseModel):
    """LLM 对一场比赛的结构化评审产出。"""

    executive_summary: str = Field(description="面向决策者的执行摘要（中文）。")
    key_strengths: list[str] = Field(default_factory=list, description="本次机会的核心优势。")
    key_risks: list[str] = Field(default_factory=list, description="核心风险。")
    why_market_may_be_wrong: str = Field(description="市场为何可能定价错误（价值从何而来）。")
    why_model_recommends_or_rejects: str = Field(
        description="模型为何推荐或拒绝（解读 gate 结论）。"
    )
    confidence_explanation: str = Field(description="对模型给出的信心水平的定性解释。")
    betting_recommendation_explanation: str = Field(
        description="下注建议的通俗解释（含风控立场）。"
    )
    disagreements: list[str] = Field(
        default_factory=list,
        description="与数学模型/gate 结论不一致之处；仅记录，不得改动任何数值。",
    )
    selection_reviews: list[SelectionReview] = Field(
        default_factory=list, description="每个候选一条评审。"
    )


# ---------------------------------------------------------------------------
# HTTP 响应
# ---------------------------------------------------------------------------


class CommitteeReviewResponse(BaseModel):
    """评审端点响应：确定性分析 + LLM 评审 + 落库结果。"""

    fixture_id: UUID
    message: str | None = None
    probabilities: ProbabilitiesOut | None = None
    selections: list[SelectionAnalysisOut] = Field(default_factory=list)
    review: CommitteeReview | None = None
    decision_log_id: UUID | None = None
    value_bet_ids: list[UUID] = Field(default_factory=list)
