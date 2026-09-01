"""Dashboard 输入数据类型。

所有字段均可为 None 或缺失——仪表盘渲染时统一显示 "暂无数据"。
不从 API 拉取数据；全部数据由调用方从数据库或归一化 JSON 注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class FixtureInfo:
    home_team: str = ""
    away_team: str = ""
    home_score: int | None = None
    away_score: int | None = None
    start_time: datetime | None = None
    venue: str | None = None
    competition: str | None = None
    status: str | None = None  # upcoming / live / closed


@dataclass
class OddsInfo:
    """1X2 赔率数据。"""

    home_odds: float | None = None
    draw_odds: float | None = None
    away_odds: float | None = None
    bookmaker: str | None = None


@dataclass
class ModelProbabilities:
    """各模型概率输出。"""

    poisson_home: float | None = None
    poisson_draw: float | None = None
    poisson_away: float | None = None
    elo_home: float | None = None
    elo_draw: float | None = None
    elo_away: float | None = None
    ensemble_home: float | None = None
    ensemble_draw: float | None = None
    ensemble_away: float | None = None


@dataclass
class ValueInfo:
    expected_value: float | None = None
    edge: float | None = None
    kelly_fraction: float | None = None
    kelly_stake: float | None = None  # EUR 金额 (e.g., 50.00)


@dataclass
class DecisionInfo:
    classification: str | None = None  # BET / WATCH / NO BET
    confidence_score: float | None = None
    why_not_bet: str | None = None
    confidence_killer: str | None = None


@dataclass
class ModelAvailability:
    poisson: bool = False
    elo: bool = False
    ensemble: bool = False
    monte_carlo: bool = False
    kelly: bool = False


@dataclass
class ScenarioInfo:
    """仿真/情景分析。"""

    items: list[str] = field(default_factory=list)


@dataclass
class MarketInfo:
    """单个推荐市场数据。"""

    market_name: str = ""
    odds: float | None = None
    model_prob: float | None = None
    market_prob: float | None = None
    ev: float | None = None
    confidence: float | None = None
    stake: float | None = None
    explanation: str = ""
    supported: bool = True


@dataclass
class ScorelineInfo:
    """比分预测条目。"""

    scoreline: str = ""
    probability: float | None = None
    is_highest: bool = False  # V2: 最高概率比分高亮标记


@dataclass
class GoalscorerInfo:
    """进球球员预测条目。"""

    player: str = ""
    probability: float | None = None


# ─────────────────────────────────────────────────
# V2 Types — Dashboard V2 新增数据模型
# ─────────────────────────────────────────────────


@dataclass
class ExecutiveSummary:
    """S1: AI 执行摘要 — 比赛详情页首要元素。"""

    final_decision: str = ""  # "BET" / "WATCH" / "NO BET"
    recommended_market: str = ""  # "主胜" / "Over 2.5" / "亚盘-0.5"
    confidence: float | None = None
    ev: float | None = None
    stake: float | None = None
    one_liner: str = ""  # 一句话结论


@dataclass
class FootballReasoning:
    """S2: 足球分析 — 将模型输出转化为自然语言足球推理。"""

    recent_form: str = ""
    attacking_strength: str = ""
    defensive_weakness: str = ""
    xg_trend: str = ""
    elo_gap: str = ""
    home_advantage: str = ""
    injury_impact: str = ""
    schedule_congestion: str = ""
    weather_impact: str = ""
    market_movement: str = ""


@dataclass
class OverUnderAnalysis:
    """S3: 大小球分析 — 专用大/小球推荐 Section。"""

    recommended_line: str = ""  # "大于 2.5" / "小于 2.5"
    model_prob: float | None = None
    market_prob: float | None = None
    ev: float | None = None
    confidence: float | None = None
    explanation_bullets: list[str] = field(default_factory=list)


@dataclass
class MarketMovement:
    """S7: 市场动向 — 赔率演变追踪。V3 扩展 high/low。"""

    opening_odds: float | None = None
    current_odds: float | None = None
    high_odds: float | None = None  # V3: 历史最高赔率
    low_odds: float | None = None  # V3: 历史最低赔率
    direction: str = ""  # "资金流向主胜" / "资金流向平局" / "无明显方向"
    change_pct: float | None = None  # e.g., -7.0
    explanation: str = ""


@dataclass
class RiskBreakdownItem:
    """S8: 风险评估单条 — 因素名称 + 严重度 + 条形标签。"""

    factor: str = ""
    severity: str = ""  # "低" / "中等" / "高"
    bar_width: int = 0  # 1-5


@dataclass
class RiskBreakdown:
    """S8: 风险评估综合面板。"""

    items: list[RiskBreakdownItem] = field(default_factory=list)
    overall_score: int = 0  # 0-100
    overall_label: str = ""  # "低风险" / "中等风险" / "高风险"


@dataclass
class DataQualityItem:
    """S9: 数据质量单条 — 数据源名称 + 星级。"""

    source: str = ""
    stars: int = 0  # 1-5
    note: str = ""
    field: str = ""  # 指标字段名（odds_available / model_probability 等）
    coverage: float = 0.0  # 覆盖率 0.0-1.0


@dataclass
class DataQuality:
    """S9: 数据质量综合指标。"""

    items: list[DataQualityItem] = field(default_factory=list)
    overall_score: float = 0.0  # 0-100
    overall_label: str = ""


@dataclass
class AIQAItem:
    """S10: AI 互动 Q&A 单条 — 预生成的问答对。"""

    question: str = ""
    answer: str = ""


@dataclass
class AIQA:
    """S10: AI 互动解释 — 预生成 Q&A 集合。"""

    items: list[AIQAItem] = field(default_factory=list)


# ─────────────────────────────────────────────────


@dataclass
class ValueOpportunity:
    """跨比赛价值机会排行条目。"""

    match_label: str = ""
    market: str = ""
    odds: float | None = None
    model_prob: float | None = None
    ev: float | None = None
    confidence: float | None = None
    explanation: str = ""


@dataclass
class RiskItem:
    """单场比赛风险条目。"""

    match_label: str = ""
    risk_factor: str = ""
    severity: str = "中"


@dataclass
class TopPick:
    """今日精选推荐条目。"""

    match_label: str = ""
    market: str = ""
    odds: float | None = None
    model_prob: float | None = None
    ev: float | None = None
    confidence: float | None = None
    stake: float | None = None
    reason: str = ""


@dataclass
class AvoidMatch:
    """建议回避的比赛条目。"""

    match_label: str = ""
    reason: str = ""
    decision: str = ""  # "NO BET" / "WATCH"


@dataclass
class AccumulatorSuggestion:
    """模拟串关建议条目。"""

    combo_name: str = ""  # "保守双串"
    match_picks: str = ""  # "阿森纳主胜 + 利物浦亚盘"
    combined_odds: float | None = None
    estimated_hit_rate: float | None = None


@dataclass
class DecisionStep:
    """决策流程中的单个步骤。"""

    step_name: str = (
        ""  # "数据采集" / "模型计算" / "期望收益 EV" / "凯利仓位" / "风险评估" / "最终决策"
    )
    status: str = "nodata"  # "passed" / "partial" / "failed" / "nodata"
    note: str = ""  # 简短说明
    detail: str = ""  # 补充细节


@dataclass
class ModelConsensusRow:
    """模型共识中单个模型的一行数据。"""

    model_name: str = ""  # "Poisson" / "Elo" / "xG" / "Monte Carlo" / "市场隐含" / "集成模型"
    predicted_outcome: str = ""  # "主胜" / "平局" / "客胜"
    home_prob: float | None = None
    draw_prob: float | None = None
    away_prob: float | None = None
    agrees: bool = False  # 是否与最终推荐一致
    available: bool = True  # 模型是否可用
    unavailable_reason: str = ""  # 不可用原因


@dataclass
class DailyRiskProfile:
    """每日风险管理概览。"""

    max_exposure_pct: float | None = None  # 建议最大敞口（银行金 %）
    total_suggested_stake: float | None = None  # 建议总仓位（%）
    recommended_trade_count: int = 0
    kelly_breakdown: list = field(  # type: ignore[type-arg]
        default_factory=list
    )  # [(match_market, kelly_pct), ...]


@dataclass
class BestOpportunity:
    """今日最佳机会 — 按市场类别展示最佳单笔推荐。"""

    category: str = (
        ""  # "今日最佳投注" / "今日最佳大小球" / "今日最佳亚盘" / "今日最佳双方进球" / "今日最佳比分" / "今日最佳进球球员"
    )
    match_label: str = ""
    market: str = ""
    selection: str = ""  # e.g., "主胜" / "大于 2.5" / "主队 -0" / "是" / "2-1" / "萨卡"
    odds: float | None = None
    model_prob: float | None = None
    market_prob: float | None = None
    ev: float | None = None
    confidence: float | None = None
    stake: float | None = None
    risk_level: str = ""  # "低" / "中" / "高"
    explanation: str = ""
    reasoning_bullets: list[str] = field(default_factory=list)
    has_qualifier: bool = True  # False → 显示"暂无符合条件的推荐"


@dataclass
class AIReasoning:
    """AI 推理块 — 基于真实数据的证据驱动推荐理由。"""

    bullets: list[str] = field(default_factory=list)
    conclusion: str = ""


@dataclass
class ConfidenceComponent:
    """置信度构成中的单个组件 — 正数为加分项，负数为扣分项。"""

    name: str = ""
    contribution: float = 0.0  # e.g., +18 for Poisson, -5 for injury


@dataclass
class ConfidenceBreakdown:
    """置信度构成明细。"""

    components: list[ConfidenceComponent] = field(default_factory=list)
    total: float = 0.0


@dataclass
class DecisionTimelineEntry:
    """决策时间线中的单条记录。"""

    timestamp: str = ""  # "上午 08:00"
    decision: str = ""  # "WATCH" / "BET" / "NO BET"
    reason: str = ""
    trigger_event: str = ""  # e.g., "首发确认" — 作为时间线箭头标签


@dataclass
class TriggerCondition:
    """升级/降级条件中的单条。"""

    condition: str = ""
    threshold: str = ""  # e.g., "赔率 > 2.60"


@dataclass
class DecisionTriggers:
    """升级/降级条件集合。"""

    upgrade: list[TriggerCondition] = field(default_factory=list)
    downgrade: list[TriggerCondition] = field(default_factory=list)


@dataclass
class MatchDashboardData:
    """单场比赛仪表盘数据。"""

    fixture: FixtureInfo = field(default_factory=FixtureInfo)
    odds: OddsInfo = field(default_factory=OddsInfo)
    probabilities: ModelProbabilities = field(default_factory=ModelProbabilities)
    value: ValueInfo = field(default_factory=ValueInfo)
    decision: DecisionInfo = field(default_factory=DecisionInfo)
    model_availability: ModelAvailability = field(default_factory=ModelAvailability)
    weather: str | None = None
    injuries: str | None = None
    scenarios: ScenarioInfo = field(default_factory=ScenarioInfo)
    data_completeness: float | None = None
    evidence_level: str | None = None
    recommended_markets: list[MarketInfo] = field(default_factory=list)
    correct_scores: list[ScorelineInfo] = field(default_factory=list)
    goalscorers: list[GoalscorerInfo] = field(default_factory=list)
    risk_items: list[RiskItem] = field(default_factory=list)
    decision_flow: list[DecisionStep] = field(default_factory=list)
    model_consensus: list[ModelConsensusRow] = field(default_factory=list)
    ai_reasoning: AIReasoning | None = None
    confidence_breakdown: ConfidenceBreakdown | None = None
    decision_timeline: list[DecisionTimelineEntry] = field(default_factory=list)
    triggers: DecisionTriggers | None = None
    # ── Dashboard V2 新增字段 ──
    executive_summary: ExecutiveSummary | None = None
    football_reasoning: FootballReasoning | None = None
    over_under: OverUnderAnalysis | None = None
    market_movement: MarketMovement | None = None
    risk_breakdown: RiskBreakdown | None = None
    data_quality: DataQuality | None = None
    ai_qa: AIQA | None = None
    nobet_checks: NoBetChecks | None = None  # V3: NO-BET 检查清单
    # ── V3.1 新增字段 ──
    counterfactual: CounterfactualExplanation | None = None  # 反事实解释
    confidence_radar: ConfidenceRadar | None = None  # 信心雷达
    odds_timeline: list[OddsTimelinePoint] = field(default_factory=list)  # 赔率时间线
    # ── Sportmonks Phase 3: Enhancement data ──
    lineup: LineupDashboard | None = None
    injury_dashboard: InjuryDashboard | None = None
    recent_form_home: RecentFormDashboard | None = None
    recent_form_away: RecentFormDashboard | None = None
    standings: StandingsDashboard | None = None
    match_centre: MatchCentreDashboard | None = None
    tv_broadcast: TVBroadcastDashboard | None = None
    generated_at: datetime | None = None


@dataclass
class TopRecommendation:
    """V3: 今日最佳推荐 — 合并 TopPick + BestOpportunity + ValueOpportunity 的统一卡片。"""

    match_label: str = ""
    market: str = ""  # e.g., "胜平负" / "大小球" / "亚盘"
    selection: str = ""  # e.g., "主胜" / "大于 2.5"
    odds: float | None = None
    model_prob: float | None = None
    ev: float | None = None
    confidence: float | None = None
    stake: float | None = None
    stars: int = 0  # 1-5, computed from EV
    reason: str = ""  # one-liner
    category: str = ""  # original category label for grouping
    risk_level: str = ""  # "低" / "中" / "高"


@dataclass
class NoBetCheckItem:
    """V3: NO-BET 清单单条 — 结构化扣分项。"""

    label: str = ""  # e.g., "EV 不足"
    detail: str = ""  # e.g., "当前 EV: -1.2%"
    passed: bool = True  # True=通过, False=不通过


@dataclass
class NoBetChecks:
    """V3: NO-BET 检查清单。"""

    items: list[NoBetCheckItem] = field(default_factory=list)
    catch_all: str = ""  # 兜底原因，所有检查通过但仍为 NO-BET 时显示


# ── Dashboard V3.1 — Polish types ──


@dataclass
class CounterfactualExplanation:
    """V3.1: 反事实解释 — 为什么不是其他选项。"""

    why_not_away: str = ""  # 为什么不推荐客胜
    why_not_draw: str = ""  # 为什么不推荐平局
    why_not_opposite_ou: str = ""  # 为什么不推荐反向大小球
    why_still_watch: str = ""  # 为什么仍是 WATCH（非 BET）
    data_quality_note: str = ""  # 数据质量备注


@dataclass
class ConfidenceRadar:
    """V3.1: 信心雷达 — 五维评分。"""

    model_consensus: float = 0.0  # 模型一致性 0-100
    data_completeness: float = 0.0  # 数据完整度 0-100
    market_efficiency: float = 0.0  # 市场效率 0-100
    fundamentals: float = 0.0  # 基本面 0-100
    risk_control: float = 0.0  # 风险控制 0-100
    label: str = ""  # 综合标签


@dataclass
class OddsTimelinePoint:
    """V3.1: 赔率时间线的单个数据点。"""

    label: str = ""  # "开盘" / "最高" / "最低" / "当前"
    odds: float = 0.0
    timestamp: str = ""  # 可选时间戳


# ─────────────────────────────────────────────────
# Sportmonks Enhancement Types — Phase 3 Dashboard
# ─────────────────────────────────────────────────


@dataclass
class LineupPlayerDashboard:
    """单个球员的阵容信息。"""

    player_name: str = ""
    jersey_number: int | None = None
    position_id: int | None = None
    is_starter: bool = False
    formation_position: int | None = None


@dataclass
class TeamLineupDashboard:
    """单支球队的阵容数据。"""

    team_name: str = ""
    team_id: int = 0
    formation: str | None = None
    starters: list[LineupPlayerDashboard] = field(default_factory=list)
    substitutes: list[LineupPlayerDashboard] = field(default_factory=list)


@dataclass
class LineupDashboard:
    """比赛阵容仪表盘数据。"""

    available: bool = False
    home_lineup: TeamLineupDashboard | None = None
    away_lineup: TeamLineupDashboard | None = None


@dataclass
class SidelinedDashboardPlayer:
    """伤病/停赛球员记录。"""

    player_name: str = ""
    type: str = ""  # "injury" / "suspension"
    description: str = ""
    expected_return: str = ""


@dataclass
class InjuryDashboard:
    """伤病/停赛仪表盘数据。"""

    available: bool = False
    players: list[SidelinedDashboardPlayer] = field(default_factory=list)


@dataclass
class RecentMatchDashboard:
    """单场近期比赛记录。"""

    opponent: str = ""
    result: str = ""  # "W" / "D" / "L"
    goals_for: int = 0
    goals_against: int = 0
    is_home: bool = False
    date: str = ""


@dataclass
class RecentFormDashboard:
    """近期状态仪表盘数据。"""

    available: bool = False
    team_name: str = ""
    trend: str = ""  # "↑" / "↓" / "→"
    matches: list[RecentMatchDashboard] = field(default_factory=list)


@dataclass
class StandingsRowDashboard:
    """积分表单行。"""

    position: int = 0
    team_name: str = ""
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_diff: int = 0
    points: int = 0


@dataclass
class StandingsDashboard:
    """联赛积分表仪表盘数据。"""

    available: bool = False
    group_name: str = ""
    rows: list[StandingsRowDashboard] = field(default_factory=list)


@dataclass
class MatchEventDashboard:
    """比赛事件单条（进球/红黄牌/换人）。"""

    minute: int = 0
    extra_minute: int | None = None
    event_type: str = ""
    player_name: str = ""
    related_player_name: str = ""
    result: str = ""
    info: str = ""


@dataclass
class MatchCentreDashboard:
    """比赛中心仪表盘数据（事件+统计）。"""

    available: bool = False
    events: list[MatchEventDashboard] = field(default_factory=list)
    timeline: list[MatchEventDashboard] = field(default_factory=list)


@dataclass
class TVStationDashboard:
    """电视频道单条。"""

    name: str = ""
    url: str | None = None


@dataclass
class TVBroadcastDashboard:
    """电视转播仪表盘数据。"""

    available: bool = False
    stations: list[TVStationDashboard] = field(default_factory=list)


# ─────────────────────────────────────────────────


@dataclass
class DailyExecutiveSummary:
    """日概览级的执行摘要 — 生产管道统计。"""

    date: str = ""
    fixtures_total: int = 0
    odds_snapshots: int = 0
    predictions_total: int = 0
    gate_approved_count: int = 0
    bet_count: int = 0
    watch_count: int = 0
    no_bet_count: int = 0
    no_odds_count: int = 0
    value_bets_created: int = 0
    decision_logs: int = 0
    settlements: int = 0
    settled_pl: float = 0.0
    performance: dict | None = None  # type: ignore[type-arg]


@dataclass
class DailyDashboardData:
    """日概览仪表盘数据。V3: top_recommendations 合并 top_picks + best_opportunities + value_opportunities。"""

    date: str = ""
    matches: list[MatchDashboardData] = field(default_factory=list)
    value_opportunities: list[ValueOpportunity] = field(default_factory=list)
    ai_trade_summary: str = ""
    top_picks: list[TopPick] = field(default_factory=list)
    avoid_matches: list[AvoidMatch] = field(default_factory=list)
    accumulator_suggestions: list[AccumulatorSuggestion] = field(default_factory=list)
    risk_profile: DailyRiskProfile | None = None
    best_opportunities: list[BestOpportunity] = field(default_factory=list)
    # ── V3 新增字段 ──
    top_recommendations: list[TopRecommendation] = field(default_factory=list)
    ai_final_recommendation: str = ""  # AI 最终推荐 自然语言
    top_pick_correct_scores: list[ScorelineInfo] = field(default_factory=list)  # 首选比赛的比分预测
    top_match_label: str = ""  # 首选比赛标识
    # ── Pipeline production dashboard fields ──
    executive_summary: DailyExecutiveSummary | None = None
    data_quality: DataQuality | None = None
    generated_at: datetime | None = None
    pipeline_version: str | None = None


# Legacy alias for backward compatibility
DashboardData = DailyDashboardData
