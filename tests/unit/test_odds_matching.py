"""赔率事件匹配的纯逻辑单元测试（无数据库）。

覆盖：保守归一化、唯一命中、未匹配、歧义（拒绝猜测）、时间容差、主客不可颠倒。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.odds_matching import (
    MatchCandidate,
    MatchOutcome,
    match_event,
    normalize_team_name,
)

KICKOFF = datetime(2026, 7, 2, 18, 30, tzinfo=UTC)
TOL = timedelta(minutes=180)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Manchester United", "manchester united"),
        ("Atlético Madrid", "atletico madrid"),
        ("Bayern München", "bayern munchen"),
        ("  Real   Madrid  ", "real madrid"),
        ("Draw", "draw"),
    ],
)
def test_normalize_team_name(raw: str, expected: str) -> None:
    assert normalize_team_name(raw) == expected


def _candidate(home: str, away: str, kickoff: datetime = KICKOFF) -> MatchCandidate:
    return MatchCandidate(
        fixture_id=uuid4(),
        home_norm=normalize_team_name(home),
        away_norm=normalize_team_name(away),
        kickoff=kickoff,
    )


@pytest.mark.unit
def test_unique_match() -> None:
    cand = _candidate("Manchester United", "Liverpool")
    result = match_event(
        event_home="manchester united",  # 大小写差异
        event_away="Liverpool",
        commence_time=KICKOFF,
        candidates=[cand],
        tolerance=TOL,
    )
    assert result.outcome is MatchOutcome.MATCHED
    assert result.fixture_id == cand.fixture_id


@pytest.mark.unit
def test_no_candidate_is_unmatched() -> None:
    result = match_event(
        event_home="Arsenal",
        event_away="Chelsea",
        commence_time=KICKOFF,
        candidates=[_candidate("Manchester United", "Liverpool")],
        tolerance=TOL,
    )
    assert result.outcome is MatchOutcome.UNMATCHED
    assert result.fixture_id is None


@pytest.mark.unit
def test_multiple_candidates_is_ambiguous_never_guesses() -> None:
    result = match_event(
        event_home="Manchester United",
        event_away="Liverpool",
        commence_time=KICKOFF,
        candidates=[
            _candidate("Manchester United", "Liverpool"),
            _candidate("Manchester United", "Liverpool", KICKOFF + timedelta(minutes=30)),
        ],
        tolerance=TOL,
    )
    assert result.outcome is MatchOutcome.AMBIGUOUS
    assert result.fixture_id is None
    assert result.candidate_count == 2


@pytest.mark.unit
def test_kickoff_outside_tolerance_is_unmatched() -> None:
    result = match_event(
        event_home="Manchester United",
        event_away="Liverpool",
        commence_time=KICKOFF + timedelta(hours=5),
        candidates=[_candidate("Manchester United", "Liverpool")],
        tolerance=TOL,
    )
    assert result.outcome is MatchOutcome.UNMATCHED


@pytest.mark.unit
def test_alias_resolver_matches_different_spelling() -> None:
    # 候选库存简称，赛事用全称：默认不匹配，提供别名解析器后命中
    cand = _candidate("Newcastle", "Liverpool")
    common = dict(
        event_home="Newcastle United",
        event_away="Liverpool",
        commence_time=KICKOFF,
        candidates=[cand],
        tolerance=TOL,
    )
    assert match_event(**common).outcome is MatchOutcome.UNMATCHED

    def resolve(norm: str) -> frozenset[str]:
        group = {normalize_team_name("Newcastle"), normalize_team_name("Newcastle United")}
        return frozenset(group) if norm in group else frozenset({norm})

    aliased = match_event(**common, alias_names=resolve)
    assert aliased.outcome is MatchOutcome.MATCHED
    assert aliased.fixture_id == cand.fixture_id


@pytest.mark.unit
def test_home_away_orientation_must_match() -> None:
    # 主客颠倒不算命中（避免把赔率关联到错误方向）。
    result = match_event(
        event_home="Liverpool",
        event_away="Manchester United",
        commence_time=KICKOFF,
        candidates=[_candidate("Manchester United", "Liverpool")],
        tolerance=TOL,
    )
    assert result.outcome is MatchOutcome.UNMATCHED
