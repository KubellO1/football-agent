"""温度缩放概率校准的单元测试（纯函数/合成数据）。"""

from __future__ import annotations

import pytest

from app.models.value_objects.score import MatchResult
from app.services.models.calibration import (
    TemperatureCalibrator,
    apply_temperature,
    fit_temperature,
    log_loss,
)


@pytest.mark.unit
def test_apply_temperature_identity_normalizes() -> None:
    out = apply_temperature([0.5, 0.3, 0.2], 1.0)
    assert out == pytest.approx([0.5, 0.3, 0.2])
    assert sum(out) == pytest.approx(1.0)


@pytest.mark.unit
def test_higher_temperature_softens_lower_sharpens() -> None:
    raw = [0.9, 0.05, 0.05]
    softer = apply_temperature(raw, 2.0)
    sharper = apply_temperature(raw, 0.5)
    assert softer[0] < 0.9  # 过度自信被压低
    assert sharper[0] > 0.9  # 更极端
    for out in (softer, sharper):
        assert sum(out) == pytest.approx(1.0)
        assert out[0] == max(out)  # argmax 不变


@pytest.mark.unit
def test_invalid_temperature_raises() -> None:
    with pytest.raises(ValueError):
        apply_temperature([0.5, 0.3, 0.2], 0.0)
    with pytest.raises(ValueError):
        TemperatureCalibrator(-1.0)


@pytest.mark.unit
def test_fit_temperature_softens_overconfident_model() -> None:
    # 模型恒称主胜 80%，实际只有 60% → 过度自信，应拟合出 T>1 且降低对数损失
    samples = [([0.8, 0.1, 0.1], 0)] * 60 + [([0.8, 0.1, 0.1], 1)] * 20
    samples += [([0.8, 0.1, 0.1], 2)] * 20
    t = fit_temperature(samples)
    assert t > 1.0
    assert log_loss(samples, t) < log_loss(samples, 1.0)


@pytest.mark.unit
def test_fit_temperature_near_one_when_already_calibrated() -> None:
    # 预测概率与实际频率一致 → 最优 T≈1
    samples = [([0.6, 0.2, 0.2], 0)] * 60 + [([0.6, 0.2, 0.2], 1)] * 20
    samples += [([0.6, 0.2, 0.2], 2)] * 20
    assert 0.85 < fit_temperature(samples) < 1.2


@pytest.mark.unit
def test_calibrator_identity_returns_input() -> None:
    from app.models.value_objects.probability import Probability

    probs = {
        MatchResult.HOME: Probability(0.6),
        MatchResult.DRAW: Probability(0.25),
        MatchResult.AWAY: Probability(0.15),
    }
    assert TemperatureCalibrator(1.0).calibrate(probs) is probs  # 恒等零开销


@pytest.mark.unit
def test_calibrator_softens_and_preserves_argmax() -> None:
    from app.models.value_objects.probability import Probability

    probs = {
        MatchResult.HOME: Probability(0.9),
        MatchResult.DRAW: Probability(0.05),
        MatchResult.AWAY: Probability(0.05),
    }
    out = TemperatureCalibrator(2.0).calibrate(probs)
    assert out[MatchResult.HOME].value < 0.9
    assert out[MatchResult.HOME].value == max(v.value for v in out.values())
    assert sum(v.value for v in out.values()) == pytest.approx(1.0)
