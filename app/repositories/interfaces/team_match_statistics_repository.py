"""球队单场统计快照仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class TeamMatchStatisticsRepository(Repository[TeamMatchStatistics]):
    """追加式球队单场统计仓储；不暴露更新或删除能力。"""

    @abstractmethod
    async def add_if_absent(self, entity: TeamMatchStatistics) -> bool:
        """按自然键幂等插入，返回是否实际新增。"""
        ...

    @abstractmethod
    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        team_id: UUID | None = None,
        source: str | None = None,
        as_of: datetime | None = None,
    ) -> list[TeamMatchStatistics]:
        """按采集时间升序返回比赛快照，可限定球队、来源和可见时间点。"""
        ...

    @abstractmethod
    async def get_latest_by_source(
        self,
        fixture_id: UUID,
        team_id: UUID,
        source: str,
        *,
        as_of: datetime | None = None,
    ) -> TeamMatchStatistics | None:
        """返回指定来源在给定时间点可见的最新快照。"""
        ...
