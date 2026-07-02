"""参考数据仓储的 SQLAlchemy 实现（球队/赛事/博彩公司/赛季）。"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition, Season
from app.models.entities.team import Team
from app.repositories.interfaces.reference import (
    BookmakerRepository,
    CompetitionRepository,
    SeasonRepository,
    TeamRepository,
)
from app.repositories.sqlalchemy.mappers import (
    BookmakerMapper,
    CompetitionMapper,
    SeasonMapper,
    TeamMapper,
)
from app.repositories.sqlalchemy.models import (
    BookmakerORM,
    CompetitionORM,
    SeasonORM,
    TeamORM,
)


class SqlAlchemyTeamRepository(TeamRepository):
    """基于 AsyncSession 的球队仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Team | None:
        row = await self._session.get(TeamORM, entity_id)
        return TeamMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Team) -> Team:
        row = TeamMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return TeamMapper.to_domain(row)

    async def get_by_name(self, name: str) -> Team | None:
        stmt = select(TeamORM).where(TeamORM.name == name).limit(1)
        row = (await self._session.execute(stmt)).scalars().first()
        return TeamMapper.to_domain(row) if row is not None else None

    async def get_by_external_id(self, source: str, external_id: str) -> Team | None:
        stmt = (
            select(TeamORM)
            .where(TeamORM.external_source == source, TeamORM.external_id == external_id)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return TeamMapper.to_domain(row) if row is not None else None

    async def list_all(self) -> list[Team]:
        stmt = select(TeamORM).order_by(TeamORM.name)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [TeamMapper.to_domain(r) for r in rows]

    async def list_by_ids(self, ids: Iterable[UUID]) -> list[Team]:
        id_list = list(ids)
        if not id_list:
            return []
        stmt = select(TeamORM).where(TeamORM.id.in_(id_list))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [TeamMapper.to_domain(r) for r in rows]


class SqlAlchemyCompetitionRepository(CompetitionRepository):
    """基于 AsyncSession 的赛事仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Competition | None:
        row = await self._session.get(CompetitionORM, entity_id)
        return CompetitionMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Competition) -> Competition:
        row = CompetitionMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return CompetitionMapper.to_domain(row)

    async def get_by_name(self, name: str) -> Competition | None:
        stmt = select(CompetitionORM).where(CompetitionORM.name == name).limit(1)
        row = (await self._session.execute(stmt)).scalars().first()
        return CompetitionMapper.to_domain(row) if row is not None else None

    async def get_by_external_id(self, source: str, external_id: str) -> Competition | None:
        stmt = (
            select(CompetitionORM)
            .where(
                CompetitionORM.external_source == source,
                CompetitionORM.external_id == external_id,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return CompetitionMapper.to_domain(row) if row is not None else None

    async def list_all(self) -> list[Competition]:
        stmt = select(CompetitionORM).order_by(CompetitionORM.name)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [CompetitionMapper.to_domain(r) for r in rows]

    async def list_by_ids(self, ids: Iterable[UUID]) -> list[Competition]:
        id_list = list(ids)
        if not id_list:
            return []
        stmt = select(CompetitionORM).where(CompetitionORM.id.in_(id_list))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [CompetitionMapper.to_domain(r) for r in rows]


class SqlAlchemyBookmakerRepository(BookmakerRepository):
    """基于 AsyncSession 的博彩公司仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Bookmaker | None:
        row = await self._session.get(BookmakerORM, entity_id)
        return BookmakerMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Bookmaker) -> Bookmaker:
        row = BookmakerMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return BookmakerMapper.to_domain(row)

    async def get_by_name(self, name: str) -> Bookmaker | None:
        stmt = select(BookmakerORM).where(BookmakerORM.name == name).limit(1)
        row = (await self._session.execute(stmt)).scalars().first()
        return BookmakerMapper.to_domain(row) if row is not None else None

    async def list_all(self) -> list[Bookmaker]:
        stmt = select(BookmakerORM).order_by(BookmakerORM.name)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [BookmakerMapper.to_domain(r) for r in rows]


class SqlAlchemySeasonRepository(SeasonRepository):
    """基于 AsyncSession 的赛季仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Season | None:
        row = await self._session.get(SeasonORM, entity_id)
        return SeasonMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Season) -> Season:
        row = SeasonMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return SeasonMapper.to_domain(row)

    async def list_by_competition(self, competition_id: UUID) -> list[Season]:
        stmt = (
            select(SeasonORM)
            .where(SeasonORM.competition_id == competition_id)
            .order_by(SeasonORM.label)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [SeasonMapper.to_domain(r) for r in rows]
