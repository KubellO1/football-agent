from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
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
        "API_FOOTBALL_KEY": "********",
        "ODDS_API_KEY": "<empty>",
        "MISSING_KEY": "<missing>",
    }
    assert secret not in repr(result)


def test_redact_environment_reports_duplicates_without_values() -> None:
    module = _load_module()

    result = module.redact_environment(
        ["API_FOOTBALL_KEY=first", "API_FOOTBALL_KEY=second"],
        ["API_FOOTBALL_KEY"],
    )

    assert result == {"API_FOOTBALL_KEY": "******** (duplicate definitions=2)"}
    assert "first" not in repr(result)
    assert "second" not in repr(result)


@pytest.mark.parametrize("name", ["bad-name", "NAME=VALUE", "*", ""])
def test_redact_environment_rejects_unsafe_names(name: str) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="Unsafe environment variable name"):
        module.redact_environment([], [name])
