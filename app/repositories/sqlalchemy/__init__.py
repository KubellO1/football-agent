"""仓储接口的 SQLAlchemy 实现。"""

from app.repositories.sqlalchemy.decision_log_repository import (
    SqlAlchemyDecisionLogRepository,
)
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.mappers import (
    BookmakerMapper,
    CompetitionMapper,
    DecisionLogMapper,
    FixtureMapper,
    PlayerAvailabilityObservationMapper,
    PredictionMapper,
    SeasonMapper,
    TeamMapper,
    TeamMatchStatisticsMapper,
    ValueBetMapper,
)
from app.repositories.sqlalchemy.models import (
    BookmakerORM,
    CompetitionORM,
    DecisionLogORM,
    FixtureORM,
    PlayerAvailabilityObservationORM,
    PredictionORM,
    SeasonORM,
    TeamMatchStatisticsORM,
    TeamORM,
    ValueBetORM,
)
from app.repositories.sqlalchemy.player_availability_repository import (
    SqlAlchemyPlayerAvailabilityObservationRepository,
)
from app.repositories.sqlalchemy.prediction_repository import SqlAlchemyPredictionRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemySeasonRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.team_match_statistics_repository import (
    SqlAlchemyTeamMatchStatisticsRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository

__all__ = [
    "BookmakerMapper",
    "BookmakerORM",
    "CompetitionMapper",
    "CompetitionORM",
    "DecisionLogMapper",
    "DecisionLogORM",
    "FixtureMapper",
    "FixtureORM",
    "PlayerAvailabilityObservationMapper",
    "PlayerAvailabilityObservationORM",
    "PredictionMapper",
    "PredictionORM",
    "SeasonMapper",
    "SeasonORM",
    "SqlAlchemyBookmakerRepository",
    "SqlAlchemyCompetitionRepository",
    "SqlAlchemyDecisionLogRepository",
    "SqlAlchemyFixtureRepository",
    "SqlAlchemyPredictionRepository",
    "SqlAlchemyPlayerAvailabilityObservationRepository",
    "SqlAlchemySeasonRepository",
    "SqlAlchemyTeamRepository",
    "SqlAlchemyTeamMatchStatisticsRepository",
    "TeamMatchStatisticsMapper",
    "TeamMatchStatisticsORM",
    "SqlAlchemyValueBetRepository",
    "TeamMapper",
    "TeamORM",
    "ValueBetMapper",
    "ValueBetORM",
]
