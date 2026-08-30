"""Prioritized current-odds provider with targeted, partial fallback."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.providers.impl.odds_api_io_provider import (
    FAILURE_REASON,
    OddsApiIoProvider,
    OddsAuthError,
    OddsProviderError,
    OddsRateLimitError,
)
from app.providers.interfaces.odds_provider import OddsProvider
from app.services.odds_matching import (
    MatchCandidate,
    MatchOutcome,
    match_event,
    normalize_team_name,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from app.providers.schemas.odds import ProviderFixtureOdds, ProviderOddsTarget

logger = get_logger(__name__)


class PrioritizedOddsProvider(OddsProvider):
    """Use Odds-API.io first and The Odds API only for missing targets."""

    def __init__(self, *, primary: OddsApiIoProvider, fallback: OddsProvider) -> None:
        self._primary = primary
        self._fallback = fallback
        self._errors: dict[str, int] = {}
        self._coverage: dict[str, Any] = {}

    async def aclose(self) -> None:
        """Close both underlying provider clients."""
        for provider in (self._primary, self._fallback):
            if hasattr(provider, "aclose"):
                await provider.aclose()

    async def get_odds(
        self,
        *,
        sport: str,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        results, failure = await self._try_provider(
            self._primary, "primary (Odds-API.io)", sport, markets, regions
        )
        if failure is None:
            self._errors.clear()
            return results

        logger.warning(
            "Odds-API.io failed for sport '%s' (%s); falling back to The Odds API",
            sport,
            failure,
        )
        fallback_results, fallback_failure = await self._try_provider(
            self._fallback, "fallback (The Odds API)", sport, markets, regions
        )
        if fallback_failure is not None:
            raise OddsProviderError(
                f"All odds providers failed for sport '{sport}': "
                f"primary={failure}, fallback={fallback_failure}"
            )
        return fallback_results

    async def get_historical_odds(
        self,
        *,
        sport: str,
        at: datetime,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        results, failure = await self._try_provider_historical(
            self._primary, "primary (Odds-API.io)", sport, at, markets, regions
        )
        if failure is None:
            self._errors.clear()
            return results

        fallback_results, fallback_failure = await self._try_provider_historical(
            self._fallback, "fallback (The Odds API)", sport, at, markets, regions
        )
        if fallback_failure is not None:
            raise OddsProviderError(f"All providers failed for historical odds (sport='{sport}')")
        return fallback_results

    async def get_odds_for_fixtures(
        self,
        *,
        sport: str,
        fixtures: Sequence[ProviderOddsTarget],
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Fallback on primary exception, empty response, or partial coverage."""
        targets = list(fixtures)
        self._errors.clear()
        self._coverage = self._empty_coverage(len(targets))
        if not targets:
            return []

        primary_before = self._request_count(self._primary)
        primary_results, primary_failure = await self._try_provider_for_fixtures(
            self._primary,
            "primary (Odds-API.io)",
            sport,
            targets,
            markets,
            regions,
        )
        self._coverage["primary_requests"] = max(
            0, self._request_count(self._primary) - primary_before
        )
        self._coverage["primary_events_returned"] = len(primary_results)

        primary_covered = self._matched_fixture_ids(primary_results, targets)
        missing = [target for target in targets if target.fixture_id not in primary_covered]
        self._coverage["primary_empty_events"] = len(missing)
        reason_counts: dict[str, int] = {}
        if missing:
            reason_counts["PRIMARY_EMPTY"] = len(missing)

        fallback_results: list[ProviderFixtureOdds] = []
        fallback_failure: str | None = None
        if missing:
            self._coverage["fallback_attempts"] = 1
            fallback_before = self._request_count(self._fallback)
            fallback_results, fallback_failure = await self._try_provider_for_fixtures(
                self._fallback,
                "fallback (The Odds API)",
                sport,
                missing,
                markets,
                regions,
            )
            self._coverage["fallback_requests"] = max(
                0, self._request_count(self._fallback) - fallback_before
            )
            self._coverage["fallback_events_returned"] = len(fallback_results)

        fallback_covered = self._matched_fixture_ids(fallback_results, missing)
        self._coverage["fallback_successes"] = len(fallback_covered)
        still_missing = [target for target in missing if target.fixture_id not in fallback_covered]
        if still_missing:
            reason_counts["NO_BOOKMAKER_ODDS"] = len(still_missing)
        if primary_failure is not None:
            reason_counts[primary_failure] = len(targets)
        if fallback_failure is not None:
            reason_counts[fallback_failure] = len(missing)

        combined = self._merge_provider_events(primary_results, fallback_results)
        combined_covered = self._matched_fixture_ids(combined, targets)
        self._coverage["combined_odds_coverage"] = len(combined_covered) / len(targets)
        self._coverage["unmatched_reason_counts"] = reason_counts

        logger.info(
            "Dual-provider targeted odds: targets=%d primary_events=%d missing=%d "
            "fallback_events=%d combined_covered=%d",
            len(targets),
            len(primary_results),
            len(missing),
            len(fallback_results),
            len(combined_covered),
        )
        return combined

    async def _try_provider(
        self,
        provider: OddsProvider,
        label: str,
        sport: str,
        markets: Sequence[str],
        regions: Sequence[str],
    ) -> tuple[list[ProviderFixtureOdds], str | None]:
        try:
            return await provider.get_odds(sport=sport, markets=markets, regions=regions), None
        except Exception as exc:
            return [], self._classify_error(label, exc)

    async def _try_provider_historical(
        self,
        provider: OddsProvider,
        label: str,
        sport: str,
        at: datetime,
        markets: Sequence[str],
        regions: Sequence[str],
    ) -> tuple[list[ProviderFixtureOdds], str | None]:
        try:
            return (
                await provider.get_historical_odds(
                    sport=sport, at=at, markets=markets, regions=regions
                ),
                None,
            )
        except Exception as exc:
            return [], self._classify_error(label, exc)

    async def _try_provider_for_fixtures(
        self,
        provider: OddsProvider,
        label: str,
        sport: str,
        fixtures: Sequence[ProviderOddsTarget],
        markets: Sequence[str],
        regions: Sequence[str],
    ) -> tuple[list[ProviderFixtureOdds], str | None]:
        try:
            return (
                await provider.get_odds_for_fixtures(
                    sport=sport,
                    fixtures=fixtures,
                    markets=markets,
                    regions=regions,
                ),
                None,
            )
        except Exception as exc:
            logger.warning("%s targeted odds failed: %s", label, exc)
            return [], self._classify_error(label, exc)

    def _classify_error(self, label: str, exc: Exception) -> str:
        self._errors[label] = self._errors.get(label, 0) + 1
        if isinstance(exc, OddsRateLimitError):
            return FAILURE_REASON["RATE_LIMIT"]
        if isinstance(exc, OddsAuthError):
            return FAILURE_REASON["AUTH"]
        if isinstance(exc, OddsProviderError):
            lowered = str(exc).lower()
            if "event_not_found" in lowered or "no event found" in lowered:
                return FAILURE_REASON["EVENT_NOT_FOUND"]
            if "market_not_found" in lowered:
                return FAILURE_REASON["MARKET_NOT_FOUND"]
            if "mapping" in lowered:
                return FAILURE_REASON["MAPPING_FAILED"]
        return FAILURE_REASON["PROVIDER_ERROR"]

    @staticmethod
    def _matched_fixture_ids(
        events: Sequence[ProviderFixtureOdds],
        fixtures: Sequence[ProviderOddsTarget],
    ) -> set[object]:
        candidates = [
            MatchCandidate(
                fixture_id=target.fixture_id,
                home_norm=normalize_team_name(target.home_team),
                away_norm=normalize_team_name(target.away_team),
                kickoff=target.kickoff,
            )
            for target in fixtures
        ]
        covered: set[object] = set()
        for event in events:
            if not PrioritizedOddsProvider._event_has_usable_h2h(event):
                continue
            result = match_event(
                event_home=event.home_team,
                event_away=event.away_team,
                commence_time=event.commence_time,
                candidates=candidates,
                tolerance=timedelta(minutes=180),
            )
            if result.outcome is MatchOutcome.MATCHED and result.fixture_id is not None:
                covered.add(result.fixture_id)
        return covered

    @staticmethod
    def _event_has_usable_h2h(event: ProviderFixtureOdds) -> bool:
        """Require one complete, timestamped 1X2 quote before claiming coverage."""
        for market in event.bookmakers:
            if market.market != "h2h" or market.last_update is None:
                continue
            selections = {
                normalize_team_name(outcome.name)
                for outcome in market.outcomes
                if outcome.price > 1.0
            }
            required = {
                normalize_team_name(event.home_team),
                normalize_team_name(event.away_team),
                "draw",
            }
            if required <= selections:
                return True
        return False

    @staticmethod
    def _merge_provider_events(
        primary: Sequence[ProviderFixtureOdds],
        fallback: Sequence[ProviderFixtureOdds],
    ) -> list[ProviderFixtureOdds]:
        merged: list[ProviderFixtureOdds] = []
        seen: set[tuple[str, str]] = set()
        for event in (*primary, *fallback):
            key = (event.source, event.provider_id)
            if key not in seen:
                merged.append(event)
                seen.add(key)
        return merged

    @staticmethod
    def _request_count(provider: OddsProvider) -> int:
        stats: dict[str, Any] = getattr(provider, "stats", lambda: {})()
        value = stats.get("requests_made", 0) if isinstance(stats, dict) else 0
        return int(value)

    @staticmethod
    def _empty_coverage(targeted_fixtures: int) -> dict[str, Any]:
        return {
            "targeted_fixtures": targeted_fixtures,
            "primary_requests": 0,
            "primary_events_returned": 0,
            "primary_empty_events": 0,
            "fallback_attempts": 0,
            "fallback_requests": 0,
            "fallback_successes": 0,
            "fallback_events_returned": 0,
            "combined_odds_coverage": 0.0,
            "unmatched_reason_counts": {},
        }

    def pop_errors(self) -> dict[str, int]:
        """Return and clear per-source error counts."""
        errors = dict(self._errors)
        self._errors.clear()
        return errors

    def pop_coverage_stats(self) -> dict[str, Any]:
        """Return and clear actual dual-provider coverage metrics."""
        coverage = dict(self._coverage)
        self._coverage.clear()
        return coverage
