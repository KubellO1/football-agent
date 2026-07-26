"""Prioritised odds provider: primary → fallback chain.

Tries the primary provider first; on failure falls back to the secondary,
recording the exact failure reason. All providers expose the same
:class:`OddsProvider` interface so downstream code (EV, Kelly, Gate,
prediction logging, ROI, Dashboard) sees a unified response shape regardless
of which provider ultimately served the data.
"""

from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger
from app.providers.impl.odds_api_io_provider import (
    FAILURE_REASON,
    OddsApiIoProvider,
    OddsAuthError,
    OddsProviderError,
    OddsRateLimitError,
)
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import ProviderFixtureOdds

logger = get_logger(__name__)


class PrioritizedOddsProvider(OddsProvider):
    """Composite odds provider that tries primary → fallback.

    Failure details are logged and surfaced so that ``prediction_logger`` can
    assign the correct ``NO_ODDS_*`` subtype.
    """

    def __init__(self, *, primary: OddsApiIoProvider, fallback: OddsProvider) -> None:
        self._primary = primary
        self._fallback = fallback
        self._errors: dict[str, int] = {}

    async def aclose(self) -> None:
        """Close the underlying HTTP clients of both providers."""
        for prov in (self._primary, self._fallback):
            if hasattr(prov, "aclose"):
                await prov.aclose()

    # -- public ------------------------------------------------------------

    async def get_odds(
        self,
        *,
        sport: str,
        markets: str = "1x2",
        regions: str = "eu",
    ) -> list[ProviderFixtureOdds]:
        results, failure = await self._try_provider(
            self._primary, "primary (Odds-API.io)", sport, markets, regions
        )
        if failure is None:
            self._errors.clear()
            return results

        # Record failure reason in the current context for prediction_logger
        _current_failure_reason = failure

        logger.warning(
            "Odds-API.io failed for sport '%s' (%s) — falling back to The Odds API",
            sport,
            failure,
        )
        fallback_results, fallback_failure = await self._try_provider(
            self._fallback, "fallback (The Odds API)", sport, markets, regions
        )
        if fallback_failure is not None:
            logger.error(
                "Both odds providers failed for sport '%s': primary=%s, fallback=%s",
                sport,
                failure,
                fallback_failure,
            )
            # Surface the combined failure
            raise OddsProviderError(
                f"All odds providers failed for sport '{sport}': "
                f"primary={failure}, fallback={fallback_failure}"
            )
        self._errors.clear()
        return fallback_results

    async def get_historical_odds(
        self,
        *,
        sport: str,
        at: datetime,
        markets: str = "1x2",
        regions: str = "eu",
    ) -> list[ProviderFixtureOdds]:
        results, failure = await self._try_provider_historical(
            self._primary, "primary (Odds-API.io)", sport, at, markets, regions
        )
        if failure is None:
            self._errors.clear()
            return results

        logger.warning(
            "Odds-API.io historical failed for sport '%s' (%s) — falling back",
            sport,
            failure,
        )
        fallback_results, fallback_failure = await self._try_provider_historical(
            self._fallback, "fallback (The Odds API)", sport, at, markets, regions
        )
        if fallback_failure is not None:
            raise OddsProviderError(
                f"All providers failed for historical odds (sport='{sport}')"
            )
        return fallback_results

    # -- internal ----------------------------------------------------------

    async def _try_provider(
        self,
        provider: OddsProvider,
        label: str,
        sport: str,
        markets: str,
        regions: str,
    ) -> tuple[list[ProviderFixtureOdds], str | None]:
        """Attempt :meth:`get_odds` on *provider*. Returns ``(results, None)`` on
        success, or ``([], failure_reason)`` on failure."""
        try:
            results = await provider.get_odds(sport=sport, markets=markets, regions=regions)
            return results, None
        except OddsRateLimitError:
            self._errors[label] = self._errors.get(label, 0) + 1
            return [], FAILURE_REASON["RATE_LIMIT"]
        except OddsAuthError:
            self._errors[label] = self._errors.get(label, 0) + 1
            return [], FAILURE_REASON["AUTH"]
        except OddsProviderError as exc:
            self._errors[label] = self._errors.get(label, 0) + 1
            err = str(exc).lower()
            if "event_not_found" in err or "no event found" in err:
                return [], FAILURE_REASON["EVENT_NOT_FOUND"]
            if "market_not_found" in err:
                return [], FAILURE_REASON["MARKET_NOT_FOUND"]
            if "mapping" in err:
                return [], FAILURE_REASON["MAPPING_FAILED"]
            return [], FAILURE_REASON["PROVIDER_ERROR"]
        except Exception:
            self._errors[label] = self._errors.get(label, 0) + 1
            logger.exception("%s provider failed for sport '%s'", label, sport)
            return [], FAILURE_REASON["PROVIDER_ERROR"]

    async def _try_provider_historical(
        self,
        provider: OddsProvider,
        label: str,
        sport: str,
        at: datetime,
        markets: str,
        regions: str,
    ) -> tuple[list[ProviderFixtureOdds], str | None]:
        """Same as :meth:`_try_provider` but for :meth:`get_historical_odds`."""
        try:
            results = await provider.get_historical_odds(
                sport=sport, at=at, markets=markets, regions=regions
            )
            return results, None
        except OddsRateLimitError:
            self._errors[label] = self._errors.get(label, 0) + 1
            return [], FAILURE_REASON["RATE_LIMIT"]
        except OddsAuthError:
            self._errors[label] = self._errors.get(label, 0) + 1
            return [], FAILURE_REASON["AUTH"]
        except OddsProviderError:
            self._errors[label] = self._errors.get(label, 0) + 1
            return [], FAILURE_REASON["PROVIDER_ERROR"]
        except Exception:
            self._errors[label] = self._errors.get(label, 0) + 1
            logger.exception("%s historical provider failed for sport '%s'", label, sport)
            return [], FAILURE_REASON["PROVIDER_ERROR"]

    def pop_errors(self) -> dict[str, int]:
        """Return and clear per-source error counts."""
        errors = dict(self._errors)
        self._errors.clear()
        return errors
