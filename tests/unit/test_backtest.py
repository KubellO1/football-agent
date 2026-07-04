"""回测统计 compute_stats 的单元测试（纯函数，合成数据）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services.backtest import BetPlaced, MatchOutcome, compute_stats


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
