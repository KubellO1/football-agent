"""Concrete provider implementations (infrastructure)."""

from __future__ import annotations

from app.providers.impl.api_football_provider import ApiFootballProvider
from app.providers.impl.odds_api_provider import TheOddsApiProvider

__all__ = ["ApiFootballProvider", "TheOddsApiProvider"]
