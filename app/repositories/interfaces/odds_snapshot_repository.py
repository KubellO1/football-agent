"""赔率快照（OddsSnapshot）仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.odds_snapshot import OddsSnapshot
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class OddsSnapshotRepository(Repository[OddsSnapshot]):
    """赔率快照仓储。时间序列写入以幂等键去重。"""

    @abstractmethod
    async def add_if_absent(self, entity: OddsSnapshot) -> bool:
        """按幂等键插入，若已存在则跳过。返回是否实际插入（True=新增）。"""
        ...

    @abstractmethod
    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        as_of: datetime | None = None,
    ) -> list[OddsSnapshot]:
        """按时间升序获取比赛快照；as_of 存在时只返回该时点及以前的数据。"""
        ...
