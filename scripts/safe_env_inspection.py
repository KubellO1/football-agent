"""Inspect container environment configuration without revealing values.

This utility is the only supported way to inspect sensitive environment names in
Docker runtime configuration.  It captures ``docker inspect`` output in memory
and emits presence/redaction markers only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

DEFAULT_NAMES: tuple[str, ...] = (
    "API_FOOTBALL_KEY",
    "INTERNAL_SYNC_TOKEN",
    "ODDS_API_KEY",
    "ODDS_API_IO_API_KEY",
    "WEATHERAPI_KEY",
    "ANTHROPIC_API_KEY",
)

_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def redact_environment(entries: Iterable[str], names: Sequence[str]) -> dict[str, str]:
    """Return requested environment names with values replaced by safe markers."""
    requested = tuple(dict.fromkeys(names))
    for name in requested:
        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Unsafe environment variable name: {name!r}")

    values_by_name: dict[str, list[str]] = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if separator and name in requested:
            values_by_name.setdefault(name, []).append(value)

    result: dict[str, str] = {}
    for name in requested:
        values = values_by_name.get(name, [])
        if not values:
            result[name] = "<missing>"
        elif len(values) > 1:
            result[name] = f"******** (duplicate definitions={len(values)})"
        elif values[0]:
            result[name] = "********"
        else:
            result[name] = "<empty>"
    return result


def inspect_container_environment(container: str, names: Sequence[str]) -> dict[str, str]:
    """Read Docker metadata internally and return redacted environment status."""
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"docker inspect failed for container {container!r}")

    payload = json.loads(completed.stdout)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise RuntimeError("docker inspect returned an unexpected environment payload")
    return redact_environment(payload, names)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect selected Docker environment names without exposing values."
    )
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        help="Environment variable name to inspect; repeat as needed.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    names = tuple(args.names or DEFAULT_NAMES)
    status = inspect_container_environment(args.container, names)
    for name, marker in status.items():
        print(f"{name}={marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
