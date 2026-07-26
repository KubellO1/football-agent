# DEPRECATED: 2026-07-17 - Removed from production. Retained for reference.
"""Sportmonks v3 implementation of :class:`SportmonksProvider`.

Talks to Sportmonks API v3 (https://docs.sportmonks.com/football). Auth uses
``api_token`` query parameter (NOT Bearer header).

Role: **Enhancement provider only.** Does NOT replace API-Football (fixtures,
statistics, competitions), The Odds API (bookmaker odds, market movement), or
WeatherAPI (weather). Sportmonks data is explanatory/display-only and must not
drive model weights, Kelly, thresholds, or decision rules.

Endpoint mapping:
- Predictions: ``football/fixtures`` + includes=predictions;predictions.type
- Statistics: ``football/fixtures/{id}`` + includes=participants;participants.statistics
- Transfers: ``football/transfers/latest`` + includes=player;fromTeam;toTeam
- Odds: ``football/odds/inplay/fixtures/{id}`` — 888Sport/Dafabet only (secondary)
- Lineups: ``football/fixtures/{id}`` + includes=lineups.player
- Injuries: ``football/fixtures/{id}`` + includes=sidelined.player
- Recent Form: ``football/teams/{id}`` + includes=latest
- Standings: ``football/standings/seasons/{id}``
- Match Centre: ``football/fixtures/{id}`` + includes=events;timeline;statistics
- TV Stations: ``football/tv-stations/fixtures/{id}``
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.sportmonks_provider import SportmonksProvider
from app.providers.schemas.sportmonks import (
    InjuryReport,
    LineupPlayer,
    LineupReport,
    MatchCentreData,
    MatchEvent,
    RecentForm,
    RecentMatch,
    SidelinedPlayer,
    SportmonksFixturePredictions,
    SportmonksOdds,
    SportmonksPrediction,
    SportmonksTeamStats,
    SportmonksTransfer,
    StandingsRow,
    StandingsTable,
    TeamLineup,
    TVStation,
)

logger = get_logger(__name__)

# Bookmakers available via Sportmonks odds (limited subset).
_SPORTMONKS_BOOKMAKERS = {"888sport", "dafabet"}


class SportmonksApiProvider(BaseHTTPProvider, SportmonksProvider):
    """Sportmonks v3 football data feed."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.sportmonks.com/v3",
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            headers={},
            client=client,
        )

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Inject ``api_token`` into query params before calling the base."""
        params = dict(params or {})
        params["api_token"] = self._api_key
        return await super()._get_json(path, params=params, headers=headers)

    # ------------------------------------------------------------------
    # Predictions (29 types → ensemble prior layer)
    # ------------------------------------------------------------------

    async def get_predictions(
        self,
        *,
        season_id: int,
    ) -> list[SportmonksFixturePredictions]:
        """Fetch predictions via fixtures endpoint with prediction includes.

        The dedicated ``/football/predictions/probabilities`` endpoint returns
        data sorted by ID (includes 2021 fixtures) and lacks ``fixtureSeasons``
        filter. Workaround: use ``football/fixtures`` with includes and filter
        by season + finished state.
        """
        params: dict[str, Any] = {
            "includes": "predictions;predictions.type",
            "filters": f"fixtureSeasons:{season_id};fixtureStates:5",
        }
        payload = await self._get_json("/football/fixtures", params=params)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return [self._parse_fixture_predictions(f) for f in data]

    @staticmethod
    def _parse_fixture_predictions(fixture: dict[str, Any]) -> SportmonksFixturePredictions:
        raw_preds = fixture.get("predictions", [])
        preds: list[SportmonksPrediction] = []
        for rp in raw_preds:
            if not isinstance(rp, dict):
                continue
            ptype = rp.get("type", {})
            predictions_inner = rp.get("predictions", {})
            # Probability may be a string like "55.12%" or a numeric value.
            yes_val = predictions_inner.get("yes") if isinstance(predictions_inner, dict) else None
            try:
                yes_float = float(str(yes_val).rstrip("%")) if yes_val is not None else None
            except (ValueError, TypeError):
                yes_float = None
            preds.append(
                SportmonksPrediction(
                    prediction_type_id=ptype.get("id", 0) if isinstance(ptype, dict) else 0,
                    prediction_type_name=ptype.get("name", "") if isinstance(ptype, dict) else "",
                    probability=yes_float,
                    winner=rp.get("winner"),
                    advice=rp.get("advice"),
                )
            )
        return SportmonksFixturePredictions(
            fixture_id=fixture.get("id", 0),
            predictions=preds,
        )

    # ------------------------------------------------------------------
    # Team statistics (no xG — supplement with API-Football)
    # ------------------------------------------------------------------

    async def get_team_statistics(
        self,
        *,
        fixture_id: int,
    ) -> list[SportmonksTeamStats]:
        params: dict[str, Any] = {"includes": "participants;participants.statistics"}
        payload = await self._get_json(f"/football/fixtures/{fixture_id}", params=params)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        stats_list: list[SportmonksTeamStats] = []

        participants = data.get("participants", [])
        for team in participants:
            if not isinstance(team, dict):
                continue
            stats_data = team.get("statistics", {})
            if isinstance(stats_data, dict):
                stats_data = stats_data.get("data", [])
            flat: dict[str, Any] = {}
            if isinstance(stats_data, list):
                for item in stats_data:
                    name = (item.get("type") or {}).get("name", "") if isinstance(item, dict) else ""
                    value = item.get("value") if isinstance(item, dict) else None
                    if name and value is not None:
                        flat[name] = value
            stats_list.append(
                SportmonksTeamStats(
                    fixture_id=fixture_id,
                    team_id=team.get("id", 0),
                    team_name=team.get("name", ""),
                    stats=flat,
                )
            )
        return stats_list

    # ------------------------------------------------------------------
    # Transfers (camelCase includes)
    # ------------------------------------------------------------------

    async def get_transfers(
        self,
        *,
        season_id: int,
    ) -> list[SportmonksTransfer]:
        params: dict[str, Any] = {"includes": "player;fromTeam;toTeam"}
        payload = await self._get_json("/football/transfers/latest", params=params)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        transfers: list[SportmonksTransfer] = []
        for t in data:
            player = t.get("player", {}) if isinstance(t, dict) else {}
            from_team = t.get("fromTeam", {}) if isinstance(t, dict) else {}
            to_team = t.get("toTeam", {}) if isinstance(t, dict) else {}
            transfers.append(
                SportmonksTransfer(
                    transfer_id=t.get("id", 0),
                    player_name=(
                        player.get("display_name", "")
                        or player.get("fullname", "")
                        or player.get("common_name", "")
                    ),
                    from_team=from_team.get("name", ""),
                    to_team=to_team.get("name", ""),
                    transfer_type=t.get("type", ""),
                    amount=t.get("amount"),
                    date=t.get("date"),
                    season_id=season_id,
                )
            )
        return transfers

    # ------------------------------------------------------------------
    # Odds (888Sport / Dafabet only — secondary source)
    # ------------------------------------------------------------------

    async def get_odds(
        self,
        *,
        fixture_id: int,
    ) -> list[SportmonksOdds]:
        params: dict[str, Any] = {"includes": "bookmaker;market"}
        payload = await self._get_json(
            f"/football/odds/inplay/fixtures/{fixture_id}", params=params
        )
        data = payload.get("data", []) if isinstance(payload, dict) else []
        odds_list: list[SportmonksOdds] = []
        for o in data:
            bookmaker = o.get("bookmaker", {}) if isinstance(o, dict) else {}
            market = o.get("market", {}) if isinstance(o, dict) else {}
            bm_name = (bookmaker.get("name") or "").lower()
            if _SPORTMONKS_BOOKMAKERS and bm_name not in _SPORTMONKS_BOOKMAKERS:
                continue
            odds_list.append(
                SportmonksOdds(
                    fixture_id=fixture_id,
                    bookmaker_name=bookmaker.get("name", ""),
                    market_name=market.get("name", ""),
                    market_id=market.get("id"),
                    outcomes=o.get("values", []),
                )
            )
        return odds_list

    # ──────────────────────────────────────────────────────────────
    # Phase 1: Enhancement methods (display-only, no model impact)
    # ──────────────────────────────────────────────────────────────

    async def get_lineups(
        self,
        *,
        fixture_id: int,
    ) -> LineupReport | None:
        """Return predicted/confirmed lineups for a fixture."""
        params: dict[str, Any] = {"includes": "lineups.player"}
        payload = await self._get_json(f"/football/fixtures/{fixture_id}", params=params)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        raw_lineups: list[dict[str, Any]] = data.get("lineups", []) or []

        if not raw_lineups:
            return None

        # Group by team_id
        teams: dict[int, dict[str, Any]] = {}
        for lu in raw_lineups:
            if not isinstance(lu, dict):
                continue
            tid = lu.get("team_id", 0)
            if tid not in teams:
                teams[tid] = {"players": []}
                # Try to get team name from participants if available
                participants = data.get("participants", [])
                for p in participants:
                    if p.get("id") == tid:
                        teams[tid]["name"] = p.get("name", "")
                        break
            p_name = lu.get("player_name", "")
            player_obj = lu.get("player", {})
            if isinstance(player_obj, dict):
                display = player_obj.get("display_name", "") or player_obj.get("common_name", "")
                if display:
                    p_name = display
            player = LineupPlayer(
                player_name=p_name,
                jersey_number=lu.get("jersey_number"),
                position_id=lu.get("position_id"),
                type_id=lu.get("type_id"),
                formation_position=lu.get("formation_position"),
                is_starter=(lu.get("type_id") == 11),
            )
            teams[tid]["players"].append(player)

        team_lineups: list[TeamLineup] = []
        for tid, tdata in teams.items():
            starters = [p for p in tdata["players"] if p.is_starter]
            substitutes = [p for p in tdata["players"] if not p.is_starter]
            team_lineups.append(TeamLineup(
                team_id=tid,
                team_name=tdata.get("name", str(tid)),
                formation=None,  # formation not available in current tier
                starters=sorted(starters, key=lambda x: x.formation_position or 99),
                substitutes=sorted(substitutes, key=lambda x: x.formation_position or 99),
            ))

        return LineupReport(fixture_id=fixture_id, lineups=team_lineups)

    async def get_sidelined(
        self,
        *,
        fixture_id: int,
    ) -> InjuryReport | None:
        """Return injuries & suspensions. EXPLANATORY SIGNAL ONLY."""
        params: dict[str, Any] = {"includes": "sidelined.player"}
        payload = await self._get_json(f"/football/fixtures/{fixture_id}", params=params)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        raw_sidelined: list[dict[str, Any]] = data.get("sidelined", []) or []

        if not raw_sidelined:
            return None

        players: list[SidelinedPlayer] = []
        for s in raw_sidelined:
            if not isinstance(s, dict):
                continue
            player_obj = s.get("player", {})
            p_name = ""
            if isinstance(player_obj, dict):
                p_name = player_obj.get("display_name", "") or player_obj.get("common_name", "")
            p_type = "injury"
            if s.get("type_id") == 538:
                p_type = "suspension"

            players.append(SidelinedPlayer(
                player_name=p_name,
                player_id=s.get("player_id", 0),
                type=p_type,
                type_id=s.get("type_id"),
                description=s.get("description") or "",
            ))

        return InjuryReport(fixture_id=fixture_id, players=players)

    async def get_team_recent(
        self,
        *,
        team_id: int,
    ) -> RecentForm | None:
        """Return last 5 match results for a team."""
        params: dict[str, Any] = {"includes": "latest"}
        payload = await self._get_json(f"/football/teams/{team_id}", params=params)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        team_name = data.get("name", str(team_id))
        latest: list[dict[str, Any]] = data.get("latest", []) or []

        if not latest:
            return None

        matches: list[RecentMatch] = []
        for m in latest[:5]:
            if not isinstance(m, dict):
                continue
            mid = m.get("id", 0)
            opponent = "?"

            # Determine opponent from fixture name "A vs B"
            name = m.get("name", "")
            parts = name.split(" vs ")
            if len(parts) == 2:
                opponent = parts[1].strip() if parts[0].strip().lower() == team_name.lower() else parts[0].strip()

            # Determine W/D/L from result_info text
            result_info = m.get("result_info", "") or ""
            result = "?"
            is_home = team_name.lower() in parts[0].lower() if parts else False

            if result_info:
                ri_lower = result_info.lower()
                tn_lower = team_name.lower()
                if ri_lower.startswith(tn_lower + " won"):
                    result = "W"
                elif ri_lower.startswith(tn_lower + " lost"):
                    result = "L"
                elif "draw" in ri_lower or "after penalty" in ri_lower:
                    result = "D"
                elif tn_lower in ri_lower and " won " in ri_lower:
                    result = "W"
                elif tn_lower in ri_lower and " lost " in ri_lower:
                    result = "L"

            # Get scores via multiple fallback strategies
            goals_for = 0
            goals_against = 0

            # Strategy 1: participants with scores
            participants = m.get("participants", []) or []
            for p in participants:
                if not isinstance(p, dict):
                    continue
                pid = p.get("id")
                scores = p.get("scores", {}) or {}
                goals = int(scores.get("goals", 0)) if isinstance(scores, dict) else 0
                if pid == team_id:
                    goals_for = max(goals_for, goals)
                elif goals:
                    goals_against = max(goals_against, goals)

            # Strategy 2: top-level scores array
            if goals_for == 0 and goals_against == 0:
                raw_scores = m.get("scores", []) or []
                for sc in raw_scores:
                    if not isinstance(sc, dict):
                        continue
                    desc = (sc.get("description") or "").upper()
                    pid = sc.get("participant_id")
                    g = int((sc.get("score") or {}).get("goals", 0)) if isinstance(sc.get("score"), dict) else 0
                    if pid == team_id:
                        goals_for = max(goals_for, g)
                    else:
                        goals_against = max(goals_against, g)

            # Strategy 3: parse from fixture name if it includes score like "Elfsborg 2-1 GAIS"
            if goals_for == 0 and goals_against == 0:
                import re
                score_match = re.search(r'(\d+)[-:](\d+)', name)
                if score_match:
                    g1, g2 = int(score_match.group(1)), int(score_match.group(2))
                    if is_home:
                        goals_for, goals_against = g1, g2
                    else:
                        goals_for, goals_against = g2, g1

            # Re-derive result from scores if available
            if goals_for > 0 or goals_against > 0:
                if goals_for > goals_against:
                    result = "W"
                elif goals_for < goals_against:
                    result = "L"
                else:
                    result = "D"

            matches.append(RecentMatch(
                fixture_id=mid,
                opponent=opponent,
                result=result,
                goals_for=goals_for,
                goals_against=goals_against,
                is_home=is_home,
                date=m.get("starting_at", ""),
            ))

        # Trend: ↑ if last 3 of 5 have >= 2 W, ↓ if >= 2 L, → otherwise
        w_count = sum(1 for m in matches if m.result == "W")
        l_count = sum(1 for m in matches if m.result == "L")
        trend = "→"
        if w_count >= 3:
            trend = "↑"
        elif l_count >= 3:
            trend = "↓"

        return RecentForm(team_id=team_id, team_name=team_name, matches=matches, trend=trend)

    async def get_standings(
        self,
        *,
        season_id: int,
    ) -> StandingsTable | None:
        """Return league standings for a season."""
        params: dict[str, Any] = {"includes": "participant"}
        payload = await self._get_json(
            f"/football/standings/seasons/{season_id}", params=params
        )
        raw_rows: list[dict[str, Any]] = payload.get("data", []) if isinstance(payload, dict) else []

        if not raw_rows:
            return None

        rows: list[StandingsRow] = []
        for s in raw_rows:
            if not isinstance(s, dict):
                continue
            participant = s.get("participant", {}) or {}
            result_str = s.get("result", "") or ""
            # Parse "W-D-L" format
            parts_str = result_str.split("-")
            try:
                w = int(parts_str[0]) if len(parts_str) > 0 else 0
                d = int(parts_str[1]) if len(parts_str) > 1 else 0
                l = int(parts_str[2]) if len(parts_str) > 2 else 0
            except (ValueError, IndexError):
                w = d = l = 0
            played = w + d + l
            # Try to get goals data from totals/goals fields
            goals_for = s.get("goals_for") or s.get("goals_scored") or 0
            goals_against = s.get("goals_against") or s.get("goals_conceded") or 0
            goal_diff = s.get("goal_diff") or (goals_for - goals_against) if isinstance(goals_for, int) and isinstance(goals_against, int) else 0

            rows.append(StandingsRow(
                position=s.get("position", 0),
                team_name=participant.get("name", f"Team {s.get('participant_id', 0)}"),
                team_id=s.get("participant_id", 0) or participant.get("id", 0),
                played=played,
                wins=w,
                draws=d,
                losses=l,
                goals_for=goals_for,
                goals_against=goals_against,
                goal_diff=goal_diff,
                points=s.get("points", 0),
            ))

        return StandingsTable(
            season_id=season_id,
            group_name=f"Season {season_id}",
            rows=rows,
        )

    async def get_match_centre(
        self,
        *,
        fixture_id: int,
    ) -> MatchCentreData | None:
        """Return combined events, timeline, and statistics for a fixture."""
        params: dict[str, Any] = {"includes": "events;timeline;statistics"}
        payload = await self._get_json(f"/football/fixtures/{fixture_id}", params=params)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}

        # Parse events
        events: list[MatchEvent] = []
        raw_events: list[dict[str, Any]] = data.get("events", []) or []
        for e in raw_events:
            if not isinstance(e, dict):
                continue
            events.append(MatchEvent(
                minute=e.get("minute", 0) or 0,
                extra_minute=e.get("extra_minute"),
                type=str(e.get("type", "") or ""),
                player_name=e.get("player_name") or "",
                related_player_name=e.get("related_player_name") or None,
                result=e.get("result") or "",
                info=e.get("info") or "",
            ))

        # Parse timeline (same structure as events)
        timeline: list[MatchEvent] = []
        raw_timeline: list[dict[str, Any]] = data.get("timeline", []) or []
        for t in raw_timeline:
            if not isinstance(t, dict):
                continue
            timeline.append(MatchEvent(
                minute=t.get("minute", 0) or 0,
                extra_minute=t.get("extra_minute"),
                type=str(t.get("type", "") or ""),
                player_name=t.get("player_name") or "",
                related_player_name=t.get("related_player_name") or None,
                result=t.get("result") or "",
                info=t.get("info") or "",
            ))

        # Statistics
        raw_statistics: list[dict[str, Any]] = data.get("statistics", []) or []

        return MatchCentreData(
            fixture_id=fixture_id,
            events=events,
            timeline=timeline,
            statistics=raw_statistics if isinstance(raw_statistics, list) else [],
        )

    async def get_tv_stations(
        self,
        *,
        fixture_id: int,
    ) -> list[TVStation]:
        """Return TV stations broadcasting a fixture."""
        payload = await self._get_json(f"/football/tv-stations/fixtures/{fixture_id}")
        data = payload.get("data", []) if isinstance(payload, dict) else []
        stations: list[TVStation] = []
        for s in data:
            if not isinstance(s, dict):
                continue
            stations.append(TVStation(
                name=s.get("name", ""),
                url=s.get("url"),
                type=s.get("type", ""),
            ))
        return stations
