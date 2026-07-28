"""FixtureRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from app.models.entities.enums import MatchStatus
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.sqlalchemy.mappers import FixtureMapper
from app.repositories.sqlalchemy.models import FixtureORM

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture

_FINISHED = MatchStatus.FINISHED.value


class SqlAlchemyFixtureRepository(FixtureRepository):
    """基于 AsyncSession 的比赛仓储实现。

    事务边界由上层的 Database.session() 上下文统一管理，这里只做读写，
    add 后 flush 以便拿到持久化状态，不在此提交。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Fixture | None:
        row = await self._session.get(FixtureORM, entity_id)
        return FixtureMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Fixture) -> Fixture:
        row = FixtureMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return FixtureMapper.to_domain(row)

    async def list_by_kickoff_window(self, start: datetime, end: datetime) -> list[Fixture]:
        stmt = (
            select(FixtureORM)
            .where(FixtureORM.kickoff >= start, FixtureORM.kickoff < end)
            .order_by(FixtureORM.kickoff)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]

    async def list_by_competition(self, competition_id: UUID) -> list[Fixture]:
        stmt = (
            select(FixtureORM)
            .where(FixtureORM.competition_id == competition_id)
            .order_by(FixtureORM.kickoff)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]

    async def get_by_external_id(self, source: str, external_id: str) -> Fixture | None:
        stmt = (
            select(FixtureORM)
            .where(
                FixtureORM.external_source == source,
                FixtureORM.external_id == external_id,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return FixtureMapper.to_domain(row) if row is not None else None

    async def list_finished_by_team(
        self,
        team_id: UUID,
        *,
        limit: int | None = None,
        exclude_fixture_id: UUID | None = None,
        before: datetime | None = None,
    ) -> list[Fixture]:
        stmt = (
            select(FixtureORM)
            .where(
                FixtureORM.status == _FINISHED,
                or_(FixtureORM.home_team_id == team_id, FixtureORM.away_team_id == team_id),
            )
            .order_by(FixtureORM.kickoff.desc())
        )
        if exclude_fixture_id is not None:
            stmt = stmt.where(FixtureORM.id != exclude_fixture_id)
        if before is not None:
            stmt = stmt.where(FixtureORM.kickoff < before)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]

    async def list_finished_by_competition(
        self,
        competition_id: UUID,
        *,
        season_id: UUID | None = None,
        limit: int | None = None,
        exclude_fixture_id: UUID | None = None,
        before: datetime | None = None,
    ) -> list[Fixture]:
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
        ):
            raise ValueError("limit must be a positive integer or None")

        stmt = (
            select(FixtureORM)
            .where(
                FixtureORM.competition_id == competition_id,
                FixtureORM.status == _FINISHED,
            )
            .order_by(FixtureORM.kickoff.desc(), FixtureORM.id.desc())
        )
        if season_id is not None:
            stmt = stmt.where(FixtureORM.season_id == season_id)
        if exclude_fixture_id is not None:
            stmt = stmt.where(FixtureORM.id != exclude_fixture_id)
        if before is not None:
            stmt = stmt.where(FixtureORM.kickoff < before)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]

    async def list_finished(
        self,
        *,
        competition_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Fixture]:
        stmt = select(FixtureORM).where(FixtureORM.status == _FINISHED)
        if competition_id is not None:
            stmt = stmt.where(FixtureORM.competition_id == competition_id)
        if start is not None:
            stmt = stmt.where(FixtureORM.kickoff >= start)
        if end is not None:
            stmt = stmt.where(FixtureORM.kickoff < end)
        stmt = stmt.order_by(FixtureORM.kickoff)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]

    async def update(self, entity: Fixture) -> Fixture:
        row = await self._session.get(FixtureORM, entity.id)
        if row is None:
            raise KeyError(f"fixture {entity.id} not found for update")

        identity_fields = (
            ("competition_id", row.competition_id, entity.competition_id),
            ("season_id", row.season_id, entity.season_id),
            ("home_team_id", row.home_team_id, entity.home_team_id),
            ("away_team_id", row.away_team_id, entity.away_team_id),
            ("external_source", row.external_source, entity.external_source),
            ("external_id", row.external_id, entity.external_id),
        )
        changed_fields = [
            name for name, persisted, incoming in identity_fields if persisted != incoming
        ]
        if changed_fields:
            names = ", ".join(changed_fields)
            raise ValueError(f"fixture identity fields cannot be changed: {names}")

        # 采集重跑只允许刷新比赛状态；聚合身份与关联关系必须保持稳定。
        row.kickoff = entity.kickoff
        row.status = entity.status.value
        row.score_home = entity.score.home if entity.score is not None else None
        row.score_away = entity.score.away if entity.score is not None else None
        await self._session.flush()
        return FixtureMapper.to_domain(row)
