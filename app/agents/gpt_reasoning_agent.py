"""基于 GPT 的比赛推理 Agent。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.interfaces import ReasoningEngine
from app.prompts.match_reasoning import SYSTEM_PROMPT, build_user_prompt
from app.schemas.reasoning import ReasoningContext, ReasoningOutput

if TYPE_CHECKING:
    from app.agents.openai_client import OpenAIClient


class GPTReasoningAgent(ReasoningEngine):
    """使用 GPT 审查数学模型产生的候选投注，不修改任何量化数值。"""

    def __init__(self, client: OpenAIClient) -> None:
        self._client = client

    async def analyze(self, context: ReasoningContext) -> ReasoningOutput:
        return await self._client.parse_structured(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(context),
            schema=ReasoningOutput,
        )
