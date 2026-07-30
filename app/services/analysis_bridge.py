"""仅用于分析的跨源桥接：把目标比赛（api-football）的球队按归一化名解析到
worldcup-excel 历史球队，从而用历史数据为「今天的比赛」建模。

安全边界（严格只读）：
- **不合并、不删除**任何球队；解析只是内存里的「名字 → id」映射。
- **不改写**任何比赛：目标 fixture 原样使用（含其自身赔率与身份）。
- 解析**有歧义或缺失**（归一化名对应 0 个或 >1 个 worldcup-excel 球队）→ 返回 None，
  该场跳过（绝不猜测）。

用法：把 BridgedAnalysisInputBuilder 注入既有的 FixtureAnalysisService 即可复用
整条确定性管线；仅「输入来源」被桥接。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.value_objects.decision import EvidenceLevel
from app.services.fixture_analysis import MatchAnalysisInputBuilder
from app.services.modeling import ModelInput
from app.services.odds_matching import normalize_team_name

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.team import Team
    from app.models.value_objects.money import Money
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
    from app.repositories.interfaces.reference import TeamRepository

_EVIDENCE_LEVEL = EvidenceLevel.B


class WorldCupTeamResolver:
    """按归一化名把任意球队名解析到唯一的 worldcup-excel 球队 id（否则 None）。"""

    def __init__(self, index: dict[str, list[UUID]], aliases: dict[str, str] | None = None) -> None:
        self._index = index
        self._aliases = aliases or {}

    @classmethod
    def from_teams(
        cls, teams: list[Team], source: str, aliases: dict[str, str] | None = None
    ) -> WorldCupTeamResolver:
        index: dict[str, list[UUID]] = {}
        for team in teams:
            if team.external_source != source:
                continue
            index.setdefault(normalize_team_name(team.name), []).append(team.id)
        return cls(index, aliases)

    def resolve(self, name: str) -> UUID | None:
        key = normalize_team_name(name)
        key = self._aliases.get(key, key)
        ids = self._index.get(key, [])
        return ids[0] if len(ids) == 1 else None  # 0=缺失 / >1=歧义 → 跳过


class BridgedAnalysisInputBuilder(MatchAnalysisInputBuilder):
    """用 worldcup-excel 历史为目标 fixture 建模的输入构造器（只读、不改数据）。"""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        teams: TeamRepository,
        odds_snapshots: OddsSnapshotRepository,
        bankroll: Money,
        resolver: WorldCupTeamResolver,
        wc_competition_id: UUID,
        form_window: int = 10,
    ) -> None:
        super().__init__(
            fixtures=fixtures,
            teams=teams,
            odds_snapshots=odds_snapshots,
            bankroll=bankroll,
            form_window=form_window,
        )
        self._resolver = resolver
        self._wc_competition_id = wc_competition_id

    async def build(self, fixture: Fixture, *, as_of: datetime) -> ModelInput | None:
        self._validate_as_of(as_of)
        home = await self._teams.get(fixture.home_team_id)
        away = await self._teams.get(fixture.away_team_id)
        if home is None or away is None:
            return None

        wc_home = self._resolver.resolve(home.name)
        wc_away = self._resolver.resolve(away.name)
        if wc_home is None or wc_away is None:
            return None  # 名字无法唯一解析 → 跳过

        # 近况/联赛/Elo 取自 worldcup-excel 历史实体；赔率取自目标 fixture 本身。
        home_stats = await self._team_stats(wc_home, exclude=fixture.id, before=as_of)
        away_stats = await self._team_stats(wc_away, exclude=fixture.id, before=as_of)
        league = await self._league_averages(self._wc_competition_id, before=as_of)
        if home_stats.matches_played == 0 or away_stats.matches_played == 0 or league is None:
            return None

        quotes = await self._quotes(fixture.id, as_of=as_of)
        home_elo, away_elo = await self._elos(wc_home, wc_away)
        completeness = self._completeness(home_stats, away_stats, quotes, home_elo, away_elo)

        return ModelInput(
            fixture=fixture,
            home_stats=home_stats,
            away_stats=away_stats,
            league=league,
            quotes=quotes,
            bankroll=self._bankroll,
            data_completeness=completeness,
            evidence_level=_EVIDENCE_LEVEL,
            home_elo=home_elo,
            away_elo=away_elo,
        )
