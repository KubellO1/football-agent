"""球员可用性观察仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from app.models.entities.player_availability import PlayerAvailabilityObservation
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class PlayerAvailabilityObservationRepository(
    Repository[PlayerAvailabilityObservation],
):
    """追加式球员可用性观察仓储，不暴露更新或删除能力。"""

    @abstractmethod
    async def add_if_absent(self, entity: PlayerAvailabilityObservation) -> bool:
        """按自然键幂等插入，返回是否实际新增。"""
        ...

    @abstractmethod
    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        team_id: UUID | None = None,
        player_id: UUID | None = None,
        source: str | None = None,
        as_of: datetime | None = None,
    ) -> list[PlayerAvailabilityObservation]:
        """按采集时间升序返回观察，并限制决策时点可见的数据。"""
        ...

    @abstractmethod
    async def get_latest_by_source(
        self,
        fixture_id: UUID,
        player_id: UUID,
        source: str,
        *,
        as_of: datetime | None = None,
    ) -> PlayerAvailabilityObservation | None:
        """返回指定来源在给定决策时点可见的最新球员观察。"""
        ...
