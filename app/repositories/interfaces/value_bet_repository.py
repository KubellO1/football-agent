"""价值投注推荐（ValueBet）仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.value_bet import ValueBet
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class ValueBetRepository(Repository[ValueBet]):
    """ValueBet 聚合根仓储。"""

    @abstractmethod
    async def list_by_fixture(self, fixture_id: UUID) -> list[ValueBet]:
        """获取某场比赛的全部推荐。"""
        ...

    @abstractmethod
    async def list_created_between(self, start: datetime, end: datetime) -> list[ValueBet]:
        """获取创建时间落在 [start, end) 区间内的推荐（用于每日报告、赛后复盘）。"""
        ...
