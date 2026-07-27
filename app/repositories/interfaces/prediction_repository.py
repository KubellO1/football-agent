"""比赛预测（MatchPrediction）仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.prediction import MatchPrediction
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from uuid import UUID


class PredictionRepository(Repository[MatchPrediction]):
    """MatchPrediction 聚合根仓储。"""

    @abstractmethod
    async def get_by_fixture(self, fixture_id: UUID) -> MatchPrediction | None:
        """获取某场比赛的最新预测，不存在返回 None。"""
        ...
