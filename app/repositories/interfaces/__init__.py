"""仓储抽象接口（依赖倒置边界）。"""

from app.repositories.interfaces.base import Repository
from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.prediction_repository import PredictionRepository
from app.repositories.interfaces.reference import (
    BookmakerRepository,
    CompetitionRepository,
    ReferenceRepository,
    SeasonRepository,
    TeamRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository

__all__ = [
    "BookmakerRepository",
    "CompetitionRepository",
    "DecisionLogRepository",
    "FixtureRepository",
    "PredictionRepository",
    "ReferenceRepository",
    "Repository",
    "SeasonRepository",
    "TeamRepository",
    "ValueBetRepository",
]
