"""ApiFootballBackfill 的集成测试（需真实 Postgres）。

用绑定测试库的容器 + 假 FixturesProvider，验证：区间回填落库、幂等（重跑不新增）、
断点续跑（仅处理未完成日期）。不触达真实外部 API、不预测、不评审。
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.config.settings import Settings, get_settings
from app.core.container import Container
from app.database.base import Base
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.repositories.sqlalchemy import models  # noqa: F401 - 注册 ORM 表
from app.repositories.sqlalchemy.models import FixtureORM
from app.services.backfill import (
    ApiFootballBackfill,
    LeagueSeasonBackfill,
    write_checkpoint,
    write_league_checkpoint,
)


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().sqlalchemy_dsn


class FakeFixturesProvider(FixturesProvider):
    """每个日期返回 1 场固定对阵（provider_id 随日期变化），并记录被查询的日期。"""

    def __init__(self) -> None:
        self.queried_dates: list[date] = []

    async def get_fixtures(self, *, on_date=None, league=None, season=None):  # type: ignore[no-untyped-def]
        self.queried_dates.append(on_date)
        kickoff = datetime.combine(on_date, time(18, 0), tzinfo=UTC)
        return [
            ProviderFixture(
                provider_id=f"fx-{on_date.isoformat()}",
                kickoff=kickoff,
                status="NS",
                home=ProviderTeam(provider_id="t-home", name="Home FC"),
                away=ProviderTeam(provider_id="t-away", name="Away FC"),
                league="Backfill League",
                league_id="1",
                league_country="Testland",
                season=on_date.year,
            )
        ]

    async def get_fixture(self, provider_id: str):  # type: ignore[no-untyped-def]
        return None


async def _fixture_count(container: Container) -> int:
    async with container.database.session() as s:
        return (await s.execute(select(func.count()).select_from(FixtureORM))).scalar_one()


@pytest_asyncio.fixture
async def container():
    settings = Settings(database_url=_test_dsn(), anthropic_api_key="test")
    ctx = Container(settings)
    ctx.init_resources()
    async with ctx.database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield ctx
    finally:
        async with ctx.database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await ctx.shutdown_resources()


async def _noop_sleep(_: float) -> None:
    return None


@pytest.mark.integration
async def test_backfill_range_and_idempotent(container: Container, tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = FakeFixturesProvider()
    container.register(FixturesProvider, provider)
    cp = tmp_path / "cp.json"
    backfill = ApiFootballBackfill(
        container,
        checkpoint_path=cp,
        min_interval_seconds=0.0,
        sleep=_noop_sleep,
        progress=lambda _msg: None,
    )
    start, end = date(2026, 1, 1), date(2026, 1, 3)

    first = await backfill.run(start, end)
    assert first.dates_processed == 3
    assert first.fixtures_created == 3
    assert provider.queried_dates == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert await _fixture_count(container) == 3

    # 断点已落到最后一天 → 再跑（不 restart）应无待处理日期、无新增
    again = await backfill.run(start, end)
    assert again.dates_processed == 0
    assert await _fixture_count(container) == 3

    # restart 强制重跑 → 全部更新、零新增、无重复
    provider.queried_dates.clear()
    forced = await backfill.run(start, end, restart=True)
    assert forced.dates_processed == 3
    assert forced.fixtures_created == 0
    assert forced.fixtures_updated == 3
    assert await _fixture_count(container) == 3


@pytest.mark.integration
async def test_backfill_resumes_from_checkpoint(container: Container, tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = FakeFixturesProvider()
    container.register(FixturesProvider, provider)
    cp = tmp_path / "cp.json"
    start, end = date(2026, 1, 1), date(2026, 1, 3)
    # 模拟上次跑到 2026-01-01 被中断
    write_checkpoint(cp, start, end, date(2026, 1, 1))

    backfill = ApiFootballBackfill(
        container,
        checkpoint_path=cp,
        min_interval_seconds=0.0,
        sleep=_noop_sleep,
        progress=lambda _msg: None,
    )
    report = await backfill.run(start, end)

    assert report.dates_processed == 2
    assert provider.queried_dates == [date(2026, 1, 2), date(2026, 1, 3)]
    assert await _fixture_count(container) == 2


class LeagueFakeFixturesProvider(FixturesProvider):
    """按 (league, season) 返回 2 场比赛（provider_id 随对变化），记录被查询的对。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def get_fixtures(self, *, on_date=None, league=None, season=None):  # type: ignore[no-untyped-def]
        self.calls.append((league, season))
        base = f"L{league}S{season}"
        return [
            ProviderFixture(
                provider_id=f"{base}-m{i}",
                kickoff=datetime.combine(date(season, 6, 1 + i), time(18, 0), tzinfo=UTC),
                status="FT",
                home=ProviderTeam(provider_id=f"L{league}-A", name=f"L{league} A"),
                away=ProviderTeam(provider_id=f"L{league}-B", name=f"L{league} B"),
                league=f"League {league}",
                league_id=str(league),
                league_country="X",
                season=season,
                home_score=1,
                away_score=0,
            )
            for i in range(2)
        ]

    async def get_fixture(self, provider_id: str):  # type: ignore[no-untyped-def]
        return None


@pytest.mark.integration
async def test_league_backfill_and_resume(container: Container, tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = LeagueFakeFixturesProvider()
    container.register(FixturesProvider, provider)
    cp = tmp_path / "lcp.json"
    leagues, seasons = [39, 140], [2024, 2025]
    backfill = LeagueSeasonBackfill(
        container,
        checkpoint_path=cp,
        min_interval_seconds=0.0,
        sleep=_noop_sleep,
        progress=lambda _msg: None,
    )

    # 全量：2 联赛 × 2 赛季 = 4 对，每对 2 场
    first = await backfill.run(leagues, seasons)
    assert first.pairs_processed == 4
    assert first.fixtures_created == 8
    assert set(provider.calls) == {(39, 2024), (39, 2025), (140, 2024), (140, 2025)}
    assert await _fixture_count(container) == 8

    # 重跑：全部已完成 → 跳过、无新增
    again = await backfill.run(leagues, seasons)
    assert again.pairs_processed == 0
    assert again.pairs_skipped == 4
    assert await _fixture_count(container) == 8


@pytest.mark.integration
async def test_league_backfill_resumes_partial(container: Container, tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = LeagueFakeFixturesProvider()
    container.register(FixturesProvider, provider)
    cp = tmp_path / "lcp.json"
    leagues, seasons = [39, 140], [2024, 2025]
    # 模拟联赛 39 的两个赛季已完成
    write_league_checkpoint(cp, leagues, seasons, {"39:2024", "39:2025"})

    backfill = LeagueSeasonBackfill(
        container,
        checkpoint_path=cp,
        min_interval_seconds=0.0,
        sleep=_noop_sleep,
        progress=lambda _msg: None,
    )
    report = await backfill.run(leagues, seasons)

    assert report.pairs_processed == 2
    assert report.pairs_skipped == 2
    assert set(provider.calls) == {(140, 2024), (140, 2025)}
    assert await _fixture_count(container) == 4
