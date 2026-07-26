# DEPRECATED: 2026-07-17 - Removed from production. Retained for reference.
"""Abstract contract for Sportmonks data feed.

Services depend on ``SportmonksProvider``, never on a concrete vendor client.
Sportmonks provides predictions, statistics, transfers, odds (secondary source),
lineups, injuries, recent form, standings, match centre, and TV stations.

Boundary: Sportmonks is an enhancement provider. It does NOT replace:
- API-Football (fixtures, statistics, competitions)
- The Odds API (bookmaker odds, market movement)
- WeatherAPI (weather)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.providers.schemas.sportmonks import (
    InjuryReport,
    LineupReport,
    MatchCentreData,
    RecentForm,
    SportmonksFixturePredictions,
    SportmonksOdds,
    SportmonksTeamStats,
    SportmonksTransfer,
    StandingsTable,
    TVStation,
)


class SportmonksProvider(ABC):
    """Read-only access to Sportmonks v3 football data.

    Primary role: explanatory/enhancement signals for the Dashboard.
    Does NOT drive model weights, Kelly, thresholds, or decision rules.
    """

    @abstractmethod
    async def get_predictions(
        self,
        *,
        season_id: int,
    ) -> list[SportmonksFixturePredictions]:
        """Return predictions for all fixtures in a season.

        Uses fixtures endpoint with includes=predictions;predictions.type
        because the dedicated predictions/probabilities endpoint lacks
        fixtureSeasons filter (returns old fixtures sorted by ID).
        """
        raise NotImplementedError

    @abstractmethod
    async def get_team_statistics(
        self,
        *,
        fixture_id: int,
    ) -> list[SportmonksTeamStats]:
        """Return team-level statistics for a fixture.

        Note: xG is NOT available — use API-Football for xG data.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_transfers(
        self,
        *,
        season_id: int,
    ) -> list[SportmonksTransfer]:
        """Return transfers for a season (camelCase includes)."""
        raise NotImplementedError

    @abstractmethod
    async def get_odds(
        self,
        *,
        fixture_id: int,
    ) -> list[SportmonksOdds]:
        """Return odds for a fixture (888Sport/Dafabet only — secondary source)."""
        raise NotImplementedError

    # ── Phase 1: Enhancement methods ──

    @abstractmethod
    async def get_lineups(
        self,
        *,
        fixture_id: int,
    ) -> LineupReport | None:
        """Return predicted/confirmed lineups for a fixture.

        Uses fixtures/{id}?includes=lineups.player.
        Returns None when lineup data is unavailable.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_sidelined(
        self,
        *,
        fixture_id: int,
    ) -> InjuryReport | None:
        """Return injuries & suspensions for a fixture.

        Uses fixtures/{id}?includes=sidelined.player.
        EXPLANATORY SIGNAL ONLY — do not feed into models/weights.
        Returns None when no sidelined data.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_team_recent(
        self,
        *,
        team_id: int,
    ) -> RecentForm | None:
        """Return last 5 match results for a team.

        Uses teams/{id}?includes=latest.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_standings(
        self,
        *,
        season_id: int,
    ) -> StandingsTable | None:
        """Return league standings for a season.

        Uses standings/seasons/{id}.
        Returns None when standings data is unavailable.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_match_centre(
        self,
        *,
        fixture_id: int,
    ) -> MatchCentreData | None:
        """Return combined events, timeline, and statistics for a fixture.

        Uses fixtures/{id}?includes=events;timeline;statistics.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_tv_stations(
        self,
        *,
        fixture_id: int,
    ) -> list[TVStation]:
        """Return TV stations broadcasting a fixture.

        Uses tv-stations/fixtures/{id}.
        """
        raise NotImplementedError
