"""Season ORM 与 Alembic 迁移链的结构合同测试。"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint

from app.repositories.sqlalchemy.models import SeasonORM


def test_season_orm_declares_canonical_natural_key() -> None:
    constraints = [
        constraint
        for constraint in SeasonORM.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_seasons_competition_label"
    ]

    assert len(constraints) == 1
    assert [column.name for column in constraints[0].columns] == ["competition_id", "label"]


def test_alembic_graph_is_linear_through_0021() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0021"]

    revisions = list(script.walk_revisions(base="base", head="heads"))
    revision_ids = [revision.revision for revision in revisions]

    assert revision_ids == [f"{revision:04d}" for revision in range(21, 0, -1)]
    assert len(revision_ids) == len(set(revision_ids))
    assert all(revision.is_branch_point is False for revision in revisions)
    assert revisions[-1].down_revision is None
    assert all(
        revision.down_revision == revisions[index + 1].revision
        for index, revision in enumerate(revisions[:-1])
    )
