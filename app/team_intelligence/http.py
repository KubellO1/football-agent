"""Conservative HTTP collection with cache, pacing and bounded failure handling."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from fnmatch import fnmatch
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from app.team_intelligence.contracts import RawPayload, SourceDefinition, SourceStatus

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class SourceAccessDenied(RuntimeError):
    pass


class RequestBudgetExceeded(RuntimeError):
    pass


class SourceCircuitOpen(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FetchResult:
    payload: RawPayload
    cache_hit: bool
    status_code: int
    attempts: int


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    content: bytes
    captured_at: datetime
    media_type: str
    expires_at: datetime | None
    etag: str | None
    last_modified: str | None

    def fresh(self, *, now: datetime, ttl: timedelta) -> bool:
        deadline = self.captured_at + ttl
        if self.expires_at is not None:
            deadline = max(deadline, self.expires_at)
        return now <= deadline


class RobotsPolicy:
    """Small robots.txt evaluator for explicit allow/disallow audit checks."""

    def __init__(self, content: str) -> None:
        self._groups = self._parse(content)

    def allowed(self, path: str, *, user_agent: str) -> bool:
        rules = self._rules_for(user_agent.casefold())
        matches = [(pattern, allow) for pattern, allow in rules if _robots_match(path, pattern)]
        if not matches:
            return True
        pattern, allow = max(matches, key=lambda item: len(item[0]))
        return allow or not pattern

    def crawl_delay(self, *, user_agent: str) -> float | None:
        delays = self._groups.get(user_agent.casefold()) or self._groups.get("*") or []
        for directive, value in delays:
            if directive == "crawl-delay":
                try:
                    return float(value)
                except ValueError:
                    return None
        return None

    def _rules_for(self, user_agent: str) -> list[tuple[str, bool]]:
        directives = self._groups.get(user_agent) or self._groups.get("*") or []
        return [
            (value, directive == "allow")
            for directive, value in directives
            if directive in {"allow", "disallow"}
        ]

    @staticmethod
    def _parse(content: str) -> dict[str, list[tuple[str, str]]]:
        groups: dict[str, list[tuple[str, str]]] = {}
        current: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            directive, value = (part.strip() for part in line.split(":", 1))
            directive = directive.casefold()
            if directive == "user-agent":
                current = [value.casefold()]
                groups.setdefault(value.casefold(), [])
                continue
            for agent in current:
                groups.setdefault(agent, []).append((directive, value))
        return groups


class CompliantHttpClient:
    """Fetch only explicitly approved sources; never bypass access controls."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        request_budget: int,
        user_agent: str,
        minimum_interval_seconds: float = 2.0,
        max_retries: int = 2,
        circuit_breaker_failures: int = 3,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if request_budget < 0 or minimum_interval_seconds < 0 or max_retries < 0:
            raise ValueError("invalid request safety configuration")
        if circuit_breaker_failures < 1 or not user_agent.strip():
            raise ValueError("circuit threshold and identifying User-Agent are required")
        self._cache_dir = cache_dir
        self._budget = request_budget
        self._user_agent = user_agent
        self._minimum_interval = minimum_interval_seconds
        self._max_retries = max_retries
        self._circuit_threshold = circuit_breaker_failures
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._requests = 0
        self._last_request: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._client = httpx.Client(
            timeout=20.0,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": user_agent},
        )

    @property
    def requests(self) -> int:
        return self._requests

    def close(self) -> None:
        self._client.close()

    def fetch(
        self,
        source: SourceDefinition,
        url: str,
        *,
        cache_ttl: timedelta,
        robots_policy: RobotsPolicy | None = None,
    ) -> FetchResult:
        self._validate_access(source, url, robots_policy=robots_policy)
        content_path, metadata_path = self._cache_paths(url)
        now = self._clock().astimezone(UTC)
        cached = self._load_cache(content_path, metadata_path)
        if cached is not None and cached.fresh(now=now, ttl=cache_ttl):
            return self._result(source, url, cached, status_code=200, attempts=0, cache_hit=True)

        host = urlparse(url).hostname or ""
        if self._failures.get(host, 0) >= self._circuit_threshold:
            raise SourceCircuitOpen(f"source circuit is open for {host}")
        attempts = 0
        while attempts <= self._max_retries:
            if self._requests >= self._budget:
                raise RequestBudgetExceeded(f"request budget exhausted: {self._budget}")
            self._pace(host)
            headers = self._conditional_headers(cached)
            response = self._client.get(url, headers=headers)
            self._requests += 1
            attempts += 1
            captured_at = self._clock().astimezone(UTC)
            if response.status_code == 304 and cached is not None:
                refreshed = _CacheEntry(
                    content=cached.content,
                    captured_at=captured_at,
                    media_type=cached.media_type,
                    expires_at=_response_datetime(response, "expires") or cached.expires_at,
                    etag=response.headers.get("etag") or cached.etag,
                    last_modified=response.headers.get("last-modified") or cached.last_modified,
                )
                self._write_cache(content_path, metadata_path, url=url, entry=refreshed)
                self._failures[host] = 0
                return self._result(
                    source,
                    url,
                    refreshed,
                    status_code=304,
                    attempts=attempts,
                    cache_hit=True,
                )
            if response.status_code == 429:
                self._failures[host] = self._failures.get(host, 0) + 1
                if attempts > self._max_retries:
                    response.raise_for_status()
                self._sleeper(_retry_delay(response, attempts))
                continue
            if 300 <= response.status_code < 400:
                self._failures[host] = self._failures.get(host, 0) + 1
                raise SourceAccessDenied(
                    "redirects require a separately audited destination source"
                )
            if 500 <= response.status_code < 600 and attempts <= self._max_retries:
                self._failures[host] = self._failures.get(host, 0) + 1
                self._sleeper(min(2**attempts, 8))
                continue
            response.raise_for_status()
            media_type = response.headers.get("content-type", "application/octet-stream")
            entry = _CacheEntry(
                content=response.content,
                captured_at=captured_at,
                media_type=media_type,
                expires_at=_response_datetime(response, "expires"),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
            self._write_cache(content_path, metadata_path, url=url, entry=entry)
            self._failures[host] = 0
            return self._result(
                source,
                url,
                entry,
                status_code=response.status_code,
                attempts=attempts,
                cache_hit=False,
            )
        raise AssertionError("bounded retry loop exhausted unexpectedly")

    def _validate_access(
        self,
        source: SourceDefinition,
        url: str,
        *,
        robots_policy: RobotsPolicy | None,
    ) -> None:
        if source.status is not SourceStatus.APPROVED or source.automation_allowed is not True:
            raise SourceAccessDenied(f"source is not approved for automation: {source.name}")
        if source.login_required:
            raise SourceAccessDenied("login-required sources are not supported")
        if not _within_source_scope(url, source.base_url):
            raise SourceAccessDenied("URL is outside the approved HTTPS source scope")
        path = urlparse(url).path or "/"
        if robots_policy is not None and not robots_policy.allowed(
            path,
            user_agent=self._user_agent,
        ):
            raise SourceAccessDenied("robots policy disallows the requested path")

    def _result(
        self,
        source: SourceDefinition,
        url: str,
        entry: _CacheEntry,
        *,
        status_code: int,
        attempts: int,
        cache_hit: bool,
    ) -> FetchResult:
        return FetchResult(
            payload=RawPayload(
                source_name=source.name,
                source_url=url,
                captured_at=entry.captured_at,
                content=entry.content,
                media_type=entry.media_type,
                source_policy=source.source_policy,
                response_headers={
                    key: value
                    for key, value in {
                        "etag": entry.etag,
                        "last-modified": entry.last_modified,
                        "expires": entry.expires_at.isoformat() if entry.expires_at else None,
                    }.items()
                    if value is not None
                },
            ),
            cache_hit=cache_hit,
            status_code=status_code,
            attempts=attempts,
        )

    def _pace(self, host: str) -> None:
        now = time.monotonic()
        previous = self._last_request.get(host)
        if previous is not None:
            delay = self._minimum_interval - (now - previous)
            if delay > 0:
                self._sleeper(delay)
        self._last_request[host] = time.monotonic()

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{key}.bin", self._cache_dir / f"{key}.metadata.json"

    def _load_cache(self, content_path: Path, metadata_path: Path) -> _CacheEntry | None:
        if not content_path.exists() or not metadata_path.exists():
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expires = metadata.get("expires_at")
        return _CacheEntry(
            content=content_path.read_bytes(),
            captured_at=datetime.fromisoformat(str(metadata["captured_at"])),
            media_type=str(metadata["media_type"]),
            expires_at=datetime.fromisoformat(expires) if isinstance(expires, str) else None,
            etag=str(metadata["etag"]) if metadata.get("etag") else None,
            last_modified=(
                str(metadata["last_modified"]) if metadata.get("last_modified") else None
            ),
        )

    @staticmethod
    def _conditional_headers(cached: _CacheEntry | None) -> dict[str, str]:
        if cached is None:
            return {}
        headers: dict[str, str] = {}
        if cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        return headers

    @staticmethod
    def _write_cache(
        content_path: Path, metadata_path: Path, *, url: str, entry: _CacheEntry
    ) -> None:
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_bytes(entry.content)
        metadata_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "captured_at": entry.captured_at.isoformat(),
                    "media_type": entry.media_type,
                    "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                    "etag": entry.etag,
                    "last_modified": entry.last_modified,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _robots_match(path: str, pattern: str) -> bool:
    if not pattern:
        return True
    normalized = pattern.replace("$", "")
    return fnmatch(path, f"{normalized}*")


def _within_source_scope(url: str, base_url: str) -> bool:
    requested = urlparse(url)
    approved = urlparse(base_url)
    if requested.scheme.casefold() != "https" or approved.scheme.casefold() != "https":
        return False
    if (requested.hostname or "").casefold() != (approved.hostname or "").casefold():
        return False
    if requested.port != approved.port:
        return False
    base_path = approved.path.rstrip("/")
    return (
        not base_path or requested.path == base_path or requested.path.startswith(f"{base_path}/")
    )


def _response_datetime(response: httpx.Response, header: str) -> datetime | None:
    value = response.headers.get(header)
    if not value:
        return None
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("retry-after")
    if value:
        try:
            return min(max(float(value), 0.0), 60.0)
        except ValueError:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return min(max((parsed - datetime.now(UTC)).total_seconds(), 0.0), 60.0)
    return float(min(2**attempt, 8))
