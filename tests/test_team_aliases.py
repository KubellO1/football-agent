"""Unit tests for team_aliases — verify all alias groups normalize to the same
canonical form, and that the cross-data-source mapping is deterministic."""

import sys
from pathlib import Path

# Ensure project root is on sys.path so that app.* imports resolve.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.services.odds_matching import normalize_team_name
from app.services.team_aliases import _ALIAS_GROUPS, _build_lookup, accepted_names


class TestNormalizeTeamName:
    """normalize_team_name: conservative normalisation (lowercase, strip accents,
    strip punctuation, collapse whitespace)."""

    def test_accents_stripped(self):
        assert normalize_team_name("Côte d'Ivoire") == "cote d ivoire"

    def test_casing_normalised(self):
        assert normalize_team_name("USA") == "usa"
        assert normalize_team_name("Usa") == "usa"

    def test_punctuation_removed(self):
        assert normalize_team_name("Côte d'Ivoire") == "cote d ivoire"
        assert normalize_team_name("Bosnia-Herzegovina") == "bosnia herzegovina"
        assert normalize_team_name("Türkiye") == "turkiye"

    def test_whitespace_collapsed(self):
        assert normalize_team_name("  Bosnia   and  Herzegovina ") == "bosnia and herzegovina"


class TestAliasGroupIntegrity:
    """Each alias group must produce exactly one canonical normalised form."""

    def test_no_collisions_between_groups(self):
        """_build_lookup raises ValueError if the same norm appears in >1 group."""
        # _ALIAS_GROUPS is already loaded without error → no collisions.
        lookup = _build_lookup(_ALIAS_GROUPS)
        assert len(lookup) > 0

    def test_every_name_in_group_normalises_to_same_union(self):
        for group in _ALIAS_GROUPS:
            norms = {normalize_team_name(name) for name in group}
            for name in group:
                norm = normalize_team_name(name)
                canon = accepted_names(norm)
                # Every name in the group must expand to the same canonical set.
                expected = frozenset(norms)
                assert canon == expected, (
                    f"Group {group}: name '{name}' normalised to '{norm}' "
                    f"→ accepted={canon}, expected={expected}"
                )


class TestNewNationalTeamAliases:
    """Validate the 9 new national-team alias groups added for World Cup 2026."""

    # (group, input_variant, expected_canonical_key_member)
    NEW_GROUPS = [
        # United States
        (["United States", "USA", "USMNT"], "USA", "united states"),
        (["United States", "USA", "USMNT"], "United States", "united states"),
        (["United States", "USA", "USMNT"], "USMNT", "united states"),
        # Ivory Coast
        (["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"], "Côte d'Ivoire", "ivory coast"),
        (["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"], "Cote d'Ivoire", "ivory coast"),
        (["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"], "Ivory Coast", "ivory coast"),
        # DR Congo
        (["DR Congo", "Congo DR", "Democratic Republic of the Congo"],
         "DR Congo", "dr congo"),
        (["DR Congo", "Congo DR", "Democratic Republic of the Congo"],
         "Congo DR", "dr congo"),
        (["DR Congo", "Congo DR", "Democratic Republic of the Congo"],
         "Democratic Republic of the Congo", "dr congo"),
        # South Korea
        (["South Korea", "Korea Republic"], "South Korea", "south korea"),
        (["South Korea", "Korea Republic"], "Korea Republic", "south korea"),
        # Cape Verde
        (["Cape Verde", "Cabo Verde"], "Cape Verde", "cape verde"),
        (["Cape Verde", "Cabo Verde"], "Cabo Verde", "cape verde"),
        # Czechia
        (["Czechia", "Czech Republic"], "Czechia", "czechia"),
        (["Czechia", "Czech Republic"], "Czech Republic", "czechia"),
        # Bosnia-Herzegovina
        (["Bosnia-Herzegovina", "Bosnia and Herzegovina"],
         "Bosnia-Herzegovina", "bosnia herzegovina"),
        (["Bosnia-Herzegovina", "Bosnia and Herzegovina"],
         "Bosnia and Herzegovina", "bosnia herzegovina"),
        # Türkiye
        (["Türkiye", "Turkey"], "Türkiye", "turkiye"),
        (["Türkiye", "Turkey"], "Turkey", "turkiye"),
        # Iran
        (["Iran", "IR Iran"], "Iran", "iran"),
        (["Iran", "IR Iran"], "IR Iran", "iran"),
    ]

    @pytest.mark.parametrize("group,input_variant,expected_in", NEW_GROUPS)
    def test_alias_expands_to_expected_norm(self, group, input_variant, expected_in):
        norm = normalize_team_name(input_variant)
        canon = accepted_names(norm)
        # expected_in should be a canonical member of the alias set.
        assert expected_in in canon, (
            f"input '{input_variant}' → norm '{norm}' → accepted {canon}: "
            f"'{expected_in}' not found"
        )


class TestMatchEventWithAliases:
    """End-to-end: match_event resolves national-team aliases correctly."""

    def test_usa_via_alias(self):
        from app.services.odds_matching import match_event, MatchCandidate, MatchResult, MatchOutcome
        from datetime import datetime, timedelta

        from app.services.team_aliases import accepted_names

        kickoff = datetime(2026, 6, 14, 20, 0)
        candidates = [
            MatchCandidate(
                fixture_id="00000000-0000-0000-0000-000000000001",
                home_norm="united states",
                away_norm="england",
                kickoff=kickoff,
            ),
        ]
        result = match_event(
            event_home="USA",
            event_away="England",
            commence_time=kickoff,
            candidates=candidates,
            tolerance=timedelta(minutes=180),
            alias_names=accepted_names,
        )
        assert result.outcome == MatchOutcome.MATCHED
        assert result.fixture_id == "00000000-0000-0000-0000-000000000001"

    def test_cote_divoire_via_alias(self):
        from app.services.odds_matching import match_event, MatchCandidate
        from datetime import datetime, timedelta

        from app.services.team_aliases import accepted_names

        kickoff = datetime(2026, 6, 15, 16, 0)
        candidates = [
            MatchCandidate(
                fixture_id="00000000-0000-0000-0000-000000000002",
                home_norm="ivory coast",
                away_norm="brazil",
                kickoff=kickoff,
            ),
        ]
        result = match_event(
            event_home="Côte d'Ivoire",
            event_away="Brazil",
            commence_time=kickoff,
            candidates=candidates,
            tolerance=timedelta(minutes=180),
            alias_names=accepted_names,
        )
        assert result.outcome == "matched"

    def test_exact_match_still_works_without_aliases(self):
        from app.services.odds_matching import match_event, MatchCandidate
        from datetime import datetime, timedelta

        kickoff = datetime(2026, 6, 14, 20, 0)
        candidates = [
            MatchCandidate(
                fixture_id="00000000-0000-0000-0000-000000000003",
                home_norm="norway",
                away_norm="england",
                kickoff=kickoff,
            ),
        ]
        result = match_event(
            event_home="Norway",
            event_away="England",
            commence_time=kickoff,
            candidates=candidates,
            tolerance=timedelta(minutes=180),
            alias_names=None,  # no aliases
        )
        assert result.outcome == "matched"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
