"""Claude-backed implementation of the reasoning engine."""

from __future__ import annotations

from app.agents.claude_client import ClaudeClient
from app.agents.interfaces import ReasoningEngine
from app.prompts.match_reasoning import SYSTEM_PROMPT, build_user_prompt
from app.schemas.reasoning import ReasoningContext, ReasoningOutput


class ClaudeReasoningAgent(ReasoningEngine):
    """Reviews model-generated recommendations using Claude.

    Assembles the evidence packet into a prompt, sends it to Claude under strict
    structured-output constraints, and returns the validated assessment. It never
    mutates the quantitative numbers — the returned ``ReasoningOutput`` carries
    only verdicts, confidence, and explanations.
    """

    def __init__(self, client: ClaudeClient) -> None:
        self._client = client

    async def analyze(self, context: ReasoningContext) -> ReasoningOutput:
        user_prompt = build_user_prompt(context)
        return await self._client.parse_structured(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            schema=ReasoningOutput,
        )
