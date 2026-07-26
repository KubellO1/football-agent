"""把赔率事件匹配到已入库比赛的**纯**逻辑（无数据库依赖，便于单测）。

核心原则（宪法/需求）：**绝不猜测**。跨数据源没有共享 id，只能用「球队名 +
开赛时间」匹配，因此宁可漏配（报告为未匹配/歧义并跳过），也不做模糊/相似度
匹配去「猜」——把赔率关联到错误的比赛在博彩系统里是危险的。

归一化保守：小写化、去重音符、去标点、压缩空白。**不**剥离 FC/SC 等后缀，也不
做相似度匹配——这些都会「推断」身份，可能把不同球队合并。代价是命中率偏低，
但这是安全且诚实的取舍（未命中会被报告，而非猜测）。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

logger = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")

# Common team-name suffixes that carry little distinguishing information
# and frequently differ between Odds-API.io and API-Football.
_STRIP_SUFFIXES = re.compile(
    r"\b(?:FC|AFC|SC|City|Town|Wanderers|and\s+Hove\s+Albion)\s*$",
    flags=re.IGNORECASE,
)


def normalize_team_name(name: str) -> str:
    """Normalise a team name for cross-provider matching.

    Steps (in order):
    1. Strip common suffixes (FC / AFC / SC / City / Town / Wanderers /
       "and Hove Albion").
    2. NFKD decompose → strip combining marks.
    3. Lowercase.
    4. Strip punctuation.
    5. Compress whitespace.
    """
    # Strip suffixes first so remaining normalisation acts on the core name.
    name = _STRIP_SUFFIXES.sub("", name)
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    no_punct = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", no_punct).strip()


class MatchOutcome(str, Enum):
    """赔率事件对比赛的匹配结果。"""

    MATCHED = "matched"  # 唯一命中
    UNMATCHED = "unmatched"  # 无候选
    AMBIGUOUS = "ambiguous"  # 多个候选，拒绝猜测


@dataclass(frozen=True)
class MatchCandidate:
    """参与匹配的一场比赛的最小标识（球队名已归一化）。"""

    fixture_id: UUID
    home_norm: str
    away_norm: str
    kickoff: datetime


@dataclass(frozen=True)
class MatchResult:
    outcome: MatchOutcome
    fixture_id: UUID | None = None
    candidate_count: int = 0


def match_event(
    *,
    event_home: str,
    event_away: str,
    commence_time: datetime,
    candidates: list[MatchCandidate],
    tolerance: timedelta,
    alias_names: Callable[[str], frozenset[str]] | None = None,
) -> MatchResult:
    """把一个赔率事件匹配到候选比赛中唯一的一场，否则报告未匹配/歧义。

    命中条件：归一化主队名与客队名**同时**相等（不允许主客颠倒），且
    ``|kickoff - commence_time| <= tolerance``。

    ``alias_names`` 可选：把某个归一化队名展开为「等价拼写集合」（人工核对的别名，
    见 team_aliases），用于消除跨数据源的拼写差异。默认恒等（不启用别名，行为不变）。
    赛事名只要落入候选方的等价集合即视为同队——仍是精确集合成员判断，绝非模糊匹配。
    """
    home = normalize_team_name(event_home)
    away = normalize_team_name(event_away)
    resolve = alias_names if alias_names is not None else (lambda n: frozenset({n}))

    # 区分 exact match vs alias match：判别名展开后集合是否仅含自身
    home_aliases = resolve(home)
    away_aliases = resolve(away)
    home_is_alias = len(home_aliases) > 1
    away_is_alias = len(away_aliases) > 1

    hits = [
        c
        for c in candidates
        if home in resolve(c.home_norm)
        and away in resolve(c.away_norm)
        and abs(c.kickoff - commence_time) <= tolerance
    ]
    if len(hits) == 1:
        matched = hits[0]
        if home_is_alias or away_is_alias:
            logger.info(
                "alias match: event '%s' vs '%s' → fixture %s (home='%s' away='%s', "
                "home_aliased=%s away_aliased=%s, time_diff=%s)",
                event_home, event_away, matched.fixture_id,
                matched.home_norm, matched.away_norm,
                home_is_alias, away_is_alias,
                abs(matched.kickoff - commence_time),
            )
        else:
            logger.info(
                "exact match: event '%s' vs '%s' → fixture %s (home='%s' away='%s', "
                "time_diff=%s)",
                event_home, event_away, matched.fixture_id,
                matched.home_norm, matched.away_norm,
                abs(matched.kickoff - commence_time),
            )
        return MatchResult(MatchOutcome.MATCHED, fixture_id=matched.fixture_id, candidate_count=1)
    if len(hits) == 0:
        return MatchResult(MatchOutcome.UNMATCHED, candidate_count=0)
    return MatchResult(MatchOutcome.AMBIGUOUS, candidate_count=len(hits))


# ---------------------------------------------------------------------------
# Odds-API.io specific matching (reuses the same normalisation but adds
# reversed-detection and works with full Fixture + ProviderFixtureOdds).
# ---------------------------------------------------------------------------


def match_odds_event_to_fixture(
    odds_event: "ProviderFixtureOdds",
    fixtures: list["Fixture"],
    team_names: dict[UUID, str],
    tolerance_minutes: int = 15,
) -> tuple["Fixture | None", str]:
    """Match an Odds-API.io event to an API-Football fixture.

    Returns ``(matched_fixture, match_method)`` where *match_method* is one of:

    * ``"EXACT"`` — team names identical after normalisation.
    * ``"FUZZY"`` — team names matched after normalisation.
    * ``"REVERSED"`` — home/away swapped match (rejected, fixture is ``None``).
    * ``"FAILED"`` — no match found within tolerance.

    Steps:
    1. Normalise both sets of team names.
    2. Check home/away swap → ``"REVERSED"`` → reject.
    3. Match home=home AND away=away within ``tolerance_minutes`` of kickoff.
    4. Single match → ``"EXACT"`` or ``"FUZZY"``.
    5. Multiple matches → ``"AMBIGUOUS"`` → reject.
    6. Zero matches → ``"FAILED"``.
    """
    tolerance = timedelta(minutes=tolerance_minutes)
    ev_home = normalize_team_name(odds_event.home_team)
    ev_away = normalize_team_name(odds_event.away_team)

    candidates: list[tuple["Fixture", str, str]] = []
    for f in fixtures:
        home_name = team_names.get(f.home_team_id)
        away_name = team_names.get(f.away_team_id)
        if home_name is None or away_name is None:
            continue
        f_home = normalize_team_name(home_name)
        f_away = normalize_team_name(away_name)
        candidates.append((f, f_home, f_away))

    # Step 2: detect reversed (home/away swap).
    reversed_hits = [
        (f, f_home, f_away)
        for f, f_home, f_away in candidates
        if ev_home == f_away
        and ev_away == f_home
        and abs(f.kickoff - odds_event.commence_time) <= tolerance
    ]
    if reversed_hits:
        logger.info(
            "odds event '%s' vs '%s' → REVERSED match (home/away swap) with %d fixture(s)",
            odds_event.home_team,
            odds_event.away_team,
            len(reversed_hits),
        )
        return None, "REVERSED"

    # Step 3: match home=home AND away=away.
    hits = [
        (f, f_home, f_away)
        for f, f_home, f_away in candidates
        if ev_home == f_home
        and ev_away == f_away
        and abs(f.kickoff - odds_event.commence_time) <= tolerance
    ]

    if len(hits) == 1:
        matched, _, _ = hits[0]
        # Determine EXACT vs FUZZY: if the raw (un-normalised) strings match, it's EXACT.
        raw_home = odds_event.home_team.strip()
        raw_away = odds_event.away_team.strip()
        fix_home_name = team_names.get(matched.home_team_id, "")
        fix_away_name = team_names.get(matched.away_team_id, "")
        if raw_home == fix_home_name and raw_away == fix_away_name:
            method = "EXACT"
        else:
            method = "FUZZY"
        logger.info(
            "odds match '%s' vs '%s' → fixture %s (%s)",
            odds_event.home_team,
            odds_event.away_team,
            matched.id,
            method,
        )
        return matched, method

    if len(hits) > 1:
        logger.warning(
            "odds event '%s' vs '%s' → AMBIGUOUS (%d candidates)",
            odds_event.home_team,
            odds_event.away_team,
            len(hits),
        )
        return None, "AMBIGUOUS"

    return None, "FAILED"
