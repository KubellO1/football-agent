"""受控合成数据集（仅供开发/测试）。

用途：真实 API-Football 数据覆盖不足（历史日期返回空），无法凑出「双方都有历史」
的比赛来端到端验证分析 + Claude 评审。本脚本直接经仓储写入一批**清晰标记**的合成
数据，与真实数据隔离且可一键删除。

隔离与清理约定：
- 所有合成的赛事/球队/博彩公司/比赛都带 ``external_source="synthetic-dev"``，
  且名称以 ``[SYNTHETIC]`` / ``[SYN]`` 前缀标记；
- 合成球队只出现在合成赛事的比赛里，故不会与真实数据混淆或相互影响分析；
- ``purge`` 按该标记删除全部合成数据（含其 odds/value_bets/decision_logs）。

本脚本**不经过**采集管线（providers/ingestion），因此不改动任何生产采集逻辑。

用法（在容器内）：
    python scripts/synthetic_dataset.py seed     # 清理旧的合成数据后重新播种，打印目标比赛 id
    python scripts/synthetic_dataset.py purge    # 删除全部合成数据
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import combinations
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.entities.team import Team
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.metrics import EloRating
from app.models.value_objects.odds import Odds
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)

SOURCE = "synthetic-dev"
PAST = datetime(2026, 5, 1, 18, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 7, 2, 18, 0, tzinfo=UTC)
ROUNDS = 4  # 每对交手 4 次 → 每队约 12 场已完赛（满足 form 覆盖）


async def purge() -> None:
    engine = create_async_engine(get_settings().sqlalchemy_dsn, echo=False)
    async with engine.begin() as conn:
        # 先删依赖 fixtures 的子表，再删 fixtures，最后删参考数据。
        await conn.execute(
            text(
                "DELETE FROM decision_logs WHERE fixture_id IN "
                "(SELECT id FROM fixtures WHERE external_source=:s)"
            ),
            {"s": SOURCE},
        )
        await conn.execute(
            text(
                "DELETE FROM value_bets WHERE fixture_id IN "
                "(SELECT id FROM fixtures WHERE external_source=:s)"
            ),
            {"s": SOURCE},
        )
        await conn.execute(
            text(
                "DELETE FROM odds_snapshots WHERE fixture_id IN "
                "(SELECT id FROM fixtures WHERE external_source=:s)"
            ),
            {"s": SOURCE},
        )
        for table in ("fixtures", "teams", "competitions", "bookmakers"):
            await conn.execute(text(f"DELETE FROM {table} WHERE external_source=:s"), {"s": SOURCE})
    await engine.dispose()
    print("Purged all synthetic-dev data.")


def _score(home_team: str, away_team: str, *, home_is: str) -> Score:
    """构造比分，使 Alpha 强、Bravo 弱、其余均势（用于产生明显的主场大热）。

    每支球队都保证有进球/失球（λ 需为正数，否则 Poisson 拒绝）：
    Alpha 必胜但会失 1 球；Bravo 必负但会进 1 球。
    """
    alpha, bravo = "Alpha", "Bravo"
    if alpha in (home_team, away_team):
        # Alpha 必胜：主 3-1 / 客 2-1（会零星失球，保证对手 λ>0）
        return Score(home=3, away=1) if home_is == alpha else Score(home=1, away=2)
    if bravo in (home_team, away_team):
        # Bravo 必负：Bravo 进 1、对手进 2（保证 Bravo λ>0）
        return Score(home=1, away=2) if home_is == bravo else Score(home=2, away=1)
    return Score(home=1, away=1)  # Charlie vs Delta 平局


async def seed() -> UUID:
    await purge()
    engine = create_async_engine(get_settings().sqlalchemy_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        comps = SqlAlchemyCompetitionRepository(session)
        teams_repo = SqlAlchemyTeamRepository(session)
        fixtures = SqlAlchemyFixtureRepository(session)
        bookmakers = SqlAlchemyBookmakerRepository(session)
        snaps = SqlAlchemyOddsSnapshotRepository(session)

        competition = await comps.add(
            Competition(
                name="[SYNTHETIC] Dev League",
                country="Testland",
                external_id="syn-comp-1",
                external_source=SOURCE,
            )
        )

        names = ["Alpha", "Bravo", "Charlie", "Delta"]
        elos = {"Alpha": 1650.0, "Bravo": 1350.0, "Charlie": 1500.0, "Delta": 1500.0}
        team_ids: dict[str, UUID] = {}
        for name in names:
            team = await teams_repo.add(
                Team(
                    name=f"[SYN] {name}",
                    external_id=f"syn-team-{name.lower()}",
                    external_source=SOURCE,
                    elo=EloRating(elos[name]),
                )
            )
            team_ids[name] = team.id

        # 已完赛循环赛：每对交手 ROUNDS 次，主客交替。
        day = 0
        for round_no in range(ROUNDS):
            for a, b in combinations(names, 2):
                home_name, away_name = (a, b) if round_no % 2 == 0 else (b, a)
                day += 1
                await fixtures.add(
                    Fixture(
                        competition_id=competition.id,
                        home_team_id=team_ids[home_name],
                        away_team_id=team_ids[away_name],
                        kickoff=PAST + timedelta(days=day),
                        status=MatchStatus.FINISHED,
                        score=_score(home_name, away_name, home_is=home_name),
                        external_id=f"syn-fin-{day}",
                        external_source=SOURCE,
                    )
                )

        # 目标比赛：Alpha(主) vs Bravo(客)，已排期。
        target = await fixtures.add(
            Fixture(
                competition_id=competition.id,
                home_team_id=team_ids["Alpha"],
                away_team_id=team_ids["Bravo"],
                kickoff=KICKOFF,
                status=MatchStatus.SCHEDULED,
                external_id="syn-target",
                external_source=SOURCE,
            )
        )

        # 目标比赛的赔率：主队慷慨赔率（对强热 Alpha 制造明显正 EV）。
        bookmaker = await bookmakers.add(
            Bookmaker(
                name="[SYN] Bookie",
                external_id="syn-bookie",
                external_source=SOURCE,
            )
        )
        for code, price in [("home", "2.50"), ("draw", "3.80"), ("away", "6.00")]:
            await snaps.add(
                OddsSnapshot(
                    fixture_id=target.id,
                    bookmaker_id=bookmaker.id,
                    selection=Selection(market=MarketType.MATCH_RESULT, code=code),
                    odds=Odds(Decimal(price)),
                    captured_at=KICKOFF - timedelta(hours=6),
                )
            )

        await session.commit()
    await engine.dispose()

    print(f"Seeded synthetic dataset. TARGET_FIXTURE_ID={target.id}")
    return target.id


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic dev dataset (seed/purge).")
    parser.add_argument("command", choices=["seed", "purge"])
    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(seed())
    else:
        asyncio.run(purge())


if __name__ == "__main__":
    main()
