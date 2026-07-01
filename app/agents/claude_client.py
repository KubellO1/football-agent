"""Thin async wrapper around the Anthropic SDK.

Isolates every Anthropic-specific detail (client construction, adaptive
thinking, structured output via ``messages.parse``, prompt caching, error
translation) so the rest of the app never imports the SDK directly.
"""

from __future__ import annotations

from typing import TypeVar

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class ClaudeClient:
    """Async client for structured, schema-constrained Claude completions."""

    def __init__(self, api_key: str, model: str, *, max_tokens: int = 4096) -> None:
        # An empty api_key falls back to environment/ambient credential resolution.
        self._client = AsyncAnthropic(api_key=api_key or None)
        self._model = model
        self._max_tokens = max_tokens

    async def parse_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        """Run one structured completion and return a validated ``schema`` instance.

        Uses adaptive thinking (Claude decides how much to reason) and caches the
        stable system prompt. Default effort is ``high`` (omitted → high), which
        suits the analytical task. The output is constrained to ``schema`` so the
        model cannot return free-form text.
        """
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.APIError as exc:  # network, rate limit, 4xx/5xx, etc.
            logger.error("Claude API error: %s", exc)
            raise ExternalServiceError("Claude reasoning request failed") from exc

        if response.stop_reason == "refusal":
            raise ExternalServiceError("Claude refused the reasoning request")

        parsed = response.parsed_output
        if parsed is None:
            raise ExternalServiceError("Claude returned no parseable structured output")
        return parsed
