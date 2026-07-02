"""数据采集（ingestion）编排。

把 Provider 层的原始 DTO 映射为领域实体，并**幂等地**写入 PostgreSQL：

- 参考数据（赛事 / 球队）：按 (external_source, external_id) 做 get-or-create，
  已存在则复用，不新增；
- 比赛（fixtures）：按同一幂等键做 update-or-create，重复采集只刷新可变字段
  （开赛时间、状态、比分），不产生重复行。

整个过程在调用方（请求作用域）提供的同一个事务/Session 内完成，成功即提交、
出错即回滚（见 Database.session）。本层不做任何预测/下注逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.core.logging import get_logger
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.team import Team
from app.models.value_objects.score import Score
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.reference import CompetitionRepository, TeamRepository
from app.schemas.sync import SyncReport

logger = get_logger(__name__)

SOURCE_API_FOOTBALL = "api-football"

# API-Football 的 status.short → 领域 MatchStatus 映射。未知值回退为 SCHEDULED。
_STATUS_MAP: dict[str, MatchStatus] = {
    "TBD": MatchStatus.SCHEDULED,
    "NS": MatchStatus.SCHEDULED,
    "1H": MatchStatus.LIVE,
    "HT": MatchStatus.LIVE,
    "2H": MatchStatus.LIVE,
    "ET": MatchStatus.LIVE,
    "BT": MatchStatus.LIVE,
    "P": MatchStatus.LIVE,
    "SUSP": MatchStatus.LIVE,
    "INT": MatchStatus.LIVE,
    "LIVE": MatchStatus.LIVE,
    "FT": MatchStatus.FINISHED,
    "AET": MatchStatus.FINISHED,
    "PEN": MatchStatus.FINISHED,
    "PST": MatchStatus.POSTPONED,
    "CANC": MatchStatus.CANCELLED,
    "ABD": MatchStatus.CANCELLED,
    "AWD": MatchStatus.CANCELLED,
    "WO": MatchStatus.CANCELLED,
}


def map_status(short: str) -> MatchStatus:
    """把 API-Football 的短状态码映射为领域 MatchStatus。"""
    return _STATUS_MAP.get(short, MatchStatus.SCHEDULED)


@dataclass
class _Counters:
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    competitions_created: int = 0
    teams_created: int = 0


class IngestionService:
    """从 FixturesProvider 采集比赛/球队/赛事并幂等持久化。"""

    def __init__(
        self,
        *,
        fixtures_provider: FixturesProvider,
        competitions: CompetitionRepository,
        teams: TeamRepository,
        fixtures: FixtureRepository,
        source: str = SOURCE_API_FOOTBALL,
    ) -> None:
        self._provider = fixtures_provider
        self._competitions = competitions
        self._teams = teams
        self._fixtures = fixtures
        self._source = source

    async def sync_today(self, on_date: date) -> SyncReport:
        """采集 ``on_date`` 当日的比赛并写库，返回统计。可安全重复运行。"""
        provider_fixtures = await self._provider.get_fixtures(on_date=on_date)
        counters = _Counters()

        for pf in provider_fixtures:
            counters.processed += 1
            if pf.league_id is None:
                # 无法确定赛事的幂等键 → 跳过（避免生成孤立/重复赛事）。
                logger.warning("Skipping fixture %s: missing league id", pf.provider_id)
                counters.skipped += 1
                continue

            competition = await self._get_or_create_competition(pf, counters)
            home = await self._get_or_create_team(pf.home, counters)
            away = await self._get_or_create_team(pf.away, counters)

            fixture = self._to_fixture(pf, competition.id, home.id, away.id)
            await self._upsert_fixture(fixture, counters)

        logger.info(
            "Sync %s: processed=%d created=%d updated=%d skipped=%d comps=%d teams=%d",
            on_date.isoformat(),
            counters.processed,
            counters.created,
            counters.updated,
            counters.skipped,
            counters.competitions_created,
            counters.teams_created,
        )
        return SyncReport(
            source=self._source,
            date=on_date.isoformat(),
            fixtures_processed=counters.processed,
            fixtures_created=counters.created,
            fixtures_updated=counters.updated,
            fixtures_skipped=counters.skipped,
            competitions_created=counters.competitions_created,
            teams_created=counters.teams_created,
        )

    # --- get-or-create（参考数据）-----------------------------------------

    async def _get_or_create_competition(
        self, pf: ProviderFixture, counters: _Counters
    ) -> Competition:
        assert pf.league_id is not None  # 调用点已校验
        existing = await self._competitions.get_by_external_id(self._source, pf.league_id)
        if existing is not None:
            return existing
        created = await self._competitions.add(
            Competition(
                name=pf.league or "Unknown",
                country=pf.league_country or "",
                external_id=pf.league_id,
                external_source=self._source,
            )
        )
        counters.competitions_created += 1
        return created

    async def _get_or_create_team(self, pt: ProviderTeam, counters: _Counters) -> Team:
        existing = await self._teams.get_by_external_id(self._source, pt.provider_id)
        if existing is not None:
            return existing
        created = await self._teams.add(
            Team(name=pt.name, external_id=pt.provider_id, external_source=self._source)
        )
        counters.teams_created += 1
        return created

    # --- update-or-create（比赛）-----------------------------------------

    def _to_fixture(
        self, pf: ProviderFixture, competition_id: UUID, home_id: UUID, away_id: UUID
    ) -> Fixture:
        score: Score | None = None
        if pf.home_score is not None and pf.away_score is not None:
            score = Score(home=pf.home_score, away=pf.away_score)
        return Fixture(
            competition_id=competition_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff=pf.kickoff,
            status=map_status(pf.status),
            score=score,
            external_id=pf.provider_id,
            external_source=self._source,
        )

    async def _upsert_fixture(self, fixture: Fixture, counters: _Counters) -> None:
        assert fixture.external_id is not None
        existing = await self._fixtures.get_by_external_id(self._source, fixture.external_id)
        if existing is not None:
            fixture.id = existing.id  # 复用既有主键，就地更新
            await self._fixtures.update(fixture)
            counters.updated += 1
        else:
            await self._fixtures.add(fixture)
            counters.created += 1
