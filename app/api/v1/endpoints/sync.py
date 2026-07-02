"""数据采集（同步）端点。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.deps import IngestionServiceDep
from app.core.exceptions import ExternalServiceError
from app.schemas.sync import SyncReport

router = APIRouter(tags=["sync"])


@router.post("/sync/today", response_model=SyncReport)
async def sync_today(
    ingestion: IngestionServiceDep,
    on_date: date | None = None,
) -> SyncReport:
    """采集指定日期（默认今日 UTC）的比赛、球队、赛事并写入数据库。

    幂等：可安全重复调用——已存在的实体只会被复用/更新，不会重复插入。
    ``on_date`` 查询参数（ISO 日期）用于回填或测试，缺省为当前 UTC 日期。
    """
    target = on_date or datetime.now(UTC).date()
    try:
        return await ingestion.sync_today(target)
    except ExternalServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
