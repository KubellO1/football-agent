"""预测写入服务：将 Decision Engine 输出自动记入 predictions 表。

在 FixtureAnalysisService 对每场比赛完成评估后，本服务提取每个 selection
的完整决策数据并写入 predictions 表。每一行对应一个 selection（1X2 选项），
包含比赛上下文、市场决策、价值评估、最终判定（BET/WATCH/NO_BET）及数据质量。

predictions 表是 settlement 和 performance 追踪的数据源——所有生产预测
自动写入 PostgreSQL，无 JSON/临时文件依赖。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.models.entities.fixture import Fixture
from app.repositories.sqlalchemy.models import PredictionORM
from app.services.fixture_analysis import (
    NO_ODDS_MESSAGE,
    DetailedAnalysis,
    SelectionAnalysis,
)
from app.services.recommendation_gate import GateDecision

logger = get_logger(__name__)

# 预测版本标识，每次部署可递增
PREDICTION_VERSION = "1.0.0"


@dataclass
class PredictionLogReport:
    """一次预测写入的汇总报告。"""

    fixture_id: str
    total_selections: int
    bet_count: int
    watch_count: int
    no_bet_count: int
    inserted: int
    errors: int = 0
    details: list[str] = field(default_factory=list)


def _derive_final_decision(decision: GateDecision, ev: float) -> str:
    """从 gate 判定推导 final_decision (BET/WATCH/NO_BET)。

    规则：
    - approved=True → BET
    - approved=False + (risk HIGH 或 EV ≤ 0) → NO_BET
    - approved=False + 其他原因 → WATCH
    """
    if decision.approved:
        return "BET"

    reasons_lower = " ".join(decision.reasons).lower()
    if "风险等级为「高」" in " ".join(decision.reasons) or ev <= 0.0:
        return "NO_BET"
    return "WATCH"


def _build_why_not_bet(decision: GateDecision) -> str | None:
    """构建未投注原因说明。"""
    if decision.approved:
        return None
    return "; ".join(decision.reasons)


def _build_confidence_killer(selection: SelectionAnalysis) -> str | None:
    """提取信心杀手标记。"""
    if selection.confidence_killer:
        return selection.confidence_killer
    if not selection.recommended and selection.confidence < 0.5:
        return f"模型信心不足 ({selection.confidence:.1%})"
    return None


async def log_fixture_predictions(
    detailed: DetailedAnalysis,
    *,
    session: Any,
    competition_name: str = "",
    home_team_name: str = "",
    away_team_name: str = "",
    model_version: str = "",
) -> PredictionLogReport:
    """将一场比赛的全部 selection 分析写入 predictions 表。

    Args:
        detailed: FixtureAnalysisService 的完整产出
        session: SQLAlchemy AsyncSession
        competition_name: 赛事名称（用于 predictions.competition 列）
        home_team_name: 主队名称
        away_team_name: 客队名称
        model_version: 模型版本标识

    Returns:
        PredictionLogReport 汇总报告
    """
    fixture = detailed.fixture
    result = detailed.result
    now = datetime.now(timezone.utc)

    # 构建 provider_sources 元数据
    provider_sources: dict[str, Any] = {
        "fixture_source": fixture.external_source or "unknown",
        "fixture_external_id": fixture.external_id or "N/A",
        "odds_source": "the-odds-api",
        "data_completeness": result.data_completeness,
    }

    report = PredictionLogReport(
        fixture_id=str(fixture.id),
        total_selections=len(result.selections),
        bet_count=0,
        watch_count=0,
        no_bet_count=0,
        inserted=0,
    )

    if not result.selections:
        # 无 selection：区分"无赔率"与"数据不足"
        is_no_odds = (result.message or "").strip() == NO_ODDS_MESSAGE.strip()
        if is_no_odds:
            # 细分 NO_ODDS 子类型（优先级依次递减）
            killer = (result.confidence_killer or "").lower()
            # ── Odds-API.io specific classifications ──
            if "rate_limit" in killer or "429" in killer:
                final_decision = "NO_ODDS_RATE_LIMIT"
                why_not = "Odds provider rate limited (HTTP 429). No odds available."
            elif "quota" in killer or "quota_exhausted" in killer:
                final_decision = "NO_ODDS_QUOTA"
                why_not = "Odds API monthly quota exhausted (x-requests-remaining=0). No odds available."
            elif "api_key" in killer or "invalid_api_key" in killer or "auth" in killer:
                final_decision = "NO_ODDS_AUTH"
                why_not = "Odds API key invalid or authentication failed. Check ODDS_API_KEY in .env."
            elif "event_not_found" in killer or "404_event" in killer:
                final_decision = "NO_ODDS_EVENT_NOT_FOUND"
                why_not = "Fixture not found in odds provider (no matching event)."
            elif "market_not_found" in killer or "no_1x2" in killer:
                final_decision = "NO_ODDS_MARKET_NOT_FOUND"
                why_not = "Odds provider returned event but no 1X2 moneyline market."
            elif "mapping_failed" in killer or "ambiguous" in killer:
                final_decision = "NO_ODDS_MAPPING_FAILED"
                why_not = "Odds event could not be mapped to any API-Football fixture (team name / kickoff mismatch)."
            elif "provider_error" in killer:
                final_decision = "NO_ODDS_PROVIDER_ERROR"
                why_not = "Odds provider returned an unexpected error."
            else:
                final_decision = "NO_ODDS_TRUE"
                why_not = "No valid bookmaker odds matched — odds provider returned no snapshots for this fixture"
        else:
            final_decision = "WATCH"
            why_not = result.message or "数据不足，无法生成预测"

        row = PredictionORM(
            id=uuid.uuid4(),
            fixture_id=fixture.id,
            kickoff_time=fixture.kickoff,
            competition=competition_name or str(fixture.competition_id),
            home_team=home_team_name or str(fixture.home_team_id),
            away_team=away_team_name or str(fixture.away_team_id),
            prediction_timestamp=now,
            prediction_version=PREDICTION_VERSION,
            model_version=model_version,
            final_decision=final_decision,
            why_not_bet=why_not,
            confidence_killer=result.confidence_killer,
            provider_sources=provider_sources,
            data_quality=result.data_completeness,
            generated_at=now,
        )
        session.add(row)
        report.inserted = 1
        if is_no_odds:
            report.no_bet_count = 1
        else:
            report.watch_count = 1
        report.details.append(f"无 selection：{why_not}")
        return report

    # 为每个 selection 写入一行
    for sel in result.selections:
        # 推导 final_decision
        # 需要从 reviewed 中找到对应的 GateDecision
        gate_decision = _find_gate_decision(detailed, sel.code)
        if gate_decision is None:
            # 没有 gate 判定（不应发生）：基于 recommended 推导
            final_decision = "BET" if sel.recommended else "WATCH"
            why_not_bet = None if sel.recommended else "gate 判定缺失"
        else:
            final_decision = _derive_final_decision(gate_decision, sel.expected_value)
            why_not_bet = _build_why_not_bet(gate_decision)

        if final_decision == "BET":
            report.bet_count += 1
        elif final_decision == "WATCH":
            report.watch_count += 1
        else:
            report.no_bet_count += 1

        try:
            row = PredictionORM(
                id=uuid.uuid4(),
                fixture_id=fixture.id,
                # 比赛上下文
                kickoff_time=fixture.kickoff,
                competition=competition_name or str(fixture.competition_id),
                home_team=home_team_name or str(fixture.home_team_id),
                away_team=away_team_name or str(fixture.away_team_id),
                # 预测元数据
                prediction_timestamp=now,
                prediction_version=PREDICTION_VERSION,
                # 市场决策
                market="1X2",
                selection=sel.code,
                odds=Decimal(str(round(sel.decimal_odds, 3))),
                market_probability=sel.implied_probability,
                model_probability=sel.model_probability,
                # 价值评估
                expected_value=sel.expected_value,
                kelly_stake=sel.kelly_stake,
                confidence=sel.confidence,
                # 最终判定
                final_decision=final_decision,
                why_not_bet=why_not_bet,
                confidence_killer=_build_confidence_killer(sel),
                # 元数据
                provider_sources=provider_sources,
                model_version=model_version,
                data_quality=result.data_completeness,
                generated_at=now,
            )
            session.add(row)
            report.inserted += 1
            report.details.append(
                f"{sel.code} → {final_decision} "
                f"(EV={sel.expected_value:+.3f}, Kelly={sel.kelly_fraction:.1%}, "
                f"conf={sel.confidence:.0%})"
            )
        except Exception:
            logger.exception(
                "Failed to insert prediction for fixture %s selection %s",
                fixture.id,
                sel.code,
            )
            report.errors += 1

    logger.info(
        "PredictionLogger: fixture=%s selections=%d BET=%d WATCH=%d NO_BET=%d inserted=%d errors=%d",
        fixture.id,
        report.total_selections,
        report.bet_count,
        report.watch_count,
        report.no_bet_count,
        report.inserted,
        report.errors,
    )
    return report


def _find_gate_decision(
    detailed: DetailedAnalysis, code: str
) -> GateDecision | None:
    """从 DetailedAnalysis.reviewed 中按 selection code 查找 gate 判定。"""
    for reviewed in detailed.reviewed:
        if reviewed.candidate.selection.code == code:
            return reviewed.decision
    return None
