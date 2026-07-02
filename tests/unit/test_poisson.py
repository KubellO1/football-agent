"""Poisson 模型单元测试。

用解析已知值校验：对称 λ 的对称性、总进球服从 Poisson(λh+λa) 的大小球概率、
BTTS 概率、概率归一化、以及正确比分 Top-N 的形状与排序。
"""

from __future__ import annotations

import math

import pytest

from app.models.value_objects.score import MatchResult
from app.services.models.poisson import PoissonModel


@pytest.mark.unit
def test_symmetric_lambda_gives_symmetric_1x2() -> None:
    probs = PoissonModel().match_result_probabilities(1.2, 1.2)
    # 对称 λ 下，主胜与客胜概率相等
    assert probs[MatchResult.HOME].value == pytest.approx(probs[MatchResult.AWAY].value)


@pytest.mark.unit
def test_1x2_probabilities_sum_to_one() -> None:
    probs = PoissonModel().match_result_probabilities(1.7, 1.1)
    total = sum(p.value for p in probs.values())
    assert total == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
def test_over_under_matches_total_goals_poisson() -> None:
    # 总进球 ~ Poisson(λh+λa=2)。Over 2.5 = P(X>=3) = 1 - e^-2 (1 + 2 + 2)
    expected_over = 1.0 - math.exp(-2.0) * (1 + 2 + 2)
    over, under = PoissonModel().over_under(1.0, 1.0, 2.5)
    assert over.value == pytest.approx(expected_over, abs=1e-3)
    assert (over.value + under.value) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
def test_btts_matches_independent_marginals() -> None:
    # BTTS(yes) = P(home>=1) * P(away>=1) = (1 - e^-1)^2
    expected_yes = (1.0 - math.exp(-1.0)) ** 2
    yes, no = PoissonModel().both_teams_to_score(1.0, 1.0)
    assert yes.value == pytest.approx(expected_yes, abs=1e-3)
    assert (yes.value + no.value) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
def test_top_correct_scores_shape_and_order() -> None:
    result = PoissonModel().top_correct_scores(1.4, 1.1, top=3)
    assert len(result) == 3
    probs = [p.value for _, p in result]
    assert probs == sorted(probs, reverse=True)  # 概率降序
    assert probs[0] > 0.0


@pytest.mark.unit
def test_invalid_lambda_rejected() -> None:
    with pytest.raises(ValueError):
        PoissonModel().match_result_probabilities(0.0, 1.0)
