"""结算与追踪仓储接口。"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models.entities.settlement import BankrollEntry, PerformanceSnapshot, Settlement
from app.repositories.interfaces.base import Repository


class SettlementRepository(Repository[Settlement]):
    @abstractmethod
    async def get_by_value_bet(self, value_bet_id: UUID) -> Settlement | None:
        """按 value_bet_id 查询结算记录（幂等检查）。"""
        ...

    @abstractmethod
    async def list_by_fixture(self, fixture_id: UUID) -> list[Settlement]:
        """查询某场比赛的所有结算记录。"""
        ...

    @abstractmethod
    async def list_unsettled_value_bet_ids(self) -> list[UUID]:
        """返回所有未结算的 value_bet ID 列表。"""
        ...

    @abstractmethod
    async def list_between(self, start: datetime, end: datetime) -> list[Settlement]:
        """查询时间段内的结算记录。"""
        ...

    @abstractmethod
    async def list_all(self) -> list[Settlement]:
        """获取所有结算记录。"""
        ...


class BankrollRepository(Repository[BankrollEntry]):
    @abstractmethod
    async def get_latest_balance(self) -> Decimal:
        """返回最新余额，无记录时返回 Decimal('0')。"""
        ...


class PerformanceSnapshotRepository(Repository[PerformanceSnapshot]):
    @abstractmethod
    async def get_latest(self) -> PerformanceSnapshot | None:
        """获取最新的性能快照。"""
        ...
