"""Entity-side enumerations."""

from __future__ import annotations

from enum import Enum


class MatchStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class PlayerPosition(str, Enum):
    GOALKEEPER = "GK"
    DEFENDER = "DEF"
    MIDFIELDER = "MID"
    FORWARD = "FWD"


class InjuryStatus(str, Enum):
    DOUBTFUL = "doubtful"
    OUT = "out"
    SUSPENDED = "suspended"
    RETURNED = "returned"
