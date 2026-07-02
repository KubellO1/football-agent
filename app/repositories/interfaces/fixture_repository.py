"""比赛（Fixture）仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from app.models.entities.fixture import Fixture
from app.repositories.interfaces.base import Repository


class FixtureRepository(Repository[Fixture]):
    """Fixture 聚合根仓储。"""

    @abstractmethod
    async def list_by_kickoff_window(
        self, start: datetime, end: datetime
    ) -> list[Fixture]:
        """获取开赛时间落在 [start, end) 区间内的比赛（用于三阶段调度、当日分析）。"""
        ...

    @abstractmethod
    async def list_by_competition(self, competition_id: UUID) -> list[Fixture]:
        """获取某赛事下的比赛。"""
        ...
