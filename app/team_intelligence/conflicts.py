"""Preserve conflicting evidence while selecting a deterministic display leader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.team_intelligence.contracts import EvidenceLevel

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.team_intelligence.contracts import Observation


_EVIDENCE_PRIORITY = {
    EvidenceLevel.OFFICIAL_MEDICAL_REPORT: 600,
    EvidenceLevel.OFFICIAL_DISCIPLINARY_NOTICE: 500,
    EvidenceLevel.OFFICIAL_MATCH_PAGE: 500,
    EvidenceLevel.OFFICIAL_CONFIRMED: 450,
    EvidenceLevel.DIRECT_COACH_QUOTE: 400,
    EvidenceLevel.UNVERIFIED: 100,
    EvidenceLevel.INFERRED: 50,
}


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    subject_key: str
    observation_ids: tuple[str, ...]
    preferred_observation_id: str
    conflict: bool


def evidence_priority(observation: Observation) -> int:
    base = _EVIDENCE_PRIORITY[observation.evidence_level]
    if observation.source_name.casefold() == "api-football":
        return 200
    return base


def resolve_conflicts(observations: Iterable[Observation]) -> tuple[ConflictResolution, ...]:
    grouped: dict[str, list[Observation]] = {}
    for observation in observations:
        subject = "|".join(
            (
                observation.fixture_id or "-",
                observation.team_id,
                observation.player_id or "-",
                observation.data_type,
                observation.capture_window,
            )
        )
        grouped.setdefault(subject, []).append(observation)

    resolutions: list[ConflictResolution] = []
    for subject, values in sorted(grouped.items()):
        ordered = sorted(
            values,
            key=lambda value: (
                evidence_priority(value),
                value.published_at or value.captured_at,
                value.observation_id,
            ),
            reverse=True,
        )
        payloads = {
            tuple(sorted((key, str(value)) for key, value in item.normalized.items()))
            for item in values
        }
        resolutions.append(
            ConflictResolution(
                subject_key=subject,
                observation_ids=tuple(item.observation_id for item in ordered),
                preferred_observation_id=ordered[0].observation_id,
                conflict=len(payloads) > 1,
            )
        )
    return tuple(resolutions)
