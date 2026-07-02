"""请求作用域的 FastAPI 依赖。

从 DI 容器取基础设施（DB session、Redis），并把仓储作为请求作用域依赖暴露。
仓储依赖返回抽象接口类型、内部构造 SQLAlchemy 实现——依赖倒置在此 wiring
边界完成：endpoint / service 只依赖接口，不见具体实现。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import container
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.prediction_repository import PredictionRepository
from app.repositories.interfaces.reference import (
    BookmakerRepository,
    CompetitionRepository,
    SeasonRepository,
    TeamRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.prediction_repository import SqlAlchemyPredictionRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemySeasonRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """从容器的 Database 产出一个事务性 session。"""
    async with container.database.session() as session:
        yield session


async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    """从容器的连接池产出一个 Redis 客户端。"""
    client = container.redis.client()
    try:
        yield client
    finally:
        await client.aclose()


# 用于 endpoint 签名的类型别名。
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]


# --- 仓储依赖（返回接口类型，注入 SQLAlchemy 实现）---


def get_fixture_repository(session: SessionDep) -> FixtureRepository:
    return SqlAlchemyFixtureRepository(session)


def get_prediction_repository(session: SessionDep) -> PredictionRepository:
    return SqlAlchemyPredictionRepository(session)


def get_value_bet_repository(session: SessionDep) -> ValueBetRepository:
    return SqlAlchemyValueBetRepository(session)


def get_team_repository(session: SessionDep) -> TeamRepository:
    return SqlAlchemyTeamRepository(session)


def get_competition_repository(session: SessionDep) -> CompetitionRepository:
    return SqlAlchemyCompetitionRepository(session)


def get_season_repository(session: SessionDep) -> SeasonRepository:
    return SqlAlchemySeasonRepository(session)


def get_bookmaker_repository(session: SessionDep) -> BookmakerRepository:
    return SqlAlchemyBookmakerRepository(session)


FixtureRepositoryDep = Annotated[FixtureRepository, Depends(get_fixture_repository)]
PredictionRepositoryDep = Annotated[PredictionRepository, Depends(get_prediction_repository)]
ValueBetRepositoryDep = Annotated[ValueBetRepository, Depends(get_value_bet_repository)]
TeamRepositoryDep = Annotated[TeamRepository, Depends(get_team_repository)]
CompetitionRepositoryDep = Annotated[CompetitionRepository, Depends(get_competition_repository)]
SeasonRepositoryDep = Annotated[SeasonRepository, Depends(get_season_repository)]
BookmakerRepositoryDep = Annotated[BookmakerRepository, Depends(get_bookmaker_repository)]

__all__ = [
    "BookmakerRepositoryDep",
    "CompetitionRepositoryDep",
    "FixtureRepositoryDep",
    "PredictionRepositoryDep",
    "RedisDep",
    "SeasonRepositoryDep",
    "SessionDep",
    "TeamRepositoryDep",
    "ValueBetRepositoryDep",
    "get_bookmaker_repository",
    "get_competition_repository",
    "get_db_session",
    "get_fixture_repository",
    "get_prediction_repository",
    "get_redis",
    "get_season_repository",
    "get_team_repository",
    "get_value_bet_repository",
]
