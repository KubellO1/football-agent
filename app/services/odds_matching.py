"""把赔率事件匹配到已入库比赛的**纯**逻辑（无数据库依赖，便于单测）。

核心原则（宪法/需求）：**绝不猜测**。跨数据源没有共享 id，只能用「球队名 +
开赛时间」匹配，因此宁可漏配（报告为未匹配/歧义并跳过），也不做模糊/相似度
匹配去「猜」——把赔率关联到错误的比赛在博彩系统里是危险的。

归一化保守：小写化、去重音符、去标点、压缩空白。**不**剥离 FC/SC 等后缀，也不
做相似度匹配——这些都会「推断」身份，可能把不同球队合并。代价是命中率偏低，
但这是安全且诚实的取舍（未命中会被报告，而非猜测）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_team_name(name: str) -> str:
    """保守归一化：小写、去重音符、去标点、压缩空白。"""
    # 去重音符：NFKD 分解后丢弃组合记号。
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
    hits = [
        c
        for c in candidates
        if home in resolve(c.home_norm)
        and away in resolve(c.away_norm)
        and abs(c.kickoff - commence_time) <= tolerance
    ]
    if len(hits) == 1:
        return MatchResult(MatchOutcome.MATCHED, fixture_id=hits[0].fixture_id, candidate_count=1)
    if len(hits) == 0:
        return MatchResult(MatchOutcome.UNMATCHED, candidate_count=0)
    return MatchResult(MatchOutcome.AMBIGUOUS, candidate_count=len(hits))
