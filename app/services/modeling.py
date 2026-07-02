"""数学模型契约（量化真相来源）。

宪法规定：概率、EV、盘口价值、下注单位等全部数值由经过验证的数学模型
（Elo / Poisson / xG / 蒙特卡洛 / Kelly 等集成）产出，是唯一真相来源，
LLM 不得覆盖。本模块定义契约与输入/输出 DTO；具体算法在 models 子包实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from app.models.entities.fixture import Fixture
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.decision import (
    DataCompleteness,
    DecisionScore,
    EvidenceLevel,
    RiskLevel,
)
from app.models.value_objects.markets import Selection
from app.models.value_objects.metrics import ExpectedGoals
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult
from app.models.value_objects.statistics import TeamStatistics

if TYPE_CHECKING:
    # 仅用于类型注解，避免与 models 子包产生循环导入
    from app.services.models.lambda_estimator import LeagueAverages


@dataclass(frozen=True, slots=True)
class MarketQuote:
    """市场上某个可下注选项的赔率报价。"""

    selection: Selection
    odds: Odds
    bookmaker_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ModelInput:
    """量化建模所需的输入数据包。

    数据完整度与证据等级由数据层评估后填入（模型不臆造这些属性）。
    """

    fixture: Fixture
    home_stats: TeamStatistics
    away_stats: TeamStatistics
    league: LeagueAverages
    quotes: list[MarketQuote]
    bankroll: Money
    data_completeness: DataCompleteness
    evidence_level: EvidenceLevel


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """模型对单个候选投注的量化产出。所有字段均为数学模型口径，权威、不可被 LLM 改动。"""

    selection: Selection
    odds: Odds
    model_probability: Probability
    edge: ValueEdge
    decision_score: DecisionScore
    data_completeness: DataCompleteness
    evidence_level: EvidenceLevel
    risk_level: RiskLevel
    stake: Stake | None = None
    bookmaker_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ModelOutput:
    """某场比赛的完整量化建模结果。"""

    outcome_probabilities: dict[MatchResult, Probability] = field(default_factory=dict)
    expected_goals: ExpectedGoals | None = None
    elo_home: float | None = None
    elo_away: float | None = None
    candidates: list[ModelCandidate] = field(default_factory=list)


class MatchModel(ABC):
    """比赛量化模型契约。由 Elo/Poisson/xG/蒙特卡洛/Kelly 等集成实现。"""

    @abstractmethod
    async def analyze(self, model_input: ModelInput) -> ModelOutput:
        """对一场比赛做量化建模，产出概率、xG、候选价值投注等。"""
        ...


class NotImplementedMatchModel(MatchModel):
    """占位桩：真实数学模型尚未开发。用于先打通端到端编排骨架。"""

    async def analyze(self, model_input: ModelInput) -> ModelOutput:
        raise NotImplementedError(
            "数学模型尚未实现（Elo / Poisson / 蒙特卡洛 / Kelly / xG）"
        )
