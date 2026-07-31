"""比赛阵容领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.value_objects.decision import EvidenceLevel


class LineupStatus(StrEnum):
    """阵容在决策时点的确认状态。"""

    PREDICTED = "predicted"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class Formation:
    """不包含门将的阵型表示，例如 4-2-3-1。"""

    notation: str

    def __post_init__(self) -> None:
        notation = self.notation.strip()
        parts = notation.split("-")
        if len(parts) < 3 or len(parts) > 4:
            raise ValueError("formation must contain three or four lines")
        if any(not part.isdigit() or int(part) <= 0 for part in parts):
            raise ValueError("formation lines must be positive integers")
        if sum(int(part) for part in parts) != 10:
            raise ValueError("formation must describe ten outfield players")
        object.__setattr__(self, "notation", notation)

    def __str__(self) -> str:
        return self.notation


@dataclass(frozen=True, slots=True)
class LineupSource:
    """阵容信息的来源及其证据等级。"""

    name: str
    evidence_level: EvidenceLevel
    reference: str | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("source name cannot be empty")
        if len(name) > 80:
            raise ValueError("source name cannot exceed 80 characters")
        object.__setattr__(self, "name", name)

        if self.reference is not None:
            reference = self.reference.strip()
            if not reference:
                raise ValueError("source reference cannot be empty")
            if len(reference) > 500:
                raise ValueError("source reference cannot exceed 500 characters")
            object.__setattr__(self, "reference", reference)
