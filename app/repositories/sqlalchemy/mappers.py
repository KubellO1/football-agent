"""领域实体 ↔ ORM 模型 的双向转换。

把持久化细节隔离在此，领域层与仓储调用方都只见到纯领域实体。
"""

from __future__ import annotations

from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition, Season
from app.models.entities.decision_log import DecisionLog
from app.models.entities.enums import MatchStatus, PlayerPosition
from app.models.entities.fixture import Fixture
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.entities.player import Player
from app.models.entities.player_availability import PlayerAvailabilityObservation
from app.models.entities.prediction import MatchPrediction
from app.models.entities.team import Team
from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.availability import AvailabilitySource, AvailabilityStatus
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.metrics import EloRating, ExpectedGoals
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult, Score
from app.models.value_objects.statistics import TeamMatchMetrics
from app.repositories.sqlalchemy.models import (
    PREDICTION_RECORD_AGGREGATE,
    BankrollEntryORM,
    BookmakerORM,
    CompetitionORM,
    DecisionLogORM,
    FixtureORM,
    OddsSnapshotORM,
    PerformanceSnapshotORM,
    PlayerAvailabilityObservationORM,
    PlayerORM,
    PredictionORM,
    SeasonORM,
    SettlementORM,
    TeamMatchStatisticsORM,
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


class PlayerMapper:
    """Player 与 PlayerORM 之间的双向转换。"""

    @staticmethod
    def to_domain(row: PlayerORM) -> Player:
        return Player(
            id=row.id,
            name=row.name,
            position=PlayerPosition(row.position),
            team_id=row.team_id,
            date_of_birth=row.date_of_birth,
            external_source=row.external_source,
            external_id=row.external_id,
        )

    @staticmethod
    def to_orm(entity: Player) -> PlayerORM:
        return PlayerORM(
            id=entity.id,
            name=entity.name,
            position=entity.position.value,
            team_id=entity.team_id,
            date_of_birth=entity.date_of_birth,
            external_source=entity.external_source,
            external_id=entity.external_id,
        )


class BookmakerMapper:
    @staticmethod
    def to_domain(row: BookmakerORM) -> Bookmaker:
        return Bookmaker(
            id=row.id,
            name=row.name,
            country=row.country,
            external_id=row.external_id,
            external_source=row.external_source,
        )

    @staticmethod
    def to_orm(entity: Bookmaker) -> BookmakerORM:
        return BookmakerORM(
            id=entity.id,
            name=entity.name,
            country=entity.country,
            external_id=entity.external_id,
            external_source=entity.external_source,
        )


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


class TeamMatchStatisticsMapper:
    """TeamMatchStatistics 与宽表 ORM 之间的双向转换。"""

    @staticmethod
    def to_domain(row: TeamMatchStatisticsORM) -> TeamMatchStatistics:
        return TeamMatchStatistics(
            id=row.id,
            fixture_id=row.fixture_id,
            team_id=row.team_id,
            source=row.source,
            captured_at=row.captured_at,
            source_updated_at=row.source_updated_at,
            is_final=row.is_final,
            metrics=TeamMatchMetrics(
                xg=row.xg,
                xg_against=row.xg_against,
                shots=row.shots,
                shots_on_target=row.shots_on_target,
                possession_percentage=row.possession_percentage,
                ppda=row.ppda,
                big_chances=row.big_chances,
                goalkeeper_saves=row.goalkeeper_saves,
                set_piece_shots=row.set_piece_shots,
                headed_shots=row.headed_shots,
                conversion_rate=row.conversion_rate,
            ),
        )

    @staticmethod
    def to_orm(entity: TeamMatchStatistics) -> TeamMatchStatisticsORM:
        metrics = entity.metrics
        return TeamMatchStatisticsORM(
            id=entity.id,
            fixture_id=entity.fixture_id,
            team_id=entity.team_id,
            source=entity.source,
            captured_at=entity.captured_at,
            source_updated_at=entity.source_updated_at,
            is_final=entity.is_final,
            xg=metrics.xg,
            xg_against=metrics.xg_against,
            shots=metrics.shots,
            shots_on_target=metrics.shots_on_target,
            possession_percentage=metrics.possession_percentage,
            ppda=metrics.ppda,
            big_chances=metrics.big_chances,
            goalkeeper_saves=metrics.goalkeeper_saves,
            set_piece_shots=metrics.set_piece_shots,
            headed_shots=metrics.headed_shots,
            conversion_rate=metrics.conversion_rate,
        )


class PlayerAvailabilityObservationMapper:
    """PlayerAvailabilityObservation 与 ORM 行之间的双向转换。"""

    @staticmethod
    def to_domain(
        row: PlayerAvailabilityObservationORM,
    ) -> PlayerAvailabilityObservation:
        return PlayerAvailabilityObservation(
            id=row.id,
            fixture_id=row.fixture_id,
            team_id=row.team_id,
            player_id=row.player_id,
            status=AvailabilityStatus(row.status),
            source=AvailabilitySource(
                name=row.source_name,
                evidence_level=EvidenceLevel(row.evidence_level),
                reference=row.source_reference,
            ),
            captured_at=row.captured_at,
            source_updated_at=row.source_updated_at,
            reason=row.reason,
            expected_return=row.expected_return,
        )

    @staticmethod
    def to_orm(
        entity: PlayerAvailabilityObservation,
    ) -> PlayerAvailabilityObservationORM:
        return PlayerAvailabilityObservationORM(
            id=entity.id,
            fixture_id=entity.fixture_id,
            team_id=entity.team_id,
            player_id=entity.player_id,
            status=entity.status.value,
            source_name=entity.source.name,
            evidence_level=entity.source.evidence_level.value,
            source_reference=entity.source.reference,
            captured_at=entity.captured_at,
            source_updated_at=entity.source_updated_at,
            reason=entity.reason,
            expected_return=entity.expected_return,
        )


class PredictionMapper:
    """MatchPrediction 与 PredictionORM 之间的转换器。

    注意：recommendations 不在此加载，由 ValueBetRepository 单独管理与组装。
    """

    @staticmethod
    def to_domain(row: PredictionORM) -> MatchPrediction:
        if row.record_kind != PREDICTION_RECORD_AGGREGATE:
            raise ValueError("decision records cannot be mapped to MatchPrediction")

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
            record_kind=PREDICTION_RECORD_AGGREGATE,
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


class OddsSnapshotMapper:
    """OddsSnapshot 与 OddsSnapshotORM 之间的转换器。"""

    @staticmethod
    def to_domain(row: OddsSnapshotORM) -> OddsSnapshot:
        return OddsSnapshot(
            id=row.id,
            fixture_id=row.fixture_id,
            bookmaker_id=row.bookmaker_id,
            selection=Selection(
                market=MarketType(row.selection_market),
                code=row.selection_code,
                line=row.selection_line,
            ),
            odds=Odds(row.odds_decimal),
            captured_at=row.captured_at,
        )

    @staticmethod
    def to_orm(entity: OddsSnapshot) -> OddsSnapshotORM:
        return OddsSnapshotORM(
            id=entity.id,
            fixture_id=entity.fixture_id,
            bookmaker_id=entity.bookmaker_id,
            selection_market=entity.selection.market.value,
            selection_code=entity.selection.code,
            selection_line=entity.selection.line,
            odds_decimal=entity.odds.decimal,
            captured_at=entity.captured_at,
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
            model_version=row.model_version,
            prompt_version=row.prompt_version,
            review=dict(row.review) if row.review is not None else None,
            evidence_snapshot=(
                dict(row.evidence_snapshot) if row.evidence_snapshot is not None else None
            ),
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
            model_version=entity.model_version,
            prompt_version=entity.prompt_version,
            review=entity.review,
            evidence_snapshot=entity.evidence_snapshot,
            created_at=entity.created_at,
        )


# ---------------------------------------------------------------------------
# 结算与追踪
# ---------------------------------------------------------------------------

from app.models.entities.settlement import (  # noqa: E402
    BankrollEntry,
    PerformanceSnapshot,
    Settlement,
    SettlementResult,
)


class SettlementMapper:
    @staticmethod
    def to_domain(row: SettlementORM) -> Settlement:
        return Settlement(
            id=row.id,
            value_bet_id=row.value_bet_id,
            fixture_id=row.fixture_id,
            result=SettlementResult(row.result),
            score_home=row.score_home,
            score_away=row.score_away,
            profit_loss=row.profit_loss,
            closing_odds=row.closing_odds,
            clv=row.clv,
            bankroll_before=row.bankroll_before,
            bankroll_after=row.bankroll_after,
            settlement_timestamp=row.settlement_timestamp,
        )

    @staticmethod
    def to_orm(entity: Settlement) -> SettlementORM:
        return SettlementORM(
            id=entity.id,
            value_bet_id=entity.value_bet_id,
            fixture_id=entity.fixture_id,
            result=entity.result.value,
            score_home=entity.score_home,
            score_away=entity.score_away,
            profit_loss=entity.profit_loss,
            closing_odds=entity.closing_odds,
            clv=entity.clv,
            bankroll_before=entity.bankroll_before,
            bankroll_after=entity.bankroll_after,
            settlement_timestamp=entity.settlement_timestamp,
        )


class BankrollEntryMapper:
    @staticmethod
    def to_domain(row: BankrollEntryORM) -> BankrollEntry:
        return BankrollEntry(
            id=row.id,
            amount=row.amount,
            balance_after=row.balance_after,
            reason=row.reason,
            created_at=row.created_at,
        )

    @staticmethod
    def to_orm(entity: BankrollEntry) -> BankrollEntryORM:
        return BankrollEntryORM(
            id=entity.id,
            amount=entity.amount,
            balance_after=entity.balance_after,
            reason=entity.reason,
            created_at=entity.created_at,
        )


class PerformanceSnapshotMapper:
    @staticmethod
    def to_domain(row: PerformanceSnapshotORM) -> PerformanceSnapshot:
        return PerformanceSnapshot(
            id=row.id,
            period_start=row.period_start,
            period_end=row.period_end,
            total_bets=row.total_bets,
            win_count=row.win_count,
            push_count=row.push_count,
            loss_count=row.loss_count,
            win_rate=row.win_rate,
            total_pl=row.total_pl,
            roi=row.roi,
            avg_ev=row.avg_ev,
            avg_clv=row.avg_clv,
            brier_score=row.brier_score,
            log_loss=row.log_loss,
            max_drawdown=row.max_drawdown,
            sharpe_ratio=row.sharpe_ratio,
            breakdown_json=row.breakdown_json,
            created_at=row.created_at,
        )

    @staticmethod
    def to_orm(entity: PerformanceSnapshot) -> PerformanceSnapshotORM:
        return PerformanceSnapshotORM(
            id=entity.id,
            period_start=entity.period_start,
            period_end=entity.period_end,
            total_bets=entity.total_bets,
            win_count=entity.win_count,
            push_count=entity.push_count,
            loss_count=entity.loss_count,
            win_rate=entity.win_rate,
            total_pl=entity.total_pl,
            roi=entity.roi,
            avg_ev=entity.avg_ev,
            avg_clv=entity.avg_clv,
            brier_score=entity.brier_score,
            log_loss=entity.log_loss,
            max_drawdown=entity.max_drawdown,
            sharpe_ratio=entity.sharpe_ratio,
            breakdown_json=entity.breakdown_json,
            created_at=entity.created_at,
        )
