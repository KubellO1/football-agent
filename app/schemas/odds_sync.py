"""赔率采集（同步）响应 DTO。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OddsSyncReport(BaseModel):
    """一次赔率采集运行的结果统计与匹配透明度报告（幂等：重复运行 created 归零）。"""

    source: str = Field(description="外部数据源名称，如 'the-odds-api'。")
    date: str = Field(description="被同步的日期（ISO，UTC）。")
    sport_keys: list[str] = Field(description="本次抓取的 The Odds API sport keys。")

    events_fetched: int = Field(description="从数据源取得的赔率事件总数。")
    events_matched: int = Field(description="唯一匹配到已入库比赛的事件数。")
    events_unmatched: int = Field(description="找不到对应比赛而跳过的事件数。")
    events_ambiguous: int = Field(description="匹配到多场比赛、因拒绝猜测而跳过的事件数。")

    snapshots_created: int = Field(description="本次新增的赔率快照数。")
    snapshots_existing: int = Field(description="因幂等键已存在而跳过的快照数。")
    outcomes_skipped: int = Field(description="无法解析或赔率非法（<=1.0）而跳过的赔项数。")

    unmatched_samples: list[str] = Field(
        default_factory=list, description="未匹配事件样例（最多若干条），便于排查名称差异。"
    )
    ambiguous_samples: list[str] = Field(
        default_factory=list, description="歧义事件样例（最多若干条）。"
    )
    primary_provider_hits: int = Field(
        default=0, description="由 primary provider 成功返回的事件数。"
    )
    fallback_provider_hits: int = Field(
        default=0, description="由 fallback provider 成功返回的事件数。"
    )
    provider_errors_by_source: dict[str, int] = Field(
        default_factory=dict,
        description="各 provider 的错误次数，如 {'odds-api.io': 1, 'the-odds-api': 0}。",
    )
    requested_fixtures: int = 0
    targeted_fixtures: int = 0
    primary_requests: int = 0
    primary_events_returned: int = 0
    primary_empty_events: int = 0
    fallback_attempts: int = 0
    fallback_requests: int = 0
    fallback_successes: int = 0
    fallback_events_returned: int = 0
    combined_odds_coverage: float = 0.0
    unmatched_reason_counts: dict[str, int] = Field(default_factory=dict)


class HistoricalOddsBackfillReport(BaseModel):
    """历史赔率回填运行的结果统计与匹配透明度报告（幂等：重复运行 created 归零）。"""

    source: str = Field(description="外部数据源名称，如 'the-odds-api'。")
    sport: str = Field(description="The Odds API sport key，如 'soccer_epl'。")
    date_from: str = Field(description="回填区间起始日（含，ISO，UTC）。")
    date_to: str = Field(description="回填区间结束日（含，ISO，UTC）。")
    days_processed: int = Field(description="实际抓取快照的天数。")
    competition_scope: str | None = Field(
        default=None, description="候选比赛限定的赛事名（None 表示未按赛事过滤）。"
    )

    events_fetched: int = Field(description="从数据源取得的赔率事件总数。")
    events_matched: int = Field(description="唯一匹配到已入库比赛的事件数。")
    events_unmatched: int = Field(description="找不到对应比赛而跳过的事件数。")
    events_ambiguous: int = Field(description="匹配到多场比赛、因拒绝猜测而跳过的事件数。")

    snapshots_created: int = Field(description="本次新增的赔率快照数。")
    snapshots_existing: int = Field(description="因幂等键已存在而跳过的快照数。")
    outcomes_skipped: int = Field(description="无法解析或赔率非法（<=1.0）而跳过的赔项数。")

    unmatched_samples: list[str] = Field(
        default_factory=list, description="未匹配事件样例（最多若干条），便于排查名称差异。"
    )
    ambiguous_samples: list[str] = Field(
        default_factory=list, description="歧义事件样例（最多若干条）。"
    )
