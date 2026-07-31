"""比赛阵容领域对象单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.entities.lineup import Lineup
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus

_CAPTURED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _players(count: int) -> tuple[UUID, ...]:
    return tuple(uuid4() for _ in range(count))


def _lineup(**overrides: object) -> Lineup:
    values: dict[str, object] = {
        "fixture_id": uuid4(),
        "team_id": uuid4(),
        "status": LineupStatus.CONFIRMED,
        "source": LineupSource(
            name="Official Club",
            evidence_level=EvidenceLevel.A,
            reference="https://example.com/team-sheet",
        ),
        "starting": _players(11),
        "substitutes": _players(9),
        "formation": Formation("4-2-3-1"),
        "captured_at": _CAPTURED_AT,
        "source_updated_at": _CAPTURED_AT - timedelta(minutes=2),
    }
    values.update(overrides)
    return Lineup(**values)  # type: ignore[arg-type]


def test_confirmed_lineup_preserves_provenance_and_structure() -> None:
    lineup = _lineup()

    assert lineup.is_confirmed is True
    assert len(lineup.starting) == 11
    assert len(lineup.substitutes) == 9
    assert lineup.bench == lineup.substitutes
    assert str(lineup.formation) == "4-2-3-1"
    assert lineup.source.evidence_level is EvidenceLevel.A


@pytest.mark.parametrize("notation", ["4-3-3", "4-4-2", "3-4-2-1"])
def test_formation_accepts_valid_outfield_structures(notation: str) -> None:
    assert str(Formation(notation)) == notation


@pytest.mark.parametrize("notation", ["4-4", "4-3-2-1-0", "4-x-3", "4-3-2"])
def test_formation_rejects_invalid_structures(notation: str) -> None:
    with pytest.raises(ValueError, match="formation"):
        Formation(notation)


def test_lineup_requires_exactly_eleven_unique_starters() -> None:
    with pytest.raises(ValueError, match="exactly eleven"):
        _lineup(starting=_players(10))

    player = uuid4()
    with pytest.raises(ValueError, match="starting players must be unique"):
        _lineup(starting=(player, player, *_players(9)))


def test_lineup_rejects_duplicate_or_overlapping_substitutes() -> None:
    substitute = uuid4()
    with pytest.raises(ValueError, match="substitute players must be unique"):
        _lineup(substitutes=(substitute, substitute))

    starters = _players(11)
    with pytest.raises(ValueError, match="both starting and a substitute"):
        _lineup(starting=starters, substitutes=(starters[0],))


@pytest.mark.parametrize(
    ("captured_at", "source_updated_at", "message"),
    [
        (
            datetime(2026, 8, 1, 12, 0),
            None,
            "captured_at must be timezone-aware",
        ),
        (
            _CAPTURED_AT,
            datetime(2026, 8, 1, 11, 59),
            "source_updated_at must be timezone-aware",
        ),
        (
            _CAPTURED_AT,
            _CAPTURED_AT + timedelta(seconds=1),
            "source_updated_at cannot be later",
        ),
    ],
)
def test_lineup_rejects_invalid_timestamps(
    captured_at: datetime,
    source_updated_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _lineup(captured_at=captured_at, source_updated_at=source_updated_at)


def test_lineup_source_normalizes_identity() -> None:
    source = LineupSource(
        name=" Official Club ",
        evidence_level=EvidenceLevel.A,
        reference=" https://example.com/team-sheet ",
    )

    assert source.name == "Official Club"
    assert source.reference == "https://example.com/team-sheet"
