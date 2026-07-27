"""决策日志（DecisionLog）仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.decision_log import DecisionLog
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class DecisionLogRepository(Repository[DecisionLog]):
    """DecisionLog 仓储。"""

    @abstractmethod
    async def list_by_fixture(self, fixture_id: UUID) -> list[DecisionLog]:
        """获取某场比赛的全部决策日志。"""
        ...

    @abstractmethod
    async def list_created_between(self, start: datetime, end: datetime) -> list[DecisionLog]:
        """获取创建时间落在 [start, end) 区间内的决策日志（供复盘）。"""
        ...
