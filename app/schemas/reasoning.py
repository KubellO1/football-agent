"""LLM 推理 Agent 的数据传输对象（DTO）。

``ReasoningContext`` 是「证据包」，由量化模型（Poisson / Elo / xG / 蒙特卡洛
等）的产出与定性信号组装而成，是 LLM 的**输入**。

``ReasoningOutput`` 是 LLM 被约束产出的**结构化结果**。按设计，它**不包含
任何概率、EV、盘口价值或下注单位**——这些数值均由数学模型负责。LLM 只能
评审（保留/降低/放弃）、给出信心、解释、并提出反方理由与未知因素。

详见 docs/agent-constitution.md（系统宪法）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 输入：证据包（其中的数值全部来自量化模型）
# ---------------------------------------------------------------------------


class OutcomeProbability(BaseModel):
    """某个胜平负结果的「模型概率 vs 市场概率」。"""

    outcome: str
    model_probability: float
    implied_probability: float | None = None
    decimal_odds: float | None = None


class CandidateBet(BaseModel):
    """模型标记、等待定性评审的候选价值投注。

    这里的每个数值字段都由量化层产出，具有权威性——LLM 只评审，不重算。
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
    """单个投注项的盘口/赔率变化。"""

    selection_label: str
    opening_odds: float
    current_odds: float
    direction: str  # shortening | drifting | stable


class InjuryNote(BaseModel):
    """球员伤停 / 出场情况。"""

    player: str
    team: str
    status: str
    note: str | None = None


class LineupSummary(BaseModel):
    """某队的首发阵容（预测或已确认）。"""

    team: str
    formation: str | None = None
    is_confirmed: bool = False
    key_absences: list[str] = Field(default_factory=list)


class TeamForm(BaseModel):
    """某队在一段时间窗口内的聚合近况。"""

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
    """交给推理 Agent 的、针对单场比赛的完整证据包。"""

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
# 输出：LLM 的受约束评审（不含任何它可能凭空编造的数值）
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    """针对候选投注的裁决。"""

    KEEP = "keep"  # 保留
    REDUCE = "reduce"  # 降低
    DISCARD = "discard"  # 放弃


class CommitteeRole(str, Enum):
    """决策委员会角色（对应宪法第 6 节）。"""

    DATA = "data_analyst"  # 数据分析师
    TACTICS = "tactics_analyst"  # 战术分析师
    PLAYERS = "player_analyst"  # 球员分析师
    LINEUP = "lineup_analyst"  # 首发分析师
    MOTIVATION = "motivation_analyst"  # 战意分析师
    MARKET = "market_analyst"  # 盘口分析师
    ODDS = "odds_analyst"  # 赔率分析师
    EV = "ev_analyst"  # EV 分析师
    RISK = "risk_manager"  # 风险经理
    RED_TEAM = "red_team"  # 反方分析


class Stance(str, Enum):
    """某角色相对模型建议的立场。"""

    SUPPORT = "support"  # 支持
    NEUTRAL = "neutral"  # 中立
    AGAINST = "against"  # 反对


class CommitteeOpinion(BaseModel):
    """决策委员会中单个角色的意见（纯定性，不含数值）。"""

    role: CommitteeRole = Field(description="发表意见的委员会角色。")
    stance: Stance = Field(description="该角色相对模型建议的立场：支持/中立/反对。")
    opinion: str = Field(description="该角色的简要意见，中文，仅基于所给证据，不含任何数值。")


class SelectionAssessment(BaseModel):
    """针对单个候选投注的评审结论。"""

    selection_label: str = Field(description="必须与某个候选投注的标签一致。")
    verdict: Verdict = Field(description="对模型建议的裁决：保留 / 降低 / 放弃。")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="对该裁决的定性信心，取值 0-1。这不是概率。",
    )
    rationale: str = Field(description="核心理由，仅基于所提供的证据。")
    supporting_reasons: list[str] = Field(
        default_factory=list, description="支持该裁决的具体理由（对应决策日志中的支持证据）。"
    )
    objections: list[str] = Field(
        default_factory=list, description="针对该候选的反对理由 / 风险点。"
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="具体风险标记，例如「关键前锋存疑」「聪明钱反向」。",
    )


class ReasoningOutput(BaseModel):
    """LLM 对某场比赛候选投注的结构化评审结果。"""

    chief_summary: str = Field(description="首席分析师对整场比赛的综合汇总（中文）。")
    committee_opinions: list[CommitteeOpinion] = Field(
        default_factory=list, description="决策委员会各角色的意见。"
    )
    selection_assessments: list[SelectionAssessment] = Field(
        description="每个被评审的候选投注对应一条。"
    )
    red_team_objections: list[str] = Field(
        default_factory=list,
        description="Red Team 反方理由，回答「这场为什么可能错」，至少 3 条。",
    )
    key_factors: list[str] = Field(
        default_factory=list, description="最影响本次评审的 2-5 个关键因素。"
    )
    unknown_factors: list[str] = Field(
        default_factory=list, description="影响判断但当前未知或缺失的因素。"
    )
    factors_that_could_change_conclusion: list[str] = Field(
        default_factory=list, description="哪些信息一旦变化会改变结论。"
    )
    self_review_notes: list[str] = Field(
        default_factory=list,
        description="自我审查发现，如近期偏差、大众偏差、过度相信热门/近期状态等。",
    )
    caveats: list[str] = Field(default_factory=list, description="其他注意事项。")
    data_quality_concerns: list[str] = Field(
        default_factory=list,
        description="削弱信心的数据问题：缺失、过期或相互矛盾的输入。",
    )
