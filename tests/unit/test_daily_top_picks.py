"""每日 Top Picks 合格判定的单元测试（纯阈值逻辑，无 DB / 无 LLM）。"""

from __future__ import annotations

import pytest

from app.services.daily_top_picks import DailyTopPicksService
from app.services.fixture_analysis import SelectionAnalysis


def _service() -> DailyTopPicksService:
    # 仓储/服务在 _qualifies 中不被使用，可传 None（仅测纯阈值判定）。
    return DailyTopPicksService(
        fixtures=None,  # type: ignore[arg-type]
        analysis=None,  # type: ignore[arg-type]
        review=None,  # type: ignore[arg-type]
        decision_logs=None,  # type: ignore[arg-type]
        teams=None,  # type: ignore[arg-type]
        competitions=None,  # type: ignore[arg-type]
        session=None,
        min_ev=0.05,
        min_kelly=0.02,
        min_confidence=0.70,
        max_picks=5,
    )


def _sel(*, recommended: bool, ev: float, kelly: float, confidence: float) -> SelectionAnalysis:
    return SelectionAnalysis(
        code="home",
        selection_label="1x2:home",
        decimal_odds=2.0,
        model_probability=0.6,
        implied_probability=0.5,
        edge=ev,
        expected_value=ev,
        kelly_fraction=kelly,
        kelly_stake=20.0,
        currency="EUR",
        recommended=recommended,
        confidence=confidence,
        reasons=[],
        explanation="",
    )


@pytest.mark.unit
def test_qualifies_when_all_thresholds_met() -> None:
    assert _service()._qualifies(_sel(recommended=True, ev=0.05, kelly=0.02, confidence=0.70))


@pytest.mark.unit
@pytest.mark.parametrize(
    "sel",
    [
        _sel(recommended=False, ev=0.10, kelly=0.05, confidence=0.90),  # gate 未通过
        _sel(recommended=True, ev=0.04, kelly=0.05, confidence=0.90),  # EV 不足
        _sel(recommended=True, ev=0.10, kelly=0.01, confidence=0.90),  # Kelly 不足
        _sel(recommended=True, ev=0.10, kelly=0.05, confidence=0.69),  # 信心不足
    ],
)
def test_does_not_qualify_when_any_threshold_fails(sel: SelectionAnalysis) -> None:
    assert not _service()._qualifies(sel)
