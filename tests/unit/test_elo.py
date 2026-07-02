"""Elo 模型单元测试。

覆盖：无主场优势的对称期望、主场优势抬升期望、强队期望、评分更新的零和守恒、
平局不变、赛果映射、非法 K 值。
"""

from __future__ import annotations

import pytest

from app.models.value_objects.score import MatchResult
from app.services.models.elo import EloModel


@pytest.mark.unit
def test_equal_ratings_no_advantage_is_even() -> None:
    model = EloModel(home_advantage=0.0)
    assert model.expected_score(1500.0, 1500.0) == pytest.approx(0.5)


@pytest.mark.unit
def test_home_advantage_raises_expected_score() -> None:
    model = EloModel(home_advantage=65.0)
    assert model.expected_score(1500.0, 1500.0) > 0.5


@pytest.mark.unit
def test_stronger_team_expected_score() -> None:
    # 领先 400 分（无主场优势）→ 期望约 0.909
    model = EloModel(home_advantage=0.0)
    assert model.expected_score(1900.0, 1500.0) == pytest.approx(1 / 1.1, abs=1e-6)


@pytest.mark.unit
def test_update_is_zero_sum_and_winner_gains() -> None:
    model = EloModel(k_factor=20.0, home_advantage=0.0)
    new_home, new_away = model.update(1500.0, 1500.0, MatchResult.HOME)
    # 期望 0.5，实际 1 → delta = 20*0.5 = 10
    assert new_home == pytest.approx(1510.0)
    assert new_away == pytest.approx(1490.0)
    assert (new_home + new_away) == pytest.approx(3000.0)  # 零和守恒


@pytest.mark.unit
def test_draw_between_equal_teams_no_change() -> None:
    model = EloModel(k_factor=20.0, home_advantage=0.0)
    new_home, new_away = model.update(1500.0, 1500.0, MatchResult.DRAW)
    assert new_home == pytest.approx(1500.0)
    assert new_away == pytest.approx(1500.0)


@pytest.mark.unit
def test_result_to_score() -> None:
    assert EloModel.result_to_score(MatchResult.HOME) == 1.0
    assert EloModel.result_to_score(MatchResult.DRAW) == 0.5
    assert EloModel.result_to_score(MatchResult.AWAY) == 0.0


@pytest.mark.unit
def test_invalid_k_factor_rejected() -> None:
    with pytest.raises(ValueError):
        EloModel(k_factor=0.0)
