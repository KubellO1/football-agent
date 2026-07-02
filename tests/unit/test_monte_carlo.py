"""蒙特卡洛模型单元测试。

用固定种子保证可复现，并与 Poisson 解析解交叉校验（允许模拟噪声容差）。
"""

from __future__ import annotations

import pytest

from app.models.value_objects.score import MatchResult
from app.services.models.monte_carlo import MonteCarloModel
from app.services.models.poisson import PoissonModel


@pytest.mark.unit
def test_probabilities_sum_to_one() -> None:
    probs = MonteCarloModel(iterations=5000, seed=1).match_result_probabilities(1.5, 1.0)
    total = sum(p.value for p in probs.values())
    assert total == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
def test_symmetric_lambda_is_roughly_symmetric() -> None:
    probs = MonteCarloModel(iterations=20000, seed=7).match_result_probabilities(1.2, 1.2)
    # 对称 λ 下主胜/客胜应接近（模拟噪声容差）
    assert probs[MatchResult.HOME].value == pytest.approx(probs[MatchResult.AWAY].value, abs=0.03)


@pytest.mark.unit
def test_cross_check_against_poisson() -> None:
    mc = MonteCarloModel(iterations=40000, seed=42).match_result_probabilities(1.6, 1.0)
    analytic = PoissonModel().match_result_probabilities(1.6, 1.0)
    for result in MatchResult:
        assert mc[result].value == pytest.approx(analytic[result].value, abs=0.03)


@pytest.mark.unit
def test_seed_is_reproducible() -> None:
    a = MonteCarloModel(iterations=3000, seed=99).match_result_probabilities(1.3, 1.1)
    b = MonteCarloModel(iterations=3000, seed=99).match_result_probabilities(1.3, 1.1)
    assert a[MatchResult.HOME].value == b[MatchResult.HOME].value


@pytest.mark.unit
def test_invalid_iterations_rejected() -> None:
    with pytest.raises(ValueError):
        MonteCarloModel(iterations=0)


@pytest.mark.unit
def test_invalid_lambda_rejected() -> None:
    with pytest.raises(ValueError):
        MonteCarloModel(seed=1).simulate(0.0, 1.0)
