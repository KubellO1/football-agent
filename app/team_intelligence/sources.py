"""Load and validate the audited source registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.team_intelligence.contracts import (
    DataType,
    SourceDefinition,
    SourcePolicy,
    SourceStatus,
    SourceType,
)

DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "research" / "team_intelligence" / "source_registry.json"
)


def load_source_registry(path: Path = DEFAULT_REGISTRY_PATH) -> tuple[SourceDefinition, ...]:
    document: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError("source registry must contain a list")
    sources: list[SourceDefinition] = []
    names: set[str] = set()
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("source registry entries must be objects")
        source = SourceDefinition(
            name=_text(item, "name"),
            base_url=_text(item, "base_url"),
            source_type=SourceType(_text(item, "source_type")),
            leagues=_text_tuple(item, "leagues"),
            clubs=_text_tuple(item, "clubs"),
            data_types=frozenset(DataType(value) for value in _text_tuple(item, "data_types")),
            terms_url=_optional_text(item, "terms_url"),
            robots_url=_optional_text(item, "robots_url"),
            automation_allowed=_optional_bool(item, "automation_allowed"),
            storage_allowed=_optional_bool(item, "storage_allowed"),
            login_required=_bool(item, "login_required"),
            page_structure=_text(item, "page_structure"),
            update_frequency=_text(item, "update_frequency"),
            parser_difficulty=_text(item, "parser_difficulty"),
            status=SourceStatus(_text(item, "status")),
            source_policy=SourcePolicy(_text(item, "source_policy")),
            notes=_text(item, "notes"),
        )
        if source.name in names:
            raise ValueError(f"duplicate source name: {source.name}")
        names.add(source.name)
        sources.append(source)
    return tuple(sources)


def _text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"source registry field {key} must be non-empty text")
    return value.strip()


def _optional_text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"source registry field {key} must be text or null")
    return value.strip()


def _text_tuple(item: dict[str, Any], key: str) -> tuple[str, ...]:
    value = item.get(key)
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise ValueError(f"source registry field {key} must be a text list")
    return tuple(entry.strip() for entry in value if entry.strip())


def _bool(item: dict[str, Any], key: str) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"source registry field {key} must be boolean")
    return value


def _optional_bool(item: dict[str, Any], key: str) -> bool | None:
    value = item.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"source registry field {key} must be boolean or null")
    return value
