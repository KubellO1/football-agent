"""赔率快照（OddsSnapshot）仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from uuid import UUID

from app.models.entities.odds_snapshot import OddsSnapshot
from app.repositories.interfaces.base import Repository


class OddsSnapshotRepository(Repository[OddsSnapshot]):
    """赔率快照仓储。时间序列写入以幂等键去重。"""

    @abstractmethod
    async def add_if_absent(self, entity: OddsSnapshot) -> bool:
        """按幂等键插入，若已存在则跳过。返回是否实际插入（True=新增）。"""
        ...

    @abstractmethod
    async def list_by_fixture(self, fixture_id: UUID) -> list[OddsSnapshot]:
        """获取某场比赛的全部赔率快照。"""
        ...
