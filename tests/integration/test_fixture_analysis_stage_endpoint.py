"""三阶段分析 API、PostgreSQL 与首发准入的真实链路测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps as api_deps
from app.api.v1.endpoints.analysis import router
from app.config.settings import Settings
from app.core.container import Container
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus, PlayerPosition
from app.models.entities.fixture import Fixture
from app.models.entities.lineup import Lineup
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.entities.player import Player
from app.models.entities.team import Team
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.metrics import EloRating
from app.models.value_objects.odds import Odds
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.lineup_repository import SqlAlchemyLineupRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import (
    SqlAlchemyOddsSnapshotRepository,
)
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.modeling import MatchModel
from app.services.models.ensemble import EnsembleMatchModel
from app.services.recommendation_gate import RecommendationGate

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_analysis_fixture(session: AsyncSession, *, now: datetime) -> Fixture:
    competition = await SqlAlchemyCompetitionRepository(session).add(
        Competition(name="Stage API League", country="Test")
    )
    teams = SqlAlchemyTeamRepository(session)
    home = await teams.add(Team(name="Stage Home", elo=EloRating(1550)))
    away = await teams.add(Team(name="Stage Away", elo=EloRating(1480)))
    opponent = await teams.add(Team(name="Stage Opponent", elo=EloRating(1500)))
    fixtures = SqlAlchemyFixtureRepository(session)

    for index in range(10):
        await fixtures.add(
            Fixture(
                competition_id=competition.id,
                home_team_id=home.id,
                away_team_id=opponent.id,
                kickoff=now - timedelta(days=30 - index),
                status=MatchStatus.FINISHED,
                score=Score(home=2, away=1),
            )
        )
        await fixtures.add(
            Fixture(
                competition_id=competition.id,
                home_team_id=opponent.id,
                away_team_id=away.id,
                kickoff=now - timedelta(days=20 - index, hours=12),
                status=MatchStatus.FINISHED,
                score=Score(home=1, away=1),
            )
        )

    target = await fixtures.add(
        Fixture(
            competition_id=competition.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff=now + timedelta(days=1),
            status=MatchStatus.SCHEDULED,
        )
    )
    await _seed_odds(session, fixture_id=target.id, captured_at=now - timedelta(minutes=5))
    return target


async def _seed_odds(
    session: AsyncSession,
    *,
    fixture_id: UUID,
    captured_at: datetime,
) -> None:
    bookmakers = [
        await SqlAlchemyBookmakerRepository(session).add(Bookmaker(name=name))
        for name in ("Stage BM A", "Stage BM B")
    ]
    odds = SqlAlchemyOddsSnapshotRepository(session)
    for bookmaker in bookmakers:
        for code, price in (("home", "2.40"), ("draw", "3.40"), ("away", "3.20")):
            await odds.add(
                OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker_id=bookmaker.id,
                    selection=Selection(market=MarketType.MATCH_RESULT, code=code),
                    odds=Odds(Decimal(price)),
                    captured_at=captured_at,
                )
            )


async def _seed_confirmed_lineups(
    session: AsyncSession,
    *,
    fixture: Fixture,
    captured_at: datetime,
) -> None:
    players = SqlAlchemyPlayerRepository(session)
    lineups = SqlAlchemyLineupRepository(session)
    for label, team_id in (("home", fixture.home_team_id), ("away", fixture.away_team_id)):
        starting_player_ids = []
        for index in range(11):
            player = await players.add(
                Player(
                    name=f"Stage {label} Player {index}",
                    position=(PlayerPosition.GOALKEEPER if index == 0 else PlayerPosition.DEFENDER),
                    team_id=team_id,
                    external_source="api-football",
                    external_id=f"stage-{label}-{index}",
                )
            )
            starting_player_ids.append(player.id)

        starting = tuple(starting_player_ids)
        await lineups.add(
            Lineup(
                fixture_id=fixture.id,
                team_id=team_id,
                status=LineupStatus.CONFIRMED,
                source=LineupSource(
                    name="api-football",
                    evidence_level=EvidenceLevel.B,
                    reference=f"/fixtures/lineups?fixture={fixture.id}",
                ),
                starting=starting,
                formation=Formation("4-3-3"),
                captured_at=captured_at,
            )
        )


def _test_container() -> Container:
    container = Container(
        Settings(
            analysis_form_window=10,
            analysis_odds_max_age_minutes=30,
            analysis_odds_min_bookmakers=2,
        )
    )
    container.register(MatchModel, EnsembleMatchModel())
    container.register(RecommendationGate, RecommendationGate())
    return container


@pytest.mark.integration
async def test_stage_endpoint_enforces_verified_lineups_with_real_postgres(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    fixture = await _seed_analysis_fixture(db_session, now=now)
    monkeypatch.setattr(api_deps, "container", _test_container())

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[api_deps.get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        initial = await client.post(f"/api/v1/fixtures/{fixture.id}/analyze?stage=initial")
        final_without_lineups = await client.post(
            f"/api/v1/fixtures/{fixture.id}/analyze?stage=final"
        )

        await _seed_confirmed_lineups(
            db_session,
            fixture=fixture,
            captured_at=now - timedelta(minutes=1),
        )
        final_with_lineups = await client.post(f"/api/v1/fixtures/{fixture.id}/analyze?stage=final")

    assert initial.status_code == 200
    initial_payload = initial.json()
    assert initial_payload["analysis_stage"] == "initial"
    assert initial_payload["lineup_admission"]["approved"] is True
    assert initial_payload["probabilities"] is not None

    assert final_without_lineups.status_code == 200
    denied_payload = final_without_lineups.json()
    assert denied_payload["analysis_stage"] == "final"
    assert denied_payload["lineup_admission"]["approved"] is False
    assert denied_payload["confidence_killer"] == "lineup_admission_failed"
    assert denied_payload["selections"]
    assert all(not selection["recommended"] for selection in denied_payload["selections"])
    assert all(selection["kelly_stake"] == 0.0 for selection in denied_payload["selections"])

    assert final_with_lineups.status_code == 200
    admitted_payload = final_with_lineups.json()
    assert admitted_payload["analysis_stage"] == "final"
    assert admitted_payload["lineup_admission"]["approved"] is True
    assert admitted_payload["confidence_killer"] != "lineup_admission_failed"
    assert admitted_payload["probabilities"] is not None
    assert admitted_payload["selections"]
