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
    async def list_by_kickoff_window(self, start: datetime, end: datetime) -> list[Fixture]:
        """获取开赛时间落在 [start, end) 区间内的比赛（用于三阶段调度、当日分析）。"""
        ...

    @abstractmethod
    async def list_by_competition(self, competition_id: UUID) -> list[Fixture]:
        """获取某赛事下的比赛。"""
        ...

    @abstractmethod
    async def get_by_external_id(self, source: str, external_id: str) -> Fixture | None:
        """按外部数据源 + 外部 id 精确查询（采集幂等键），不存在返回 None。"""
        ...

    @abstractmethod
    async def update(self, entity: Fixture) -> Fixture:
        """就地更新一场已存在的比赛（按 id），返回更新后的实体。

        采集重跑时用于刷新可变字段（开赛时间、状态、比分），保持行不重复。
        """
        ...
