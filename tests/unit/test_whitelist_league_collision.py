"""Test whitelist league_id exact matching prevents cross-league collisions.

Verifies that:
- England Premier League (league_id=39) is matched, not Belarus PL
- Italy Serie A (league_id=135) is matched, not Brazil Serie A
- Fallback name+country matching works when league_id is None
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config.whitelist import (
    CompetitionWhitelist,
    _normalize,
    clear_whitelist_cache,
)


# ---------------------------------------------------------------------------
# Shared test data — minimal whitelist with collision-prone names
# ---------------------------------------------------------------------------

def _make_test_data() -> dict[str, Any]:
    return {
        "filter_mode": "whitelist",
        "competitions": {
            "europe_top": [
                {
                    "name": "English Premier League",
                    "sport_keys": ["soccer_epl"],
                    "match_names": ["EPL", "English Premier League", "Premier League"],
                    "api_football_league_id": 39,
                    "country": "England",
                    "enabled": True,
                },
                {
                    "name": "Italian Serie A",
                    "sport_keys": ["soccer_italy_serie_a"],
                    "match_names": ["Serie A TIM", "Italian Serie A", "Serie A"],
                    "api_football_league_id": 135,
                    "country": "Italy",
                    "enabled": True,
                },
            ],
            "other": [
                {
                    # Simulates: Belarus Premier League → same name, different league_id
                    "name": "Belarus Premier League",
                    "sport_keys": [],
                    "match_names": [
                        "Belarusian Premier League",
                        "Vysheyshaya Liga",
                    ],
                    "enabled": False,
                },
                {
                    # Simulates: Brazil Serie A → same name, different league_id
                    "name": "Brazilian Serie A",
                    "sport_keys": [],
                    "match_names": [
                        "Brasileirão",
                        "Campeonato Brasileiro Série A",
                    ],
                    "enabled": False,
                },
            ],
        },
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the singleton between tests."""
    clear_whitelist_cache()
    yield
    clear_whitelist_cache()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLeagueIdExactMatch:
    """league_id exact match is the strongest signal, bypassing name collisions."""

    def test_england_premier_league_passes(self):
        """league_id=39 → English Premier League (should ALWAYS pass)."""
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.is_allowed("Premier League", league_id=39, country="England") is True

    def test_england_premier_league_by_id_only(self):
        """league_id=39 alone passes even with empty/missing name."""
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.is_allowed("", league_id=39) is True
        assert wl.is_allowed("Some Random Name", league_id=39) is True

    def test_belarus_premier_league_rejected(self):
        """league_id=999 + name='Premier League' → REJECTED (not England PL)."""
        wl = CompetitionWhitelist(_make_test_data())
        assert (
            wl.is_allowed("Premier League", league_id=999, country="Belarus")
            is False
        )

    def test_italy_serie_a_passes(self):
        """league_id=135 → Italy Serie A (should ALWAYS pass)."""
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.is_allowed("Serie A", league_id=135, country="Italy") is True

    def test_brazil_serie_a_rejected(self):
        """league_id=777 + name='Serie A' → REJECTED (not Italy Serie A)."""
        wl = CompetitionWhitelist(_make_test_data())
        assert (
            wl.is_allowed("Serie A", league_id=777, country="Brazil")
            is False
        )

    def test_unknown_league_id_rejected(self):
        """A league_id not in any whitelist entry → False even with matching name."""
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.is_allowed("Premier League", league_id=98765) is False


class TestNameAndCountryFallback:
    """When league_id is None, fallback to name + country matching."""

    def test_missing_league_id_fallback_to_name_country(self):
        """league_id=None, name='Premier League', country='England' → PASS."""
        wl = CompetitionWhitelist(_make_test_data())
        assert (
            wl.is_allowed("Premier League", league_id=None, country="England")
            is True
        )

    def test_missing_league_id_wrong_country_rejected(self):
        """league_id=None, name='Premier League', country='Belarus' → FAIL."""
        wl = CompetitionWhitelist(_make_test_data())
        # "Premier League" matches match_names in EPL, but country="Belarus" != "England"
        assert (
            wl.is_allowed("Premier League", league_id=None, country="Belarus")
            is False
        )

    def test_missing_league_id_no_country_fallback_to_name_only(self):
        """league_id=None, no country → name-only match (backward compat)."""
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.is_allowed("Premier League") is True
        assert wl.is_allowed("EPL") is True
        assert wl.is_allowed("Serie A") is True
        assert wl.is_allowed("Serie A TIM") is True


class TestEntryResolution:
    """get_entry returns correct WhitelistEntry."""

    def test_get_entry_by_league_id(self):
        wl = CompetitionWhitelist(_make_test_data())
        entry = wl.get_entry("Premier League", league_id=39)
        assert entry is not None
        assert entry.name == "English Premier League"
        assert entry.api_football_league_id == 39
        assert entry.country == "England"

    def test_get_entry_by_league_id_ignores_name_for_id_match(self):
        wl = CompetitionWhitelist(_make_test_data())
        entry = wl.get_entry("Serie A", league_id=135)
        assert entry is not None
        assert entry.name == "Italian Serie A"

    def test_get_entry_none_for_unknown_league_id(self):
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.get_entry("Serie A", league_id=999) is None


class TestSportKeyResolution:
    """get_sport_key_for returns correct sport_key."""

    def test_epl_sport_key(self):
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.get_sport_key_for("Premier League", league_id=39) == "soccer_epl"

    def test_serie_a_sport_key(self):
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.get_sport_key_for("Serie A", league_id=135) == "soccer_italy_serie_a"


class TestNormalization:
    """Normalization is case-insensitive and diacritic-insensitive."""

    def test_normalize_lowercase(self):
        assert _normalize("PReMiEr LeAgUe") == _normalize("premier league")

    def test_normalize_whitespace(self):
        assert _normalize("  Serie   A  ") == "serie a"

    def test_normalize_diacritics(self):
        assert _normalize("Süper Lig") == _normalize("Super Lig")
        assert _normalize("Brasileirão") == _normalize("Brasileirao")


class TestBackwardCompatibility:
    """Existing callers without league_id/country still work."""

    def test_old_style_call_still_works(self):
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.is_allowed("Premier League") is True
        assert wl.is_allowed("Serie A") is True
        assert wl.is_allowed("NonExistent League") is False

    def test_old_style_get_sport_key(self):
        wl = CompetitionWhitelist(_make_test_data())
        assert wl.get_sport_key_for("Premier League") == "soccer_epl"
