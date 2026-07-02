"""仓储通用契约（抽象接口）。

聚合根仓储的最小公共接口，异步。service 层只依赖这些抽象；具体持久化实现
放在 app/repositories/sqlalchemy 下。此模块不包含任何数据库/ORM 代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from app.models.entities.base import Entity

T = TypeVar("T", bound=Entity)


class Repository(ABC, Generic[T]):
    """聚合根仓储的通用契约。"""

    @abstractmethod
    async def get(self, entity_id: UUID) -> T | None:
        """按 id 获取聚合，不存在返回 None。"""
        ...

    @abstractmethod
    async def add(self, entity: T) -> T:
        """新增（或持久化）一个聚合，返回持久化后的实体。"""
        ...
