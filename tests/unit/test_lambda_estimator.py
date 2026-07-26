"""强度→λ 估计的单元测试。

用可解析的强度值校验：平均队对平均队、强攻主队、xG 与实际进球切换、
主场优势系数、数据不足/非法参数，以及 λ 下限保护边缘情况。
"""

from __future__ import annotations

import pytest

from app.models.value_objects.statistics import TeamStatistics
from app.services.models.lambda_estimator import (
    LAMBDA_FLOOR,
    LambdaEstimate,
    LambdaEstimator,
    LambdaWarning,
    LambdaWarningType,
    LeagueAverages,
)


def _stats(*, gf: int, ga: int, matches: int = 10) -> TeamStatistics:
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
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=14, ga=14)
    away = _stats(gf=14, ga=14)

    estimator = LambdaEstimator(home_advantage=1.15, use_xg=False)
    result = estimator.estimate(home, away, league)

    assert result.lam_home == pytest.approx(1.4 * 1.15)
    assert result.lam_away == pytest.approx(1.4)
    assert result.warnings == []


@pytest.mark.unit
def test_strong_home_attack_raises_home_lambda() -> None:
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=20, ga=14)
    away = _stats(gf=14, ga=14)

    result = LambdaEstimator(home_advantage=1.0, use_xg=False).estimate(home, away, league)
    assert result.lam_home == pytest.approx(2.0)


@pytest.mark.unit
def test_use_xg_switches_source() -> None:
    league = LeagueAverages(goals_per_game=1.4)
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

    result_xg = LambdaEstimator(home_advantage=1.0, use_xg=True).estimate(home, away, league)
    result_goals = LambdaEstimator(home_advantage=1.0, use_xg=False).estimate(home, away, league)
    assert result_xg.lam_home == pytest.approx(2.1)
    assert result_goals.lam_home == pytest.approx(1.4)


@pytest.mark.unit
def test_zero_matches_returns_floor_with_insufficient_data() -> None:
    """matches_played=0: estimate() returns floor values + INSUFFICIENT_DATA (no crash)."""
    league = LeagueAverages(goals_per_game=1.4)
    empty = TeamStatistics(
        matches_played=0, wins=0, draws=0, losses=0, goals_for=0, goals_against=0,
    )
    result = LambdaEstimator().estimate(empty, _stats(gf=14, ga=14), league)
    assert result.lam_home == pytest.approx(LAMBDA_FLOOR)
    assert result.lam_away == pytest.approx(LAMBDA_FLOOR)
    assert result.has_insufficient_data
    assert len(result.warnings) == 1
    assert result.warnings[0].warning_type == LambdaWarningType.INSUFFICIENT_DATA


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0.0, -1.4])
def test_invalid_league_average_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        LeagueAverages(goals_per_game=bad)


@pytest.mark.unit
def test_invalid_home_advantage_rejected() -> None:
    with pytest.raises(ValueError):
        LambdaEstimator(home_advantage=0.0)


# ---------------------------------------------------------------------------
# Lambda floor edge-case tests (2026-07-11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lambda_zero_triggers_insufficient_data() -> None:
    """raw λ = 0 → INSUFFICIENT_DATA warning + floor applied."""
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=0, ga=14, matches=10)
    away = _stats(gf=14, ga=14, matches=10)

    result = LambdaEstimator(home_advantage=1.0, use_xg=False).estimate(home, away, league)

    assert result.lam_home == pytest.approx(LAMBDA_FLOOR)
    assert result.has_insufficient_data
    assert len(result.warnings) >= 1
    home_warnings = [w for w in result.warnings if w.team == "home"]
    assert len(home_warnings) == 1
    assert home_warnings[0].warning_type == LambdaWarningType.INSUFFICIENT_DATA
    assert home_warnings[0].raw_lambda == pytest.approx(0.0)
    assert "insufficient scoring history" in home_warnings[0].reason.lower()


@pytest.mark.unit
def test_lambda_negative_triggers_insufficient_data() -> None:
    """raw λ ≤ 0 → INSUFFICIENT_DATA + floor."""
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=0, ga=14, matches=10)
    away = _stats(gf=14, ga=14, matches=10)

    result = LambdaEstimator(use_xg=False).estimate(home, away, league)

    home_warnings = [w for w in result.warnings if w.team == "home"]
    assert len(home_warnings) == 1
    assert home_warnings[0].warning_type == LambdaWarningType.INSUFFICIENT_DATA


@pytest.mark.unit
def test_newly_promoted_team_no_recent_matches() -> None:
    """Newly promoted team with 0 goals in recent window → INSUFFICIENT_DATA."""
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=0, ga=14, matches=10)
    away = _stats(gf=14, ga=14, matches=10)

    result = LambdaEstimator(use_xg=False).estimate(home, away, league)

    assert result.has_insufficient_data
    home_w = [w for w in result.warnings if w.team == "home"]
    assert len(home_w) == 1
    assert home_w[0].warning_type == LambdaWarningType.INSUFFICIENT_DATA
    assert "goals_for=0" in home_w[0].reason


@pytest.mark.unit
def test_five_consecutive_scoreless_matches() -> None:
    """Team with 5 scoreless matches → λ at floor with INSUFFICIENT_DATA."""
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=0, ga=14, matches=5)
    away = _stats(gf=14, ga=14, matches=5)

    result = LambdaEstimator(use_xg=False).estimate(home, away, league)

    assert result.has_insufficient_data
    assert result.lam_home == pytest.approx(LAMBDA_FLOOR)


@pytest.mark.unit
def test_missing_recent_goals_entirely() -> None:
    """Both teams have zero goals → both get INSUFFICIENT_DATA."""
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=0, ga=0, matches=10)
    away = _stats(gf=0, ga=0, matches=10)

    result = LambdaEstimator(use_xg=False).estimate(home, away, league)

    assert result.has_insufficient_data
    assert len(result.warnings) == 2
    assert all(w.warning_type == LambdaWarningType.INSUFFICIENT_DATA for w in result.warnings)
    assert result.lam_home == pytest.approx(LAMBDA_FLOOR)
    assert result.lam_away == pytest.approx(LAMBDA_FLOOR)


@pytest.mark.unit
def test_genuine_low_lambda_below_floor() -> None:
    """Very low but positive raw λ → GENUINE_LOW warning + floor applied."""
    league = LeagueAverages(goals_per_game=4.0)
    home = _stats(gf=1, ga=40, matches=10)
    away = _stats(gf=40, ga=40, matches=10)

    result = LambdaEstimator(use_xg=False).estimate(home, away, league)

    home_warnings = [w for w in result.warnings if w.team == "home"]
    if home_warnings:
        w = home_warnings[0]
        assert w.warning_type == LambdaWarningType.GENUINE_LOW
        assert w.raw_lambda > 0
        assert w.raw_lambda < LAMBDA_FLOOR
    assert result.lam_home >= LAMBDA_FLOOR


@pytest.mark.unit
def test_has_insufficient_data_property() -> None:
    """LambdaEstimate.has_insufficient_data returns True only for INSUFFICIENT_DATA."""
    est = LambdaEstimate(
        lam_home=0.05,
        lam_away=0.05,
        warnings=[
            LambdaWarning(
                team="home",
                raw_lambda=0.03,
                warning_type=LambdaWarningType.GENUINE_LOW,
                reason="low",
            )
        ],
    )
    assert not est.has_insufficient_data

    est2 = LambdaEstimate(
        lam_home=0.05,
        lam_away=0.05,
        warnings=[
            LambdaWarning(
                team="home",
                raw_lambda=0.0,
                warning_type=LambdaWarningType.INSUFFICIENT_DATA,
                reason="no data",
            )
        ],
    )
    assert est2.has_insufficient_data


@pytest.mark.unit
def test_floor_parameter_respected() -> None:
    """Custom lambda_floor is applied."""
    league = LeagueAverages(goals_per_game=1.4)
    home = _stats(gf=0, ga=14, matches=10)
    away = _stats(gf=14, ga=14, matches=10)

    result = LambdaEstimator(lambda_floor=0.10, use_xg=False).estimate(home, away, league)
    assert result.lam_home == pytest.approx(0.10)
