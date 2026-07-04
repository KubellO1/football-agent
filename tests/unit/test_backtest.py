"""回测统计 compute_stats 的单元测试（纯函数，合成数据）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import math

from app.services.backtest import BetPlaced, MatchOutcome, compute_stats


def _po(
    p_home: float,
    p_draw: float,
    p_away: float,
    actual: str,
    *,
    bet: BetPlaced | None = None,
) -> MatchOutcome:
    """概率可控的 outcome（用于 Brier/LogLoss/校准测试）。"""
    return MatchOutcome(
        fixture_id=uuid4(),
        kickoff=datetime(2024, 1, 1, tzinfo=UTC),
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        predicted=max(
            {"home": p_home, "draw": p_draw, "away": p_away},
            key=lambda k: {"home": p_home, "draw": p_draw, "away": p_away}[k],
        ),
        actual=actual,
        p_home=p_home,
        p_draw=p_draw,
        p_away=p_away,
        total_goals=3,
        over_pred=None,
        over_actual=True,
        bet=bet,
    )


def _outcome(
    predicted: str,
    actual: str,
    *,
    over_pred: bool | None,
    over_actual: bool,
    bet: BetPlaced | None = None,
) -> MatchOutcome:
    return MatchOutcome(
        fixture_id=uuid4(),
        kickoff=datetime(2024, 1, 1, tzinfo=UTC),
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        predicted=predicted,
        actual=actual,
        p_home=0.5,
        p_draw=0.3,
        p_away=0.2,
        total_goals=3,
        over_pred=over_pred,
        over_actual=over_actual,
        bet=bet,
    )


@pytest.mark.unit
def test_empty_outcomes() -> None:
    stats = compute_stats([], fixtures_skipped=5)
    assert stats.fixtures_evaluated == 0
    assert stats.fixtures_skipped == 5
    assert stats.bets_placed == 0
    assert stats.over_under_accuracy is None


@pytest.mark.unit
def test_full_stats() -> None:
    outcomes = [
        _outcome(
            "home",
            "home",
            over_pred=True,
            over_actual=True,
            bet=BetPlaced("home", 2.0, 0.1, 0.1, 0.8, won=True),
        ),
        _outcome("draw", "draw", over_pred=False, over_actual=False),
        _outcome(
            "home",
            "away",
            over_pred=True,
            over_actual=False,
            bet=BetPlaced("home", 3.0, 0.2, 0.05, 0.7, won=False),
        ),
        _outcome("away", "away", over_pred=None, over_actual=True),
    ]
    s = compute_stats(outcomes, fixtures_skipped=1)

    assert s.fixtures_evaluated == 4
    assert s.fixtures_skipped == 1
    assert s.winner_accuracy == pytest.approx(0.75)  # 3/4
    assert s.draw_precision == pytest.approx(1.0)  # predicted 1 draw, correct
    assert s.draw_recall == pytest.approx(1.0)  # 1 actual draw, caught
    assert s.over_under_accuracy == pytest.approx(2 / 3)  # 3 have O/U preds, 2 correct

    assert s.bets_placed == 2
    assert s.avg_ev == pytest.approx(0.15)
    assert s.avg_kelly == pytest.approx(0.075)
    assert s.avg_confidence == pytest.approx(0.75)
    assert s.win_rate == pytest.approx(0.5)

    # flat: +1.0 then -1.0 -> cum [1.0, 0.0]; roi 0/2; drawdown 1.0
    assert s.flat_curve == pytest.approx([1.0, 0.0])
    assert s.flat_roi == pytest.approx(0.0)
    assert s.max_drawdown == pytest.approx(1.0)

    # kelly: 1.0 -> 1.1 (win) -> 1.045 (loss stake 0.055); roi 0.045
    assert s.kelly_curve == pytest.approx([1.1, 1.045])
    assert s.kelly_roi == pytest.approx(0.045)
    assert s.kelly_max_drawdown == pytest.approx((1.1 - 1.045) / 1.1)


@pytest.mark.unit
def test_no_bets_leaves_betting_metrics_zero() -> None:
    outcomes = [_outcome("home", "home", over_pred=True, over_actual=True)]
    s = compute_stats(outcomes)
    assert s.bets_placed == 0
    assert s.flat_roi == 0.0
    assert s.kelly_roi == 0.0
    assert s.win_rate == 0.0
    assert s.max_drawdown == 0.0
    assert s.winner_accuracy == pytest.approx(1.0)
    # 概率类指标即使无下注也应算出
    assert s.brier_score is not None
    assert s.log_loss is not None
    # 无下注 → 下注类指标为空
    assert s.sharpe_ratio is None
    assert s.clv is None
    assert s.confidence_buckets == []
    assert s.odds_buckets == []


@pytest.mark.unit
def test_brier_and_log_loss() -> None:
    # p=(0.5,0.3,0.2), actual=home → Brier=(0.5-1)^2+0.3^2+0.2^2=0.38; LogLoss=-ln(0.5)
    s = compute_stats([_po(0.5, 0.3, 0.2, "home")])
    assert s.brier_score == pytest.approx(0.38)
    assert s.log_loss == pytest.approx(-math.log(0.5))


@pytest.mark.unit
def test_calibration_pools_all_three_classes() -> None:
    # 两场都 p=(0.5,0.3,0.2)，actual 分别 home/draw
    s = compute_stats([_po(0.5, 0.3, 0.2, "home"), _po(0.5, 0.3, 0.2, "draw")])
    by_lo = {round(b.lo, 1): b for b in s.calibration}
    # 0.5 箱：home 类两点 (命中/未命中) → 观测频率 0.5
    assert by_lo[0.5].count == 2
    assert by_lo[0.5].observed_freq == pytest.approx(0.5)
    # 0.3 箱：draw 类两点 (未命中/命中) → 0.5；0.2 箱：away 两点均未命中 → 0.0
    assert by_lo[0.3].observed_freq == pytest.approx(0.5)
    assert by_lo[0.2].observed_freq == pytest.approx(0.0)


@pytest.mark.unit
def test_sharpe_and_profit_buckets() -> None:
    bets = [
        BetPlaced("home", 2.0, 0.1, 0.1, 0.8, won=True),  # ret +1, conf .8, odds 2
        BetPlaced("home", 3.0, 0.2, 0.05, 0.7, won=False),  # ret -1, conf .7, odds 3
    ]
    outcomes = [_po(0.5, 0.3, 0.2, "home", bet=b) for b in bets]
    s = compute_stats(outcomes)

    assert s.sharpe_ratio == pytest.approx(0.0)  # mean(±1)=0
    conf = {b.label: b for b in s.confidence_buckets}
    assert conf["70%-80%"].roi == pytest.approx(-1.0)
    assert conf["80%-90%"].roi == pytest.approx(1.0)
    odds = {b.label: b for b in s.odds_buckets}
    assert odds["2-3"].win_rate == pytest.approx(1.0)
    assert odds["3-5"].win_rate == pytest.approx(0.0)


@pytest.mark.unit
def test_clv_only_when_closing_odds_present() -> None:
    with_close = [
        _po(
            0.5, 0.3, 0.2, "home", bet=BetPlaced("home", 2.0, 0.1, 0.1, 0.8, True, closing_odds=1.8)
        )
    ]
    assert compute_stats(with_close).clv == pytest.approx(2.0 / 1.8 - 1.0)

    without = [_po(0.5, 0.3, 0.2, "home", bet=BetPlaced("home", 2.0, 0.1, 0.1, 0.8, won=True))]
    assert compute_stats(without).clv is None
