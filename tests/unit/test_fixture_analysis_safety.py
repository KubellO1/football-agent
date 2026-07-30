"""单场分析输入构建器的安全边界测试。"""

from __future__ import annotations

from inspect import signature

from app.services.fixture_analysis import MatchAnalysisInputBuilder


def test_unverified_injury_data_cannot_enter_quantitative_builder() -> None:
    """外部伤停源和伤停人数不得成为量化输入或完整度参数。"""
    constructor_parameters = signature(MatchAnalysisInputBuilder).parameters
    completeness_parameters = signature(MatchAnalysisInputBuilder._completeness).parameters

    assert "injury_provider" not in constructor_parameters
    assert "injury_count" not in completeness_parameters
