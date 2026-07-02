"""数学模型子包（真相来源）。

Elo / Poisson / 蒙特卡洛 / Kelly / 价值检测等子模型，各自独立、可单测，
最终由 EnsembleMatchModel 组合实现 MatchModel 契约。
"""

from app.services.models.kelly import KellyCalculator

__all__ = ["KellyCalculator"]
