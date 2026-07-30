"""Player 主数据仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.player import Player
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID


class PlayerRepository(Repository[Player]):
    """Player 聚合仓储；身份匹配不依赖可能重复的姓名。"""

    @abstractmethod
    async def get_by_external_id(
        self,
        source: str,
        external_id: str,
    ) -> Player | None:
        """按外部来源和外部 ID 精确查询采集幂等身份。"""
        ...

    @abstractmethod
    async def list_by_team(self, team_id: UUID) -> list[Player]:
        """按姓名、ID 稳定排序返回当前归属某球队的球员。"""
        ...

    @abstractmethod
    async def list_by_ids(self, ids: Iterable[UUID]) -> list[Player]:
        """批量读取球员，避免服务层产生 N+1 查询。"""
        ...

    @abstractmethod
    async def update(self, entity: Player) -> Player:
        """按领域 ID 更新球员可变主数据并返回持久化实体。"""
        ...
