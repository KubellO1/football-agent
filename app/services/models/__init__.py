"""数学模型子包（真相来源）。

Elo / Poisson / 蒙特卡洛 / Kelly / 价值检测等子模型，各自独立、可单测，
由 EnsembleMatchModel 组合实现 MatchModel 契约。
"""

from app.services.models.kelly import KellyCalculator
from app.services.models.lambda_estimator import LambdaEstimator, LeagueAverages
from app.services.models.poisson import PoissonModel
from app.services.models.value_detector import ValueAssessment, ValueDetector

# EnsembleMatchModel 依赖 app.services.modeling，放最后导入以避免循环。
from app.services.models.ensemble import EnsembleMatchModel

__all__ = [
    "EnsembleMatchModel",
    "KellyCalculator",
    "LambdaEstimator",
    "LeagueAverages",
    "PoissonModel",
    "ValueAssessment",
    "ValueDetector",
]
