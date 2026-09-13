"""有预算、缓存和来源级限速的只读 HTTP 客户端。"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import httpx

from app.intelligence.contracts import RawPayload, SourcePolicy

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class FetchResult:
    payload: RawPayload
    cache_hit: bool
    status_code: int


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    content: bytes
    captured_at: datetime
    media_type: str
    expires_at: datetime | None
    last_modified: str | None

    def is_fresh(self, *, now: datetime, ttl: timedelta) -> bool:
        deadline = self.captured_at + ttl
        if self.expires_at is not None:
            deadline = max(deadline, self.expires_at)
        return now <= deadline


class RequestBudgetExceeded(RuntimeError):
    pass


class CachedHttpClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        request_budget: int,
        user_agent: str,
        minimum_interval_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if request_budget < 0 or minimum_interval_seconds < 0:
            raise ValueError("request budget and interval must be non-negative")
        if not user_agent.strip():
            raise ValueError("an identifying User-Agent is required")
        self._cache_dir = cache_dir
        self._budget = request_budget
        self._minimum_interval = minimum_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._last_request_at: float | None = None
        self._requests = 0
        self._client = httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    @property
    def requests(self) -> int:
        return self._requests

    def close(self) -> None:
        self._client.close()

    def get_json(
        self,
        url: str,
        *,
        source: str,
        source_policy: SourcePolicy,
        cache_ttl: timedelta,
    ) -> FetchResult:
        if not url.startswith("https://"):
            raise ValueError("only HTTPS sources are accepted")
        content_path, metadata_path = self._cache_paths(url)
        now = self._clock().astimezone(UTC)
        cached = self._load_cache(content_path, metadata_path)
        if cached is not None and cached.is_fresh(now=now, ttl=cache_ttl):
            return FetchResult(
                RawPayload(
                    source=source,
                    source_url=url,
                    captured_at=cached.captured_at,
                    content=cached.content,
                    media_type=cached.media_type,
                    source_policy=source_policy,
                ),
                True,
                200,
            )
        if self._requests >= self._budget:
            raise RequestBudgetExceeded(f"request budget exhausted: {self._budget}")
        self._throttle()
        request_headers = (
            {"If-Modified-Since": cached.last_modified}
            if cached is not None and cached.last_modified is not None
            else None
        )
        response = self._client.get(url, headers=request_headers)
        self._requests += 1
        captured_at = self._clock().astimezone(UTC)
        if response.status_code == 304 and cached is not None:
            self._write_metadata(
                metadata_path,
                url=url,
                captured_at=captured_at,
                media_type=cached.media_type,
                expires_at=_response_datetime(response, "expires") or cached.expires_at,
                last_modified=response.headers.get("last-modified") or cached.last_modified,
            )
            return FetchResult(
                RawPayload(
                    source=source,
                    source_url=url,
                    captured_at=captured_at,
                    content=cached.content,
                    media_type=cached.media_type,
                    source_policy=source_policy,
                ),
                True,
                response.status_code,
            )
        response.raise_for_status()
        response.json()
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_bytes(response.content)
        media_type = response.headers.get("content-type", "application/json")
        self._write_metadata(
            metadata_path,
            url=url,
            captured_at=captured_at,
            media_type=media_type,
            expires_at=_response_datetime(response, "expires"),
            last_modified=response.headers.get("last-modified"),
        )
        return FetchResult(
            RawPayload(
                source=source,
                source_url=url,
                captured_at=captured_at,
                content=response.content,
                media_type=media_type,
                source_policy=source_policy,
            ),
            False,
            response.status_code,
        )

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()
        return self._cache_dir / f"{key}.json", self._cache_dir / f"{key}.metadata.json"

    def _load_cache(
        self,
        content_path: Path,
        metadata_path: Path,
    ) -> _CacheEntry | None:
        if not content_path.exists() or not metadata_path.exists():
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        captured_at = datetime.fromisoformat(metadata["captured_at"])
        media_type = str(metadata.get("media_type", "application/json"))
        expires_value = metadata.get("expires_at")
        expires_at = (
            datetime.fromisoformat(expires_value) if isinstance(expires_value, str) else None
        )
        last_modified = metadata.get("last_modified")
        return _CacheEntry(
            content=content_path.read_bytes(),
            captured_at=captured_at,
            media_type=media_type,
            expires_at=expires_at,
            last_modified=str(last_modified) if last_modified else None,
        )

    @staticmethod
    def _write_metadata(
        metadata_path: Path,
        *,
        url: str,
        captured_at: datetime,
        media_type: str,
        expires_at: datetime | None,
        last_modified: str | None,
    ) -> None:
        metadata_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "captured_at": captured_at.isoformat(),
                    "media_type": media_type,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "last_modified": last_modified,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            delay = self._minimum_interval - (now - self._last_request_at)
            if delay > 0:
                self._sleeper(delay)
        self._last_request_at = time.monotonic()


def _response_datetime(response: httpx.Response, header: str) -> datetime | None:
    value = response.headers.get(header)
    if not value:
        return None
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
