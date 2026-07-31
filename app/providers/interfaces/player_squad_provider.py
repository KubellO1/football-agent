"""球队当前阵容外部数据源契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.schemas.player_squad import ProviderSquadBatch


class PlayerSquadProvider(ABC):
    """按球队读取当前球员身份与位置事实的只读 Provider。"""

    @abstractmethod
    async def get_team_squad(
        self,
        *,
        team_external_id: str,
    ) -> ProviderSquadBatch:
        """返回完整球队阵容；上游故障不能伪装成空阵容。"""
        raise NotImplementedError
