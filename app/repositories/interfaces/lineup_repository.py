"""比赛阵容快照仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.lineup import Lineup
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.value_objects.lineup import LineupStatus


class LineupRepository(Repository[Lineup]):
    """追加式比赛阵容仓储，不暴露更新或删除能力。"""

    @abstractmethod
    async def add_if_absent(self, entity: Lineup) -> bool:
        """按自然键幂等插入，返回是否实际新增。"""
        ...

    @abstractmethod
    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        team_id: UUID | None = None,
        source: str | None = None,
        status: LineupStatus | None = None,
        as_of: datetime | None = None,
    ) -> list[Lineup]:
        """按采集时间升序返回阵容，并限制决策时点可见范围。"""
        ...

    @abstractmethod
    async def get_latest_by_source(
        self,
        fixture_id: UUID,
        team_id: UUID,
        source: str,
        *,
        status: LineupStatus | None = None,
        as_of: datetime | None = None,
    ) -> Lineup | None:
        """返回指定来源在决策时点可见的最新球队阵容。"""
        ...
