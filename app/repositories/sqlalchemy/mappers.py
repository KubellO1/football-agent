"""领域实体 ↔ ORM 模型 的双向转换。

把持久化细节隔离在此，领域层与仓储调用方都只见到纯领域实体。
"""

from __future__ import annotations

from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition, Season
from app.models.entities.decision_log import DecisionLog
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.prediction import MatchPrediction
from app.models.entities.team import Team
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.metrics import EloRating, ExpectedGoals
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult, Score
from app.repositories.sqlalchemy.models import (
    BookmakerORM,
    CompetitionORM,
    DecisionLogORM,
    FixtureORM,
    PredictionORM,
    SeasonORM,
    TeamORM,
    ValueBetORM,
)

# ---------------------------------------------------------------------------
# 参考数据
# ---------------------------------------------------------------------------


class CompetitionMapper:
    @staticmethod
    def to_domain(row: CompetitionORM) -> Competition:
        return Competition(
            id=row.id,
            name=row.name,
            country=row.country,
            tier=row.tier,
            external_id=row.external_id,
            external_source=row.external_source,
        )

    @staticmethod
    def to_orm(entity: Competition) -> CompetitionORM:
        return CompetitionORM(
            id=entity.id,
            name=entity.name,
            country=entity.country,
            tier=entity.tier,
            external_id=entity.external_id,
            external_source=entity.external_source,
        )


class SeasonMapper:
    @staticmethod
    def to_domain(row: SeasonORM) -> Season:
        return Season(
            id=row.id,
            competition_id=row.competition_id,
            label=row.label,
            start_date=row.start_date,
            end_date=row.end_date,
        )

    @staticmethod
    def to_orm(entity: Season) -> SeasonORM:
        return SeasonORM(
            id=entity.id,
            competition_id=entity.competition_id,
            label=entity.label,
            start_date=entity.start_date,
            end_date=entity.end_date,
        )


class TeamMapper:
    @staticmethod
    def to_domain(row: TeamORM) -> Team:
        elo = EloRating(row.elo) if row.elo is not None else None
        return Team(
            id=row.id,
            name=row.name,
            short_name=row.short_name,
            country=row.country,
            elo=elo,
            external_id=row.external_id,
            external_source=row.external_source,
        )

    @staticmethod
    def to_orm(entity: Team) -> TeamORM:
        return TeamORM(
            id=entity.id,
            name=entity.name,
            short_name=entity.short_name,
            country=entity.country,
            elo=entity.elo.value if entity.elo is not None else None,
            external_id=entity.external_id,
            external_source=entity.external_source,
        )


class BookmakerMapper:
    @staticmethod
    def to_domain(row: BookmakerORM) -> Bookmaker:
        return Bookmaker(id=row.id, name=row.name, country=row.country)

    @staticmethod
    def to_orm(entity: Bookmaker) -> BookmakerORM:
        return BookmakerORM(id=entity.id, name=entity.name, country=entity.country)


# ---------------------------------------------------------------------------
# 核心聚合
# ---------------------------------------------------------------------------


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
            external_id=row.external_id,
            external_source=row.external_source,
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
            external_id=entity.external_id,
            external_source=entity.external_source,
        )


class PredictionMapper:
    """MatchPrediction 与 PredictionORM 之间的转换器。

    注意：recommendations 不在此加载，由 ValueBetRepository 单独管理与组装。
    """

    @staticmethod
    def to_domain(row: PredictionORM) -> MatchPrediction:
        probabilities: dict[MatchResult, Probability] = {}
        if row.prob_home is not None:
            probabilities[MatchResult.HOME] = Probability(row.prob_home)
        if row.prob_draw is not None:
            probabilities[MatchResult.DRAW] = Probability(row.prob_draw)
        if row.prob_away is not None:
            probabilities[MatchResult.AWAY] = Probability(row.prob_away)

        expected_goals: ExpectedGoals | None = None
        if row.xg_home is not None and row.xg_away is not None:
            expected_goals = ExpectedGoals(home=row.xg_home, away=row.xg_away)

        return MatchPrediction(
            id=row.id,
            fixture_id=row.fixture_id,
            outcome_probabilities=probabilities,
            expected_goals=expected_goals,
            model_version=row.model_version,
            generated_at=row.generated_at,
        )

    @staticmethod
    def to_orm(entity: MatchPrediction) -> PredictionORM:
        probs = entity.outcome_probabilities
        home = probs.get(MatchResult.HOME)
        draw = probs.get(MatchResult.DRAW)
        away = probs.get(MatchResult.AWAY)
        return PredictionORM(
            id=entity.id,
            fixture_id=entity.fixture_id,
            prob_home=home.value if home is not None else None,
            prob_draw=draw.value if draw is not None else None,
            prob_away=away.value if away is not None else None,
            xg_home=entity.expected_goals.home if entity.expected_goals is not None else None,
            xg_away=entity.expected_goals.away if entity.expected_goals is not None else None,
            model_version=entity.model_version,
            generated_at=entity.generated_at,
        )


class ValueBetMapper:
    """ValueBet 与 ValueBetORM 之间的转换器。ValueEdge 为派生值，由概率+赔率重建。"""

    @staticmethod
    def to_domain(row: ValueBetORM) -> ValueBet:
        probability = Probability(row.model_probability)
        odds = Odds(row.odds_decimal)

        stake: Stake | None = None
        if (
            row.stake_amount is not None
            and row.stake_currency is not None
            and row.stake_fraction is not None
        ):
            stake = Stake(
                amount=Money(row.stake_amount, row.stake_currency),
                fraction_of_bankroll=row.stake_fraction,
            )

        return ValueBet(
            id=row.id,
            fixture_id=row.fixture_id,
            selection=Selection(
                market=MarketType(row.selection_market),
                code=row.selection_code,
                line=row.selection_line,
            ),
            odds=odds,
            bookmaker_id=row.bookmaker_id,
            model_probability=probability,
            edge=ValueEdge(model_probability=probability, odds=odds),
            stake=stake,
            confidence=row.confidence,
            rationale=row.rationale,
            created_at=row.created_at,
        )

    @staticmethod
    def to_orm(entity: ValueBet) -> ValueBetORM:
        stake = entity.stake
        return ValueBetORM(
            id=entity.id,
            fixture_id=entity.fixture_id,
            selection_market=entity.selection.market.value,
            selection_code=entity.selection.code,
            selection_line=entity.selection.line,
            odds_decimal=entity.odds.decimal,
            bookmaker_id=entity.bookmaker_id,
            model_probability=entity.model_probability.value,
            stake_amount=stake.amount.amount if stake is not None else None,
            stake_currency=stake.amount.currency if stake is not None else None,
            stake_fraction=stake.fraction_of_bankroll if stake is not None else None,
            confidence=entity.confidence,
            rationale=entity.rationale,
            created_at=entity.created_at,
        )


class DecisionLogMapper:
    """DecisionLog 与 DecisionLogORM 之间的转换器。列表字段直接存/取 JSON。"""

    @staticmethod
    def to_domain(row: DecisionLogORM) -> DecisionLog:
        return DecisionLog(
            id=row.id,
            fixture_id=row.fixture_id,
            summary=row.summary,
            value_bet_id=row.value_bet_id,
            supporting_evidence=list(row.supporting_evidence),
            risks=list(row.risks),
            rejected_alternatives=list(row.rejected_alternatives),
            change_conditions=list(row.change_conditions),
            created_at=row.created_at,
        )

    @staticmethod
    def to_orm(entity: DecisionLog) -> DecisionLogORM:
        return DecisionLogORM(
            id=entity.id,
            fixture_id=entity.fixture_id,
            value_bet_id=entity.value_bet_id,
            summary=entity.summary,
            supporting_evidence=list(entity.supporting_evidence),
            risks=list(entity.risks),
            rejected_alternatives=list(entity.rejected_alternatives),
            change_conditions=list(entity.change_conditions),
            created_at=entity.created_at,
        )
