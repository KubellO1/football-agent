"""比赛官方阵容外部数据源契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.schemas.fixture_lineup import ProviderFixtureLineupBatch


class FixtureLineupProvider(ABC):
    """按比赛读取官方首发和替补事实的只读 Provider。"""

    @abstractmethod
    async def get_fixture_lineups(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderFixtureLineupBatch:
        """返回完整批次；上游故障不能伪装成尚未公布阵容。"""
        raise NotImplementedError
