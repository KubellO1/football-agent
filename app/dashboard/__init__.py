"""Dashboard 模块 — 数据驱动的 HTML 仪表盘渲染。

从归一化 JSON / 数据库数据生成 Bloomberg/Opta 风格的深色主题 HTML 仪表盘。
支持两种类型：日概览与单场详情。
"""

from app.dashboard.renderer import DashboardRenderer
from app.dashboard.types import (
    DailyDashboardData,
    DashboardData,
    InjuryDashboard,
    LineupDashboard,
    MatchCentreDashboard,
    MatchDashboardData,
    RecentFormDashboard,
    StandingsDashboard,
    TVBroadcastDashboard,
)

__all__ = [
    "DailyDashboardData",
    "DashboardData",
    "DashboardRenderer",
    "InjuryDashboard",
    "LineupDashboard",
    "MatchCentreDashboard",
    "MatchDashboardData",
    "RecentFormDashboard",
    "StandingsDashboard",
    "TVBroadcastDashboard",
]
