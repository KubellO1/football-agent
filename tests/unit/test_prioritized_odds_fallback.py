"""Targeted dual-provider fallback contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from app.providers.impl.odds_api_io_provider import OddsProviderError
from app.providers.impl.prioritized_odds_provider import PrioritizedOddsProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import (
    BookmakerMarket,
    OddsOutcome,
    ProviderFixtureOdds,
    ProviderOddsTarget,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

KICKOFF = datetime(2026, 8, 30, 18, 0, tzinfo=UTC)


class _TargetedProvider(OddsProvider):
    def __init__(
        self,
        events: Sequence[ProviderFixtureOdds] = (),
        *,
        failure: Exception | None = None,
    ) -> None:
        self.events = list(events)
        self.failure = failure
        self.calls: list[list[ProviderOddsTarget]] = []
        self.requests_made = 0

    async def get_odds(
        self,
        *,
        sport: str,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        del sport, markets, regions
        return list(self.events)

    async def get_odds_for_fixtures(
        self,
        *,
        sport: str,
        fixtures: Sequence[ProviderOddsTarget],
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        del sport, markets, regions
        self.calls.append(list(fixtures))
        self.requests_made += 1
        if self.failure is not None:
            raise self.failure
        requested = {
            (target.home_team, target.away_team, target.kickoff) for target in fixtures
        }
        return [
            event
            for event in self.events
            if (event.home_team, event.away_team, event.commence_time) in requested
        ]

    def stats(self) -> dict[str, int]:
        return {"requests_made": self.requests_made}


def _target(seed: int, home: str, away: str) -> ProviderOddsTarget:
    return ProviderOddsTarget(
        fixture_id=UUID(int=seed),
        home_team=home,
        away_team=away,
        kickoff=KICKOFF,
        sport_key="soccer_epl",
    )


def _event(source: str, event_id: str, home: str, away: str) -> ProviderFixtureOdds:
    return ProviderFixtureOdds(
        source=source,
        provider_id=event_id,
        commence_time=KICKOFF,
        home_team=home,
        away_team=away,
        bookmakers=[
            BookmakerMarket(
                bookmaker_key="bet365",
                bookmaker_title="Bet365",
                market="h2h",
                last_update=KICKOFF,
                outcomes=[
                    OddsOutcome(name=home, price=2.0),
                    OddsOutcome(name="Draw", price=3.2),
                    OddsOutcome(name=away, price=3.8),
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_primary_exception_falls_back_for_all_targets() -> None:
    targets = [_target(1, "Alpha", "Beta"), _target(2, "Gamma", "Delta")]
    primary = _TargetedProvider(failure=OddsProviderError("upstream failed"))
    fallback = _TargetedProvider(
        [_event("the-odds-api", "fallback-1", "Alpha", "Beta")]
    )
    provider = PrioritizedOddsProvider(primary=primary, fallback=fallback)  # type: ignore[arg-type]

    events = await provider.get_odds_for_fixtures(sport="football", fixtures=targets)
    coverage = provider.pop_coverage_stats()

    assert [event.provider_id for event in events] == ["fallback-1"]
    assert fallback.calls == [targets]
    assert coverage["fallback_attempts"] == 1
    assert coverage["fallback_successes"] == 1
    assert coverage["unmatched_reason_counts"]["NO_ODDS_PROVIDER_ERROR"] == 2


@pytest.mark.asyncio
async def test_primary_empty_falls_back_for_all_targets() -> None:
    targets = [_target(1, "Alpha", "Beta")]
    primary = _TargetedProvider()
    fallback = _TargetedProvider(
        [_event("the-odds-api", "fallback-1", "Alpha", "Beta")]
    )
    provider = PrioritizedOddsProvider(primary=primary, fallback=fallback)  # type: ignore[arg-type]

    events = await provider.get_odds_for_fixtures(sport="football", fixtures=targets)
    coverage = provider.pop_coverage_stats()

    assert [event.provider_id for event in events] == ["fallback-1"]
    assert fallback.calls == [targets]
    assert coverage["primary_empty_events"] == 1
    assert coverage["combined_odds_coverage"] == 1.0


@pytest.mark.asyncio
async def test_primary_event_without_usable_odds_falls_back() -> None:
    target = _target(1, "Alpha", "Beta")
    empty_event = ProviderFixtureOdds(
        source="odds-api.io",
        provider_id="primary-empty",
        commence_time=KICKOFF,
        home_team="Alpha",
        away_team="Beta",
    )
    primary = _TargetedProvider([empty_event])
    fallback = _TargetedProvider(
        [_event("the-odds-api", "fallback-1", "Alpha", "Beta")]
    )
    provider = PrioritizedOddsProvider(primary=primary, fallback=fallback)  # type: ignore[arg-type]

    events = await provider.get_odds_for_fixtures(
        sport="football", fixtures=[target]
    )
    coverage = provider.pop_coverage_stats()

    assert fallback.calls == [[target]]
    assert [event.provider_id for event in events] == [
        "primary-empty",
        "fallback-1",
    ]
    assert coverage["primary_empty_events"] == 1
    assert coverage["fallback_successes"] == 1


@pytest.mark.asyncio
async def test_primary_partial_falls_back_only_for_missing_target() -> None:
    first = _target(1, "Alpha", "Beta")
    missing = _target(2, "Gamma", "Delta")
    primary = _TargetedProvider(
        [_event("odds-api.io", "primary-1", "Alpha", "Beta")]
    )
    fallback = _TargetedProvider(
        [_event("the-odds-api", "fallback-2", "Gamma", "Delta")]
    )
    provider = PrioritizedOddsProvider(primary=primary, fallback=fallback)  # type: ignore[arg-type]

    events = await provider.get_odds_for_fixtures(
        sport="football", fixtures=[first, missing]
    )
    coverage = provider.pop_coverage_stats()

    assert [(event.source, event.provider_id) for event in events] == [
        ("odds-api.io", "primary-1"),
        ("the-odds-api", "fallback-2"),
    ]
    assert fallback.calls == [[missing]]
    assert coverage["primary_empty_events"] == 1
    assert coverage["fallback_events_returned"] == 1
    assert coverage["combined_odds_coverage"] == 1.0


@pytest.mark.asyncio
async def test_primary_full_coverage_does_not_call_fallback() -> None:
    targets = [_target(1, "Alpha", "Beta")]
    primary = _TargetedProvider(
        [_event("odds-api.io", "primary-1", "Alpha", "Beta")]
    )
    fallback = _TargetedProvider()
    provider = PrioritizedOddsProvider(primary=primary, fallback=fallback)  # type: ignore[arg-type]

    events = await provider.get_odds_for_fixtures(sport="football", fixtures=targets)
    coverage = provider.pop_coverage_stats()

    assert [event.provider_id for event in events] == ["primary-1"]
    assert fallback.calls == []
    assert coverage["fallback_attempts"] == 0
    assert coverage["combined_odds_coverage"] == 1.0


@pytest.mark.asyncio
async def test_provider_scoped_event_ids_are_not_collapsed() -> None:
    target = _target(1, "Alpha", "Beta")
    primary_event = _event("odds-api.io", "shared-id", "Alpha", "Beta")
    fallback_event = _event("the-odds-api", "shared-id", "Alpha", "Beta")

    merged = PrioritizedOddsProvider._merge_provider_events(
        [primary_event], [fallback_event]
    )

    assert [(event.source, event.provider_id) for event in merged] == [
        ("odds-api.io", "shared-id"),
        ("the-odds-api", "shared-id"),
    ]
    assert target.fixture_id == UUID(int=1)
