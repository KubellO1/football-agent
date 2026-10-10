from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.team_intelligence.contracts import CaptureWindow
from app.team_intelligence.matching import FixtureIdentity, FixtureMatcher, IdentityResolver
from app.team_intelligence.planning import collection_windows, natural_capture_window

KICKOFF = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)


def test_identity_resolver_prefers_external_id_and_fails_closed_on_ambiguity() -> None:
    resolver = IdentityResolver()
    resolver.register(
        entity_type="team",
        canonical_id="team-1",
        source_name="api-football",
        external_id="10",
        names=("Paris Saint-Germain",),
    )
    resolver.register(
        entity_type="team",
        canonical_id="team-2",
        source_name="another",
        external_id="20",
        names=("Paris Saint Germain",),
    )

    assert (
        resolver.resolve(
            entity_type="team", source_name="api-football", external_id="10"
        ).canonical_id
        == "team-1"
    )
    ambiguous = resolver.resolve(
        entity_type="team",
        source_name="unknown",
        external_id=None,
        name="Paris Saint-Germain",
    )
    assert ambiguous.canonical_id is None
    assert ambiguous.reason_code == "AMBIGUOUS_EXACT_NAME"


def test_fixture_match_requires_exact_names_and_kickoff() -> None:
    fixture = FixtureIdentity("fixture-1", "Serie A", KICKOFF, "Inter", "Juventus")
    candidate = FixtureIdentity("external", "Serie A", KICKOFF, "Inter", "Juventus")
    result = FixtureMatcher().match(candidate, (fixture,))
    assert result.canonical_id == "fixture-1"

    late = FixtureIdentity(
        "external", "Serie A", KICKOFF + timedelta(minutes=20), "Inter", "Juventus"
    )
    assert FixtureMatcher().match(late, (fixture,)).canonical_id is None


def test_natural_windows_include_t15_without_backdating() -> None:
    assert (
        natural_capture_window(kickoff=KICKOFF, now=KICKOFF - timedelta(minutes=60))
        is CaptureWindow.T60
    )
    assert (
        natural_capture_window(kickoff=KICKOFF, now=KICKOFF - timedelta(minutes=15))
        is CaptureWindow.T15
    )
    assert natural_capture_window(kickoff=KICKOFF, now=KICKOFF - timedelta(minutes=45)) is None
    assert natural_capture_window(kickoff=KICKOFF, now=KICKOFF + timedelta(seconds=1)) is None
    assert CaptureWindow.T15 in collection_windows()
