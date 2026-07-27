"""参考数据仓储接口。

参考数据（赛事、赛季、球队、博彩公司）以读取为主。在通用 get/add 之外，
提供按名查询与全量列出；赛季按赛事查询（用 label 而非 name）。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, TypeVar

from app.models.entities.base import Entity
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition, Season
from app.models.entities.team import Team
from app.repositories.interfaces.base import Repository

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

T = TypeVar("T", bound=Entity)


class ReferenceRepository(Repository[T]):
    """参考数据的通用契约：按名查询 + 全量列出。"""

    @abstractmethod
    async def get_by_name(self, name: str) -> T | None:
        """按名称精确查询，不存在返回 None。"""
        ...

    @abstractmethod
    async def list_all(self) -> list[T]:
        """列出全部（按名称排序）。"""
        ...


class TeamRepository(ReferenceRepository[Team]):
    """球队仓储。"""

    @abstractmethod
    async def get_by_external_id(self, source: str, external_id: str) -> Team | None:
        """按外部数据源 + 外部 id 精确查询（采集幂等键），不存在返回 None。"""
        ...

    @abstractmethod
    async def list_by_ids(self, ids: Iterable[UUID]) -> list[Team]:
        """按一组 id 批量获取（用于读端点批量解析，避免 N+1）。"""
        ...

    @abstractmethod
    async def update(self, entity: Team) -> Team:
        """就地更新一支已存在的球队（按 id），返回更新后的实体。"""
        ...


class CompetitionRepository(ReferenceRepository[Competition]):
    """赛事仓储。"""

    @abstractmethod
    async def get_by_external_id(self, source: str, external_id: str) -> Competition | None:
        """按外部数据源 + 外部 id 精确查询（采集幂等键），不存在返回 None。"""
        ...

    @abstractmethod
    async def list_by_ids(self, ids: Iterable[UUID]) -> list[Competition]:
        """按一组 id 批量获取（用于读端点批量解析，避免 N+1）。"""
        ...


class BookmakerRepository(ReferenceRepository[Bookmaker]):
    """博彩公司仓储。"""

    @abstractmethod
    async def get_by_external_id(self, source: str, external_id: str) -> Bookmaker | None:
        """按外部数据源 + 外部 id 精确查询（采集幂等键），不存在返回 None。"""
        ...


class SeasonRepository(Repository[Season]):
    """赛季仓储。赛季用 label 标识，故按赛事查询而非按名。"""

    @abstractmethod
    async def list_by_competition(self, competition_id: UUID) -> list[Season]:
        """获取某赛事下的全部赛季。"""
        ...
