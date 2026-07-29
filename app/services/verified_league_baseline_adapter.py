"""将已验证的联赛 xG 基准转换为数学模型的显式指标契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.models.lambda_estimator import BaselineMetric, LeagueBaseline

if TYPE_CHECKING:
    from app.services.verified_league_baseline import VerifiedLeagueBaselineResult


class VerifiedLeagueBaselineAdapter:
    """在质量准入结果与数学模型输入之间建立单向边界。"""

    @staticmethod
    def adapt(result: VerifiedLeagueBaselineResult) -> LeagueBaseline:
        """仅将通过准入的 xG 基准转换为数学模型输入。"""
        if not result.accepted or result.baseline is None:
            raise ValueError("only a verified league baseline result can be adapted")

        return LeagueBaseline(
            rate_per_team_match=result.baseline.xg_per_team_match,
            metric=BaselineMetric.XG,
        )
