"""Dashboard HTML 渲染器。

从 DashboardData 生成 Bloomberg/Opta 风格的深色主题 HTML。
无外部模板引擎依赖——纯 Python 字符串构建。
所有缺失数据统一显示 "暂无数据"，绝不臆造数值。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.dashboard.types import (
    AccumulatorSuggestion,
    AIQA,
    AIQAItem,
    AIReasoning,
    AvoidMatch,
    BestOpportunity,
    ConfidenceBreakdown,
    ConfidenceComponent,
    ConfidenceRadar,
    CounterfactualExplanation,
    DailyDashboardData,
    DailyExecutiveSummary,
    DailyRiskProfile,
    DataQuality,
    DataQualityItem,
    DecisionInfo,
    DecisionStep,
    DecisionTimelineEntry,
    DecisionTriggers,
    FixtureInfo,
    FootballReasoning,
    GoalscorerInfo,
    InjuryDashboard,
    LineupDashboard,
    MarketInfo,
    MarketMovement,
    MatchCentreDashboard,
    MatchDashboardData,
    ModelAvailability,
    ModelConsensusRow,
    ModelProbabilities,
    NoBetCheckItem,
    NoBetChecks,
    OddsInfo,
    OddsTimelinePoint,
    OverUnderAnalysis,
    RecentFormDashboard,
    RiskBreakdown,
    RiskBreakdownItem,
    RiskItem,
    ScenarioInfo,
    ScorelineInfo,
    StandingsDashboard,
    TopPick,
    TopRecommendation,
    TriggerCondition,
    TVBroadcastDashboard,
    ValueInfo,
    ValueOpportunity,
)

NA = "暂无数据"


def _val(value: Any, fmt: str | None = None, default: str = NA) -> str:
    """安全格式化值，None 或空返回默认值。"""
    if value is None:
        return default
    if isinstance(value, float) and fmt:
        return f"{value:{fmt}}"
    if isinstance(value, float):
        return f"{value:.3f}"
    s = str(value).strip()
    return s if s else default


def _pct(value: float | None) -> str:
    if value is None:
        return NA
    return f"{value * 100:.1f}%"


_STATUS_CN = {"upcoming": "即将开始", "live": "进行中", "closed": "已结束"}


def _status_cn(status: str | None) -> str:
    """将状态值映射为中文显示。"""
    if status is None:
        return "未知"
    return _STATUS_CN.get(status.lower(), status)


def _classification_badge(cls: str | None) -> str:
    if cls is None:
        return '<span class="badge watch">持续观察</span>'
    cls_upper = cls.upper().strip()
    if cls_upper == "BET":
        return '<span class="badge bet">建议投注</span>'
    elif cls_upper == "NO BET":
        return '<span class="badge no-bet">不建议投注</span>'
    else:
        return '<span class="badge watch">持续观察</span>'


def _classification_css_class(cls: str | None) -> str:
    """将分类映射为 CSS class 名。"""
    if cls is None:
        return "watch"
    cls_upper = cls.upper().strip()
    if cls_upper == "BET":
        return "bet"
    elif cls_upper == "NO BET":
        return "no-bet"
    return "watch"


class DashboardRenderer:
    """数据驱动的 HTML 仪表盘渲染器。"""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_daily_overview(self, data: DailyDashboardData) -> str:
        parts: list[str] = [
            self._head(f"每日比赛看板 | {data.date}"),
            '<div class="container">',
            self._daily_hero(data),
            # ── Provider Health Section ──
            self._provider_health_section(),
            # ── Enhancement 3: Run Timeline ──
            self._run_timeline_section(),
            # ── Enhancement 4: ROI Dashboard ──
            self._roi_dashboard_section(),
            # ── V3.1: AI 最终推荐 (最顶层大红卡片) ──
            self._ai_final_recommendation(data),
            # ── V3.1: 今日最佳推荐 (星级排序, 3-5 张卡片) ──
            self._todays_best_recommendations(data),
            # ── V3.1: 今日比赛 (可折叠卡片, 首选默认展开) ──
            '<div class="section">',
            '<h2>今日比赛</h2>',
        ]
        for i, match in enumerate(data.matches):
            parts.append(self._render_match_card_v3(match, index=i + 1, total=len(data.matches)))
        parts.append('</div>')
        # ── V3.1: 风险管理 (精简摘要条) ──
        if data.risk_profile:
            parts.append(self._risk_summary_bar(data.risk_profile))
        # ── V3.1: 详细分析 (可折叠, 默认收起) ──
        parts.append(self._detailed_analysis_section(data))
        parts.append(self._footer(data.generated_at, data.pipeline_version))
        parts.append("</div></body></html>")
        return "\n".join(parts)

    def render_match_detail(self, data: MatchDashboardData) -> str:
        home = data.fixture.home_team or "Home"
        away = data.fixture.away_team or "Away"
        title = f"{home} vs {away} | 比赛看板"

        parts: list[str] = [
            self._head(title),
            '<div class="container">',
            self._match_hero(data),
            # ── V2 Section 1: AI 执行摘要 (最高优先级, 置于 decision_summary 之上) ──
            self._section_no_divider("AI 执行摘要", self._executive_summary_display(data.executive_summary)) if data.executive_summary else "",
            self._decision_summary(data),
            # ── V3: NO-BET 检查清单 (WATCH/NO BET 时展示) ──
            (self._section_no_divider("为什么不投注？", self._why_not_bet_display(data.nobet_checks))
             if data.nobet_checks and (data.decision.classification or "").upper().strip() in ("NO BET", "WATCH") else ""),
            # ── V3.1: 反事实解释 (为什么不是其他选项) ──
            self._section_no_divider("为什么不是？", self._counterfactual_display(data)) if data.counterfactual else "",
            self._ai_reasoning_section(data.ai_reasoning),
            # ── V2 Section 2: 足球分析 ──
            self._section_no_divider("足球分析", self._football_reasoning_display(data.football_reasoning)) if data.football_reasoning else "",
            # ── V3.1: 信心雷达 (替换旧置信度构成, 优先雷达) ──
            self._section_no_divider("信心雷达", self._confidence_radar_display(data.confidence_radar)) if data.confidence_radar else "",
            self._section("置信度构成", self._confidence_breakdown_enhanced(
                data.confidence_breakdown, data.decision.confidence_score
            )),
            self._section("比赛信息", self._fixture_info_table(data.fixture)),
            # ── V2 Section 7: 市场动向 ──
            self._section_no_divider("市场动向", self._market_movement_display(data.market_movement)) if data.market_movement else "",
            self._section("赔率", self._odds_display(data.odds)),
            # ── V3.1: 赔率时间线 ──
            self._section_no_divider("赔率变化时间轴", self._odds_timeline_display(data.odds_timeline)) if data.odds_timeline else "",
            self._section("模型概率", self._probabilities_display(data.probabilities)),
            self._section("价值评估", self._value_display(data.value)),
            self._section("决策引擎", self._decision_display(data.decision)),
            self._section("决策流程", self._decision_flow_display(data.decision_flow)),
            self._section("模型可用性", self._model_availability_display(data.model_availability)),
            # ── V2 Section 6: 模型共识投票面板 (替换旧表格) ──
            self._section_no_divider("模型投票", self._model_consensus_voting(data.model_consensus, data.decision)),
            self._section("推荐市场", self._recommended_markets(data)),
            # ── V2 Section 3: 大小球分析 (插入在推荐市场与比分预测之间) ──
            self._section_no_divider("大小球分析", self._over_under_display(data.over_under)) if data.over_under else "",
            # ── V2 Section 4: 比分可视化 (替换旧比分预测) ──
            self._section_no_divider("比分预测", self._correct_scores_enhanced(data.correct_scores, data.model_availability)),
            # ── V2 Section 5: 进球球员预测 (替换旧) ──
            self._section_no_divider("进球球员预测", self._goalscorers_enhanced(data.goalscorers)),
            # ── V2 Section 8: 风险评估面板 ──
            self._section_no_divider("风险评估", self._risk_breakdown_display(data.risk_breakdown)) if data.risk_breakdown else "",
            self._section("风险摘要", self._risk_summary(data.risk_items)),
            self._section(
                "决策时间线",
                self._decision_timeline_display(
                    data.decision_timeline,
                    data.decision.classification or ""
                )
            ),
            self._section(
                "升级/降级条件",
                self._triggers_display(data.triggers)
            ),
            # ── V2 Section 9: 数据质量 ──
            self._section_no_divider("数据质量", self._data_quality_display(data.data_quality)) if data.data_quality else "",
            # ── V2 Section 10: AI 互动 Q&A (仅在有推荐时展示) ──
            self._section_no_divider("AI 问答", self._ai_qa_display(data.ai_qa)) if (data.ai_qa and data.ai_qa.items) else "",
        ]

        if data.weather:
            parts.append(self._section("天气", f'<p class="info-text">{data.weather}</p>'))

        if data.injuries:
            parts.append(self._section("伤病情况", f'<p class="info-text">{data.injuries}</p>'))

        # ── Sportmonks Phase 3: Enhancement Sections ──
        parts.append(self._section("联赛积分表", self._standings_display(data.standings)))

        parts.append(self._section("近期状态", self._recent_form_display(data)))

        parts.append(self._section("伤病 & 停赛", self._injury_dashboard_display(data.injury_dashboard)))

        parts.append(self._section("阵容", self._lineup_display(data.lineup)))

        parts.append(self._section("比赛中心", self._match_centre_display(data.match_centre)))

        parts.append(self._section("电视转播", self._tv_broadcast_display(data.tv_broadcast)))

        parts.append(self._section("模拟场景", self._scenarios_display(data.scenarios)))

        if data.data_completeness is not None:
            parts.append(
                self._section(
                    "数据完整性",
                    f'<p class="info-text">{data.data_completeness:.1f}% '
                    f'(证据：{_val(data.evidence_level)})</p>',
                )
            )

        parts.append(self._footer(data.generated_at))
        parts.append("</div></body></html>")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    @staticmethod
    def _section_no_divider(title: str, body: str) -> str:
        """V2: 无 divider 的 Section（用于独立样式 card 内嵌 section 框架）。"""
        if not body.strip():
            return ""
        return (
            f'<div class="section">\n'
            f'  <h2>{title}</h2>\n'
            f"  {body}\n"
            f"</div>"
        )

    @staticmethod
    def _section(title: str, body: str) -> str:
        return (
            f'<div class="section">\n'
            f'  <h2>{title}</h2>\n'
            f'  <div class="divider"></div>\n'
            f"  {body}\n"
            f"</div>"
        )

    @staticmethod
    def _provider_health_section() -> str:
        """Render Provider Health section from data/provider_health.json.
        Enhancement 1: 30-day uptime sparkline. Enhancement 2: response time warnings."""
        health_path = Path(__file__).resolve().parents[2] / "data" / "provider_health.json"
        if not health_path.exists():
            return '<div class="section"><h2>Provider Health</h2><p class="info-text">暂无数据 — 运行 health_check 后生成</p></div>'

        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return '<div class="section"><h2>Provider Health</h2><p class="info-text">数据读取失败</p></div>'

        providers = [
            ("api_football", "API-Football"),
            ("odds_api", "The Odds API"), ("weather_api", "WeatherAPI"),
            ("postgresql", "PostgreSQL"), ("redis", "Redis"),
            ("openai", "GPT / OpenAI"),
        ]

        def _sparkline(history: list) -> str:
            """Build ASCII sparkline from uptime history. Max 30 chars wide."""
            if not history:
                return ""
            values = [h.get("uptime", 100) for h in history[-30:]]
            max_v = max(values) if values else 100
            min_v = min(values) if values else 100
            rng = max_v - min_v if max_v != min_v else 1
            chars = "▁▂▃▄▅▆▇█"
            result = []
            for v in values:
                idx = int((v - min_v) / rng * (len(chars) - 1))
                idx = max(0, min(idx, len(chars) - 1))
                # Color: green if all >= 99, yellow if any < 99
                has_low = any(h.get("uptime", 100) < 99 for h in history[-30:])
                color = "#9e6a03" if has_low else "#238636"
                result.append(f'<span style="color:{color};font-size:11px">{chars[idx]}</span>')
            return "".join(result)

        def _resp_indicator(p: dict) -> str:
            """Response time with baseline comparison and warning."""
            current = p.get("response_time_ms", 0)
            baseline = p.get("resp_baseline_avg_ms", 0)
            rng = p.get("resp_baseline_range", [0, 0])
            alert = p.get("resp_alert", "OK")
            if alert == "CRITICAL":
                icon = '<span style="color:#da3633;font-weight:700" title="CRITICAL: >5x baseline">🔴</span>'
            elif alert == "WARNING":
                icon = '<span style="color:#9e6a03;font-weight:700" title="WARNING: >2x baseline">⚠</span>'
            else:
                icon = '<span style="color:#238636">✓</span>'
            if baseline > 0 and rng[1] > 0:
                return f'{icon} {current}ms <span style="font-size:10px;color:#8b949e">(avg:{baseline:.0f}ms range:{rng[0]}-{rng[1]}ms)</span>'
            return f'{icon} {current}ms'

        rows: list[str] = []
        for key, label in providers:
            p = health.get(key, {})
            if not isinstance(p, dict):
                continue
            uptime = p.get("uptime", 0)
            if uptime >= 99:
                color = "#238636"
            elif uptime >= 95:
                color = "#9e6a03"
            else:
                color = "#da3633"

            history = p.get("history", [])
            spark = _sparkline(history) if history else ""

            quota_rem = p.get("quota_remaining", 0)
            quota_max = 100
            quota_pct = min(100, max(0, int(quota_rem / quota_max * 100))) if quota_max > 0 else 0
            calls_today = p.get("calls_today", 0)
            calls_month = p.get("calls_this_month", 0)

            resp_cell = _resp_indicator(p)

            rows.append(
                f'<tr>'
                f'<td style="font-weight:600;white-space:nowrap">{label}</td>'
                f'<td style="white-space:nowrap"><span style="color:{color};font-weight:700">{uptime}%</span>'
                f'<br><span style="font-size:10px">{spark}</span></td>'
                f'<td style="white-space:nowrap;font-size:12px">{resp_cell}</td>'
                f'<td>'
                f'<div style="background:#21262d;border-radius:4px;height:8px;width:100%">'
                f'<div style="background:#58a6ff;border-radius:4px;height:8px;width:{quota_pct}%"></div>'
                f'</div>'
                f'<span style="font-size:11px;color:#8b949e">{quota_rem} left</span>'
                f'</td>'
                f'<td style="font-size:12px;color:#8b949e">今日:{calls_today} 月:{calls_month}</td>'
                f'</tr>'
            )

        return (
            '<div class="section">\n'
            '<h2>Provider Health</h2>\n'
            '<table style="width:100%;border-collapse:collapse;font-size:13px">\n'
            '<tr><th style="text-align:left;padding:6px 10px">Provider</th>'
            '<th style="text-align:left;padding:6px 10px">Uptime (30d)</th>'
            '<th style="text-align:left;padding:6px 10px">Response</th>'
            '<th style="text-align:left;padding:6px 10px">Quota</th>'
            '<th style="text-align:left;padding:6px 10px">Calls</th></tr>\n'
            + "\n".join(rows) +
            '\n</table>\n</div>'
        )

    @staticmethod
    def _run_timeline_section() -> str:
        """Enhancement 3: Render run timeline from data/run_timeline.json."""
        timeline_path = Path(__file__).resolve().parents[2] / "data" / "run_timeline.json"
        if not timeline_path.exists():
            return ""

        try:
            entries = json.loads(timeline_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""

        if not entries:
            return ""

        # Get today's entries only, last 20
        from datetime import date as dt_date
        today = dt_date.today().isoformat()
        # Timeline entries don't have date field, so take last 20
        recent = entries[-20:]

        status_icons = {
            "success": '<span style="color:#238636">&#x2714;</span>',
            "pass": '<span style="color:#238636">&#x2714;</span>',
            "failed": '<span style="color:#da3633">&#x2718;</span>',
            "running": '<span style="color:#9e6a03">&#x23F3;</span>',
            "partial": '<span style="color:#9e6a03">&#x26A0;</span>',
        }
        task_labels = {
            "health_check": "ProviderHealthCheck",
            "daily_run": "DailyProductionRun",
            "recovery_check": "ProductionRecoveryCheck",
            "pre_kickoff": "PreKickoffValidation",
            "settlement_fallback": "SettlementFallback",
            "daily_report": "DailyPerformanceReport",
            "weekly_report": "WeeklyPerformanceReport",
            "dashboard_refresh": "DashboardRefresh",
        }

        rows: list[str] = []
        for e in recent:
            time_str = e.get("time", "--:--")
            task = e.get("task", "")
            status = e.get("status", "")
            dur = e.get("duration_s", 0)
            details = e.get("details", "")
            if isinstance(dur, (int, float)) and dur > 0:
                dur_str = f"{dur:.1f}s"
            else:
                dur_str = ""
            icon = status_icons.get(status, '<span style="color:#8b949e">?</span>')
            label = task_labels.get(task, task)
            status_class = "pass" if status in ("success", "pass") else ("fail" if status == "failed" else "pending")

            row = (
                f'<tr class="timeline-{status_class}">'
                f'<td style="white-space:nowrap;color:#8b949e;font-size:12px;padding:3px 8px">{time_str}</td>'
                f'<td style="padding:3px 8px">{icon}</td>'
                f'<td style="font-weight:600;padding:3px 8px">{label}</td>'
                f'<td style="color:#8b949e;font-size:12px;padding:3px 8px">{dur_str}</td>'
                f'<td style="color:#8b949e;font-size:11px;padding:3px 8px">{details}</td>'
                f'</tr>'
            )
            rows.append(row)

        # Add pending tasks for today
        pending = [
            ("17:00", "DashboardRefresh-1700", "&#x23F3; PENDING", ""),
            ("23:00", "SettlementFallback", "&#x23F3; PENDING", ""),
            ("23:30", "DailyPerformanceReport", "&#x23F3; PENDING", ""),
        ]
        existing_times = {e.get("time", "") for e in entries}
        for pt, pn, pi, pd in pending:
            if pt not in existing_times:
                rows.append(
                    f'<tr class="timeline-pending">'
                    f'<td style="white-space:nowrap;color:#8b949e;font-size:12px;padding:3px 8px">{pt}</td>'
                    f'<td style="padding:3px 8px"><span style="color:#8b949e">&#x23F3;</span></td>'
                    f'<td style="font-weight:600;padding:3px 8px;color:#8b949e">{pn}</td>'
                    f'<td style="color:#8b949e;font-size:12px;padding:3px 8px"></td>'
                    f'<td style="color:#8b949e;font-size:11px;padding:3px 8px">PENDING</td>'
                    f'</tr>'
                )

        return (
            '<div class="section">\n'
            '<h2>Run Timeline</h2>\n'
            '<table style="width:100%;border-collapse:collapse;font-size:13px">\n'
            '<tr><th style="text-align:left;padding:3px 8px">Time</th>'
            '<th style="text-align:left;padding:3px 8px"></th>'
            '<th style="text-align:left;padding:3px 8px">Task</th>'
            '<th style="text-align:right;padding:3px 8px">Dur</th>'
            '<th style="text-align:left;padding:3px 8px">Details</th></tr>\n'
            + "\n".join(rows) +
            '\n</table>\n</div>'
        )

    @staticmethod
    def _roi_dashboard_section() -> str:
        """Enhancement 4: Render ROI dashboard from data/roi_metrics.json."""
        roi_path = Path(__file__).resolve().parents[2] / "data" / "roi_metrics.json"
        if not roi_path.exists():
            return ""

        try:
            roi = json.loads(roi_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""

        if not roi:
            return ""

        def _arrow(val: float, is_pct: bool = True) -> str:
            if val > 0:
                return f'<span style="color:#10b981">&#x25B2;</span>'
            elif val < 0:
                return f'<span style="color:#ef4444">&#x25BC;</span>'
            return ""

        cards: list[str] = []

        # Row 1: Weekly ROI | 30-Day ROI | CLV
        w = roi.get("weekly", {})
        m = roi.get("monthly", {})
        c = roi.get("clv", {})

        w_roi = w.get("roi_pct", 0)
        m_roi = m.get("roi_pct", 0)
        cards.append(
            '<div class="roi-card-row">'
            f'<div class="roi-card">'
            f'<div class="roi-label">Weekly ROI</div>'
            f'<div class="roi-value">{"+" if w_roi >= 0 else ""}{w_roi}% {_arrow(w_roi)}</div>'
            f'<div class="roi-sub">({w.get("won", 0)}/{w.get("bets", 0)} bets)</div>'
            f'</div>'
            f'<div class="roi-card">'
            f'<div class="roi-label">30-Day ROI</div>'
            f'<div class="roi-value">{"+" if m_roi >= 0 else ""}{m_roi}% {_arrow(m_roi)}</div>'
            f'<div class="roi-sub">({m.get("won", 0)}/{m.get("bets", 0)} bets)</div>'
            f'</div>'
            f'<div class="roi-card">'
            f'<div class="roi-label">CLV</div>'
            f'<div class="roi-value">{c.get("avg", 0):+.1%}</div>'
            f'<div class="roi-sub">{c.get("positive_pct", 0)}% positive</div>'
            f'</div>'
            f'</div>'
        )

        # Row 2: Brier | Kelly | Avg EV
        b = roi.get("brier", {})
        k = roi.get("kelly", {})
        e = roi.get("ev", {})
        cards.append(
            '<div class="roi-card-row">'
            f'<div class="roi-card">'
            f'<div class="roi-label">Brier Score</div>'
            f'<div class="roi-value">{b.get("score", "N/A")}</div>'
            f'<div class="roi-sub">lower is better</div>'
            f'</div>'
            f'<div class="roi-card">'
            f'<div class="roi-label">Kelly Fraction</div>'
            f'<div class="roi-value">{k.get("avg_fraction", 0):.2f}</div>'
            f'<div class="roi-sub">avg stake used</div>'
            f'</div>'
            f'<div class="roi-card">'
            f'<div class="roi-label">Avg EV</div>'
            f'<div class="roi-value">{e.get("avg_pct", 0)}%</div>'
            f'<div class="roi-sub">per recommendation</div>'
            f'</div>'
            f'</div>'
        )

        # Bottom row: Win Rate + Total P&L
        wr = roi.get("win_rate", {})
        tp = roi.get("total_pnl", 0)
        cards.append(
            '<div class="roi-card-row roi-bottom">'
            f'<div class="roi-card roi-wide">'
            f'<span style="margin-right:24px"><b>Win Rate:</b> {wr.get("overall", 0)}%</span>'
            f'<span><b>Total P&L:</b> <span style="color:{"#10b981" if tp >= 0 else "#ef4444"};font-weight:700">{"+" if tp >= 0 else ""}{tp:.2f} units</span></span>'
            f'</div>'
            f'</div>'
        )

        return (
            '<div class="section">\n'
            '<h2>ROI Dashboard</h2>\n'
            + "\n".join(cards) +
            '\n</div>'
        )

    # ------------------------------------------------------------------
    # Hero banners
    # ------------------------------------------------------------------

    def _daily_hero(self, data: DailyDashboardData) -> str:
        return (
            '<div class="hero">\n'
            f'  <div class="badge upcoming">每日概览</div>\n'
            f'  <div class="score" style="font-size:2em">{data.date}</div>\n'
            f'  <div class="meta"><span>{len(data.matches)} 场比赛</span></div>\n'
            "</div>"
        )

    def _match_hero(self, data: MatchDashboardData) -> str:
        f = data.fixture
        cls = data.decision.classification
        status_class = "live" if f.status == "live" else ("upcoming" if f.status != "closed" else "closed")
        badge_text = _status_cn(f.status)
        return (
            '<div class="hero">\n'
            f'  <div class="badge {status_class}">{badge_text}</div>\n'
            f'  {_classification_badge(cls)}\n'
            '  <div class="score-row">\n'
            f'    <div class="team"><div class="name">{_val(f.home_team)}</div></div>\n'
            f'    <div class="score">vs</div>\n'
            f'    <div class="team"><div class="name">{_val(f.away_team)}</div></div>\n'
            "  </div>\n"
            f'  <div class="meta">\n'
            f'    <span>{_val(f.competition)}</span>\n'
            f'    <span>{_val(f.venue)}</span>\n'
            f'    <span>{self._fmt_time(f.start_time)}</span>\n'
            "  </div>\n"
            "</div>"
        )

    # ------------------------------------------------------------------
    # Decision Summary (决策摘要 — placed first for information hierarchy)
    # ------------------------------------------------------------------

    def _decision_summary(self, data: MatchDashboardData) -> str:
        """生成决策摘要卡片 — 单场详情页顶部。"""
        d = data.decision
        v = data.value
        cls = (d.classification or "").upper().strip()

        # Badge
        badge_label = {"BET": "建议投注", "NO BET": "不建议投注"}.get(cls, "持续观察")
        badge_css = {"BET": "bet", "NO BET": "no-bet"}.get(cls, "watch")

        # Confidence bar
        conf = d.confidence_score
        conf_display = f"{conf:.1f}%" if conf is not None else "暂无数据"
        conf_pct = max(0, min(100, conf or 0))
        conf_fill = f'<div class="ds-bar"><div class="ds-bar-fill" style="width:{conf_pct}%"></div></div>'

        # EV
        ev = v.expected_value if v else None
        ev_display = f"{ev:+.1%}" if ev is not None else "暂无数据"
        ev_class = "pos" if (ev is not None and ev > 0) else ("neg" if ev is not None else "")

        # Stake (Kelly EUR amount)
        kelly_stake = v.kelly_stake if v else None
        stake_display = f"€{kelly_stake:,.2f}" if kelly_stake is not None else "暂无数据"

        # One-liner
        oneliner = self._build_oneliner(d, ev)

        return (
            '<div class="decision-summary">\n'
            f'  <div class="ds-head">\n'
            f'    <span style="color:#64748b;font-size:.8em;letter-spacing:2px">决策摘要</span>\n'
            f'    <span class="ds-badge {badge_css}">{badge_label}</span>\n'
            f'  </div>\n'
            f'  <div class="ds-grid">\n'
            f'    <div class="ds-metric">\n'
            f'      <div class="ds-label">置信度</div>\n'
            f'      <div class="ds-value">{conf_display}</div>\n'
            f'      {conf_fill}\n'
            f'    </div>\n'
            f'    <div class="ds-metric">\n'
            f'      <div class="ds-label">期望收益（EV）</div>\n'
            f'      <div class="ds-value {ev_class}">{ev_display}</div>\n'
            f'    </div>\n'
            f'    <div class="ds-metric">\n'
            f'      <div class="ds-label">建议仓位</div>\n'
            f'      <div class="ds-value">{stake_display}</div>\n'
            f'    </div>\n'
            f'  </div>\n'
            f'  <div class="ds-oneliner">{oneliner}</div>\n'
            f'</div>'
        )

    def _decision_mini(self, data: MatchDashboardData) -> str:
        """每日概览卡片中的紧凑决策行。"""
        d = data.decision
        v = data.value
        cls = (d.classification or "").upper().strip()

        badge_label = {"BET": "建议投注", "NO BET": "不建议投注"}.get(cls, "持续观察")
        badge_css = {"BET": "bet", "NO BET": "no-bet"}.get(cls, "watch")

        conf = d.confidence_score
        conf_str = f"{conf:.0f}%" if conf is not None else "--"

        ev = v.expected_value if v else None
        ev_str = f"{ev:+.1%}" if ev is not None else "--"
        ev_color = "#10b981" if (ev is not None and ev > 0) else ("#ef4444" if ev is not None else "#94a3b8")

        return (
            f'<div class="ds-mini">\n'
            f'  <span class="ds-mini-badge {badge_css}">{badge_label}</span>\n'
            f'  <span class="ds-mini-stat">置信度 <b>{conf_str}</b></span>\n'
            f'  <span class="ds-mini-stat">EV <b style="color:{ev_color}">{ev_str}</b></span>\n'
            f'</div>'
        )

    @staticmethod
    def _build_oneliner(d: DecisionInfo, ev: float | None) -> str:
        cls = (d.classification or "").upper().strip()
        if cls == "BET":
            if ev is not None and ev > 0:
                return f"模型概率显著优于市场定价，期望收益 {ev:+.1%}，建议以凯利比例仓位投注。"
            return "模型综合评估为正向，建议按仓位执行投注。"
        if cls == "NO BET":
            if d.why_not_bet:
                return d.why_not_bet
            return "当前赔率未发现正向期望收益，不建议投注。"
        if d.confidence_killer:
            return f"核心关注点：{d.confidence_killer}。待信息明朗后评估是否升级。"
        return "当前数据尚不足以形成明确投注信号，持续跟踪中。"

    # ------------------------------------------------------------------
    # V3.1 — 反事实解释 "为什么不是？"
    # ------------------------------------------------------------------

    def _counterfactual_display(self, data: MatchDashboardData) -> str:
        """V3.1: 为什么不是其他选项？反事实解释。"""
        cf = data.counterfactual
        if cf is None:
            return ""

        cls = (data.decision.classification or "").upper().strip()
        blocks: list[str] = ['<div class="v31-cfact">']

        if cf.why_not_away:
            blocks.append(
                '<div class="v31-cf-item">'
                '<div class="v31-cf-q">为什么不推荐客胜？</div>'
                f'<div class="v31-cf-a">{cf.why_not_away}</div>'
                '</div>'
            )
        if cf.why_not_draw:
            blocks.append(
                '<div class="v31-cf-item">'
                '<div class="v31-cf-q">为什么不是平局？</div>'
                f'<div class="v31-cf-a">{cf.why_not_draw}</div>'
                '</div>'
            )
        if cf.why_not_opposite_ou:
            blocks.append(
                '<div class="v31-cf-item">'
                '<div class="v31-cf-q">为什么不推荐反向大小球？</div>'
                f'<div class="v31-cf-a">{cf.why_not_opposite_ou}</div>'
                '</div>'
            )
        if cf.why_still_watch and cls == "WATCH":
            blocks.append(
                '<div class="v31-cf-item" style="border-left-color:#f59e0b">'
                '<div class="v31-cf-q" style="color:#f59e0b">为什么仍是 WATCH？</div>'
                f'<div class="v31-cf-a">{cf.why_still_watch}</div>'
                '</div>'
            )

        if not blocks[1:]:
            return ""

        blocks.append('</div>')
        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # V3.1 — 信心雷达 (CSS pentagon)
    # ------------------------------------------------------------------

    def _confidence_radar_display(self, radar: ConfidenceRadar | None) -> str:
        """V3.1: 五维信心雷达条。"""
        if radar is None:
            return ""

        dims = [
            ("模型一致性", radar.model_consensus),
            ("数据完整度", radar.data_completeness),
            ("市场效率", radar.market_efficiency),
            ("基本面", radar.fundamentals),
            ("风险控制", radar.risk_control),
        ]

        bars = ""
        for name, val in dims:
            pct = max(0, min(100, val))
            color = "#10b981" if pct >= 75 else ("#f59e0b" if pct >= 50 else "#ef4444")
            bars += (
                f'<div class="radar-row">'
                f'<span class="radar-label">{name}</span>'
                f'<span class="radar-bar-wrap">'
                f'<span class="radar-bar" style="width:{pct}%;background:{color}"></span>'
                f'</span>'
                f'<span class="radar-val">{int(pct)}</span>'
                f'</div>'
            )

        label_html = f'<div class="radar-total-label">{radar.label}</div>' if radar.label else ""
        return (
            f'<div class="v31-radar">'
            f'<div class="radar-header">信心雷达</div>'
            f'{bars}'
            f'{label_html}'
            f'</div>'
        )

    # ------------------------------------------------------------------
    # V3.1 — 增强置信度构成 (彩色 progress bars)
    # ------------------------------------------------------------------

    def _confidence_breakdown_enhanced(
        self, cb: ConfidenceBreakdown | None, total: float | None
    ) -> str:
        """V3.1: 彩色模型贡献条。"""
        if cb is None or not cb.components:
            return f'<p class="info-text">{NA}</p>'

        COLORS = [
            "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b",
            "#ef4444", "#06b6d4", "#ec4899", "#84cc16",
        ]

        max_contrib = max(abs(c.contribution) for c in cb.components)
        bars: list[str] = []
        for i, c in enumerate(cb.components):
            sign = "+" if c.contribution >= 0 else ""
            color = COLORS[i % len(COLORS)]
            bar_pct = int(abs(c.contribution) / max(max_contrib, 1) * 100)
            bars.append(
                f'<div class="mb-row">'
                f'<span class="mb-name">{c.name}</span>'
                f'<span class="mb-bar-wrap">'
                f'<span class="mb-bar" style="width:{bar_pct}%;background:{color}"></span>'
                f'</span>'
                f'<span class="mb-val" style="color:{color}">{sign}{c.contribution}</span>'
                f'</div>'
            )

        total_str = f"<div class='mb-total'>总计 <span>{total:.0f}</span></div>" if total is not None else ""
        return "\n".join(bars) + total_str

    # ------------------------------------------------------------------
    # V3.1 — 赔率变化时间轴
    # ------------------------------------------------------------------

    def _odds_timeline_display(self, timeline: list[OddsTimelinePoint]) -> str:
        """V3.1: 赔率变化时间轴 (垂直条)。"""
        if not timeline:
            return ""

        max_odds = max(pt.odds for pt in timeline)
        min_odds = min(pt.odds for pt in timeline)
        span = max_odds - min_odds if max_odds != min_odds else 1

        rows: list[str] = []
        for pt in timeline:
            bar_pct = int((pt.odds - min_odds) / span * 100)
            ts_display = f'<span class="ot-ts">{pt.timestamp}</span>' if pt.timestamp else ""
            rows.append(
                f'<div class="ot-row">'
                f'<span class="ot-label">{pt.label}</span>'
                f'<span class="ot-odds">{pt.odds:.2f}</span>'
                f'<span class="ot-bar-wrap"><span class="ot-bar" style="width:{bar_pct}%"></span></span>'
                f'{ts_display}'
                f'</div>'
            )

        return (
            '<div class="v31-odds-timeline">'
            + "\n".join(rows)
            + '</div>'
        )

    # ------------------------------------------------------------------
    # V3.1 — 可折叠比赛卡片 (日概览)
    # ------------------------------------------------------------------

    def _render_match_card_v3(self, data: MatchDashboardData, index: int = 1, total: int = 1) -> str:
        """V3.1: 可折叠比赛卡片 — 默认只展开首选, 其余收起。"""
        f = data.fixture
        home = f.home_team or "Home"
        away = f.away_team or "Away"
        cls = data.decision.classification or ""
        cls_upper = cls.upper().strip()
        is_top = (index == 1)
        open_attr = " open" if is_top else ""

        badge_class = {"BET": "badge-bet", "WATCH": "badge-watch", "NO BET": "badge-nobet", "INSUFFICIENT DATA": "badge-insufficient"}.get(cls_upper, "badge-watch")
        badge_text = {"BET": "BET", "WATCH": "WATCH", "NO BET": "NO BET", "INSUFFICIENT DATA": "INSUFFICIENT DATA"}.get(cls_upper, cls)

        ev = data.value.expected_value
        conf = data.decision.confidence_score
        kelly_stake = data.value.kelly_stake
        ev_str = f"EV {ev:+.1%}" if ev is not None else "EV --"
        ev_color = "#10b981" if (ev is not None and ev > 0) else ("#ef4444" if ev is not None else "#94a3b8")
        conf_str = f"Conf {conf:.0f}" if conf is not None else "Conf --"
        kelly_str = f"€{kelly_stake:,.2f}" if kelly_stake is not None else "Kelly --"

        stars = self._compute_stars(ev)
        summary = f"{home} vs {away}"
        venue = f.venue or ""
        comp = f.competition or ""
        start = self._fmt_time(f.start_time)
        meta = " · ".join(x for x in [comp, venue, start] if x)

        body_parts = [
            f'<div class="v31-mc-meta">{meta}</div>',
        ]
        if data.executive_summary:
            body_parts.append(self._executive_summary_display(data.executive_summary))
        if data.counterfactual:
            body_parts.append(self._counterfactual_display(data))
        if data.confidence_radar:
            body_parts.append(f'<div class="section"><h2>信心雷达</h2>{self._confidence_radar_display(data.confidence_radar)}</div>')
        if data.odds_timeline:
            body_parts.append(f'<div class="section"><h2>赔率变化时间轴</h2>{self._odds_timeline_display(data.odds_timeline)}</div>')
        if data.market_movement:
            body_parts.append(self._market_movement_display(data.market_movement))
        if data.over_under:
            body_parts.append(self._over_under_display(data.over_under))
        body_parts.append(self._correct_scores_enhanced(data.correct_scores, data.model_availability))
        if data.risk_breakdown:
            body_parts.append(self._risk_breakdown_display(data.risk_breakdown))
        if data.nobet_checks and cls_upper in ("NO BET", "WATCH"):
            body_parts.append(self._why_not_bet_display(data.nobet_checks))
        if data.ai_qa and data.ai_qa.items:
            body_parts.append(self._ai_qa_display(data.ai_qa))

        return (
            f'<details class="v31-match-card"{open_attr}>'
            f'<summary class="v31-mc-summary">'
            f'<span class="v31-mc-stars">{self._star_html(stars)}</span>'
            f'<span class="v31-mc-teams">{home} <span class="v31-mc-vs">vs</span> {away}</span>'
            f'<span class="v31-mc-badge {badge_class}">{badge_text}</span>'
            f'<span class="v31-mc-stats">'
            f'<span class="v31-mc-ev" style="color:{ev_color}">{ev_str}</span>'
            f'<span class="v31-mc-conf">{conf_str}</span>'
            f'<span class="v31-mc-kelly">{kelly_str}</span>'
            f'</span>'
            f'</summary>'
            f'<div class="v31-mc-body">'
            + "\n".join(body_parts)
            + f'</div>'
            f'</details>'
        )

    # ------------------------------------------------------------------
    # V3.1 — 风险管理摘要条
    # ------------------------------------------------------------------

    def _risk_summary_bar(self, rp: DailyRiskProfile) -> str:
        """V3.1: 精简风险管理摘要条。"""
        exposure = rp.max_exposure_pct or 0
        stake = rp.total_suggested_stake or 0
        trades = rp.recommended_trade_count or 0
        exp_color = "#10b981" if exposure <= 5 else ("#f59e0b" if exposure <= 10 else "#ef4444")
        return (
            '<div class="v31-risk-bar">'
            '<div class="v31-risk-grid">'
            f'<div class="v31-risk-item"><span class="v31-risk-label">最大敞口</span><span class="v31-risk-val" style="color:{exp_color}">{exposure:.1f}%</span></div>'
            f'<div class="v31-risk-item"><span class="v31-risk-label">建议总仓位</span><span class="v31-risk-val">{stake:.1f}%</span></div>'
            f'<div class="v31-risk-item"><span class="v31-risk-label">建议交易数</span><span class="v31-risk-val">{trades}</span></div>'
            '</div></div>'
        )

    # ------------------------------------------------------------------
    # V3.1 — 详细分析 (可折叠)
    # ------------------------------------------------------------------

    def _detailed_analysis_section(self, data: DailyDashboardData) -> str:
        """V3.1: 可折叠的详细分析区。"""
        parts = ['<details class="v31-detail-section">',
                 '<summary class="v31-ds-summary">详细分析</summary>',
                 '<div class="v31-ds-body">']
        parts.append(self._section("建议回避", self._avoid_matches_display_v3(data.avoid_matches, data.matches)))
        parts.append(self._section("模拟串关建议", self._accumulator_display(data.accumulator_suggestions)))
        parts.append(self._section("每日风险管理", self._daily_risk_display(data.risk_profile)))
        parts.append('</div></details>')
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # V3 — AI 最终推荐 (日概览最顶层)
    # ------------------------------------------------------------------

    def _ai_final_recommendation(self, data: DailyDashboardData) -> str:
        """V3: AI 最终推荐 — 日概览页最顶层的统一决策卡片。"""
        text = data.ai_final_recommendation.strip()
        recs = data.top_recommendations

        # Build fallback from top_recommendations if ai_final_recommendation is empty
        if not text:
            lines: list[str] = []
            top_win = None      # first 胜平负
            top_ou = None       # first 大小球
            top_score = None    # first with score-like selection
            for r in recs:
                if top_win is None and "胜平负" in r.market:
                    top_win = r
                if top_ou is None and "大小球" in r.market:
                    top_ou = r
                if top_score is None and r.selection and "-" in r.selection and len(r.selection) == 3:
                    top_score = r
            if top_win:
                lines.append(f"推荐市场: {top_win.market} — {top_win.selection}")
            if top_ou:
                lines.append(f"推荐大小球: {top_ou.selection}")
            if top_score:
                lines.append(f"推荐比分: {top_score.selection}")
            if recs:
                lines.append(f"建议仓位: {recs[0].stake * 100:.1f}%" if recs[0].stake else "建议仓位: 待定")
                lines.append(f"一句话: {recs[0].reason}" if recs[0].reason else "一句话: 今日重点关注主胜机会。")
            if not lines:
                return ""
            text = "\n".join(lines)

        return (
            '<div class="v3-final-rec">\n'
            '  <div class="v3fr-header">\n'
            '    <span class="v3fr-icon">&#9670;</span>\n'
            '    <div>\n'
            '      <div class="v3fr-title">AI 最终推荐</div>\n'
            '      <div class="v3fr-subtitle">FINAL RECOMMENDATION</div>\n'
            '    </div>\n'
            '  </div>\n'
            f'  <div class="v3fr-body">{text.replace(chr(10), "<br>")}</div>\n'
            '</div>'
        )

    # ------------------------------------------------------------------
    # V3 — 今日最佳推荐 (合并三个旧 Section)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_stars(ev: float | None) -> int:
        """V3: 基于 EV 计算星级。"""
        if ev is None:
            return 0
        if ev >= 0.05:
            return 5
        if ev >= 0.03:
            return 4
        if ev >= 0.02:
            return 3
        if ev >= 0.01:
            return 2
        if ev > 0:
            return 1
        return 0

    @staticmethod
    def _star_html(stars: int) -> str:
        filled = "★" * stars
        empty = "☆" * (5 - stars)
        return f'<span class="v3-stars s{stars}">{filled}{empty}</span>'

    def _todays_best_recommendations(self, data: DailyDashboardData) -> str:
        """V3: 今日最佳推荐 — 合并 TopPick + BestOpportunity + ValueOpportunity 为统一星级卡片。"""
        recs = data.top_recommendations

        # Fallback: build from top_picks + best_opportunities + value_opportunities
        if not recs:
            merged: list[dict] = []
            # from top_picks
            for p in data.top_picks:
                merged.append({
                    "match_label": p.match_label, "market": p.market, "selection": p.market,
                    "odds": p.odds, "model_prob": p.model_prob, "ev": p.ev,
                    "confidence": p.confidence, "stake": p.stake, "reason": p.reason,
                    "category": "精选", "risk_level": "",
                })
            # from best_opportunities
            for bo in data.best_opportunities:
                if not bo.has_qualifier:
                    continue
                merged.append({
                    "match_label": bo.match_label, "market": bo.market or bo.category,
                    "selection": bo.selection,
                    "odds": bo.odds, "model_prob": bo.model_prob, "ev": bo.ev,
                    "confidence": bo.confidence, "stake": bo.stake, "reason": bo.explanation,
                    "category": bo.category, "risk_level": bo.risk_level,
                })
            # from value_opportunities
            for vo in data.value_opportunities:
                merged.append({
                    "match_label": vo.match_label, "market": vo.market, "selection": "",
                    "odds": vo.odds, "model_prob": vo.model_prob, "ev": vo.ev,
                    "confidence": vo.confidence, "stake": None, "reason": vo.explanation or "",
                    "category": "价值机会", "risk_level": "",
                })
            # dedup by (match_label, market)
            seen = set()
            deduped = []
            for m in merged:
                key = (m["match_label"], m["market"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(m)
            # sort by EV descending
            deduped.sort(key=lambda x: x["ev"] or -999, reverse=True)
            # assign stars
            for m in deduped:
                m["stars"] = self._compute_stars(m["ev"])
            recs_typed = [TopRecommendation(**m) for m in deduped]
        else:
            # ensure stars are computed
            for r in recs:
                r.stars = self._compute_stars(r.ev)
            recs_typed = recs

        if not recs_typed:
            return (
                '<div class="v3-today-best v3-tb-empty">\n'
                '  <div class="v3tb-header">\n'
                '    <span class="v3tb-icon">&#9733;</span>\n'
                '    <div>\n'
                '      <div class="v3tb-title">今日最佳推荐</div>\n'
                '      <div class="v3tb-subtitle">TODAY\'S TOP RECOMMENDATIONS</div>\n'
                '    </div>\n'
                '  </div>\n'
                '  <div class="v3tb-empty">今日暂无符合条件的推荐</div>\n'
                '  <div class="v3tb-threshold">筛选条件：期望收益 EV &gt; 0</div>\n'
                '</div>'
            )

        # Sort by stars descending then EV descending
        recs_typed.sort(key=lambda r: (r.stars, r.ev or -999), reverse=True)

        cards: list[str] = []
        for r in recs_typed:
            ev_str = f"{r.ev:+.1%}" if r.ev is not None else NA
            conf_str = f"{r.confidence:.0f}" if r.confidence is not None else NA
            stake_str = f"{r.stake:.1%}" if r.stake is not None else NA
            odds_str = f"{r.odds:.2f}" if r.odds is not None else NA
            stars = r.stars
            ev_color = "#10b981" if (r.ev is not None and r.ev > 0) else ("#ef4444" if r.ev is not None else "#94a3b8")

            cards.append(
                f'<div class="v3tb-card">\n'
                f'  <div class="v3tb-stars-row">{self._star_html(stars)}</div>\n'
                f'  <div class="v3tb-main">\n'
                f'    <span class="v3tb-match">{r.match_label}</span>\n'
                f'    <span class="v3tb-selection">{r.selection or r.market}</span>\n'
                f'  </div>\n'
                f'  <div class="v3tb-grid">\n'
                f'    <div class="v3tb-metric">\n'
                f'      <div class="v3tb-label">EV</div>\n'
                f'      <div class="v3tb-value" style="color:{ev_color}">{ev_str}</div>\n'
                f'    </div>\n'
                f'    <div class="v3tb-metric">\n'
                f'      <div class="v3tb-label">置信度</div>\n'
                f'      <div class="v3tb-value">{conf_str}</div>\n'
                f'    </div>\n'
                f'    <div class="v3tb-metric">\n'
                f'      <div class="v3tb-label">Kelly</div>\n'
                f'      <div class="v3tb-value">{stake_str}</div>\n'
                f'    </div>\n'
                f'    <div class="v3tb-metric">\n'
                f'      <div class="v3tb-label">市场</div>\n'
                f'      <div class="v3tb-value v3tb-market">{r.market}</div>\n'
                f'    </div>\n'
                f'    <div class="v3tb-metric">\n'
                f'      <div class="v3tb-label">赔率</div>\n'
                f'      <div class="v3tb-value">{odds_str}</div>\n'
                f'    </div>\n'
                f'  </div>\n'
                f'  <div class="v3tb-reason">{r.reason or "暂无说明"}</div>\n'
                f'</div>'
            )

        return (
            '<div class="v3-today-best">\n'
            '  <div class="v3tb-header">\n'
            '    <span class="v3tb-icon">&#9733;</span>\n'
            '    <div>\n'
            '      <div class="v3tb-title">今日最佳推荐</div>\n'
            '      <div class="v3tb-subtitle">TODAY\'S TOP RECOMMENDATIONS</div>\n'
            '    </div>\n'
            '  </div>\n'
            + "\n".join(cards)
            + "\n</div>"
        )

    # ------------------------------------------------------------------
    # V3 — 正确比分 Mini 版 (日概览页)
    # ------------------------------------------------------------------

    def _correct_scores_mini(self, match_label: str, scores: list[ScorelineInfo]) -> str:
        """V3: 日概览页首选比赛的正确比分 Mini 版。"""
        if not scores:
            return ""
        max_prob = max((s.probability or 0) for s in scores)
        bars: list[str] = []
        for s in scores[:5]:  # top 5 only on overview
            p = s.probability or 0
            pct = f"{p * 100:.1f}%"
            bar_pct = int(p / max(max_prob, 0.001) * 100) if max_prob > 0 else 0
            is_max = s.is_highest or (p >= max_prob and max_prob > 0)
            bar_cls = "cs-bar-max" if is_max else "cs-bar"
            bars.append(
                f'<div class="cs-row">'
                f'<span class="cs-score">{s.scoreline}</span>'
                f'<span class="cs-bar-wrap"><span class="{bar_cls}" style="width:{bar_pct}%"></span></span>'
                f'<span class="cs-pct">{pct}</span>'
                f'</div>'
            )
        return (
            f'<div class="section">\n'
            f'  <h2>首选比分预测 — {match_label}</h2>\n'
            f'  <div class="divider"></div>\n'
            f'  <div class="cs-section">\n'
            f'    <div class="cs-header">Poisson 模型预测</div>\n'
            + "\n".join(bars) + "\n"
            f'  </div>\n'
            f'</div>'
        )

    # ------------------------------------------------------------------
    # V3 — 建议回避增强版 (含 WHY-NOT 清单)
    # ------------------------------------------------------------------

    def _avoid_matches_display_v3(self, avoids: list[AvoidMatch], matches: list[MatchDashboardData]) -> str:
        """V3: 建议回避 — 结构化 WHY-NOT 清单。"""
        if not avoids:
            # Check if ANY match has actual prediction data (not all NULL/generic)
            any_data = any(
                m.data_completeness is not None and m.data_completeness > 0
                for m in matches
            )
            if not any_data:
                return (
                    '<p class="info-text">历史数据不足，无法完成模型评估，今日无正式推荐。</p>'
                )
            return f'<p class="info-text">所有比赛已完成评估，无需要回避的比赛。</p>'

        decision_badges = {
            "NO BET": '<span class="badge no-bet" style="font-size:.75em">不建议投注</span>',
            "WATCH": '<span class="badge watch" style="font-size:.75em">持续观察</span>',
        }

        # Build a lookup for MatchDashboardData by match_label
        match_map: dict[str, MatchDashboardData] = {}
        for m in matches:
            if m.fixture.home_team and m.fixture.away_team:
                label = f"{m.fixture.home_team} vs {m.fixture.away_team}"
                match_map[label] = m

        head = "<tr><th>比赛</th><th>回避原因</th><th>决策</th></tr>"
        rows: list[str] = []
        for a in avoids:
            badge = decision_badges.get(a.decision.upper().strip(),
                                        f'<span class="badge watch" style="font-size:.75em">{a.decision}</span>')
            md = match_map.get(a.match_label)

            # Build checklist if match data with nobet_checks exists
            checklist_html = ""
            if md and md.nobet_checks and md.nobet_checks.items:
                check_items: list[str] = []
                for c in md.nobet_checks.items:
                    icon = "&#10003;" if c.passed else "&#10007;"
                    color = "#10b981" if c.passed else "#ef4444"
                    detail = f" ({c.detail})" if c.detail else ""
                    check_items.append(
                        f'<span class="v3-nb-item" style="color:{color}">{icon} {c.label}{detail}</span>'
                    )
                if md.nobet_checks.catch_all:
                    check_items.append(
                        f'<span class="v3-nb-item" style="color:#f59e0b">&#9888; {md.nobet_checks.catch_all}</span>'
                    )
                checklist_html = '<div class="v3-nb-checklist">' + "<br>".join(check_items) + '</div>'

            reason_cell = f"{a.reason}{checklist_html}" if checklist_html else a.reason
            rows.append(
                f"<tr>"
                f"<td style='font-weight:700'>{a.match_label}</td>"
                f"<td style='font-size:.85em;line-height:1.6'>{reason_cell}</td>"
                f"<td>{badge}</td>"
                f"</tr>"
            )
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    # ------------------------------------------------------------------
    # V3 — NO-BET 检查清单 (单场详情页)
    # ------------------------------------------------------------------

    def _why_not_bet_display(self, checks: NoBetChecks | None) -> str:
        """V3: 结构化 WHY-NOT-BET 清单。"""
        if checks is None or not checks.items:
            return ""

        items: list[str] = []
        all_pass = True
        for c in checks.items:
            icon = "&#10003;" if c.passed else "&#10007;"
            color = "#10b981" if c.passed else "#ef4444"
            detail = f" ({c.detail})" if c.detail else ""
            items.append(f'<div class="v3-wnb-item" style="color:{color}">{icon} {c.label}{detail}</div>')
            if not c.passed:
                all_pass = False

        if all_pass and checks.catch_all:
            items.append(f'<div class="v3-wnb-item" style="color:#f59e0b">&#9888; {checks.catch_all}</div>')

        return (
            '<div class="v3-why-not-bet">\n'
            '  <div class="v3-wnb-title">为什么不能下注？</div>\n'
            + "\n".join(items) + "\n"
            '</div>'
        )


    def _best_opportunities_display(self, opportunities: list[BestOpportunity]) -> str:
        """今日最佳机会 — 6 大类市场的最佳单笔推荐。"""
        if not opportunities:
            return (
                '<div class="best-opps">\n'
                '  <div class="best-opps-header">\n'
                '    <span class="best-opps-icon">&#9889;</span>\n'
                '    <div>\n'
                '      <div class="best-opps-title">今日最佳机会</div>\n'
                '      <div class="best-opps-subtitle">TODAY\'S BEST OPPORTUNITIES</div>\n'
                '    </div>\n'
                '  </div>\n'
                '  <div class="best-opps-empty">今日暂无最佳机会数据</div>\n'
                "</div>"
            )

        cards: list[str] = []
        risk_colors = {"低": "#10b981", "中": "#f59e0b", "高": "#ef4444"}

        for op in opportunities:
            if not op.has_qualifier:
                cards.append(
                    f'<div class="bo-card bo-no-qualifier">\n'
                    f'  <div class="bo-category">{op.category}</div>\n'
                    f'  <div class="bo-no-data">暂无符合条件的推荐</div>\n'
                    f'</div>'
                )
                continue

            odds_str = _val(op.odds, fmt=".2f")
            model_pct = _pct(op.model_prob)
            market_pct = _pct(op.market_prob)
            ev_str = f"{op.ev:+.1%}" if op.ev is not None else NA
            ev_color = (
                "#10b981" if (op.ev is not None and op.ev > 0)
                else ("#ef4444" if op.ev is not None else "#94a3b8")
            )
            conf_str = f"{op.confidence:.1f}%" if op.confidence is not None else NA
            stake_str = f"{op.stake:.1%}" if op.stake is not None else NA
            risk_color = risk_colors.get(op.risk_level, "#94a3b8")

            cards.append(
                f'<div class="bo-card">\n'
                f'  <div class="bo-head">\n'
                f'    <div class="bo-category">{op.category}</div>\n'
                f'    <div class="bo-match">{op.match_label}'
                f' &mdash; <span style="color:#ffc400">{op.selection}</span></div>\n'
                f'  </div>\n'
                f'  <div class="bo-grid">\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">赔率</div>\n'
                f'      <div class="bo-value">{odds_str}</div>\n'
                f'    </div>\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">模型概率</div>\n'
                f'      <div class="bo-value">{model_pct}</div>\n'
                f'    </div>\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">市场概率</div>\n'
                f'      <div class="bo-value">{market_pct}</div>\n'
                f'    </div>\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">期望收益 EV</div>\n'
                f'      <div class="bo-value" style="color:{ev_color}">{ev_str}</div>\n'
                f'    </div>\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">置信度</div>\n'
                f'      <div class="bo-value">{conf_str}</div>\n'
                f'    </div>\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">建议仓位</div>\n'
                f'      <div class="bo-value">{stake_str}</div>\n'
                f'    </div>\n'
                f'    <div class="bo-metric">\n'
                f'      <div class="bo-label">风险等级</div>\n'
                f'      <div class="bo-value" style="color:{risk_color}">{op.risk_level}</div>\n'
                f'    </div>\n'
                f'  </div>\n'
                f'  <div class="bo-explanation">{op.explanation}</div>\n'
                f'  {self._ai_reasoning_block(op.reasoning_bullets, "")}\n'
                f'</div>'
            )

        return (
            '<div class="best-opps">\n'
            '  <div class="best-opps-header">\n'
            '    <span class="best-opps-icon">&#9889;</span>\n'
            '    <div>\n'
            '      <div class="best-opps-title">今日最佳机会</div>\n'
            '      <div class="best-opps-subtitle">TODAY\'S BEST OPPORTUNITIES</div>\n'
            '    </div>\n'
            '  </div>\n'
            + "\n".join(cards)
            + "\n</div>"
        )

    # ------------------------------------------------------------------
    # Priority 2 — AI 推理 (Evidence-Based Reasoning)
    # ------------------------------------------------------------------

    @staticmethod
    def _ai_reasoning_block(bullets: list[str], conclusion: str) -> str:
        """AI 推理块 — 证据驱动推荐理由。无数据时显示 '暂无足够数据生成推理'。"""
        if not bullets:
            return '<div class="ai-reasoning"><div class="ai-r-empty">暂无足够数据生成推理</div></div>'

        bullet_html = "\n".join(f"<li>{b}</li>" for b in bullets)
        concl_html = f'<div class="ai-r-conclusion">{conclusion}</div>' if conclusion else ""

        return (
            '<div class="ai-reasoning">\n'
            '  <div class="ai-r-header">\n'
            '    <span class="ai-r-icon">&#128269;</span>\n'
            '    <span class="ai-r-title">AI 推理</span>\n'
            '  </div>\n'
            f'  <div class="ai-r-body">\n'
            f'    <div class="ai-r-question">为什么推荐？</div>\n'
            f'    <ul class="ai-r-bullets">\n'
            f'      {bullet_html}\n'
            f'    </ul>\n'
            f'    {concl_html}\n'
            f'  </div>\n'
            f'</div>'
        )

    def _ai_reasoning_section(self, reasoning: AIReasoning | None) -> str:
        """单场详情页 — 包装为 Section 的 AI 推理块。"""
        if reasoning is None:
            return self._section("AI 推理", self._ai_reasoning_block([], ""))
        block = self._ai_reasoning_block(reasoning.bullets, reasoning.conclusion)
        return self._section("AI 推理", block)

    # ------------------------------------------------------------------
    # Priority 3 — 置信度构成 (Confidence Breakdown)
    # ------------------------------------------------------------------

    @staticmethod
    def _confidence_breakdown_display(
        breakdown: ConfidenceBreakdown | None, fallback_score: float | None
    ) -> str:
        """置信度构成 — 各组件贡献值条形图。"""
        if breakdown is None or not breakdown.components:
            fallback_html = (
                f"置信度：{fallback_score:.1f}%（无细项数据）"
                if fallback_score is not None else "暂无数据"
            )
            return (
                '<div class="conf-breakdown">\n'
                f'  <div class="cb-header">置信度构成</div>\n'
                f'  <div class="cb-fallback">{fallback_html}</div>\n'
                f'</div>'
            )

        max_abs = max(abs(c.contribution) for c in breakdown.components)
        max_abs = max(max_abs, 1)

        rows: list[str] = []
        for c in breakdown.components:
            color = "#10b981" if c.contribution >= 0 else "#ef4444"
            bar_width = abs(c.contribution) / max_abs * 100
            sign = "+" if c.contribution >= 0 else ""
            rows.append(
                f'<div class="cb-row">\n'
                f'  <div class="cb-name">{c.name}</div>\n'
                f'  <div class="cb-bar-wrap">\n'
                f'    <div class="cb-bar" style="width:{bar_width:.0f}%;'
                f'background:{color}"></div>\n'
                f'  </div>\n'
                f'  <div class="cb-score" style="color:{color}">'
                f'{sign}{c.contribution:.0f}</div>\n'
                f'</div>'
            )

        total_color = "#10b981" if breakdown.total >= 0 else "#ef4444"

        return (
            '<div class="conf-breakdown">\n'
            '  <div class="cb-header">置信度构成</div>\n'
            + "\n".join(rows)
            + '\n  <div class="cb-divider"></div>\n'
            f'  <div class="cb-row cb-total">\n'
            f'    <div class="cb-name" style="font-weight:900;color:#fff">总计</div>\n'
            f'    <div class="cb-bar-wrap"></div>\n'
            f'    <div class="cb-score" style="color:{total_color};'
            f'font-weight:900;font-size:1.1em">{breakdown.total:.0f}</div>\n'
            f'  </div>\n'
            f'</div>'
        )

    # ------------------------------------------------------------------
    # Priority 4 — 决策时间线 (Decision Timeline)
    # ------------------------------------------------------------------

    @staticmethod
    def _decision_timeline_display(
        timeline: list[DecisionTimelineEntry], current_decision: str
    ) -> str:
        """决策时间线 — 推荐在一天中的变化历程。"""
        if not timeline:
            decision_labels = {
                "BET": "建议投注", "WATCH": "持续观察", "NO BET": "不建议投注",
            }
            label = decision_labels.get(
                current_decision.upper().strip(), current_decision
            )
            return (
                '<div class="decision-timeline">\n'
                '  <div class="dt-header">决策时间线</div>\n'
                f'  <div class="dt-unchanged">'
                f'今日推荐未发生变化 — 自首次分析起维持 <b>{label}</b></div>\n'
                f'</div>'
            )

        decision_colors = {"BET": "#10b981", "WATCH": "#f59e0b", "NO BET": "#ef4444"}
        decision_labels = {
            "BET": "建议投注", "WATCH": "持续观察", "NO BET": "不建议投注",
        }

        entries: list[str] = []
        for i, entry in enumerate(timeline):
            color = decision_colors.get(entry.decision.upper().strip(), "#94a3b8")
            label = decision_labels.get(
                entry.decision.upper().strip(), entry.decision
            )

            is_last = (i == len(timeline) - 1)
            dot_html = (
                f'<div class="dt-dot-wrap">\n'
                f'  <div class="dt-dot" style="background:{color}"></div>\n'
                + ("" if is_last else '  <div class="dt-line"></div>\n')
                + f'</div>'
            )

            entries.append(
                f'<div class="dt-entry">\n'
                f'  {dot_html}\n'
                f'  <div class="dt-content">\n'
                f'    <div class="dt-time">{entry.timestamp}</div>\n'
                f'    <div class="dt-decision">\n'
                f'      <span class="dt-badge" style="background:{color};color:#000;'
                f'padding:1px 8px;border-radius:4px;font-size:.78em;margin-right:8px">'
                f'{label}</span>\n'
                f'      {entry.reason}\n'
                f'    </div>\n'
                f'  </div>\n'
                f'</div>'
            )

            if entry.trigger_event and not is_last:
                entries.append(
                    f'<div class="dt-trigger">\n'
                    f'  <div class="dt-trigger-line"></div>\n'
                    f'  <div class="dt-trigger-label">'
                    f'&darr; {entry.trigger_event}</div>\n'
                    f'</div>'
                )

        return (
            '<div class="decision-timeline">\n'
            '  <div class="dt-header">决策时间线</div>\n'
            + "\n".join(entries)
            + "\n</div>"
        )

    # ------------------------------------------------------------------
    # Priority 5 — 升级/降级条件 (Upgrade/Downgrade Triggers)
    # ------------------------------------------------------------------

    @staticmethod
    def _triggers_display(triggers: DecisionTriggers | None) -> str:
        """升级/降级条件 — 什么情况会改变推荐？"""
        if triggers is None or (not triggers.upgrade and not triggers.downgrade):
            return (
                '<div class="triggers-section">\n'
                '  <div class="tr-content">暂无数据 — 升级和降级条件未配置</div>\n'
                f'</div>'
            )

        parts: list[str] = ['<div class="triggers-section">']

        if triggers.upgrade:
            bullets = "\n".join(
                f"<li>{t.condition}"
                + (f"（{t.threshold}）" if t.threshold else "")
                + "</li>"
                for t in triggers.upgrade
            )
            parts.extend([
                '<div class="tr-block tr-upgrade">',
                '  <div class="tr-label">升级条件</div>',
                f'  <ul class="tr-list">{bullets}</ul>',
                '</div>',
            ])

        if triggers.downgrade:
            bullets = "\n".join(
                f"<li>{t.condition}"
                + (f"（{t.threshold}）" if t.threshold else "")
                + "</li>"
                for t in triggers.downgrade
            )
            parts.extend([
                '<div class="tr-block tr-downgrade">',
                '  <div class="tr-label">降级条件</div>',
                f'  <ul class="tr-list">{bullets}</ul>',
                '</div>',
            ])

        parts.append('</div>')
        return '\n'.join(parts)

    # ------------------------------------------------------------------
    # Match card (used in daily overview)
    # ------------------------------------------------------------------

    def _render_match_card(self, data: MatchDashboardData, *, index: int, total: int) -> str:
        f = data.fixture
        home = _val(f.home_team)
        away = _val(f.away_team)
        return (
            f'<div class="section">\n'
            f'  <h2>比赛 {index}/{total}: {home} vs {away}</h2>\n'
            f'  <div class="divider"></div>\n'
            f'  {self._decision_mini(data)}\n'
            f'  <div class="cards-2">\n'
            f'    <div class="card">{self._fixture_info_compact(data)}</div>\n'
            f'    <div class="card">{self._odds_display(data.odds)}</div>\n'
            f"  </div>\n"
            f'  <div class="cards-3" style="margin-top:12px">\n'
            f'    <div class="card">{self._model_availability_display(data.model_availability)}</div>\n'
            f'    <div class="card">{self._value_display(data.value)}</div>\n'
            f'    <div class="card">{self._decision_display(data.decision)}</div>\n'
            f"  </div>\n"
            f"</div>"
        )

    # ------------------------------------------------------------------
    # Sub-displays
    # ------------------------------------------------------------------

    def _fixture_info_table(self, f: FixtureInfo) -> str:
        rows = [
            ("主队", _val(f.home_team)),
            ("客队", _val(f.away_team)),
            ("联赛", _val(f.competition)),
            ("比赛场地", _val(f.venue)),
            ("开球时间", self._fmt_time(f.start_time)),
            ("状态", _status_cn(f.status)),
            ("比分", f"{f.home_score or 0} - {f.away_score or 0}" if f.home_score is not None else NA),
        ]
        tbody = "\n".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
        return f'<table class="info-table"><tbody>{tbody}</tbody></table>'

    def _fixture_info_compact(self, data: MatchDashboardData) -> str:
        f = data.fixture
        lines = [
            f"<strong>{_val(f.home_team)}</strong> vs <strong>{_val(f.away_team)}</strong>",
            f"联赛：{_val(f.competition)}",
            f"场地：{_val(f.venue)}",
            f"时间：{self._fmt_time(f.start_time)}",
            f"状态：{_status_cn(f.status)}",
            f"数据完整性：{_val(data.data_completeness, fmt='.1f')}%",
        ]
        return "<br>".join(f'<p class="info-text">{line}</p>' for line in lines)

    def _odds_display(self, o: OddsInfo) -> str:
        rows = [
            ("主胜", _val(o.home_odds, fmt=".2f")),
            ("平局", _val(o.draw_odds, fmt=".2f")),
            ("客胜", _val(o.away_odds, fmt=".2f")),
            ("博彩公司", _val(o.bookmaker)),
        ]
        tbody = "\n".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
        return f"<h3>赔率</h3><table class='info-table'><tbody>{tbody}</tbody></table>"

    def _probabilities_display(self, p: ModelProbabilities) -> str:
        rows = [
            ("Poisson", _pct(p.poisson_home), _pct(p.poisson_draw), _pct(p.poisson_away)),
            ("Elo", _pct(p.elo_home), _pct(p.elo_draw), _pct(p.elo_away)),
            ("Ensemble", _pct(p.ensemble_home), _pct(p.ensemble_draw), _pct(p.ensemble_away)),
        ]
        head = "<tr><th>模型</th><th>主胜</th><th>平局</th><th>客胜</th></tr>"
        tbody = "\n".join(
            f"<tr><td>{name}</td><td>{h}</td><td>{d}</td><td>{a}</td></tr>"
            for name, h, d, a in rows
        )
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _value_display(self, v: ValueInfo) -> str:
        rows = [
            ("期望收益（EV）", _val(v.expected_value, fmt="+.3f")),
            ("优势", _val(v.edge, fmt="+.3f")),
            ("凯利比例", _pct(v.kelly_fraction)),
        ]
        tbody = "\n".join(f"<tr><td>{k}</td><td>{v2}</td></tr>" for k, v2 in rows)
        return f"<h3>价值评估</h3><table class='info-table'><tbody>{tbody}</tbody></table>"

    def _decision_display(self, d: DecisionInfo) -> str:
        badge = _classification_badge(d.classification)
        parts = [
            f"<h3>决策：{badge}</h3>",
        ]
        if d.confidence_score is not None:
            parts.append(f'<p class="info-text">置信度：{d.confidence_score:.1f}%</p>')
        if d.why_not_bet:
            parts.append(f'<p class="info-text"><strong>不建议投注原因：</strong> {d.why_not_bet}</p>')
        if d.confidence_killer:
            parts.append(
                f'<p class="info-text" style="color:#ef4444">'
                f'<strong>最大风险因素：</strong> {d.confidence_killer}</p>'
            )
        if not d.confidence_score and not d.why_not_bet and not d.confidence_killer:
            parts.append(f'<p class="info-text">{NA}</p>')
        return "\n".join(parts)

    def _model_availability_display(self, ma: ModelAvailability) -> str:
        rows = [
            ("Poisson", "可用" if ma.poisson else "不可用"),
            ("Elo", "可用" if ma.elo else "不可用"),
            ("Ensemble", "可用" if ma.ensemble else "不可用"),
            ("Monte Carlo", "可用" if ma.monte_carlo else "不可用"),
            ("Kelly", "可用" if ma.kelly else "不可用"),
        ]
        tbody = "\n".join(
            f"<tr><td>{name}</td><td>{status}</td></tr>" for name, status in rows
        )
        return f"<h3>数据源状态</h3><table class='info-table'><tbody>{tbody}</tbody></table>"

    # ── Sportmonks Phase 3: Enhancement Display Methods ──

    def _standings_display(self, s: StandingsDashboard | None) -> str:
        if not s or not s.available or not s.rows:
            return f'<p class="info-text">Unavailable</p>'
        head = "<tr><th>#</th><th>球队</th><th>赛</th><th>胜</th><th>平</th><th>负</th><th>GF</th><th>GA</th><th>GD</th><th>分</th></tr>"
        rows = []
        for r in s.rows:
            rows.append(
                f"<tr><td>{r.position}</td><td>{r.team_name}</td><td>{r.played}</td>"
                f"<td>{r.wins}</td><td>{r.draws}</td><td>{r.losses}</td>"
                f"<td>{r.goals_for}</td><td>{r.goals_against}</td><td>{r.goal_diff}</td>"
                f"<td><strong>{r.points}</strong></td></tr>"
            )
        tbody = "\n".join(rows)
        return f"<p class='info-text'>{s.group_name}</p><table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _recent_form_display(self, data: MatchDashboardData) -> str:
        parts = []
        for side, rf in [("主场", data.recent_form_home), ("客场", data.recent_form_away)]:
            if not rf or not rf.available:
                continue
            badge_map = {"W": '<span class="badge badge-bet">W</span>',
                         "D": '<span class="badge badge-watch">D</span>',
                         "L": '<span class="badge badge-nobet">L</span>'}
            form_badges = " ".join(badge_map.get(m.result, m.result) for m in rf.matches)
            trend_icon = rf.trend
            parts.append(
                f"<h3>{side} · {rf.team_name} <span style='font-size:1.2em'>{trend_icon}</span></h3>"
                f"<p class='info-text'>最近 5 场：{form_badges}</p>"
                f"<table class='info-table'><thead><tr><th>对手</th><th>结果</th><th>比分</th></tr></thead><tbody>"
                + "\n".join(
                    f"<tr><td>{'vs' if m.is_home else '@'} {m.opponent}</td>"
                    f"<td>{m.result}</td><td>{m.goals_for}-{m.goals_against}</td></tr>"
                    for m in rf.matches
                )
                + "</tbody></table>"
            )
        if not parts:
            return f'<p class="info-text">Unavailable</p>'
        return "".join(parts)

    def _injury_dashboard_display(self, inj: InjuryDashboard | None) -> str:
        if not inj or not inj.available or not inj.players:
            return f'<p class="info-text">Unavailable</p>'
        head = "<tr><th>球员</th><th>类型</th><th>详情</th><th>预计归期</th></tr>"
        rows = "\n".join(
            f"<tr><td>{p.player_name}</td><td>{p.type}</td><td>{p.description}</td><td>{p.expected_return or '未知'}</td></tr>"
            for p in inj.players
        )
        return f"<h3>共 {len(inj.players)} 人</h3><table class='info-table'><thead>{head}</thead><tbody>{rows}</tbody></table>"

    def _lineup_display(self, lu: LineupDashboard | None) -> str:
        if not lu or not lu.available:
            return f'<p class="info-text">Unavailable</p>'
        parts = []
        for side_label, tl in [("主场", lu.home_lineup), ("客场", lu.away_lineup)]:
            if not tl:
                continue
            starters = "\n".join(
                f"<tr><td>{p.jersey_number or '?'}</td><td>{p.player_name}</td><td>{p.formation_position or ''}</td></tr>"
                for p in tl.starters
            )
            subs = "\n".join(
                f"<tr><td>{p.jersey_number or '?'}</td><td>{p.player_name}</td></tr>"
                for p in tl.substitutes
            )
            fm = f" · {tl.formation}" if tl.formation else ""
            parts.append(
                f"<h3>{side_label} · {tl.team_name}{fm}</h3>"
                f"<p><strong>首发（{len(tl.starters)} 人）</strong></p>"
                f"<table class='info-table'><thead><tr><th>#</th><th>球员</th><th>位置</th></tr></thead><tbody>{starters}</tbody></table>"
                f"<p><strong>替补（{len(tl.substitutes)} 人）</strong></p>"
                f"<table class='info-table'><thead><tr><th>#</th><th>球员</th></tr></thead><tbody>{subs}</tbody></table>"
            )
        if not parts:
            return f'<p class="info-text">Unavailable</p>'
        return "".join(parts)

    def _match_centre_display(self, mc: MatchCentreDashboard | None) -> str:
        if not mc or not mc.available or not mc.timeline:
            return f'<p class="info-text">Unavailable</p>'
        items = []
        for ev in sorted(mc.timeline, key=lambda x: x.minute):
            em = f"+{ev.extra_minute}" if ev.extra_minute else ""
            items.append(
                f"<tr><td>{ev.minute}{em}'</td><td>{ev.event_type}</td>"
                f"<td>{ev.player_name}</td><td>{ev.info or ev.result}</td></tr>"
            )
        tbody = "\n".join(items) if items else '<tr><td colspan="4">暂无事件</td></tr>'
        return f"<table class='info-table'><thead><tr><th>时间</th><th>类型</th><th>球员</th><th>详情</th></tr></thead><tbody>{tbody}</tbody></table>"

    def _tv_broadcast_display(self, tv: TVBroadcastDashboard | None) -> str:
        if not tv or not tv.available or not tv.stations:
            return f'<p class="info-text">Unavailable</p>'
        rows = "\n".join(
            f"<tr><td>{s.name}</td><td>{s.url or 'N/A'}</td></tr>" for s in tv.stations
        )
        return f"<table class='info-table'><thead><tr><th>电视台</th><th>链接</th></tr></thead><tbody>{rows}</tbody></table>"

    def _scenarios_display(self, s: ScenarioInfo) -> str:
        if not s.items:
            return f'<p class="info-text">{NA}</p>'
        items = "\n".join(f"<li>{item}</li>" for item in s.items)
        return f"<ul class='scenario-list'>{items}</ul>"

    # ------------------------------------------------------------------
    # Betting Intelligence Sections (6 new sections)
    # ------------------------------------------------------------------

    def _recommended_markets(self, data: MatchDashboardData) -> str:
        """推荐市场 — 胜平负/大小球/双方进球/亚盘 多市场对比表格。"""
        markets = data.recommended_markets
        if not markets:
            return f'<p class="info-text">{NA} — 模型概率未启用</p>'

        head = (
            "<tr><th>市场</th><th>赔率</th><th>模型概率</th><th>市场概率</th>"
            "<th>期望收益 EV</th><th>置信度</th><th>建议仓位</th><th>一句话说明</th></tr>"
        )
        rows: list[str] = []
        for m in markets:
            if not m.supported:
                rows.append(
                    f"<tr><td>{m.market_name}</td>"
                    f"<td colspan='7' style='color:#64748b'>{NA} — 模型不支持</td></tr>"
                )
                continue
            odds_str = _val(m.odds, fmt=".2f")
            model_pct = _pct(m.model_prob)
            market_pct = _pct(m.market_prob)
            ev_str = f"{m.ev:+.1%}" if m.ev is not None else NA
            ev_color = (
                "#10b981" if (m.ev is not None and m.ev > 0)
                else ("#ef4444" if m.ev is not None else "")
            )
            conf_str = f"{m.confidence:.1f}%" if m.confidence is not None else NA
            stake_str = f"{m.stake:.1%}" if m.stake is not None else NA
            rows.append(
                f"<tr>"
                f"<td>{m.market_name}</td>"
                f"<td>{odds_str}</td>"
                f"<td>{model_pct}</td>"
                f"<td>{market_pct}</td>"
                f"<td style='color:{ev_color}'>{ev_str}</td>"
                f"<td>{conf_str}</td>"
                f"<td>{stake_str}</td>"
                f"<td style='font-size:.78em;color:#94a3b8;max-width:200px'>{m.explanation or '—'}</td>"
                f"</tr>"
            )
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _correct_scores_display(
        self, scores: list[ScorelineInfo], ma: ModelAvailability
    ) -> str:
        if not scores:
            if not ma.poisson:
                return f'<p class="info-text">{NA} — Poisson 模型未启用</p>'
            return f'<p class="info-text">{NA} — 比分矩阵未生成</p>'

        head = "<tr><th>#</th><th>比分</th><th>概率</th><th>概率分布</th></tr>"
        max_p = max((s.probability or 0) for s in scores) if scores else 0.01
        rows: list[str] = []
        for i, s in enumerate(scores, 1):
            pct = _pct(s.probability)
            prob_val = s.probability or 0
            bar_width = (prob_val / max_p * 100) if max_p > 0 else 0
            rows.append(
                f"<tr>"
                f"<td>{i}</td>"
                f"<td style='font-weight:700;color:#fff'>{s.scoreline}</td>"
                f"<td>{pct}</td>"
                f"<td>"
                f"<div style='background:#1a2035;height:6px;border-radius:3px;overflow:hidden'>"
                f"<div style='width:{bar_width:.0f}%;height:100%;"
                f"background:linear-gradient(90deg,#1e90ff,#10b981);border-radius:3px'></div>"
                f"</div>"
                f"</td>"
                f"</tr>"
            )
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _goalscorers_display(self, scorers: list[GoalscorerInfo]) -> str:
        if not scorers:
            return f'<p class="info-text">{NA} — 球员数据未接入</p>'

        head = "<tr><th>#</th><th>球员</th><th>进球概率</th></tr>"
        rows: list[str] = []
        for i, g in enumerate(scorers, 1):
            pct = _pct(g.probability)
            rows.append(f"<tr><td>{i}</td><td>{g.player}</td><td>{pct}</td></tr>")
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _value_opportunities_display(
        self, opportunities: list[ValueOpportunity]
    ) -> str:
        if not opportunities:
            return (
                f'<p class="info-text">今日无符合条件的价值机会</p>'
                f'<p class="info-text" style="font-size:.75em;color:#64748b">'
                f'筛选条件：期望收益 EV ≥ 3%</p>'
            )

        head = (
            "<tr><th>#</th><th>比赛</th><th>市场</th><th>赔率</th>"
            "<th>模型概率</th><th>期望收益 EV</th><th>置信度</th><th>一句话说明</th></tr>"
        )
        rows: list[str] = []
        for i, op in enumerate(opportunities, 1):
            odds_str = _val(op.odds, fmt=".2f")
            prob_str = _pct(op.model_prob)
            ev_str = f"{op.ev:+.1%}" if op.ev is not None else NA
            conf_str = f"{op.confidence:.1f}%" if op.confidence is not None else NA
            rows.append(
                f"<tr>"
                f"<td>{i}</td>"
                f"<td style='font-weight:700'>{op.match_label}</td>"
                f"<td>{op.market}</td>"
                f"<td>{odds_str}</td>"
                f"<td>{prob_str}</td>"
                f"<td style='color:#10b981;font-weight:700'>{ev_str}</td>"
                f"<td>{conf_str}</td>"
                f"<td style='font-size:.78em;color:#94a3b8;max-width:180px'>{op.explanation or '—'}</td>"
                f"</tr>"
            )
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _ai_trade_summary_display(self, summary: str) -> str:
        if not summary.strip():
            return f'<p class="info-text">今日暂无符合条件的交易机会。</p>'
        return (
            f'<div style="background:#0d1525;border-radius:8px;padding:18px;'
            f'border-left:3px solid #ffc400;line-height:1.8;font-size:.95em">'
            f'{summary}</div>'
        )

    def _risk_summary(self, risks: list[RiskItem]) -> str:
        if not risks:
            return f'<p class="info-text">{NA} — 无风险数据</p>'

        severity_colors = {"高": "#ef4444", "中": "#f59e0b", "低": "#10b981"}
        head = "<tr><th>比赛</th><th>风险因素</th><th>严重程度</th></tr>"
        rows: list[str] = []
        for r in risks:
            color = severity_colors.get(r.severity, "#64748b")
            rows.append(
                f"<tr>"
                f"<td>{r.match_label}</td>"
                f"<td>{r.risk_factor}</td>"
                f"<td><span style='color:{color};font-weight:700'>{r.severity}</span></td>"
                f"</tr>"
            )
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    # ------------------------------------------------------------------
    # Sections 4-6 — 决策流程 / 模型共识 / 每日风险管理
    # ------------------------------------------------------------------

    def _decision_flow_display(self, steps: list[DecisionStep]) -> str:
        """决策流程可视化管道 — 6 步推理链。

        数据采集 → 模型计算 → 期望收益 EV → 凯利仓位 → 风险评估 → 最终决策
        每步显示状态图标（✓/⚠/✗）和简要说明。
        """
        if not steps:
            return f'<p class="info-text">{NA} — 决策流程数据未注入</p>'

        status_icons = {
            "passed":   ("&#10003;", "#10b981", "passed"),
            "partial":  ("&#9888;", "#f59e0b", "partial"),
            "failed":   ("&#10007;", "#ef4444", "failed"),
            "nodata":   ("&#8854;", "#64748b", "nodata"),
        }

        step_cards: list[str] = []
        for i, s in enumerate(steps):
            icon, color, css_class = status_icons.get(s.status, ("&#8854;", "#64748b", "nodata"))
            note_style = f'color:{color}' if s.status == "failed" else ""
            step_cards.append(
                f'<div class="df-step df-step-{css_class}">\n'
                f'  <div class="df-status" style="color:{color}">{icon}</div>\n'
                f'  <div class="df-name">{s.step_name}</div>\n'
                f'  <div class="df-note" style="{note_style}">{s.note or "—"}</div>\n'
                f'</div>'
            )

        # Interleave cards with arrows
        arrows = '<div class="df-arrow">&rarr;</div>'
        pipeline = [step_cards[0]]
        for card in step_cards[1:]:
            pipeline.append(arrows)
            pipeline.append(card)

        return (
            '<div class="df-pipeline">\n'
            + '\n'.join(pipeline)
            + '\n</div>'
        )

    def _model_consensus_display(
        self, rows: list[ModelConsensusRow], decision: DecisionInfo
    ) -> str:
        """模型共识对比表 — 所有模型预测方向与最终推荐的一致性。"""
        if not rows:
            return f'<p class="info-text">{NA} — 无模型共识数据</p>'

        final_cls = (decision.classification or "").upper().strip()
        final_label = {"BET": "建议投注", "NO BET": "不建议投注"}.get(final_cls, "持续观察")

        head = (
            "<tr><th>模型</th><th>预测结果</th><th>主胜概率</th><th>平局概率</th>"
            "<th>客胜概率</th><th>与推荐一致</th></tr>"
        )

        body_rows: list[str] = []
        for r in rows:
            if not r.available:
                body_rows.append(
                    f"<tr class='mc-unavailable'>"
                    f"<td style='font-weight:700'>{r.model_name}</td>"
                    f"<td colspan='5' style='color:#64748b;font-style:italic'>"
                    f"未启用 — {r.unavailable_reason or '模型未接入'}</td>"
                    f"</tr>"
                )
                continue

            outcome_display = r.predicted_outcome or "—"
            home_pct = _pct(r.home_prob)
            draw_pct = _pct(r.draw_prob)
            away_pct = _pct(r.away_prob)
            agree_icon = (
                '<span style="color:#10b981;font-size:1.2em">&#10003;</span>' if r.agrees
                else '<span style="color:#ef4444;font-size:1.2em">&#10007;</span>'
            )
            row_class = "" if r.agrees else "consensus-warn"
            body_rows.append(
                f"<tr class='{row_class}'>"
                f"<td style='font-weight:700'>{r.model_name}</td>"
                f"<td>{outcome_display}</td>"
                f"<td>{home_pct}</td>"
                f"<td>{draw_pct}</td>"
                f"<td>{away_pct}</td>"
                f"<td style='text-align:center'>{agree_icon}</td>"
                f"</tr>"
            )

        tbody = "\n".join(body_rows)
        return (
            f'<p class="info-text" style="margin-bottom:12px">'
            f'最终推荐：<span class="badge {_classification_css_class(final_cls)}">{final_label}</span>'
            f'</p>\n'
            f'<table class="info-table consensus-table"><thead>{head}</thead><tbody>{tbody}</tbody></table>'
        )

    def _daily_risk_display(self, rp: DailyRiskProfile | None) -> str:
        """每日风险管理 — 敞口可视化 + 仓位汇总。"""
        if rp is None:
            return (
                f'<p class="info-text">风险敞口数据未注入，请运行完整流水线。</p>'
            )

        if rp.recommended_trade_count == 0:
            return (
                f'<p class="info-text" style="color:#10b981;font-size:.95em">'
                f'今日无推荐交易，风险敞口为零。</p>'
            )

        max_exp = rp.max_exposure_pct
        total_stake = rp.total_suggested_stake

        # Gauge bar — total stake vs max exposure
        gauge_used_pct = 0
        if max_exp and max_exp > 0 and total_stake is not None:
            gauge_used_pct = min(100, (total_stake / max_exp) * 100)

        gauge_color = (
            "#10b981" if gauge_used_pct <= 60
            else ("#f59e0b" if gauge_used_pct <= 80 else "#ef4444")
        )

        max_exp_str = f"{max_exp:.1f}%" if max_exp is not None else NA
        total_stake_str = f"{total_stake:.1f}%" if total_stake is not None else NA

        gauge_html = (
            f'<div class="risk-gauge">\n'
            f'  <div class="risk-gauge-labels">\n'
            f'    <div>\n'
            f'      <span class="rg-label">建议最大敞口</span>\n'
            f'      <span class="rg-value">{max_exp_str}</span>\n'
            f'    </div>\n'
            f'    <div style="text-align:right">\n'
            f'      <span class="rg-label">当前总仓位</span>\n'
            f'      <span class="rg-value" style="color:{gauge_color}">{total_stake_str}</span>\n'
            f'    </div>\n'
            f'  </div>\n'
            f'  <div class="risk-gauge-track">\n'
            f'    <div class="risk-gauge-fill" style="width:{gauge_used_pct:.0f}%;'
            f'background:{gauge_color}"></div>\n'
            f'  </div>\n'
            f'  <div class="risk-gauge-trade-count">'
            f'今日推荐交易数：<b style="color:#fff">{rp.recommended_trade_count}</b> 笔'
            f'</div>\n'
            f'</div>'
        )

        # Kelly breakdown table
        kelly_rows: list[str] = []
        if rp.kelly_breakdown:
            kelly_head = "<tr><th>#</th><th>比赛 / 市场</th><th>凯利仓位</th></tr>"
            for i, item in enumerate(rp.kelly_breakdown, 1):
                match_label = item[0] if isinstance(item, (list, tuple)) and len(item) >= 1 else str(item)
                kelly_pct = item[1] if isinstance(item, (list, tuple)) and len(item) >= 2 else None
                kelly_str = f"{kelly_pct:.1%}" if kelly_pct is not None else NA
                kelly_rows.append(
                    f"<tr><td>{i}</td><td style='font-weight:700'>{match_label}</td><td>{kelly_str}</td></tr>"
                )
            kelly_tbody = "\n".join(kelly_rows)
            kelly_table = (
                f'<table class="info-table" style="margin-top:16px">'
                f'<thead>{kelly_head}</thead><tbody>{kelly_tbody}</tbody></table>'
            )
        else:
            kelly_table = (
                f'<p class="info-text" style="margin-top:16px">凯利仓位明细未提供。</p>'
            )

        return gauge_html + "\n" + kelly_table

    # ------------------------------------------------------------------
    # New Sections — 今日精选 / 建议回避 / 模拟串关
    # ------------------------------------------------------------------

    def _top_picks_hero(self, data: DailyDashboardData) -> str:
        """今日精选 — 日概览页首屏最显眼区域。"""
        picks = data.top_picks
        if not picks:
            return (
                '<div class="top-picks">\n'
                '  <div class="top-picks-header">\n'
                '    <span class="top-picks-icon">&#9733;</span>\n'
                '    <div>\n'
                '      <div class="top-picks-title">今日精选</div>\n'
                '      <div class="top-picks-subtitle">TODAY\'S TOP PICKS</div>\n'
                '    </div>\n'
                '  </div>\n'
                '  <div class="top-picks-empty">今日暂无精选推荐</div>\n'
                "</div>"
            )

        cards: list[str] = []
        for i, p in enumerate(picks, 1):
            odds_str = _val(p.odds, fmt=".2f")
            prob_str = _pct(p.model_prob)
            ev_str = f"{p.ev:+.1%}" if p.ev is not None else NA
            conf_str = f"{p.confidence:.1f}%" if p.confidence is not None else NA
            stake_str = f"{p.stake:.1%}" if p.stake is not None else NA
            cards.append(
                f'<div class="top-pick-card">\n'
                f'  <div class="tp-rank">#{i}</div>\n'
                f'  <div class="tp-match">{p.match_label}</div>\n'
                f'  <div class="tp-market">{p.market}</div>\n'
                f'  <div class="tp-grid">\n'
                f'    <div class="tp-metric">\n'
                f'      <div class="tp-label">赔率</div>\n'
                f'      <div class="tp-value">{odds_str}</div>\n'
                f'    </div>\n'
                f'    <div class="tp-metric">\n'
                f'      <div class="tp-label">模型概率</div>\n'
                f'      <div class="tp-value">{prob_str}</div>\n'
                f'    </div>\n'
                f'    <div class="tp-metric">\n'
                f'      <div class="tp-label">期望收益 EV</div>\n'
                f'      <div class="tp-value" style="color:#10b981">{ev_str}</div>\n'
                f'    </div>\n'
                f'    <div class="tp-metric">\n'
                f'      <div class="tp-label">建议仓位</div>\n'
                f'      <div class="tp-value">{stake_str}</div>\n'
                f'    </div>\n'
                f'  </div>\n'
                f'  <div class="tp-reason">{p.reason}</div>\n'
                f"</div>"
            )

        return (
            '<div class="top-picks">\n'
            '  <div class="top-picks-header">\n'
            '    <span class="top-picks-icon">&#9733;</span>\n'
            '    <div>\n'
            '      <div class="top-picks-title">今日精选</div>\n'
            '      <div class="top-picks-subtitle">TODAY\'S TOP PICKS &mdash; '
            f'{len(picks)} 个精选推荐</div>\n'
            '    </div>\n'
            '  </div>\n'
            + "\n".join(cards)
            + "\n</div>"
        )

    def _avoid_matches_display(self, avoids: list[AvoidMatch]) -> str:
        if not avoids:
            return f'<p class="info-text">今日所有比赛均通过筛选，无需要回避的比赛。</p>'

        decision_badges = {
            "NO BET": '<span class="badge no-bet" style="font-size:.75em">不建议投注</span>',
            "WATCH": '<span class="badge watch" style="font-size:.75em">持续观察</span>',
        }
        head = "<tr><th>比赛</th><th>回避原因</th><th>决策</th></tr>"
        rows: list[str] = []
        for a in avoids:
            badge = decision_badges.get(a.decision.upper().strip(),
                                        f'<span class="badge watch" style="font-size:.75em">{a.decision}</span>')
            rows.append(
                f"<tr>"
                f"<td style='font-weight:700'>{a.match_label}</td>"
                f"<td style='color:#ef4444'>{a.reason}</td>"
                f"<td>{badge}</td>"
                f"</tr>"
            )
        tbody = "\n".join(rows)
        return f"<table class='info-table'><thead>{head}</thead><tbody>{tbody}</tbody></table>"

    def _accumulator_display(self, accs: list[AccumulatorSuggestion]) -> str:
        if not accs:
            return f'<p class="info-text">{NA} — 符合条件的推荐不足 2 个，无法生成串关建议</p>'

        cards: list[str] = [
            '<p style="color:#997a00;font-size:.78em;margin-bottom:16px;letter-spacing:1px">'
            '&#9888; 模拟交易，非实际投注建议</p>'
        ]
        for a in accs:
            odds_str = _val(a.combined_odds, fmt=".2f")
            hit_str = _pct(a.estimated_hit_rate)
            cards.append(
                f'<div class="top-pick-card" style="border-left-color:#1e90ff">\n'
                f'  <div class="tp-match" style="font-size:1em">{a.combo_name}</div>\n'
                f'  <div class="tp-market" style="color:#1e90ff">{a.match_picks}</div>\n'
                f'  <div class="tp-grid" style="grid-template-columns:1fr 1fr">\n'
                f'    <div class="tp-metric">\n'
                f'      <div class="tp-label">组合赔率</div>\n'
                f'      <div class="tp-value">{odds_str}</div>\n'
                f'    </div>\n'
                f'    <div class="tp-metric">\n'
                f'      <div class="tp-label">预估命中率</div>\n'
                f'      <div class="tp-value">{hit_str}</div>\n'
                f'    </div>\n'
                f'  </div>\n'
                f"</div>"
            )
        return "\n".join(cards)

    # ------------------------------------------------------------------
    # Dashboard V2 — 10 New Sections
    # ------------------------------------------------------------------

    # ── S1: AI 执行摘要 ──

    def _executive_summary_display(self, es: DailyExecutiveSummary | None) -> str:
        if es is None:
            return ""
        badge_cls = _classification_css_class(es.final_decision)
        badge_map = {
            "BET": ("AA", "bet", "#10b981"),
            "WATCH": ("A", "watch", "#f59e0b"),
            "NO BET": ("BB", "no-bet", "#ef4444"),
        }
        label, bcls, bcolor = badge_map.get(es.final_decision.upper().strip(), (es.final_decision or NA, "watch", "#f59e0b"))
        ev_str = f"{es.ev:+.1%}" if es.ev is not None else NA
        ev_color = "#10b981" if (es.ev or 0) >= 0 else "#ef4444"
        conf_str = f"{es.confidence:.0f}" if es.confidence is not None else NA
        stake_str = f"{es.stake:.1%}" if es.stake is not None else NA
        return (
            '<div class="exec-summary">\n'
            '  <div class="es-badge-wrap">\n'
            f'    <span class="es-badge {bcls}">{label}</span>\n'
            f'    <span class="es-market">{_val(es.recommended_market)}</span>\n'
            '  </div>\n'
            '  <div class="es-grid">\n'
            f'    <div class="es-item"><span class="es-label">置信度</span><span class="es-value">{conf_str}</span></div>\n'
            f'    <div class="es-item"><span class="es-label">期望收益 EV</span><span class="es-value ev" style="color:{ev_color}">{ev_str}</span></div>\n'
            f'    <div class="es-item"><span class="es-label">建议仓位</span><span class="es-value">{stake_str}</span></div>\n'
            '  </div>\n'
            f'  <div class="es-oneliner">{_val(es.one_liner)}</div>\n'
            '</div>'
        )

    # ── S2: 足球分析 ──

    def _football_reasoning_display(self, fr: FootballReasoning | None) -> str:
        if fr is None:
            return ""
        items: list[tuple[str, str]] = [
            ("近期状态", fr.recent_form),
            ("进攻火力", fr.attacking_strength),
            ("防守弱点", fr.defensive_weakness),
            ("xG 趋势", fr.xg_trend),
            ("Elo 差距", fr.elo_gap),
            ("主场优势", fr.home_advantage),
            ("伤病影响", fr.injury_impact),
            ("赛程密度", fr.schedule_congestion),
            ("天气因素", fr.weather_impact),
            ("市场动向", fr.market_movement),
        ]
        rows = []
        for label, text in items:
            if not text or text.strip() in ("", NA):
                continue
            rows.append(
                f'<div class="fr-row">\n'
                f'  <span class="fr-label">{label}</span>\n'
                f'  <span class="fr-text">{text}</span>\n'
                f'</div>'
            )
        if not rows:
            return f'<p class="info-text">{NA} — 缺少足球分析数据</p>'
        body = "\n".join(rows)
        return (
            '<div class="football-reasoning">\n'
            f'  {body}\n'
            '</div>'
        )

    # ── S3: 大小球分析 ──

    def _over_under_display(self, ou: OverUnderAnalysis | None) -> str:
        if ou is None:
            return ""
        mp_str = _pct(ou.model_prob)
        mkp_str = _pct(ou.market_prob)
        ev_str = f"{ou.ev:+.1%}" if ou.ev is not None else NA
        ev_color = "#10b981" if (ou.ev or 0) >= 0 else "#ef4444"
        conf_str = f"{ou.confidence:.0f}" if ou.confidence is not None else NA
        blocks = [
            '<div class="ou-card">',
            f'  <div class="ou-header">推荐盘口: <span class="ou-line">{_val(ou.recommended_line)}</span></div>',
            '  <div class="ou-grid">',
            f'    <div class="ou-item"><span class="ou-label">模型概率</span><span class="ou-value">{mp_str}</span></div>',
            f'    <div class="ou-item"><span class="ou-label">市场概率</span><span class="ou-value">{mkp_str}</span></div>',
            f'    <div class="ou-item"><span class="ou-label">期望收益 EV</span><span class="ou-value" style="color:{ev_color}">{ev_str}</span></div>',
            f'    <div class="ou-item"><span class="ou-label">置信度</span><span class="ou-value">{conf_str}</span></div>',
            '  </div>',
        ]
        if ou.explanation_bullets:
            blist = "\n".join(
                f'    <li>{b}</li>' for b in ou.explanation_bullets
            )
            # Determine title based on over/under
            is_over = "大" in ou.recommended_line if ou.recommended_line else True
            title = "为什么推荐大球？" if is_over else "为什么推荐小球？"
            blocks.append(f'  <div class="ou-explain"><strong>{title}</strong></div>')
            blocks.append(f'  <ul class="ou-bullets">\n{blist}\n  </ul>')
        blocks.append('</div>')
        return "\n".join(blocks)

    # ── S4: 比分可视化 ──

    def _correct_scores_enhanced(self, scores: list[ScorelineInfo], availability: ModelAvailability | None) -> str:
        poisson_ok = availability is not None and availability.poisson
        if not scores:
            return f'<p class="info-text">{NA} — 比分数据未提供</p>'
        if not poisson_ok:
            return f'<p class="info-text">{NA} — Poisson 模型未启用</p>'

        # Find max probability for highlighting
        max_prob = max((s.probability or 0) for s in scores)
        bars: list[str] = []
        cumulative = 0.0
        for s in scores:
            p = s.probability or 0
            cumulative += p
            pct = f"{p * 100:.1f}%"
            bar_pct = int(p / max(max_prob, 0.001) * 100) if max_prob > 0 else 0
            is_max = s.is_highest or (p >= max_prob and max_prob > 0)
            marker = " ← 最高概率" if is_max else ""
            bar_cls = "cs-bar-max" if is_max else "cs-bar"
            bars.append(
                f'<div class="cs-row">'
                f'<span class="cs-score">{s.scoreline}</span>'
                f'<span class="cs-bar-wrap"><span class="{bar_cls}" style="width:{bar_pct}%"></span></span>'
                f'<span class="cs-pct">{pct}{marker}</span>'
                f'</div>'
            )

        # Compute realistic range (top-k that accumulate to ~60%)
        range_scores = []
        range_cum = 0.0
        for s in scores:
            if range_cum >= 0.6:
                break
            range_scores.append(s.scoreline)
            range_cum += (s.probability or 0)
        range_label = f"{range_scores[0] if range_scores else '—'} 至 {range_scores[-1] if range_scores else '—'}"
        range_pct = f"{range_cum * 100:.1f}%"

        return (
            '<div class="cs-section">\n'
            f'  <div class="cs-header">比分预测 (Poisson 模型)</div>\n'
            + "\n".join(bars) + "\n"
            f'  <div class="cs-range">最可能区间: {range_label}（累计概率 {range_pct}）</div>\n'
            '</div>'
        )

    # ── S5: 进球球员预测 ──

    def _goalscorers_enhanced(self, goalscorers: list[GoalscorerInfo]) -> str:
        if not goalscorers:
            return f'<p class="info-text">{NA} — 球员进球数据未接入</p>'
        max_p = max((g.probability or 0) for g in goalscorers)
        bars: list[str] = []
        for g in goalscorers:
            p = g.probability or 0
            pct = f"{p * 100:.0f}%"
            bar_pct = int(p / max(max_p, 0.001) * 100) if max_p > 0 else 0
            bars.append(
                f'<div class="gs-row">'
                f'<span class="gs-player">{g.player}</span>'
                f'<span class="gs-bar-wrap"><span class="gs-bar" style="width:{bar_pct}%"></span></span>'
                f'<span class="gs-pct">{pct}</span>'
                f'</div>'
            )
        return '\n'.join(bars)

    # ── S6: 模型共识投票面板 ──

    def _model_consensus_voting(self, rows: list[ModelConsensusRow], decision: DecisionInfo) -> str:
        if not rows:
            return f'<p class="info-text">{NA} — 模型共识数据未提供</p>'

        vote_icons = {"主胜": "✅", "平局": "⚠️", "客胜": "❌"}
        vote_rows: list[str] = []
        agree_count = 0
        total_models = 0

        for row in rows:
            if not row.available:
                reason = row.unavailable_reason or "不可用"
                vote_rows.append(
                    f'<div class="mv-row mv-warn">'
                    f'<span class="mv-model">{row.model_name}</span>'
                    f'<span class="mv-vote" style="color:#64748b">— {reason}</span>'
                    f'<span class="mv-agree" style="color:#64748b">—</span>'
                    f'</div>'
                )
                continue

            total_models += 1
            model_name = row.model_name or "Unknown"
            prediction = row.predicted_outcome or NA
            icon = vote_icons.get(prediction, "")

            is_agree = row.agrees
            agree_mark = "✓" if is_agree else "✗"
            row_cls = "" if is_agree else "mv-warn"
            if is_agree:
                agree_count += 1

            vote_rows.append(
                f'<div class="mv-row {row_cls}">'
                f'<span class="mv-model">{model_name}</span>'
                f'<span class="mv-vote">{icon} {prediction}</span>'
                f'<span class="mv-agree">{agree_mark}</span>'
                f'</div>'
            )

        if total_models == 0:
            return '<div class="model-voting">\n</div>'

        consensus_text = f"{agree_count}/{total_models} 模型支持推荐决策"
        conf_str = f"{decision.confidence_score:.0f}%" if decision.confidence_score is not None else NA

        return (
            '<div class="model-voting">\n'
            + "\n".join(vote_rows) + "\n"
            '  <div class="mv-divider"></div>\n'
            f'  <div class="mv-consensus">共识: {consensus_text}</div>\n'
            f'  <div class="mv-conf">置信度加权: {conf_str}</div>\n'
            '</div>'
        )

    # ── S7: 市场动向 ──

    def _market_movement_display(self, mm: MarketMovement | None) -> str:
        if mm is None:
            return f'<p class="info-text">{NA} — 缺少历史赔率数据</p>'
        open_str = f"{mm.opening_odds:.2f}" if mm.opening_odds is not None else NA
        curr_str = f"{mm.current_odds:.2f}" if mm.current_odds is not None else NA
        high_str = f"{mm.high_odds:.2f}" if mm.high_odds is not None else NA
        low_str = f"{mm.low_odds:.2f}" if mm.low_odds is not None else NA
        chg_str = f"{mm.change_pct:+.1f}%" if mm.change_pct is not None else NA
        chg_val = mm.change_pct or 0
        arrow = "&#8595;" if chg_val < 0 else ("&#8593;" if chg_val > 0 else "&#8594;")
        direction = mm.direction or "无明显方向"

        # Build the odds range bar
        has_range = mm.high_odds is not None and mm.low_odds is not None
        min_val = mm.low_odds if has_range else min(filter(None, [mm.opening_odds, mm.current_odds]))
        max_val = mm.high_odds if has_range else max(filter(None, [mm.opening_odds, mm.current_odds]))
        span = max_val - min_val if max_val != min_val else 1
        curr_pos = int((mm.current_odds - min_val) / span * 100) if mm.current_odds is not None else 50
        open_pos = int((mm.opening_odds - min_val) / span * 100) if mm.opening_odds is not None else 0
        curr_pos = max(0, min(100, curr_pos))
        open_pos = max(0, min(100, open_pos))

        range_bar = (
            f'<div class="mm-range">'
            f'  <div class="mm-range-labels"><span class="mm-range-low">{low_str}</span>'
            f'  <span class="mm-range-high">{high_str}</span></div>'
            f'  <div class="mm-range-track">'
            f'    <div class="mm-range-zone" style="left:{min(open_pos, curr_pos)}%;width:{abs(curr_pos - open_pos)}%"></div>'
            f'    <div class="mm-range-dot mm-dot-open" style="left:{open_pos}%" title="开盘 {open_str}"></div>'
            f'    <div class="mm-range-dot mm-dot-curr" style="left:{curr_pos}%" title="当前 {curr_str}"></div>'
            f'  </div>'
            f'</div>'
        ) if has_range else ""

        blocks = [
            '<div class="mkt-move">',
            '  <div class="mm-header">市场动向</div>',
            '  <div class="mm-flow">',
            f'    <div class="mm-step"><span class="mm-label">开盘</span><span class="mm-val">{open_str}</span></div>',
            '    <div class="mm-arrow">&#8594;</div>',
            f'    <div class="mm-step"><span class="mm-label">最高</span><span class="mm-val">{high_str}</span></div>',
            '    <div class="mm-arrow">&#8594;</div>',
            f'    <div class="mm-step"><span class="mm-label">最低</span><span class="mm-val">{low_str}</span></div>',
            '    <div class="mm-arrow">&#8594;</div>',
            f'    <div class="mm-step"><span class="mm-label">当前</span><span class="mm-val">{curr_str}</span></div>',
            '  </div>',
            f'  <div class="mm-direction">{arrow} {direction} ({chg_str})</div>',
        ]
        if has_range:
            blocks.append(range_bar)
        if mm.explanation:
            blocks.append(f'  <div class="mm-explain">{mm.explanation}</div>')
        blocks.append('</div>')
        return "\n".join(blocks)

    # ── S8: 风险评估面板 ──

    def _risk_breakdown_display(self, rb: RiskBreakdown | None) -> str:
        if rb is None or not rb.items:
            return f'<p class="info-text">{NA} — 风险评估数据未提供</p>'
        bars: list[str] = []
        severity_colors = {"低": "#10b981", "中等": "#f59e0b", "高": "#ef4444"}
        for item in rb.items:
            bw = max(1, min(5, item.bar_width))
            color = severity_colors.get(item.severity, "#64748b")
            bars.append(
                f'<div class="rk-row">'
                f'<span class="rk-factor">{item.factor}</span>'
                f'<span class="rk-bar-wrap"><span class="rk-bar" style="width:{bw * 20}%;background:{color}"></span></span>'
                f'<span class="rk-sev" style="color:{color}">{item.severity}</span>'
                f'</div>'
            )
        score = rb.overall_score
        label = rb.overall_label
        score_color = "#10b981" if score <= 40 else ("#f59e0b" if score <= 70 else "#ef4444")
        bars.append(
            f'<div class="rk-divider"></div>'
            f'<div class="rk-overall"><span>综合风险评分</span>'
            f'<span class="rk-score" style="color:{score_color}">{score}/100</span>'
            f'<span>（{label}）</span></div>'
        )
        return '<div class="risk-breakdown">\n' + "\n".join(bars) + '\n</div>'

    # ── S9: 数据质量指标 ──

    def _data_quality_display(self, dq: DataQuality | None) -> str:
        if dq is None or not dq.items:
            return f'<p class="info-text">{NA} — 数据质量指标未计算</p>'
        rows: list[str] = []
        for item in dq.items:
            stars_html = "★" * item.stars + "☆" * (5 - item.stars)
            note_suffix = f" <span class='dq-note'>({item.note})</span>" if item.note else ""
            rows.append(
                f'<div class="dq-row">'
                f'<span class="dq-source">{item.source}</span>'
                f'<span class="dq-stars">{stars_html}{note_suffix}</span>'
                f'</div>'
            )
        rows.append(
            f'<div class="dq-divider"></div>'
            f'<div class="dq-overall"><span>综合可靠度</span>'
            f'<span class="dq-score">{dq.overall_score:.0f}%</span></div>'
        )
        return '<div class="data-quality">\n' + "\n".join(rows) + '\n</div>'

    # ── S10: AI 互动 Q&A ──

    def _ai_qa_display(self, qa: AIQA | None) -> str:
        if qa is None or not qa.items:
            return ""
        qa_items: list[str] = []
        for idx, item in enumerate(qa.items):
            qid = f"qa{idx}"
            qa_items.append(
                f'<div class="aq-item">'
                f'<div class="aq-question" onclick="var e=document.getElementById(\'{qid}\');'
                f'e.style.display=e.style.display===\'none\'?\'block\':\'none\'">'
                f'<span class="aq-arrow">▸</span> {item.question}</div>'
                f'<div class="aq-answer" id="{qid}" style="display:none">{item.answer}</div>'
                f'</div>'
            )
        return (
            '<div class="ai-qa">\n'
            + "\n".join(qa_items) + "\n"
            '</div>'
        )

    # ------------------------------------------------------------------
    # HTML head / footer
    # ------------------------------------------------------------------
    def _head(self, title: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:#0a0e17;color:#c8cdd8;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;line-height:1.6}}
    .container{{max-width:1200px;margin:0 auto;padding:20px}}
    .hero{{background:linear-gradient(135deg,#0d1525,#151a30,#0d1525);border:1px solid #2a2f45;border-radius:12px;padding:32px;text-align:center;margin-bottom:24px}}
    .hero::before{{content:'';display:block;height:3px;background:linear-gradient(90deg,#c60b1e,#ffc400);margin:-32px -32px 24px;border-radius:12px 12px 0 0}}
    .hero .badge{{display:inline-block;padding:4px 14px;border-radius:20px;font-size:.8em;font-weight:700;letter-spacing:2px;margin:0 6px 12px}}
    .badge.live{{background:#ef4444;color:#fff;animation:pulse 2s infinite}}
    .badge.upcoming{{background:#f59e0b;color:#000}}
    .badge.closed{{background:#10b981;color:#000}}
    .badge.bet{{background:#10b981;color:#000}}
    .badge.watch{{background:#f59e0b;color:#000}}
    .badge.no-bet{{background:#ef4444;color:#fff}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.6}}}}
    .score-row{{display:flex;justify-content:center;align-items:center;gap:32px}}
    .team{{text-align:center}}
    .team .name{{color:#fff;font-size:1.3em;font-weight:700}}
    .score{{font-size:3.5em;font-weight:900;color:#fff;letter-spacing:8px}}
    .meta{{display:flex;justify-content:center;gap:24px;color:#5a6785;font-size:.8em;margin-top:12px;flex-wrap:wrap}}
    .section{{background:#111827;border:1px solid #2a2f45;border-radius:12px;padding:24px;margin-bottom:20px}}
    .section h2{{color:#fff;font-size:1.15em;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #1e90ff}}
    .divider{{width:40px;height:3px;background:#1e90ff;margin:10px 0 16px}}
    .cards-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
    .cards-3{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
    .card{{background:#0d1525;border-radius:10px;padding:18px;border:1px solid #1a2035}}
    .card h3{{color:#1e90ff;font-size:.9em;margin-bottom:8px}}
    .info-text{{color:#94a3b8;font-size:.85em;line-height:1.7}}
    .info-table{{width:100%;border-collapse:collapse;font-size:.85em}}
    .info-table th{{text-align:left;padding:8px 10px;color:#64748b;font-size:.75em;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #1e293b}}
    .info-table td{{padding:8px 10px;border-bottom:1px solid #151d30;color:#c8cdd8}}
    .info-table tr:hover td{{background:#151d30}}
    .scenario-list{{padding-left:20px;color:#94a3b8;font-size:.85em}}
    .scenario-list li{{margin-bottom:6px}}
    .footer{{text-align:center;color:#3a4565;font-size:.65em;padding:14px;border-top:1px solid #151d30;margin-top:20px}}
    .decision-summary{{background:linear-gradient(135deg,#0d1525,#151a30);border:1px solid #2a2f45;border-left:4px solid #1e90ff;border-radius:12px;padding:24px;margin-bottom:20px}}
    .decision-summary .ds-head{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:18px}}
    .decision-summary .ds-badge{{font-size:1.4em;font-weight:900;padding:8px 24px;border-radius:24px;letter-spacing:2px}}
    .decision-summary .ds-badge.bet{{background:#10b981;color:#000}}
    .decision-summary .ds-badge.watch{{background:#f59e0b;color:#000}}
    .decision-summary .ds-badge.no-bet{{background:#ef4444;color:#fff}}
    .decision-summary .ds-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:16px}}
    .decision-summary .ds-metric{{background:#0a0e17;border-radius:8px;padding:14px 16px}}
    .decision-summary .ds-label{{color:#64748b;font-size:.72em;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}}
    .decision-summary .ds-value{{color:#fff;font-size:1.5em;font-weight:800}}
    .decision-summary .ds-value.pos{{color:#10b981}}
    .decision-summary .ds-value.neg{{color:#ef4444}}
    .decision-summary .ds-bar{{height:8px;background:#1a2035;border-radius:4px;margin-top:8px;overflow:hidden}}
    .decision-summary .ds-bar-fill{{height:100%;background:linear-gradient(90deg,#1e90ff,#10b981);border-radius:4px}}
    .decision-summary .ds-oneliner{{background:#0a0e17;border-radius:8px;padding:14px 16px;color:#c8cdd8;font-size:.95em;line-height:1.6;border-left:3px solid #ffc400}}
    .card .ds-mini{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}}
    .card .ds-mini .ds-mini-badge{{font-size:.85em;font-weight:800;padding:3px 12px;border-radius:16px}}
    .card .ds-mini .ds-mini-badge.bet{{background:#10b981;color:#000}}
    .card .ds-mini .ds-mini-badge.watch{{background:#f59e0b;color:#000}}
    .card .ds-mini .ds-mini-badge.no-bet{{background:#ef4444;color:#fff}}
    .card .ds-mini .ds-mini-stat{{color:#94a3b8;font-size:.82em}}
    .card .ds-mini .ds-mini-stat b{{color:#fff}}
    .top-picks{{background:linear-gradient(135deg,#1a1500,#2a2000,#1a1500);border:2px solid #ffc400;border-radius:16px;padding:32px;margin-bottom:24px;position:relative}}
    .top-picks::before{{content:'';display:block;height:4px;background:linear-gradient(90deg,#ffc400,#ffd700,#ffc400);margin:-32px -32px 24px;border-radius:16px 16px 0 0}}
    .top-picks-header{{display:flex;align-items:center;gap:12px;margin-bottom:24px}}
    .top-picks-icon{{font-size:1.8em;color:#ffc400}}
    .top-picks-title{{color:#ffc400;font-size:1.4em;font-weight:900;letter-spacing:4px}}
    .top-picks-subtitle{{color:#997a00;font-size:.75em;letter-spacing:2px}}
    .top-picks-empty{{color:#997a00;font-size:.95em;text-align:center;padding:20px}}
    .top-pick-card{{background:linear-gradient(135deg,#0d1525,#1a1f35);border:1px solid #2a2f45;border-left:4px solid #ffc400;border-radius:10px;padding:20px 24px;margin-bottom:14px;position:relative}}
    .top-pick-card:last-child{{margin-bottom:0}}
    .tp-rank{{position:absolute;top:12px;right:16px;color:#ffc400;font-size:1.3em;font-weight:900;opacity:.6}}
    .tp-match{{color:#fff;font-size:1.15em;font-weight:700;margin-bottom:4px}}
    .tp-market{{color:#ffc400;font-size:.85em;font-weight:600;margin-bottom:12px;letter-spacing:2px}}
    .tp-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:12px}}
    .tp-metric{{background:#0a0e17;border-radius:8px;padding:10px 14px}}
    .tp-label{{color:#64748b;font-size:.68em;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}}
    .tp-value{{color:#fff;font-size:1.2em;font-weight:800}}
    .tp-reason{{color:#c8cdd8;font-size:.88em;line-height:1.6;padding:10px 14px;background:#0a0e17;border-radius:8px;border-left:3px solid #997a00}}
    .df-pipeline{{display:flex;align-items:flex-start;gap:0;overflow-x:auto;padding:8px 0}}
    .df-step{{flex:1 0 140px;min-width:130px;max-width:180px;background:#0d1525;border-radius:10px;padding:16px 12px;text-align:center;border:1px solid #1a2035;transition:border-color .2s}}
    .df-step-passed{{border-left:3px solid #10b981}}
    .df-step-partial{{border-left:3px solid #f59e0b}}
    .df-step-failed{{border-left:3px solid #ef4444}}
    .df-step-nodata{{border-left:3px solid #64748b}}
    .df-status{{font-size:1.6em;margin-bottom:8px;font-weight:900}}
    .df-name{{color:#fff;font-size:.85em;font-weight:700;margin-bottom:6px;letter-spacing:.5px}}
    .df-note{{color:#94a3b8;font-size:.72em;line-height:1.5}}
    .df-arrow{{flex:0 0 30px;display:flex;align-items:center;justify-content:center;color:#3a4565;font-size:1.4em;padding-top:28px}}
    .consensus-table tr.consensus-warn td{{background:#1a1500;color:#f59e0b}}
    .consensus-table tr.mc-unavailable td{{background:#0d1117;color:#64748b}}
    .consensus-table td{{vertical-align:middle}}
    .risk-gauge{{background:#0d1525;border-radius:12px;padding:20px 24px;border:1px solid #2a2f45}}
    .risk-gauge-labels{{display:flex;justify-content:space-between;margin-bottom:12px}}
    .rg-label{{display:block;color:#64748b;font-size:.72em;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}}
    .rg-value{{display:block;color:#fff;font-size:1.5em;font-weight:800}}
    .risk-gauge-track{{height:12px;background:#1a2035;border-radius:6px;overflow:hidden;margin-bottom:14px}}
    .risk-gauge-fill{{height:100%;border-radius:6px;transition:width .4s ease}}
    .risk-gauge-trade-count{{color:#94a3b8;font-size:.85em;text-align:center}}
    @media(max-width:768px){{.cards-2,.cards-3{{grid-template-columns:1fr}}.decision-summary .ds-grid{{grid-template-columns:1fr}}.top-pick-card .tp-grid{{grid-template-columns:repeat(2,1fr)}}.tp-rank{{position:static;margin-bottom:8px}}.df-pipeline{{flex-wrap:wrap;gap:8px}}.df-arrow{{flex:0 0 24px;transform:rotate(90deg);padding-top:0}}.df-step{{flex:1 0 100%;min-width:100%;max-width:100%}}}}
    /* ── Priority 1: 今日最佳机会 ── */
    .best-opps{{background:linear-gradient(135deg,#0d121f,#181f35,#0d121f);border:2px solid #3b82f6;border-radius:16px;padding:32px;margin-bottom:24px}}
    .best-opps-header{{display:flex;align-items:center;gap:12px;margin-bottom:24px}}
    .best-opps-icon{{font-size:1.8em;color:#3b82f6}}
    .best-opps-title{{color:#3b82f6;font-size:1.4em;font-weight:900;letter-spacing:4px}}
    .best-opps-subtitle{{color:#5b7abf;font-size:.75em;letter-spacing:2px}}
    .best-opps-empty{{color:#5b7abf;font-size:.95em;text-align:center;padding:20px}}
    .bo-card{{background:linear-gradient(135deg,#0d1525,#1a1f35);border:1px solid #2a2f45;border-left:4px solid #3b82f6;border-radius:10px;padding:18px 22px;margin-bottom:12px}}
    .bo-card:last-child{{margin-bottom:0}}
    .bo-no-qualifier{{border-left-color:#475569;opacity:.65}}
    .bo-head{{margin-bottom:12px}}
    .bo-category{{color:#60a5fa;font-size:.9em;font-weight:700;letter-spacing:1px;margin-bottom:4px}}
    .bo-match{{color:#fff;font-size:1em;font-weight:600}}
    .bo-no-data{{color:#64748b;font-size:.88em;padding:8px 0}}
    .bo-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;margin-bottom:12px}}
    .bo-metric{{background:#0a0e17;border-radius:8px;padding:8px 10px;text-align:center}}
    .bo-label{{color:#64748b;font-size:.62em;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}}
    .bo-value{{color:#fff;font-size:1.05em;font-weight:800}}
    .bo-explanation{{color:#94a3b8;font-size:.82em;line-height:1.5;padding:8px 12px;background:#0a0e17;border-radius:8px;border-left:3px solid #3b82f6;margin-bottom:10px}}
    /* ── Priority 2: AI 推理 ── */
    .ai-reasoning{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;border-left:4px solid #8b5cf6;padding:0;margin-bottom:0}}
    .ai-r-header{{display:flex;align-items:center;gap:8px;padding:14px 18px;border-bottom:1px solid #1e293b}}
    .ai-r-icon{{font-size:1em;color:#8b5cf6}}
    .ai-r-title{{color:#8b5cf6;font-size:.95em;font-weight:800;letter-spacing:2px}}
    .ai-r-body{{padding:16px 18px}}
    .ai-r-question{{color:#a78bfa;font-size:.85em;font-weight:700;margin-bottom:12px}}
    .ai-r-bullets{{list-style:none;padding:0}}
    .ai-r-bullets li{{color:#c8cdd8;font-size:.85em;line-height:1.8;padding:3px 0;padding-left:16px;position:relative}}
    .ai-r-bullets li::before{{content:'•';color:#8b5cf6;position:absolute;left:0;font-weight:700}}
    .ai-r-conclusion{{color:#e2e8f0;font-size:.85em;line-height:1.6;margin-top:10px;padding:10px 14px;background:#0a0e17;border-radius:8px;border-left:3px solid #a78bfa;font-weight:600}}
    .ai-r-empty{{color:#64748b;font-size:.85em;padding:14px 18px;text-align:center}}
    /* ── Priority 3: 置信度构成 ── */
    .conf-breakdown{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .cb-header{{color:#fff;font-size:.95em;font-weight:800;letter-spacing:2px;margin-bottom:14px}}
    .cb-row{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}
    .cb-name{{width:90px;color:#94a3b8;font-size:.8em;text-align:right;flex-shrink:0}}
    .cb-bar-wrap{{flex:1;height:8px;background:#1a2035;border-radius:4px;overflow:hidden}}
    .cb-bar{{height:100%;border-radius:4px;transition:width .4s ease}}
    .cb-score{{width:40px;font-size:.85em;font-weight:700;text-align:right;flex-shrink:0}}
    .cb-divider{{height:1px;background:#1e293b;margin:10px 0}}
    .cb-total{{margin-top:4px}}
    .cb-fallback{{color:#64748b;font-size:.85em;padding:8px 0}}
    /* ── Priority 4: 决策时间线 ── */
    .decision-timeline{{padding:4px 0}}
    .dt-header{{color:#fff;font-size:.95em;font-weight:800;letter-spacing:1px;margin-bottom:16px}}
    .dt-unchanged{{color:#94a3b8;font-size:.85em;padding:12px 16px;background:#0d1525;border-radius:8px;border:1px solid #1e293b}}
    .dt-entry{{display:flex;gap:14px;margin-bottom:0}}
    .dt-dot-wrap{{display:flex;flex-direction:column;align-items:center;width:20px;flex-shrink:0}}
    .dt-dot{{width:12px;height:12px;border-radius:50%;border:2px solid #1e293b;flex-shrink:0}}
    .dt-line{{width:2px;flex:1;background:#1e293b;margin:4px 0;min-height:16px}}
    .dt-content{{padding-bottom:16px;flex:1}}
    .dt-time{{color:#64748b;font-size:.72em;letter-spacing:1px;margin-bottom:4px}}
    .dt-decision{{color:#c8cdd8;font-size:.85em;line-height:1.5}}
    .dt-badge{{display:inline-block}}
    .dt-trigger{{display:flex;align-items:center;gap:14px;margin-bottom:8px;padding-left:3px}}
    .dt-trigger-line{{width:2px;height:20px;background:#1e293b;margin-left:9px}}
    .dt-trigger-label{{color:#5b7abf;font-size:.75em;font-style:italic}}
    /* ── Priority 5: 升级/降级条件 ── */
    .triggers-section{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
    .tr-block{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .tr-upgrade{{border-left:4px solid #10b981}}
    .tr-downgrade{{border-left:4px solid #ef4444}}
    .tr-label{{font-size:.9em;font-weight:800;letter-spacing:2px;margin-bottom:12px}}
    .tr-upgrade .tr-label{{color:#10b981}}
    .tr-downgrade .tr-label{{color:#ef4444}}
    .tr-list{{list-style:none;padding:0}}
    .tr-list li{{color:#c8cdd8;font-size:.82em;line-height:1.8;padding:2px 0;padding-left:14px;position:relative}}
    .tr-list li::before{{content:'•';position:absolute;left:0;font-weight:700}}
    .tr-upgrade .tr-list li::before{{color:#10b981}}
    .tr-downgrade .tr-list li::before{{color:#ef4444}}
    .tr-content{{color:#64748b;font-size:.85em;text-align:center;padding:12px}}
    @media(max-width:768px){{.bo-grid{{grid-template-columns:repeat(3,1fr)}}.triggers-section{{grid-template-columns:1fr}}.cb-name{{width:60px;font-size:.72em}}}}
    /* ────────────────────────────────────────────
       Dashboard V2 — 10 New Sections CSS
       ──────────────────────────────────────────── */
    /* S1: AI 执行摘要 */
    .exec-summary{{background:linear-gradient(135deg,#0d1525,#181f35,#0d1525);border:2px solid #1e90ff;border-radius:14px;padding:28px 24px;margin-bottom:0}}
    .es-badge-wrap{{display:flex;align-items:center;gap:16px;margin-bottom:20px;flex-wrap:wrap}}
    .es-badge{{font-size:1.6em;font-weight:900;padding:10px 28px;border-radius:28px;letter-spacing:4px}}
    .es-badge.bet{{background:#10b981;color:#000}}
    .es-badge.watch{{background:#f59e0b;color:#000}}
    .es-badge.no-bet{{background:#ef4444;color:#fff}}
    .es-market{{color:#60a5fa;font-size:1.1em;font-weight:700;letter-spacing:2px}}
    .es-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px}}
    .es-item{{background:#0a0e17;border-radius:10px;padding:16px 18px;text-align:center}}
    .es-label{{display:block;color:#64748b;font-size:.72em;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}}
    .es-value{{display:block;color:#fff;font-size:1.8em;font-weight:900}}
    .es-oneliner{{background:#0a0e17;border-radius:10px;padding:16px 20px;color:#c8cdd8;font-size:.95em;line-height:1.7;border-left:3px solid #1e90ff}}
    /* S2: 足球分析 */
    .football-reasoning{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;border-left:4px solid #22c55e;padding:18px 20px}}
    .fr-row{{display:flex;align-items:flex-start;gap:14px;padding:7px 0;border-bottom:1px solid #151d30}}
    .fr-row:last-child{{border-bottom:none}}
    .fr-label{{min-width:80px;color:#22c55e;font-size:.8em;font-weight:700;letter-spacing:1px;padding-top:1px}}
    .fr-text{{color:#c8cdd8;font-size:.88em;line-height:1.6;flex:1}}
    /* S3: 大小球分析 */
    .ou-card{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;border-left:4px solid #ffc400;padding:20px 22px}}
    .ou-header{{color:#fff;font-size:1.05em;font-weight:800;margin-bottom:16px}}
    .ou-line{{color:#ffc400;font-size:1.2em;font-weight:900;letter-spacing:1px}}
    .ou-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}}
    .ou-item{{background:#0a0e17;border-radius:8px;padding:12px 14px;text-align:center}}
    .ou-label{{display:block;color:#64748b;font-size:.68em;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
    .ou-value{{display:block;color:#fff;font-size:1.3em;font-weight:800}}
    .ou-explain{{background:#1a1500;border-radius:8px;padding:14px 16px;color:#ffc400;font-size:.88em;margin-bottom:10px}}
    .ou-bullets{{list-style:none;padding:0}}
    .ou-bullets li{{color:#c8cdd8;font-size:.85em;line-height:1.8;padding:4px 0;padding-left:16px;position:relative}}
    .ou-bullets li::before{{content:'•';color:#ffc400;position:absolute;left:0;font-weight:700}}
    /* S4: 比分可视化 */
    .cs-section{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .cs-header{{color:#fff;font-size:.95em;font-weight:800;margin-bottom:14px;letter-spacing:1px}}
    .cs-row{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
    .cs-score{{color:#94a3b8;font-size:.85em;min-width:130px;font-weight:700}}
    .cs-bar-wrap{{flex:1;height:10px;background:#1a2035;border-radius:5px;overflow:hidden}}
    .cs-bar{{height:100%;background:linear-gradient(90deg,#60a5fa,#3b82f6);border-radius:5px}}
    .cs-bar-max{{height:100%;background:linear-gradient(90deg,#10b981,#34d399);border-radius:5px}}
    .cs-pct{{color:#fff;font-size:.85em;font-weight:700;min-width:120px}}
    .cs-range{{margin-top:14px;padding:10px 14px;background:#0a0e17;border-radius:8px;color:#60a5fa;font-size:.85em;font-weight:600}}
    /* S5: 进球球员预测 */
    .gs-row{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
    .gs-player{{color:#94a3b8;font-size:.85em;min-width:120px;font-weight:700}}
    .gs-bar-wrap{{flex:1;height:10px;background:#1a2035;border-radius:5px;overflow:hidden}}
    .gs-bar{{height:100%;background:linear-gradient(90deg,#a78bfa,#8b5cf6);border-radius:5px}}
    .gs-pct{{color:#fff;font-size:.85em;font-weight:700;min-width:50px}}
    /* S6: 模型投票 */
    .model-voting{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .mv-row{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #151d30}}
    .mv-row:last-of-type{{border-bottom:none}}
    .mv-warn{{background:#1a1500;border-radius:6px;padding:8px 10px;margin:4px -10px}}
    .mv-model{{color:#94a3b8;font-size:.85em;font-weight:700;min-width:110px}}
    .mv-vote{{color:#c8cdd8;font-size:.88em;flex:1}}
    .mv-agree{{font-size:.9em;min-width:30px;text-align:center}}
    .mv-divider{{height:1px;background:#1e293b;margin:12px 0}}
    .mv-consensus{{color:#10b981;font-size:.88em;font-weight:700}}
    .mv-conf{{color:#94a3b8;font-size:.82em;margin-top:4px}}
    /* S7: 市场动向 */
    .mkt-move{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;border-left:4px solid #f97316;padding:18px 20px}}
    .mm-header{{color:#fff;font-size:.95em;font-weight:800;letter-spacing:1px;margin-bottom:16px}}
    .mm-flow{{display:flex;align-items:center;gap:0;flex-wrap:wrap;margin-bottom:16px}}
    .mm-step{{background:#0a0e17;border-radius:8px;padding:14px 18px;text-align:center;min-width:120px;flex:1}}
    .mm-label{{display:block;color:#64748b;font-size:.68em;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
    .mm-val{{display:block;color:#fff;font-size:1.15em;font-weight:800}}
    .mm-arrow{{color:#f97316;font-size:1.6em;padding:0 10px;font-weight:900;flex-shrink:0}}
    .mm-explain{{color:#c8cdd8;font-size:.85em;line-height:1.7;padding:12px 14px;background:#0a0e17;border-radius:8px;border-left:3px solid #f97316}}
    /* S8: 风险评估 */
    .risk-breakdown{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .rk-row{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}
    .rk-factor{{color:#94a3b8;font-size:.85em;min-width:110px}}
    .rk-bar-wrap{{flex:1;height:8px;background:#1a2035;border-radius:4px;overflow:hidden}}
    .rk-bar{{height:100%;border-radius:4px}}
    .rk-sev{{font-size:.8em;font-weight:700;min-width:40px}}
    .rk-divider{{height:1px;background:#1e293b;margin:10px 0}}
    .rk-overall{{display:flex;align-items:center;gap:10px;color:#c8cdd8;font-size:.88em}}
    .rk-score{{font-weight:900;font-size:1.2em}}
    /* S9: 数据质量 */
    .data-quality{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .dq-row{{display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid #151d30}}
    .dq-row:last-of-type{{border-bottom:none}}
    .dq-source{{color:#94a3b8;font-size:.85em;min-width:100px}}
    .dq-stars{{color:#f59e0b;font-size:.88em;letter-spacing:1px}}
    .dq-note{{color:#64748b;font-size:.78em;margin-left:6px}}
    .dq-divider{{height:1px;background:#1e293b;margin:10px 0}}
    .dq-overall{{display:flex;align-items:center;gap:10px;color:#c8cdd8;font-size:.88em}}
    .dq-score{{font-weight:900;font-size:1.2em;color:#10b981}}
    /* S10: AI Q&A */
    .ai-qa{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;padding:18px 20px}}
    .aq-item{{margin-bottom:8px}}
    .aq-item:last-child{{margin-bottom:0}}
    .aq-question{{color:#a78bfa;font-size:.85em;font-weight:600;padding:10px 14px;background:#0a0e17;border-radius:8px;cursor:pointer;user-select:none;transition:background .2s}}
    .aq-question:hover{{background:#12192a}}
    .aq-arrow{{display:inline-block;margin-right:6px;transition:transform .2s}}
    .aq-answer{{color:#c8cdd8;font-size:.85em;line-height:1.7;padding:12px 14px;margin-top:4px;background:#0a0e17;border-radius:8px;border-left:3px solid #8b5cf6}}
    /* ────────────────────────────────────────────
       Dashboard V3 — New & Enhanced CSS
       ──────────────────────────────────────────── */
    /* V3: AI 最终推荐 */
    .v3-final-rec{{background:linear-gradient(135deg,#0a1628,#101e3a,#0a1628);border:2px solid #c60b1e;border-radius:16px;padding:28px 32px;margin-bottom:24px;position:relative}}
    .v3-final-rec::before{{content:'';display:block;height:4px;background:linear-gradient(90deg,#c60b1e,#e63946,#c60b1e);margin:-28px -32px 24px;border-radius:16px 16px 0 0}}
    .v3fr-header{{display:flex;align-items:center;gap:14px;margin-bottom:18px}}
    .v3fr-icon{{font-size:2em;color:#e63946}}
    .v3fr-title{{color:#e63946;font-size:1.5em;font-weight:900;letter-spacing:6px}}
    .v3fr-subtitle{{color:#8a2028;font-size:.75em;letter-spacing:3px}}
    .v3fr-body{{color:#e2e8f0;font-size:.95em;line-height:2;padding:16px 20px;background:#0a1220;border-radius:10px;border-left:4px solid #e63946}}
    /* V3: 今日最佳推荐 */
    .v3-today-best{{background:linear-gradient(135deg,#1a1500,#2a2000,#1a1500);border:2px solid #ffc400;border-radius:16px;padding:32px;margin-bottom:24px}}
    .v3-today-best.v3-tb-empty{{border-color:#5a5030}}
    .v3tb-header{{display:flex;align-items:center;gap:12px;margin-bottom:24px}}
    .v3tb-icon{{font-size:1.8em;color:#ffc400}}
    .v3tb-title{{color:#ffc400;font-size:1.4em;font-weight:900;letter-spacing:4px}}
    .v3tb-subtitle{{color:#997a00;font-size:.75em;letter-spacing:2px}}
    .v3tb-empty{{color:#997a00;font-size:.95em;text-align:center;padding:20px}}
    .v3tb-threshold{{color:#665500;font-size:.78em;text-align:center;margin-top:8px}}
    .v3tb-card{{background:linear-gradient(135deg,#0d1525,#1a1f35);border:1px solid #2a2f45;border-left:4px solid #ffc400;border-radius:10px;padding:18px 22px;margin-bottom:12px}}
    .v3tb-card:last-child{{margin-bottom:0}}
    .v3tb-stars-row{{margin-bottom:6px}}
    .v3-stars{{font-size:1.1em;letter-spacing:2px}}
    .v3-stars.s5{{color:#ffc400}} .v3-stars.s4{{color:#e6b000}} .v3-stars.s3{{color:#cc9600}} .v3-stars.s2{{color:#b38000}} .v3-stars.s1{{color:#997300}} .v3-stars.s0{{color:#5a5030}}
    .v3tb-main{{display:flex;align-items:baseline;gap:12px;margin-bottom:12px}}
    .v3tb-match{{color:#fff;font-size:1.1em;font-weight:700}}
    .v3tb-selection{{color:#ffc400;font-size:1em;font-weight:800;letter-spacing:1px}}
    .v3tb-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:12px}}
    .v3tb-metric{{background:#0a0e17;border-radius:8px;padding:8px 10px;text-align:center}}
    .v3tb-label{{color:#64748b;font-size:.62em;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}}
    .v3tb-value{{color:#fff;font-size:1.05em;font-weight:800}}
    .v3tb-market{{color:#60a5fa;font-size:.82em}}
    .v3tb-reason{{color:#c8cdd8;font-size:.85em;line-height:1.6;padding:10px 14px;background:#0a0e17;border-radius:8px;border-left:3px solid #997a00}}
    /* V3: NO-BET 检查清单 */
    .v3-why-not-bet{{background:#0d1525;border-radius:10px;border:1px solid #2a2f45;border-left:4px solid #ef4444;padding:18px 20px}}
    .v3-wnb-title{{color:#ef4444;font-size:.95em;font-weight:800;letter-spacing:1px;margin-bottom:12px}}
    .v3-wnb-item{{font-size:.88em;line-height:1.9;padding:2px 0}}
    .v3-nb-checklist{{margin-top:8px;padding:10px 14px;background:#0a0e17;border-radius:8px}}
    .v3-nb-item{{font-size:.82em;line-height:1.8}}
    /* V3: 市场动向范围条 */
    .mm-direction{{text-align:center;color:#f97316;font-size:.9em;font-weight:700;margin-bottom:14px;letter-spacing:1px}}
    .mm-range{{margin-top:4px;padding:0 4px}}
    .mm-range-labels{{display:flex;justify-content:space-between;color:#64748b;font-size:.7em;margin-bottom:4px}}
    .mm-range-track{{position:relative;height:10px;background:#1a2035;border-radius:5px;overflow:visible}}
    .mm-range-zone{{position:absolute;top:0;height:100%;background:rgba(249,115,22,.25);border-radius:5px}}
    .mm-range-dot{{position:absolute;top:-3px;width:16px;height:16px;border-radius:50%;transform:translateX(-50%)}}
    .mm-dot-open{{background:#64748b;border:2px solid #94a3b8}}
    .mm-dot-curr{{background:#f97316;border:2px solid #ffa94d;z-index:1}}
    /* ────────────────────────────────────────────
       Dashboard V3.1 — Polish & Visual Enhancements
       ──────────────────────────────────────────── */
    /* V3.1: 反事实解释 */
    .v31-cfact{{margin:16px 0}}
    .v31-cf-item{{background:#0d1525;border-radius:8px;border-left:3px solid #60a5fa;padding:12px 16px;margin-bottom:8px}}
    .v31-cf-q{{color:#60a5fa;font-size:.85em;font-weight:700;margin-bottom:4px}}
    .v31-cf-a{{color:#c8cdd8;font-size:.83em;line-height:1.6}}
    /* V3.1: 信心雷达 */
    .v31-radar{{background:linear-gradient(135deg,#0d1525,#121a2a);border:1px solid #1e2d4a;border-radius:12px;padding:20px 24px}}
    .radar-header{{color:#f59e0b;font-size:.9em;font-weight:800;letter-spacing:1px;margin-bottom:14px}}
    .radar-row{{display:flex;align-items:center;gap:10px;margin-bottom:10px}}
    .radar-label{{color:#94a3b8;font-size:.78em;width:80px;flex-shrink:0;text-align:right}}
    .radar-bar-wrap{{flex:1;height:8px;background:#1a2035;border-radius:4px;overflow:hidden}}
    .radar-bar{{height:100%;border-radius:4px;transition:width .4s}}
    .radar-val{{color:#fff;font-size:.82em;font-weight:700;width:30px;text-align:right}}
    .radar-total-label{{text-align:center;color:#f59e0b;font-size:.8em;font-weight:600;margin-top:8px;letter-spacing:.5px}}
    /* V3.1: 彩色模型贡献条 */
    .mb-row{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
    .mb-name{{color:#94a3b8;font-size:.82em;width:110px;flex-shrink:0;text-align:right}}
    .mb-bar-wrap{{flex:1;height:12px;background:#1a2035;border-radius:6px;overflow:hidden}}
    .mb-bar{{height:100%;border-radius:6px;transition:width .5s}}
    .mb-val{{font-size:.85em;font-weight:700;width:36px;text-align:left}}
    .mb-total{{text-align:center;color:#94a3b8;font-size:.85em;margin-top:12px;padding-top:10px;border-top:1px solid #1e2d4a}}
    .mb-total span{{color:#fff;font-weight:900;font-size:1.3em;margin-left:8px}}
    /* V3.1: 赔率时间轴 */
    .v31-odds-timeline{{background:#0d1525;border:1px solid #1e2d4a;border-radius:10px;padding:16px 20px}}
    .ot-row{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
    .ot-label{{color:#64748b;font-size:.78em;width:42px;flex-shrink:0}}
    .ot-odds{{color:#f97316;font-size:.95em;font-weight:800;width:48px;flex-shrink:0}}
    .ot-bar-wrap{{flex:1;height:14px;background:#1a2035;border-radius:7px;overflow:hidden}}
    .ot-bar{{height:100%;background:linear-gradient(90deg,#f97316,#fbbf24);border-radius:7px;transition:width .4s;min-width:3px}}
    .ot-ts{{color:#475569;font-size:.7em;width:80px;flex-shrink:0;text-align:right}}
    /* V3.1: 可折叠比赛卡片 */
    .v31-match-card{{background:linear-gradient(135deg,#0a0f1a,#12192a);border:1px solid #1e2d4a;border-radius:12px;margin-bottom:12px;overflow:hidden}}
    .v31-match-card[open]{{border-color:#3b82f6}}
    .v31-mc-summary{{display:flex;align-items:center;gap:12px;padding:14px 18px;cursor:pointer;user-select:none;list-style:none}}
    .v31-mc-summary::-webkit-details-marker{{display:none}}
    .v31-mc-summary:hover{{background:#12192a}}
    .v31-mc-stars{{flex-shrink:0}}
    .v31-mc-teams{{color:#fff;font-size:1.05em;font-weight:700;flex:1}}
    .v31-mc-vs{{color:#475569;font-weight:400;margin:0 4px}}
    .v31-mc-badge{{font-size:.7em;font-weight:800;padding:4px 12px;border-radius:4px;letter-spacing:1px}}
    .badge-bet{{background:rgba(16,185,129,.15);color:#10b981;border:1px solid rgba(16,185,129,.3)}}
    .badge-watch{{background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3)}}
    .badge-nobet{{background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3)}}
    .badge-insufficient{{background:rgba(148,163,184,.15);color:#94a3b8;border:1px solid rgba(148,163,184,.3)}}
    .v31-mc-stats{{display:flex;gap:16px;font-size:.78em;color:#94a3b8;flex-shrink:0}}
    .v31-mc-ev{{font-weight:800}}
    .v31-mc-conf{{font-weight:600}}
    .v31-mc-kelly{{font-weight:600}}
    .v31-mc-body{{padding:8px 18px 18px;border-top:1px solid #1e2d4a;background:#070b14}}
    .v31-mc-meta{{color:#64748b;font-size:.78em;margin-bottom:12px}}
    /* V3.1: 风险管理摘要条 */
    .v31-risk-bar{{background:linear-gradient(135deg,#0a0f1a,#12192a);border:1px solid #1e2d4a;border-radius:10px;padding:14px 20px;margin-bottom:16px}}
    .v31-risk-grid{{display:flex;gap:24px}}
    .v31-risk-item{{flex:1;display:flex;flex-direction:column;align-items:center}}
    .v31-risk-label{{color:#64748b;font-size:.7em;letter-spacing:.5px;margin-bottom:4px}}
    .v31-risk-val{{color:#fff;font-size:1.3em;font-weight:900}}
    /* V3.1: 详细分析折叠 */
    .v31-detail-section{{margin-top:20px}}
    .v31-ds-summary{{color:#64748b;font-size:.85em;font-weight:600;padding:10px 16px;background:#0a0f1a;border:1px dashed #1e2d4a;border-radius:8px;cursor:pointer;display:flex;align-items:center;justify-content:center;letter-spacing:1px}}
    .v31-ds-summary:hover{{color:#94a3b8;border-color:#3b82f6}}
    .v31-ds-body{{padding-top:12px}}
    /* V3.1 Responsive */
    @media(max-width:768px){{.v31-mc-summary{{flex-wrap:wrap;gap:8px}}.v31-mc-stats{{width:100%;justify-content:flex-end}}.v31-risk-grid{{flex-direction:column;gap:10px}}.ot-ts{{display:none}}}}
    /* V3 Responsive */
    @media(max-width:768px){{.v3tb-grid{{grid-template-columns:repeat(3,1fr)}}.v3tb-main{{flex-direction:column;gap:4px}}.v3fr-body{{font-size:.88em}}}}
    /* V2 Responsive */
    @media(max-width:768px){{.es-grid,.ou-grid{{grid-template-columns:repeat(2,1fr)}}.mm-flow{{flex-direction:column;gap:8px;align-items:stretch}}.mm-arrow{{transform:rotate(90deg);padding:4px 0}}.cs-score{{min-width:90px;font-size:.78em}}.gs-player{{min-width:90px;font-size:.78em}}}}
    /* Enhancement 4: ROI Dashboard Cards */
    .roi-card-row{{display:flex;gap:16px;margin-bottom:14px}}
    .roi-card{{flex:1;background:#0d1525;border:1px solid #1a2035;border-radius:10px;padding:18px 20px;text-align:center}}
    .roi-card-row.roi-bottom .roi-card{{flex:1;text-align:center;padding:14px 20px}}
    .roi-label{{color:#64748b;font-size:.72em;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}}
    .roi-value{{color:#fff;font-size:1.8em;font-weight:900}}
    .roi-sub{{color:#64748b;font-size:.75em;margin-top:4px}}
    @media(max-width:768px){{.roi-card-row{{flex-direction:column}}}}
</style>
</head>
<body>"""

    def _footer(self, generated_at: datetime | None = None, version: str | None = None) -> str:
        now = generated_at or datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        ver = f" | Pipeline: {version}" if version else ""
        return (
            f'<div class="footer">\n'
            f'  生成时间：{ts}{ver} | Marvis AI | 非投资建议\n'
            f"</div>"
        )

    @staticmethod
    def _fmt_time(dt: datetime | None) -> str:
        if dt is None:
            return NA
        return dt.strftime("%Y-%m-%d %H:%M UTC")
