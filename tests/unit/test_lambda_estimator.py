"""强度→λ 估计的单元测试。

用可解析的强度值校验：平均队对平均队、强攻主队、xG 与实际进球切换、
主场优势系数、以及数据不足/非法参数。
"""

from __future__ import annotations

import pytest

from app.models.value_objects.statistics import TeamStatistics
from app.services.models.lambda_estimator import LambdaEstimator, LeagueAverages


def _stats(*, gf: int, ga: int, matches: int = 10) -> TeamStatistics:
    # 结果分布只需合法即可（不影响 λ），进/失球才是关键
    return TeamStatistics(
        matches_played=matches,
        wins=matches,
        draws=0,
        losses=0,
        goals_for=gf,
        goals_against=ga,
        xg_for=float(gf),
        xg_against=float(ga),
    )


@pytest.mark.unit
def test_average_teams_yield_baseline_lambda() -> None:
    # 两队都恰好等于联赛平均（场均 1.4），强度均为 1
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=14, ga=14)
    away = _stats(gf=14, ga=14)

    estimator = LambdaEstimator(home_advantage=1.15, use_xg=False)
    lam_home, lam_away = estimator.estimate(home, away, league)

    assert lam_home == pytest.approx(1.4 * 1.15)  # 主场优势加成
    assert lam_away == pytest.approx(1.4)


@pytest.mark.unit
def test_strong_home_attack_raises_home_lambda() -> None:
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=20, ga=14)  # 进攻强度 = 2.0/1.4
    away = _stats(gf=14, ga=14)  # 防守强度 = 1.0

    lam_home, _ = LambdaEstimator(home_advantage=1.0, use_xg=False).estimate(home, away, league)
    assert lam_home == pytest.approx(2.0)  # (2.0/1.4)*1.0*1.4*1.0


@pytest.mark.unit
def test_use_xg_switches_source() -> None:
    league = LeagueAverages(goals_per_game=1.4)
    # 实际进球场均 1.4，但 xG 场均 2.1
    home = TeamStatistics(
        matches_played=10,
        wins=10,
        draws=0,
        losses=0,
        goals_for=14,
        goals_against=14,
        xg_for=21.0,
        xg_against=14.0,
    )
    away = _stats(gf=14, ga=14)

    lam_home_xg, _ = LambdaEstimator(home_advantage=1.0, use_xg=True).estimate(home, away, league)
    lam_home_goals, _ = LambdaEstimator(home_advantage=1.0, use_xg=False).estimate(
        home, away, league
    )
    assert lam_home_xg == pytest.approx(2.1)
    assert lam_home_goals == pytest.approx(1.4)


@pytest.mark.unit
def test_zero_matches_rejected() -> None:
    league = LeagueAverages(goals_per_game=1.4)
    empty = TeamStatistics(
        matches_played=0,
        wins=0,
        draws=0,
        losses=0,
        goals_for=0,
        goals_against=0,
    )
    with pytest.raises(ValueError):
        LambdaEstimator().estimate(empty, _stats(gf=14, ga=14), league)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0.0, -1.4])
def test_invalid_league_average_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        LeagueAverages(goals_per_game=bad)


@pytest.mark.unit
def test_invalid_home_advantage_rejected() -> None:
    with pytest.raises(ValueError):
        LambdaEstimator(home_advantage=0.0)
