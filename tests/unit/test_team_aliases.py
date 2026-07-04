"""球队别名 accepted_names 的单元测试（人工核对的显式别名，非模糊匹配）。"""

from __future__ import annotations

import pytest

from app.services.odds_matching import normalize_team_name
from app.services.team_aliases import _build_lookup, accepted_names


@pytest.mark.unit
def test_alias_is_symmetric() -> None:
    # 简称与全称互为等价拼写，双向都能解析到同一组
    short = accepted_names(normalize_team_name("Newcastle"))
    full = accepted_names(normalize_team_name("Newcastle United"))
    assert short == full
    assert normalize_team_name("Newcastle") in short
    assert normalize_team_name("Newcastle United") in short


@pytest.mark.unit
def test_unknown_name_passes_through() -> None:
    # 无别名的名字只返回自身（不会凭空扩展）
    norm = normalize_team_name("Some Random FC")
    assert accepted_names(norm) == frozenset({norm})


@pytest.mark.unit
def test_ampersand_and_and_are_equivalent() -> None:
    # "Brighton & Hove Albion" 与 "Brighton and Hove Albion" 都归到 Brighton 组
    group = accepted_names(normalize_team_name("Brighton"))
    assert normalize_team_name("Brighton and Hove Albion") in group
    assert normalize_team_name("Brighton & Hove Albion") in group


@pytest.mark.unit
def test_conflicting_alias_groups_raise() -> None:
    # 同一归一化名出现在两个组 → 加载即报错（防止一名映射到两队）
    with pytest.raises(ValueError, match="more than one group"):
        _build_lookup([["Alpha", "Shared"], ["Beta", "Shared"]])
