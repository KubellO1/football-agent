"""预测写入服务：将 Decision Engine 输出自动记入 predictions 表。

在 FixtureAnalysisService 对每场比赛完成评估后，本服务提取每个 selection
的完整决策数据并写入 predictions 表。每一行对应一个 selection（1X2 选项），
包含比赛上下文、市场决策、价值评估、最终判定（BET/WATCH/NO_BET）及数据质量。

predictions 表是 settlement 和 performance 追踪的数据源——所有生产预测
自动写入 PostgreSQL，无 JSON/临时文件依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import get_logger
from app.repositories.sqlalchemy.models import (
    PREDICTION_RECORD_DECISION,
    PredictionORM,
)
from app.services.fixture_analysis import (
    NO_ODDS_MESSAGE,
    DetailedAnalysis,
    SelectionAnalysis,
)
from app.services.prediction_decision_identity import (
    PredictionDecisionContext,
    PredictionDecisionIdentity,
    PredictionDecisionWorkflow,
    build_prediction_decision_identity,
)

if TYPE_CHECKING:
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
    reused: int = 0
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


def _default_decision_context(
    detailed: DetailedAnalysis,
    model_version: str,
) -> PredictionDecisionContext:
    if model_version != "pre_kickoff":
        return PredictionDecisionContext.daily(detailed.analysis_as_of.date())
    minutes_before = (detailed.fixture.kickoff - detailed.analysis_as_of).total_seconds() / 60.0
    if minutes_before <= 30.0:
        checkpoint = "T30"
    elif minutes_before <= 60.0:
        checkpoint = "T60"
    else:
        checkpoint = "T90"
    return PredictionDecisionContext.pre_kickoff(checkpoint)


_SEMANTIC_VALUE_FIELDS = (
    "market",
    "selection",
    "odds",
    "market_probability",
    "model_probability",
    "expected_value",
    "kelly_stake",
    "confidence",
    "final_decision",
    "why_not_bet",
    "confidence_killer",
    "data_quality",
)


def _identity_metadata(
    identity: PredictionDecisionIdentity,
    context: PredictionDecisionContext,
) -> dict[str, Any]:
    return {
        "version": 1,
        "key": identity.key,
        "workflow": context.workflow.value,
        "checkpoint": context.checkpoint,
        "run_date": context.run_date.isoformat() if context.run_date is not None else None,
        "input_fingerprint": identity.input_fingerprint,
        "decision_fingerprint": identity.decision_fingerprint,
    }


def _legacy_context_matches(
    row: PredictionORM,
    context: PredictionDecisionContext,
) -> bool:
    timestamp = row.prediction_timestamp
    if timestamp is None:
        return False
    if context.workflow is PredictionDecisionWorkflow.DAILY:
        return (
            context.run_date is not None
            and row.kickoff_time is not None
            and row.kickoff_time.date() == context.run_date
        )

    kickoff = row.kickoff_time
    if kickoff is None:
        return False
    minutes_before = (kickoff - timestamp).total_seconds() / 60.0
    if context.checkpoint == "T90":
        return 60.0 < minutes_before <= 100.0
    if context.checkpoint == "T60":
        return 30.0 < minutes_before <= 60.0
    return -5.0 <= minutes_before <= 30.0


def _legacy_row_matches(
    row: PredictionORM,
    *,
    values: dict[str, Any],
    context: PredictionDecisionContext,
) -> bool:
    sources = row.provider_sources or {}
    metadata = sources.get("decision_identity")
    if isinstance(metadata, dict):
        return False
    if not _legacy_context_matches(row, context):
        return False
    return all(getattr(row, field) == values.get(field) for field in _SEMANTIC_VALUE_FIELDS)


async def _persist_decision_if_absent(
    *,
    session: Any,
    detailed: DetailedAnalysis,
    context: PredictionDecisionContext,
    model_version: str,
    provider_sources: dict[str, Any],
    values: dict[str, Any],
) -> bool:
    semantic_payload = {field: values.get(field) for field in _SEMANTIC_VALUE_FIELDS}
    identity = build_prediction_decision_identity(
        detailed=detailed,
        context=context,
        prediction_version=PREDICTION_VERSION,
        model_version=model_version,
        decision_payload=semantic_payload,
    )

    existing = await session.get(PredictionORM, identity.row_id)
    if existing is not None:
        return False

    legacy_stmt = select(PredictionORM).where(
        PredictionORM.fixture_id == detailed.fixture.id,
        PredictionORM.record_kind == PREDICTION_RECORD_DECISION,
        PredictionORM.prediction_version == PREDICTION_VERSION,
        PredictionORM.model_version == model_version,
    )
    legacy_rows = (await session.execute(legacy_stmt)).scalars().all()
    if any(_legacy_row_matches(row, values=values, context=context) for row in legacy_rows):
        return False

    enriched_sources = dict(provider_sources)
    enriched_sources["decision_identity"] = _identity_metadata(identity, context)
    statement = (
        pg_insert(PredictionORM)
        .values(
            id=identity.row_id,
            fixture_id=detailed.fixture.id,
            record_kind=PREDICTION_RECORD_DECISION,
            prediction_version=PREDICTION_VERSION,
            model_version=model_version,
            provider_sources=enriched_sources,
            **values,
        )
        .on_conflict_do_nothing(index_elements=[PredictionORM.id])
        .returning(PredictionORM.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def log_fixture_predictions(
    detailed: DetailedAnalysis,
    *,
    session: Any,
    competition_name: str = "",
    home_team_name: str = "",
    away_team_name: str = "",
    model_version: str = "",
    decision_context: PredictionDecisionContext | None = None,
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
    now = datetime.now(UTC)
    decision_context = decision_context or _default_decision_context(detailed, model_version)

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
                why_not = (
                    "Odds API monthly quota exhausted (x-requests-remaining=0). No odds available."
                )
            elif "api_key" in killer or "invalid_api_key" in killer or "auth" in killer:
                final_decision = "NO_ODDS_AUTH"
                why_not = (
                    "Odds API key invalid or authentication failed. Check ODDS_API_KEY in .env."
                )
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

        values: dict[str, Any] = {
            "kickoff_time": fixture.kickoff,
            "competition": competition_name or str(fixture.competition_id),
            "home_team": home_team_name or str(fixture.home_team_id),
            "away_team": away_team_name or str(fixture.away_team_id),
            "prediction_timestamp": now,
            "final_decision": final_decision,
            "why_not_bet": why_not,
            "confidence_killer": result.confidence_killer,
            "data_quality": result.data_completeness,
            "generated_at": now,
        }
        inserted = await _persist_decision_if_absent(
            session=session,
            detailed=detailed,
            context=decision_context,
            model_version=model_version,
            provider_sources=provider_sources,
            values=values,
        )
        report.inserted = int(inserted)
        report.reused = int(not inserted)
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
            values = {
                # 比赛上下文
                "kickoff_time": fixture.kickoff,
                "competition": competition_name or str(fixture.competition_id),
                "home_team": home_team_name or str(fixture.home_team_id),
                "away_team": away_team_name or str(fixture.away_team_id),
                # 预测元数据
                "prediction_timestamp": now,
                # 市场决策
                "market": "1X2",
                "selection": sel.code,
                "odds": Decimal(str(round(sel.decimal_odds, 3))),
                "market_probability": sel.implied_probability,
                "model_probability": sel.model_probability,
                # 价值评估
                "expected_value": sel.expected_value,
                "kelly_stake": sel.kelly_stake,
                "confidence": sel.confidence,
                # 最终判定
                "final_decision": final_decision,
                "why_not_bet": why_not_bet,
                "confidence_killer": _build_confidence_killer(sel),
                # 元数据
                "data_quality": result.data_completeness,
                "generated_at": now,
            }
            inserted = await _persist_decision_if_absent(
                session=session,
                detailed=detailed,
                context=decision_context,
                model_version=model_version,
                provider_sources=provider_sources,
                values=values,
            )
            report.inserted += int(inserted)
            report.reused += int(not inserted)
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
        "PredictionLogger: fixture=%s selections=%d BET=%d WATCH=%d NO_BET=%d "
        "inserted=%d reused=%d errors=%d",
        fixture.id,
        report.total_selections,
        report.bet_count,
        report.watch_count,
        report.no_bet_count,
        report.inserted,
        report.reused,
        report.errors,
    )
    return report


def _find_gate_decision(detailed: DetailedAnalysis, code: str) -> GateDecision | None:
    """从 DetailedAnalysis.reviewed 中按 selection code 查找 gate 判定。"""
    for reviewed in detailed.reviewed:
        if reviewed.candidate.selection.code == code:
            return reviewed.decision
    return None
