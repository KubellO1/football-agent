"""球员可用性外部数据源契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.schemas.player_availability import ProviderAvailabilityBatch


class PlayerAvailabilityProvider(ABC):
    """按比赛读取球员伤停与可用性事实的只读 Provider。"""

    @abstractmethod
    async def get_fixture_availability(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderAvailabilityBatch:
        """返回一次完整采集批次。

        成功但没有记录时返回完整空批次。网络、鉴权、配额或解析失败必须
        抛出异常，不能用空批次掩盖上游故障。
        """
        raise NotImplementedError
