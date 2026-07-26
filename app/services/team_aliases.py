"""球队名别名（用于赔率↔比赛匹配）——人工核对、显式、绝不模糊。

跨数据源球队名拼写不同：The Odds API 用全称（"Newcastle United"），API-Football
存简称（"Newcastle"）。这里用**人工核对**的别名组消除这种差异，从而在不放松
「绝不猜测」原则（见 odds_matching）的前提下提高命中率——不做相似度/模糊/后缀
剥离等任何推断。

每组是同一支球队在不同数据源下的等价拼写；每个归一化名至多属于一个组，加载时
校验冲突。当前覆盖英超 + 西甲 + 德甲 + 意甲 + 法甲（由 _alias_probe 对比两数据源
队名探得）；其它联赛按需逐步补充。
"""

from __future__ import annotations

from app.services.odds_matching import normalize_team_name

# 每组内的名称视为同一支球队的等价拼写（原始文本，加载时统一归一化）。
# 每组左侧为库内(API-Football)名，右侧为 The Odds API 的拼写（由 _alias_probe 探得）。
_ALIAS_GROUPS: list[list[str]] = [
    # --- Premier League ---
    ["Newcastle", "Newcastle United"],
    ["Wolves", "Wolverhampton Wanderers", "Wolverhampton"],
    ["Ipswich", "Ipswich Town"],
    ["Brighton", "Brighton and Hove Albion", "Brighton & Hove Albion"],
    ["West Ham", "West Ham United"],
    ["Tottenham", "Tottenham Hotspur", "Spurs"],
    ["Leicester", "Leicester City"],
    # --- La Liga ---
    ["Athletic Club", "Athletic Bilbao"],
    ["Osasuna", "CA Osasuna"],
    # --- Bundesliga ---
    ["FC Augsburg", "Augsburg"],
    ["Bayern München", "Bayern Munich"],
    ["1899 Hoffenheim", "TSG Hoffenheim"],
    # --- Serie A ---
    ["Atalanta", "Atalanta BC"],
    ["Inter", "Inter Milan"],
    # --- Ligue 1 ---
    ["Monaco", "AS Monaco"],
    ["Stade Brestois 29", "Brest"],
    ["Lens", "RC Lens"],
    ["Reims", "Stade de Reims"],
    # --- National Teams (World Cup 2026) ---
    ["United States", "USA", "USMNT"],
    ["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"],
    ["DR Congo", "Congo DR", "Democratic Republic of the Congo"],
    ["South Korea", "Korea Republic"],
    ["Cape Verde", "Cabo Verde"],
    ["Czechia", "Czech Republic"],
    ["Bosnia-Herzegovina", "Bosnia and Herzegovina"],
    ["Türkiye", "Turkey"],
    ["Iran", "IR Iran"],
]


def _build_lookup(groups: list[list[str]]) -> dict[str, frozenset[str]]:
    """把别名组编译为「归一化名 → 该组全部归一化名」的查找表，冲突即报错。"""
    lookup: dict[str, frozenset[str]] = {}
    for group in groups:
        norms = frozenset(normalize_team_name(name) for name in group)
        for norm in norms:
            if norm in lookup:
                raise ValueError(f"team alias {norm!r} belongs to more than one group")
            lookup[norm] = norms
    return lookup


_LOOKUP = _build_lookup(_ALIAS_GROUPS)


def accepted_names(norm: str) -> frozenset[str]:
    """返回归一化名 ``norm`` 的全部等价拼写（含自身）；无别名时仅含自身。"""
    return _LOOKUP.get(norm, frozenset({norm}))
