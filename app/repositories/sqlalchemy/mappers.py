"""领域实体 ↔ ORM 模型 的双向转换。

把持久化细节隔离在此，领域层与仓储调用方都只见到纯领域实体。
"""

from __future__ import annotations

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy.models import FixtureORM


class FixtureMapper:
    """Fixture 与 FixtureORM 之间的转换器。"""

    @staticmethod
    def to_domain(row: FixtureORM) -> Fixture:
        # 两列都有值时才还原 Score 值对象（未开赛无比分）
        score: Score | None = None
        if row.score_home is not None and row.score_away is not None:
            score = Score(home=row.score_home, away=row.score_away)

        return Fixture(
            id=row.id,
            competition_id=row.competition_id,
            season_id=row.season_id,
            home_team_id=row.home_team_id,
            away_team_id=row.away_team_id,
            kickoff=row.kickoff,
            status=MatchStatus(row.status),
            score=score,
        )

    @staticmethod
    def to_orm(entity: Fixture) -> FixtureORM:
        return FixtureORM(
            id=entity.id,
            competition_id=entity.competition_id,
            season_id=entity.season_id,
            home_team_id=entity.home_team_id,
            away_team_id=entity.away_team_id,
            kickoff=entity.kickoff,
            status=entity.status.value,
            score_home=entity.score.home if entity.score is not None else None,
            score_away=entity.score.away if entity.score is not None else None,
        )
