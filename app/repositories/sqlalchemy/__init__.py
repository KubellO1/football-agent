"""仓储接口的 SQLAlchemy 实现。"""

from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.mappers import (
    BookmakerMapper,
    CompetitionMapper,
    FixtureMapper,
    PredictionMapper,
    SeasonMapper,
    TeamMapper,
    ValueBetMapper,
)
from app.repositories.sqlalchemy.models import (
    BookmakerORM,
    CompetitionORM,
    FixtureORM,
    PredictionORM,
    SeasonORM,
    TeamORM,
    ValueBetORM,
)
from app.repositories.sqlalchemy.prediction_repository import SqlAlchemyPredictionRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemySeasonRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository

__all__ = [
    "BookmakerMapper",
    "BookmakerORM",
    "CompetitionMapper",
    "CompetitionORM",
    "FixtureMapper",
    "FixtureORM",
    "PredictionMapper",
    "PredictionORM",
    "SeasonMapper",
    "SeasonORM",
    "SqlAlchemyBookmakerRepository",
    "SqlAlchemyCompetitionRepository",
    "SqlAlchemyFixtureRepository",
    "SqlAlchemyPredictionRepository",
    "SqlAlchemySeasonRepository",
    "SqlAlchemyTeamRepository",
    "SqlAlchemyValueBetRepository",
    "TeamMapper",
    "TeamORM",
    "ValueBetMapper",
    "ValueBetORM",
]
