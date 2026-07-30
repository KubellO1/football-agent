"""决策日志实体（宪法第 16 节）。

记录每次推荐或放弃的可追溯依据：为什么推荐、支持证据、风险、为什么放弃其他
玩法、哪些信息变化会改变结论。用于审计与长期复盘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.models.entities.base import Entity, utcnow

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


@dataclass(eq=False, kw_only=True)
class DecisionLog(Entity):
    """一条决策记录，关联某场比赛（可选关联具体推荐）。"""

    fixture_id: UUID
    summary: str  # 为什么推荐；或为什么判定无价值
    value_bet_id: UUID | None = None
    supporting_evidence: list[str] = field(default_factory=list)  # 前三支持证据
    risks: list[str] = field(default_factory=list)  # 前三风险
    rejected_alternatives: list[str] = field(default_factory=list)  # 为什么放弃其他玩法
    change_conditions: list[str] = field(default_factory=list)  # 哪些信息变化会改变结论
    # 可复现性（宪法第 16 节）：记录评审所用的模型与提示词版本，以及 AI 评审的
    # 完整结构化产出（原样存档，供审计与复盘；不参与任何数值计算）。
    model_version: str | None = None
    prompt_version: str | None = None
    review: dict[str, Any] | None = None
    evidence_snapshot: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utcnow)
