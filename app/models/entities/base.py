"""Entity base class.

Entities are distinguished by identity (a UUID), not by their attribute values.
Two entities are equal iff they are the same type and share the same id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp (default for created/updated fields)."""
    return datetime.now(timezone.utc)


@dataclass(eq=False, kw_only=True)
class Entity:
    """Base for all domain entities: identity-based equality."""

    id: UUID = field(default_factory=uuid4)

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self.id == other.id  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))
