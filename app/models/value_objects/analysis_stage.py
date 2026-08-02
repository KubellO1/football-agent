"""赛前分析阶段值对象。"""

from enum import StrEnum


class AnalysisStage(StrEnum):
    """明确区分三阶段分析，避免根据时间隐式猜测。"""

    INITIAL = "initial"
    POST_LINEUP = "post_lineup"
    FINAL = "final"

    @property
    def requires_confirmed_lineups(self) -> bool:
        """首发公布后和最终阶段必须具备已验证主客队首发。"""
        return self in {self.POST_LINEUP, self.FINAL}
