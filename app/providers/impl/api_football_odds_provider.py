"""Targeted API-Football named-bookmaker pre-match odds provider."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.core.exceptions import ExternalServiceError
from app.providers.api_football_http import ApiFootballHTTPProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import (
    BookmakerMarket,
    OddsOutcome,
    ProviderFixtureOdds,
    ProviderOddsTarget,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from app.providers.api_football_rate_limit import ApiFootballRequestLimiter

SOURCE_API_FOOTBALL = "api-football"
_H2H = "h2h"
_MATCH_WINNER_BET_ID = 1


class ApiFootballOddsProvider(ApiFootballHTTPProvider, OddsProvider):
    """Read only explicitly targeted 1X2 odds from API-Football."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        bookmakers: Sequence[str],
        client: httpx.AsyncClient | None = None,
        rate_limiter: ApiFootballRequestLimiter | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            headers={"x-apisports-key": api_key},
            client=client,
            rate_limiter=rate_limiter,
        )
        self._bookmakers = {name.strip().casefold() for name in bookmakers if name.strip()}
        if not self._bookmakers:
            raise ValueError("at least one API-Football bookmaker is required")
        self._requests_made = 0
        self._errors: dict[str, int] = {}
        self._coverage: dict[str, Any] = {}

    async def get_odds(
        self,
        *,
        sport: str,
        markets: Sequence[str] = (_H2H,),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Reject broad scans; production queries must name approved fixtures."""
        raise ExternalServiceError("API_FOOTBALL_ODDS_REQUIRES_FIXTURE_TARGETS")

    async def get_odds_for_fixtures(
        self,
        *,
        sport: str,
        fixtures: Sequence[ProviderOddsTarget],
        markets: Sequence[str] = (_H2H,),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        del sport, regions
        if set(markets) != {_H2H}:
            raise ExternalServiceError("API_FOOTBALL_ODDS_SUPPORTS_H2H_ONLY")

        self._errors.clear()
        reasons: dict[str, int] = {}
        events: list[ProviderFixtureOdds] = []
        requests_before = self._requests_made
        targeted = 0
        empty = 0

        for target in fixtures:
            provider_id = (target.provider_fixture_id or "").strip()
            if not provider_id:
                self._increment(reasons, "MISSING_PROVIDER_EVENT")
                continue
            targeted += 1
            self._requests_made += 1
            try:
                payload = await self._get_json(
                    "/odds",
                    params={"fixture": provider_id, "bet": _MATCH_WINNER_BET_ID},
                )
                event = self._parse_targeted_payload(payload, target=target)
            except ExternalServiceError:
                self._increment(self._errors, SOURCE_API_FOOTBALL)
                self._increment(reasons, "API_FOOTBALL_ERROR")
                continue
            if event is None:
                empty += 1
                self._increment(reasons, "NO_BOOKMAKER_ODDS")
                continue
            events.append(event)

        logical_requests = self._requests_made - requests_before
        self._coverage = {
            "targeted_fixtures": targeted,
            "primary_requests": logical_requests,
            "primary_events_returned": len(events),
            "primary_empty_events": empty,
            "fallback_attempts": 0,
            "fallback_requests": 0,
            "fallback_successes": 0,
            "fallback_events_returned": 0,
            "combined_odds_coverage": len(events) / len(fixtures) if fixtures else 0.0,
            "unmatched_reason_counts": reasons,
        }
        return events

    def _parse_targeted_payload(
        self,
        payload: Any,
        *,
        target: ProviderOddsTarget,
    ) -> ProviderFixtureOdds | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), list):
            raise ExternalServiceError("API_FOOTBALL_ODDS_INVALID_PAYLOAD")
        provider_id = (target.provider_fixture_id or "").strip()
        for raw_event in payload["response"]:
            if not isinstance(raw_event, dict):
                continue
            fixture = raw_event.get("fixture")
            if not isinstance(fixture, dict) or str(fixture.get("id")) != provider_id:
                continue
            event_update = self._parse_datetime(raw_event.get("update"))
            markets = self._parse_bookmakers(
                raw_event.get("bookmakers"),
                target=target,
                event_update=event_update,
            )
            if not markets:
                return None
            commence_time = self._parse_datetime(fixture.get("date")) or target.kickoff
            return ProviderFixtureOdds(
                provider_id=provider_id,
                commence_time=commence_time,
                home_team=target.home_team,
                away_team=target.away_team,
                sport_key=target.sport_key,
                bookmakers=markets,
                source=SOURCE_API_FOOTBALL,
            )
        return None

    def _parse_bookmakers(
        self,
        raw_bookmakers: Any,
        *,
        target: ProviderOddsTarget,
        event_update: datetime | None,
    ) -> list[BookmakerMarket]:
        if not isinstance(raw_bookmakers, list):
            return []
        parsed: list[BookmakerMarket] = []
        for raw_bookmaker in raw_bookmakers:
            if not isinstance(raw_bookmaker, dict):
                continue
            title = str(raw_bookmaker.get("name") or "").strip()
            if title.casefold() not in self._bookmakers:
                continue
            last_update = self._parse_datetime(raw_bookmaker.get("update")) or event_update
            if last_update is None:
                continue
            bets = raw_bookmaker.get("bets")
            if not isinstance(bets, list):
                continue
            for bet in bets:
                if not isinstance(bet, dict):
                    continue
                name = str(bet.get("name") or "").strip().casefold()
                if bet.get("id") != _MATCH_WINNER_BET_ID and name != "match winner":
                    continue
                outcomes = self._parse_outcomes(bet.get("values"), target=target)
                if len(outcomes) != 3:
                    continue
                key = re.sub(r"[^a-z0-9]+", "", title.casefold())
                parsed.append(
                    BookmakerMarket(
                        bookmaker_key=key,
                        bookmaker_title=title,
                        market=_H2H,
                        last_update=last_update,
                        outcomes=outcomes,
                    )
                )
        return parsed

    @staticmethod
    def _parse_outcomes(
        raw_values: Any,
        *,
        target: ProviderOddsTarget,
    ) -> list[OddsOutcome]:
        if not isinstance(raw_values, list):
            return []
        names = {
            "home": target.home_team,
            "draw": "Draw",
            "away": target.away_team,
        }
        prices: dict[str, float] = {}
        for item in raw_values:
            if not isinstance(item, dict):
                continue
            label = str(item.get("value") or "").strip().casefold()
            if label not in names:
                continue
            raw_price = item.get("odd")
            if isinstance(raw_price, bool) or not isinstance(raw_price, (str, int, float)):
                continue
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if math.isfinite(price) and price > 1.0:
                prices[label] = price
        if set(prices) != set(names):
            return []
        return [OddsOutcome(name=names[label], price=prices[label]) for label in names]

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    @staticmethod
    def _increment(values: dict[str, int], key: str) -> None:
        values[key] = values.get(key, 0) + 1

    def stats(self) -> dict[str, int]:
        return {"requests_made": self._requests_made}

    def pop_errors(self) -> dict[str, int]:
        errors = dict(self._errors)
        self._errors.clear()
        return errors

    def pop_coverage_stats(self) -> dict[str, Any]:
        coverage = dict(self._coverage)
        self._coverage.clear()
        return coverage
