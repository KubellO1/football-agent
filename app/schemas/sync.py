"""数据采集（同步）相关的响应 DTO。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SyncReport(BaseModel):
    """一次采集运行的结果统计（幂等：重复运行 created 会归零）。"""

    source: str = Field(description="外部数据源名称，如 'api-football'。")
    date: str = Field(description="被同步的日期（ISO，UTC）。")
    fixtures_processed: int = Field(description="从数据源取得并处理的比赛数。")
    fixtures_created: int = Field(description="本次新增的比赛数。")
    fixtures_updated: int = Field(description="本次更新的已存在比赛数。")
    fixtures_skipped: int = Field(description="缺少必要字段（如联赛 id）而跳过的比赛数。")
    competitions_created: int = Field(description="本次新增的赛事数。")
    teams_created: int = Field(description="本次新增的球队数。")
