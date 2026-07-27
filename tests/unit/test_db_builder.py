"""Dashboard db_builder 单元测试 — 覆盖 value_bets → TopPick/TopRecommendation 转换逻辑。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.db_builder import build_daily_dashboard
from app.dashboard.types import DailyDashboardData, TopPick
from app.repositories.sqlalchemy.models import (
    PREDICTION_RECORD_DECISION,
    FixtureORM,
    PredictionORM,
    ValueBetORM,
)


def _make_fixture() -> FixtureORM:
    return FixtureORM(
        id=uuid4(),
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=datetime(2026, 7, 16, 14, 0, tzinfo=UTC),
        status="upcoming",
    )


def _make_prediction(fixture_id, home: str, away: str) -> PredictionORM:
    return PredictionORM(
        id=uuid4(),
        fixture_id=fixture_id,
        record_kind=PREDICTION_RECORD_DECISION,
        home_team=home,
        away_team=away,
        final_decision="WATCH",
        generated_at=datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
    )


def _make_value_bet(fixture_id) -> ValueBetORM:
    return ValueBetORM(
        id=uuid4(),
        fixture_id=fixture_id,
        selection_market="1x2",
        selection_code="home",
        selection_line=None,
        odds_decimal=Decimal("2.50"),
        model_probability=0.48,
        confidence=0.85,
        stake_fraction=0.04,
        rationale="Clear value edge on home side.",
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _patch_whitelist():
    """返回 mock whitelist: is_allowed 总是返回 True。"""
    m = MagicMock()
    m.is_allowed.return_value = True
    return patch("app.config.whitelist.get_whitelist", return_value=m)


def _patch_comp_repo():
    """返回 mock CompRepo: get() 返回简单 competition 对象。"""
    m = MagicMock()
    m.get = AsyncMock(return_value=MagicMock(name="MockComp"))
    return patch(
        "app.repositories.sqlalchemy.reference_repositories.SqlAlchemyCompetitionRepository",
        return_value=m,
    )


# ── Tests ────────────────────────────────────────────────────────────


def _setup_session(
    *,
    fixtures=None,
    predictions=None,
    value_bets=None,
    odds_count=0,
    vb_count=0,
    decision_count=0,
    settlement_count=0,
    pl_sum=0.0,
    latest_perf=None,
):
    """返回一个配置好的 AsyncMock session，无需手动排列 side_effect。"""
    session = AsyncMock(spec=AsyncSession)

    # Mock execute() — 不同 query 返回不同结果
    def _execute_side_effect(query, *args, **kwargs):
        result = MagicMock()
        stmt_str = str(query)

        if "fixtures" in stmt_str and "FROM fixtures" in stmt_str:
            result = MagicMock()
            result.scalars.return_value.all.return_value = fixtures or []
            return result
        elif "predictions" in stmt_str and "FROM predictions" in stmt_str:
            result = MagicMock()
            result.scalars.return_value.all.return_value = predictions or []
            return result
        elif "performance_snapshots" in stmt_str:
            result = MagicMock()
            result.scalars.return_value.first.return_value = latest_perf
            return result
        elif "value_bets" in stmt_str:
            result = MagicMock()
            result.scalars.return_value.all.return_value = value_bets or []
            return result
        elif "settlement" in stmt_str and "profit_loss" in stmt_str.lower():
            result = MagicMock()
            result.scalar.return_value = pl_sum
            return result
        # Default: return empty
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_execute_side_effect)

    # Mock scalar() — used for count queries
    def _scalar_side_effect(query, *args, **kwargs):
        stmt_str = str(query)
        if "odds_snapshots" in stmt_str:
            return odds_count
        elif "value_bets" in stmt_str:
            return vb_count
        elif "decision_logs" in stmt_str:
            return decision_count
        elif "settlements" in stmt_str:
            return settlement_count
        return 0

    session.scalar = AsyncMock(side_effect=_scalar_side_effect)

    return session


@pytest.mark.unit
def test_empty_value_bets_yields_empty_top_picks() -> None:
    """当日没有 value_bets 时，top_picks 和 top_recommendations 为空列表。"""

    async def _run() -> None:
        fix = _make_fixture()
        pred = _make_prediction(fix.id, "Arsenal", "Chelsea")

        session = _setup_session(
            fixtures=[fix],
            predictions=[pred],
            value_bets=[],
            odds_count=0,
            vb_count=0,
        )

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        assert data.top_picks == []
        assert data.top_recommendations == []

    asyncio.run(_run())


@pytest.mark.unit
def test_value_bets_become_top_picks_and_recommendations() -> None:
    """value_bets 被正确转换为 TopPick 和 TopRecommendation。"""

    async def _run() -> None:
        fix = _make_fixture()
        pred = _make_prediction(fix.id, "Barcelona", "Real Madrid")
        vb = _make_value_bet(fix.id)

        session = _setup_session(
            fixtures=[fix],
            predictions=[pred],
            value_bets=[vb],
            odds_count=1,
            vb_count=1,
            decision_count=1,
            settlement_count=1,
            pl_sum=0.0,
            latest_perf=None,
        )

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        # Verify TopPick
        assert len(data.top_picks) == 1
        tp = data.top_picks[0]
        assert tp.match_label == "Barcelona vs Real Madrid"
        assert tp.market == "1x2"
        assert tp.odds == 2.50
        assert tp.model_prob == 0.48
        assert tp.ev == pytest.approx(0.48 * 2.50 - 1.0)
        assert tp.confidence == 0.85
        assert tp.stake == 0.04
        assert tp.reason == "Clear value edge on home side."

        # Verify TopRecommendation
        assert len(data.top_recommendations) == 1
        tr = data.top_recommendations[0]
        assert tr.match_label == "Barcelona vs Real Madrid"
        assert tr.market == "1x2"
        assert tr.selection == "home"
        assert tr.odds == 2.50
        assert tr.model_prob == 0.48
        assert tr.ev == pytest.approx(0.48 * 2.50 - 1.0)
        assert tr.confidence == 0.85
        assert tr.stake == 0.04
        assert tr.reason == "Clear value edge on home side."
        assert tr.category == "精选"

    asyncio.run(_run())


@pytest.mark.unit
def test_value_bets_no_rationale_uses_empty_string() -> None:
    """rationale 为空时，reason 填充空字符串而非 None。"""

    async def _run() -> None:
        fix = _make_fixture()
        pred = _make_prediction(fix.id, "PSG", "Marseille")
        vb = _make_value_bet(fix.id)
        vb.rationale = None

        session = _setup_session(
            fixtures=[fix],
            predictions=[pred],
            value_bets=[vb],
            odds_count=1,
            vb_count=1,
            decision_count=1,
            settlement_count=1,
        )

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        assert data.top_picks[0].reason == ""
        assert data.top_recommendations[0].reason == ""

    asyncio.run(_run())


@pytest.mark.unit
def test_fixture_ids_empty_when_no_fixtures() -> None:
    """当日无比赛时，不查询 value_bets 也不崩溃。"""

    async def _run() -> None:
        session = _setup_session(fixtures=[], predictions=[])

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        assert data.top_picks == []
        assert data.top_recommendations == []

    asyncio.run(_run())


# ── Extended tests ────────────────────────────────────────────────────


@pytest.mark.unit
def test_multiple_value_bets_sorted_by_ev_descending() -> None:
    """多个 ValueBet 按 expected_value 从高到低排序。"""

    async def _run() -> None:
        fix_a = _make_fixture()
        fix_b = _make_fixture()
        fix_c = _make_fixture()

        pred_a = _make_prediction(fix_a.id, "Arsenal", "Chelsea")
        pred_b = _make_prediction(fix_b.id, "Barcelona", "Real Madrid")
        pred_c = _make_prediction(fix_c.id, "Bayern", "Dortmund")

        # EV = model_prob * odds - 1.0
        # vb_a: 0.48 * 2.50 - 1 = 0.20
        vb_a = ValueBetORM(
            id=uuid4(),
            fixture_id=fix_a.id,
            selection_market="1x2",
            selection_code="home",
            odds_decimal=Decimal("2.50"),
            model_probability=0.48,
            confidence=0.85,
            stake_fraction=0.04,
            rationale="Value on Arsenal",
        )
        # vb_b: 0.55 * 3.00 - 1 = 0.65 (highest)
        vb_b = ValueBetORM(
            id=uuid4(),
            fixture_id=fix_b.id,
            selection_market="1x2",
            selection_code="away",
            odds_decimal=Decimal("3.00"),
            model_probability=0.55,
            confidence=0.72,
            stake_fraction=0.03,
            rationale="Barca underrated away",
        )
        # vb_c: 0.40 * 2.80 - 1 = 0.12 (lowest)
        vb_c = ValueBetORM(
            id=uuid4(),
            fixture_id=fix_c.id,
            selection_market="1x2",
            selection_code="draw",
            odds_decimal=Decimal("2.80"),
            model_probability=0.40,
            confidence=0.66,
            stake_fraction=0.02,
            rationale="Draw value",
        )

        session = _setup_session(
            fixtures=[fix_a, fix_b, fix_c],
            predictions=[pred_a, pred_b, pred_c],
            value_bets=[vb_a, vb_b, vb_c],
            odds_count=3,
            vb_count=3,
            decision_count=3,
            settlement_count=3,
        )

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        assert len(data.top_picks) == 3
        ev_values = [tp.ev for tp in data.top_picks]
        assert ev_values == sorted(
            ev_values, reverse=True
        ), f"Expected EV descending, got {ev_values}"
        # Verify specific order: Barca(0.65) > Arsenal(0.20) > Bayern(0.12)
        assert data.top_picks[0].match_label == "Barcelona vs Real Madrid"
        assert data.top_picks[0].ev == pytest.approx(0.65)
        assert data.top_picks[1].match_label == "Arsenal vs Chelsea"
        assert data.top_picks[1].ev == pytest.approx(0.20)
        assert data.top_picks[2].match_label == "Bayern vs Dortmund"
        assert data.top_picks[2].ev == pytest.approx(0.12)

    asyncio.run(_run())


@pytest.mark.unit
def test_multiple_bets_per_fixture_not_deduped() -> None:
    """同一场比赛的多个 selection（如 home + over_2.5）不会去重或丢失。"""

    async def _run() -> None:
        fix = _make_fixture()
        pred = _make_prediction(fix.id, "Man City", "Liverpool")

        vb_home = ValueBetORM(
            id=uuid4(),
            fixture_id=fix.id,
            selection_market="1x2",
            selection_code="home",
            odds_decimal=Decimal("2.20"),
            model_probability=0.52,
            confidence=0.80,
            stake_fraction=0.04,
            rationale="Home edge",
        )
        vb_over = ValueBetORM(
            id=uuid4(),
            fixture_id=fix.id,
            selection_market="over_under_2_5",
            selection_code="over",
            odds_decimal=Decimal("1.90"),
            model_probability=0.60,
            confidence=0.75,
            stake_fraction=0.03,
            rationale="Expected goals high",
        )

        session = _setup_session(
            fixtures=[fix],
            predictions=[pred],
            value_bets=[vb_home, vb_over],
            odds_count=1,
            vb_count=2,
            decision_count=2,
            settlement_count=2,
        )

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        assert len(data.top_picks) == 2, f"Expected 2 picks, got {len(data.top_picks)}"
        markets = {tp.market for tp in data.top_picks}
        assert markets == {"1x2", "over_under_2_5"}, f"Unexpected markets: {markets}"

        # Both share the same match_label
        for tp in data.top_picks:
            assert tp.match_label == "Man City vs Liverpool"

    asyncio.run(_run())


@pytest.mark.unit
def test_missing_fixture_in_team_lookup_uses_fallback() -> None:
    """ValueBet 的 fixture_id 在 predictions 中无对应记录时，优雅降级为 ? vs ?。"""

    async def _run() -> None:
        fix = _make_fixture()
        # pred belongs to a different fixture — team_lookup won't cover fix.id
        other_fix = _make_fixture()
        pred = _make_prediction(other_fix.id, "Inter", "Milan")

        vb = _make_value_bet(fix.id)

        session = _setup_session(
            fixtures=[fix],
            predictions=[pred],
            value_bets=[vb],
            odds_count=1,
            vb_count=1,
            decision_count=1,
            settlement_count=1,
        )

        with _patch_whitelist(), _patch_comp_repo():
            data: DailyDashboardData = await build_daily_dashboard(session, date(2026, 7, 16))

        assert len(data.top_picks) == 1
        assert data.top_picks[0].match_label == "? vs ?"
        # Other fields should still be populated normally
        assert data.top_picks[0].odds == 2.50
        assert data.top_picks[0].market == "1x2"

    asyncio.run(_run())


@pytest.mark.unit
def test_renderer_shows_cards_when_top_picks_exist() -> None:
    """当 top_picks 非空时，渲染器输出推荐卡片而非"暂无推荐"。"""

    from app.dashboard.renderer import DashboardRenderer

    tp = TopPick(
        match_label="Arsenal vs Chelsea",
        market="1x2",
        odds=2.50,
        model_prob=0.48,
        ev=0.20,
        confidence=0.85,
        stake=0.04,
        reason="Value on home side.",
    )

    data = DailyDashboardData(
        date="2026-07-16",
        top_picks=[tp],
    )

    renderer = DashboardRenderer()
    html = renderer._todays_best_recommendations(data)

    assert "v3tb-card" in html, f"Expected card HTML, got:\n{html[:500]}"
    assert "今日暂无符合条件的推荐" not in html
    assert "v3tb-empty" not in html

    # Also verify empty case still renders fallback
    empty_data = DailyDashboardData(date="2026-07-16", top_picks=[])
    empty_html = renderer._todays_best_recommendations(empty_data)
    assert "今日暂无符合条件的推荐" in empty_html
