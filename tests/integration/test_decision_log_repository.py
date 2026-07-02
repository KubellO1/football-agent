"""SqlAlchemyDecisionLogRepository 的集成测试（需真实 Postgres）。

覆盖：add + get 往返（含 JSON 列表字段）、按比赛查询、按创建时间区间查询。
依赖 persisted_fixture 满足 fixtures 外键。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.decision_log import DecisionLog
from app.models.entities.fixture import Fixture
from app.repositories.sqlalchemy.decision_log_repository import (
    SqlAlchemyDecisionLogRepository,
)


@pytest.mark.integration
async def test_add_and_get_roundtrip(
    db_session: AsyncSession, persisted_fixture: Fixture
) -> None:
    repo = SqlAlchemyDecisionLogRepository(db_session)
    log = DecisionLog(
        fixture_id=persisted_fixture.id,
        summary="存在正 EV，推荐主胜",
        supporting_evidence=["主队近期 xG 领先", "客队关键中卫停赛"],
        risks=["主队门将存疑"],
        change_conditions=["若客队核心复出则重新评估"],
    )

    saved = await repo.add(log)
    got = await repo.get(saved.id)

    assert got is not None
    assert got.fixture_id == persisted_fixture.id
    assert got.summary == "存在正 EV，推荐主胜"
    # JSON 列表字段应完整往返
    assert got.supporting_evidence == ["主队近期 xG 领先", "客队关键中卫停赛"]
    assert got.risks == ["主队门将存疑"]
    assert got.change_conditions == ["若客队核心复出则重新评估"]
    assert got.rejected_alternatives == []


@pytest.mark.integration
async def test_list_by_fixture(
    db_session: AsyncSession, persisted_fixture: Fixture
) -> None:
    repo = SqlAlchemyDecisionLogRepository(db_session)
    await repo.add(DecisionLog(fixture_id=persisted_fixture.id, summary="第一次分析"))
    await repo.add(DecisionLog(fixture_id=persisted_fixture.id, summary="首发后更新"))

    logs = await repo.list_by_fixture(persisted_fixture.id)
    assert len(logs) == 2


@pytest.mark.integration
async def test_list_created_between(
    db_session: AsyncSession, persisted_fixture: Fixture
) -> None:
    repo = SqlAlchemyDecisionLogRepository(db_session)
    await repo.add(DecisionLog(fixture_id=persisted_fixture.id, summary="今日记录"))

    now = datetime.now(timezone.utc)
    logs = await repo.list_created_between(now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(logs) >= 1
