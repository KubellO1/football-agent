"""VerifiedLineupService 的时间边界与证据准入单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.entities.fixture import Fixture
from app.models.entities.lineup import Lineup
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus
from app.repositories.interfaces.lineup_repository import LineupRepository
from app.services.verified_lineup import (
    LineupVerificationIssue,
    VerifiedLineupPolicy,
    VerifiedLineupService,
)

_NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)


def _fixture() -> Fixture:
    return Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=_NOW + timedelta(hours=1),
    )


def _lineup(
    fixture_id: UUID,
    team_id: UUID,
    *,
    source: str = "api-football",
    evidence: EvidenceLevel = EvidenceLevel.A,
    status: LineupStatus = LineupStatus.CONFIRMED,
    captured_at: datetime = _NOW,
) -> Lineup:
    return Lineup(
        fixture_id=fixture_id,
        team_id=team_id,
        status=status,
        source=LineupSource(name=source, evidence_level=evidence),
        starting=tuple(uuid4() for _ in range(11)),
        substitutes=tuple(uuid4() for _ in range(7)),
        formation=Formation("4-3-3"),
        captured_at=captured_at,
    )


def _service(*responses: Lineup | None) -> tuple[VerifiedLineupService, AsyncMock]:
    repository = AsyncMock(spec=LineupRepository)
    repository.get_latest_by_source.side_effect = responses
    return (
        VerifiedLineupService(repository=cast("LineupRepository", repository)),
        repository,
    )


@pytest.mark.unit
async def test_accepts_confirmed_home_and_away_lineups_as_of_decision_time() -> None:
    fixture = _fixture()
    home = _lineup(fixture.id, fixture.home_team_id)
    away = _lineup(fixture.id, fixture.away_team_id)
    service, repository = _service(home, away)

    result = await service.verify(fixture, as_of=_NOW)

    assert result.accepted
    assert result.home.lineup is home
    assert result.away.lineup is away
    assert repository.get_latest_by_source.await_count == 2
    repository.get_latest_by_source.assert_any_await(
        fixture.id,
        fixture.home_team_id,
        "api-football",
        status=LineupStatus.CONFIRMED,
        as_of=_NOW,
    )


@pytest.mark.unit
async def test_missing_away_lineup_rejects_fixture_without_prediction_fallback() -> None:
    fixture = _fixture()
    service, _ = _service(_lineup(fixture.id, fixture.home_team_id), None)

    result = await service.verify(fixture, as_of=_NOW)

    assert not result.accepted
    assert result.home.accepted
    assert result.away.lineup is None
    assert result.away.issues == (LineupVerificationIssue.MISSING,)


@pytest.mark.unit
async def test_low_evidence_lineup_is_retained_but_rejected_for_audit() -> None:
    fixture = _fixture()
    home = _lineup(fixture.id, fixture.home_team_id, evidence=EvidenceLevel.C)
    away = _lineup(fixture.id, fixture.away_team_id)
    service, _ = _service(home, away)

    result = await service.verify(fixture, as_of=_NOW)

    assert not result.accepted
    assert result.home.lineup is home
    assert result.home.issues == (LineupVerificationIssue.EVIDENCE_BELOW_MINIMUM,)


@pytest.mark.unit
async def test_repository_contract_violations_are_reported_defensively() -> None:
    fixture = _fixture()
    wrong = _lineup(
        uuid4(),
        uuid4(),
        source="other-provider",
        status=LineupStatus.PREDICTED,
        captured_at=_NOW + timedelta(seconds=1),
    )
    service, _ = _service(wrong, _lineup(fixture.id, fixture.away_team_id))

    result = await service.verify(fixture, as_of=_NOW)

    assert result.home.issues == (
        LineupVerificationIssue.FIXTURE_MISMATCH,
        LineupVerificationIssue.TEAM_MISMATCH,
        LineupVerificationIssue.SOURCE_MISMATCH,
        LineupVerificationIssue.NOT_CONFIRMED,
        LineupVerificationIssue.FUTURE_SNAPSHOT,
    )


@pytest.mark.unit
async def test_naive_as_of_is_rejected_before_repository_access() -> None:
    fixture = _fixture()
    service, repository = _service()

    with pytest.raises(ValueError, match="timezone-aware"):
        await service.verify(fixture, as_of=datetime(2026, 8, 2, 18, 0))

    repository.get_latest_by_source.assert_not_awaited()


def test_policy_rejects_ambiguous_configuration() -> None:
    with pytest.raises(ValueError, match="non-empty and trimmed"):
        VerifiedLineupPolicy(source=" api-football ")
    with pytest.raises(ValueError, match="EvidenceLevel"):
        VerifiedLineupPolicy(minimum_evidence=cast("EvidenceLevel", "B"))
