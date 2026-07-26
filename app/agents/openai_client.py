"""OpenAI Responses API 的异步结构化输出客户端。"""

from __future__ import annotations

from typing import Literal, TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class OpenAIClient:
    """通过 Responses API 返回受 Pydantic schema 约束的结构化结果。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        reasoning_effort: ReasoningEffort = "high",
        max_output_tokens: int = 4096,
        timeout_seconds: float = 60.0,
    ) -> None:
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        # 未配置 Key 时允许基础服务启动；实际推理调用必须失败关闭。
        normalized_key = api_key.strip()
        self._client = (
            AsyncOpenAI(api_key=normalized_key, timeout=timeout_seconds) if normalized_key else None
        )
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens

    async def parse_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        """执行一次结构化推理并返回通过 Pydantic 校验的对象。"""
        if self._client is None:
            logger.error("OpenAI API key is not configured")
            raise ExternalServiceError("OpenAI API key is not configured")

        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                reasoning={"effort": self._reasoning_effort},
                max_output_tokens=self._max_output_tokens,
                text_format=schema,
                store=False,
            )
        except openai.OpenAIError as exc:
            logger.error("OpenAI API request failed: %s", type(exc).__name__)
            raise ExternalServiceError("OpenAI reasoning request failed") from exc
        except PydanticValidationError as exc:
            logger.error("OpenAI structured output validation failed")
            raise ExternalServiceError("OpenAI returned invalid structured output") from exc

        if response.status == "incomplete":
            reason = (
                response.incomplete_details.reason
                if response.incomplete_details is not None
                else "unknown"
            )
            logger.warning("OpenAI response incomplete: %s", reason)
            raise ExternalServiceError(f"OpenAI reasoning response incomplete: {reason}")

        for output in response.output:
            if output.type != "message":
                continue
            for item in output.content:
                if item.type == "refusal":
                    logger.warning("OpenAI refused the reasoning request")
                    raise ExternalServiceError("OpenAI refused the reasoning request")

        parsed = response.output_parsed
        if parsed is None:
            raise ExternalServiceError("OpenAI returned no parseable structured output")
        return parsed
