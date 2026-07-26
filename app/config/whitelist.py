"""Production competition whitelist loader and filter.

Loads ``production_whitelist.json`` from project root and provides a fast
normalized lookup to decide whether a competition (identified by its
API-Football league name) should be processed or skipped.

Only whitelisted competitions consume API/LLM quota, appear on the
Dashboard, and generate BET/WATCH/NO BET predictions. All others are
logged as SKIPPED_UNSUPPORTED_COMPETITION and silently skipped.

Usage::

    from app.config.whitelist import get_whitelist

    whitelist = get_whitelist()
    if not whitelist.is_allowed("Premier League"):
        # log SKIPPED_UNSUPPORTED_COMPETITION, skip fixture
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Cached at project root alongside .env
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WHITELIST_PATH = PROJECT_ROOT / "production_whitelist.json"

# Global cache — loaded once at first access
_whitelist: CompetitionWhitelist | None = None


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
def _normalize(name: str) -> str:
    """Normalise a competition name for robust matching.

    - lowercase
    - strip accents / diacritics (NFKD → drop combining chars)
    - collapse all whitespace to single spaces
    - strip leading/trailing whitespace
    """
    n = name.lower().strip()
    n = unicodedata.normalize("NFKD", n)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = " ".join(n.split())
    return n


# ---------------------------------------------------------------------------
# Data class per whitelist entry
# ---------------------------------------------------------------------------
class WhitelistEntry:
    """A single competition on the whitelist."""

    __slots__ = (
        "name", "sport_keys", "match_names_norm", "category",
        "enabled", "api_football_league_id", "country",
    )

    def __init__(
        self,
        name: str,
        sport_keys: list[str],
        match_names: list[str],
        category: str = "",
        enabled: bool = True,
        api_football_league_id: int | None = None,
        country: str | None = None,
    ) -> None:
        self.name = name
        self.sport_keys = sport_keys
        self.match_names_norm = frozenset(_normalize(m) for m in match_names)
        self.category = category
        self.enabled = enabled
        self.api_football_league_id = api_football_league_id
        self.country = country


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------
class CompetitionWhitelist:
    """Loaded production whitelist with fast-lookup structures."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._meta: dict[str, Any] = data.get("_meta", {})
        self._filter_mode: str = data.get("filter_mode", "whitelist")
        self._excluded_categories: dict[str, Any] = data.get("excluded_categories", {})

        self.entries: list[WhitelistEntry] = []
        self._norm_lookup: dict[str, WhitelistEntry] = {}
        self._league_id_lookup: dict[int, WhitelistEntry] = {}
        self._all_sport_keys: set[str] = set()

        # Track collisions for audit
        collisions: list[tuple[str, str, str]] = []  # (norm_name, existing_entry, new_entry)

        competitions = data.get("competitions", {})
        for category, entries in competitions.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                lid = entry.get("api_football_league_id")
                we = WhitelistEntry(
                    name=entry.get("name", ""),
                    sport_keys=entry.get("sport_keys", []),
                    match_names=entry.get("match_names", []),
                    category=category,
                    enabled=entry.get("enabled", True),
                    api_football_league_id=lid,
                    country=entry.get("country"),
                )
                self.entries.append(we)
                if we.enabled:
                    # Build league_id lookup (fast exact-match)
                    if lid is not None:
                        existing_lid = self._league_id_lookup.get(lid)
                        if existing_lid is not None:
                            collisions.append(
                                (f"league_id={lid}", existing_lid.name, we.name)
                            )
                        else:
                            self._league_id_lookup[lid] = we
                    for norm in we.match_names_norm:
                        existing = self._norm_lookup.get(norm)
                        if existing is not None:
                            collisions.append((norm, existing.name, we.name))
                        else:
                            self._norm_lookup[norm] = we
                    for sk in we.sport_keys:
                        self._all_sport_keys.add(sk)

        # Report collisions
        if collisions:
            logger.warning(
                "Alias collisions detected in whitelist (%d):",
                len(collisions),
            )
            for norm, existing, new_entry in collisions:
                logger.warning(
                    "  alias='%s' first-claimed='%s' ignored='%s'",
                    norm, existing, new_entry,
                )
            logger.warning(
                "Only the first-claimed entry will match. Remove ambiguous generic "
                "match_names (e.g. 'Premier League', 'Super League') from all entries "
                "and use only qualified names to prevent cross-competition mismatches."
            )

        # Also add sport_keys themselves as match targets (e.g. "soccer_epl")
        for we in self.entries:
            if we.enabled:
                for sk in we.sport_keys:
                    nk = _normalize(sk)
                    if nk not in self._norm_lookup:
                        self._norm_lookup[nk] = we

        enabled_count = sum(1 for e in self.entries if e.enabled)
        logger.info(
            "Whitelist loaded: %d entries (%d enabled) across %d categories, "
            "%d sport keys, %d league-id entries, filter_mode=%s",
            len(self.entries),
            enabled_count,
            len([k for k in competitions if isinstance(competitions[k], list)]),
            len(self._all_sport_keys),
            len(self._league_id_lookup),
            self._filter_mode,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def version(self) -> str:
        return str(self._meta.get("version", "0.0.0"))

    @property
    def sport_keys(self) -> frozenset[str]:
        """All Odds API sport keys for enabled whitelist competitions."""
        return frozenset(self._all_sport_keys)

    def is_allowed(
        self,
        competition_name: str,
        *,
        league_id: int | None = None,
        country: str | None = None,
    ) -> bool:
        """Return True if the competition matches any enabled whitelist entry.

        Matching priority (first hit wins):
        1. ``league_id`` exact match (prevents cross-league name collisions
           like Belarus Premier League vs England Premier League).
           When ``league_id`` is provided, it is the **only** signal used —
           name/name+country fallback is deliberately skipped.
        2. ``competition_name`` normalized match against ``match_names``
           (with optional ``country`` filter to disambiguate shared names
           like "Serie A" → Italy vs Brazil). Only used when ``league_id``
           is None.
        """
        # -- path 1: league_id exact match (strongest & exclusive signal) --
        if league_id is not None:
            entry = self._league_id_lookup.get(league_id)
            return entry is not None and entry.enabled

        # -- path 2: name-based match with optional country filter ---------
        if not competition_name or not competition_name.strip():
            return False
        norm = _normalize(competition_name)
        entry = self._norm_lookup.get(norm)
        if entry is None or not entry.enabled:
            return False
        if country is not None and entry.country is not None:
            if _normalize(country) != _normalize(entry.country):
                return False
        return True

    def get_entry(
        self,
        competition_name: str,
        *,
        league_id: int | None = None,
        country: str | None = None,
    ) -> WhitelistEntry | None:
        """Return the matching whitelist entry, or None.

        Same matching logic as :meth:`is_allowed`."""
        # league_id exact match first (exclusive when provided)
        if league_id is not None:
            entry = self._league_id_lookup.get(league_id)
            if entry is not None and entry.enabled:
                return entry
            return None
        # fallback to name-based match
        if not competition_name or not competition_name.strip():
            return None
        norm = _normalize(competition_name)
        entry = self._norm_lookup.get(norm)
        if entry is None or not entry.enabled:
            return None
        if country is not None and entry.country is not None:
            if _normalize(country) != _normalize(entry.country):
                return None
        return entry

    def get_sport_key_for(
        self,
        competition_name: str,
        *,
        league_id: int | None = None,
        country: str | None = None,
    ) -> str | None:
        """Best-effort: first sport_key from the matching entry, or None."""
        entry = self.get_entry(competition_name, league_id=league_id, country=country)
        if entry and entry.sport_keys:
            return entry.sport_keys[0]
        return None

    def count_enabled(self) -> int:
        return sum(1 for e in self.entries if e.enabled)

    def list_categories(self) -> list[str]:
        seen: list[str] = []
        for e in self.entries:
            if e.category and e.category not in seen:
                seen.append(e.category)
        return seen


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
def get_whitelist(reload: bool = False) -> CompetitionWhitelist:
    """Return the cached CompetitionWhitelist singleton.

    Loads ``production_whitelist.json`` on first call; pass ``reload=True``
    to force a fresh load (useful for testing/hot-reload).
    """
    global _whitelist
    if _whitelist is None or reload:
        if not WHITELIST_PATH.exists():
            raise FileNotFoundError(
                f"Whitelist file not found at {WHITELIST_PATH}. "
                "Create production_whitelist.json at the project root."
            )
        raw = WHITELIST_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        _whitelist = CompetitionWhitelist(data)
    return _whitelist


def clear_whitelist_cache() -> None:
    """Clear the cached whitelist (for testing)."""
    global _whitelist
    _whitelist = None
