from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


def _load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "safe_env_inspection.py"
    spec = importlib.util.spec_from_file_location("safe_env_inspection", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_redact_environment_never_returns_secret_values() -> None:
    module = _load_module()
    secret = "sensitive-value-that-must-not-appear"

    result = module.redact_environment(
        [f"API_FOOTBALL_KEY={secret}", "ODDS_API_KEY="],
        ["API_FOOTBALL_KEY", "ODDS_API_KEY", "MISSING_KEY"],
    )

    assert result == {
        "API_FOOTBALL_KEY": "REDACTED",
        "ODDS_API_KEY": "EMPTY",
        "MISSING_KEY": "EMPTY",
    }
    assert secret not in repr(result)


def test_redact_environment_reports_duplicates_without_values() -> None:
    module = _load_module()

    result = module.redact_environment(
        ["API_FOOTBALL_KEY=first", "API_FOOTBALL_KEY=second"],
        ["API_FOOTBALL_KEY"],
    )

    assert result == {"API_FOOTBALL_KEY": "REDACTED"}
    assert "first" not in repr(result)
    assert "second" not in repr(result)


@pytest.mark.parametrize("name", ["bad-name", "NAME=VALUE", "*", ""])
def test_redact_environment_rejects_unsafe_names(name: str) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="Unsafe environment variable name"):
        module.redact_environment([], [name])


def test_only_safe_markers_can_be_emitted() -> None:
    module = _load_module()

    result = module.redact_environment(
        ["API_FOOTBALL_KEY=secret", "ODDS_API_KEY="],
        ["API_FOOTBALL_KEY", "ODDS_API_KEY", "MISSING_KEY"],
    )

    assert set(result.values()) <= {"SET", "EMPTY", "REDACTED"}


def test_no_other_script_reads_docker_config_env() -> None:
    repository = Path(__file__).parents[2]
    safe_helper = repository / "scripts" / "safe_env_inspection.py"
    offenders: list[str] = []

    for path in (repository / "scripts").rglob("*"):
        if path == safe_helper or path.suffix.lower() not in {".py", ".ps1", ".sh"}:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if ".Config.Env" in content:
            offenders.append(str(path.relative_to(repository)))

    assert offenders == []
