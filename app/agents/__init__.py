"""AI 推理 Agent 的组合入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.gpt_committee_reviewer import GPTCommitteeReviewer
from app.agents.gpt_reasoning_agent import GPTReasoningAgent
from app.agents.interfaces import CommitteeReviewer, ReasoningEngine
from app.agents.openai_client import OpenAIClient

if TYPE_CHECKING:
    from app.config.settings import Settings


def build_reasoning_agent(settings: Settings) -> ReasoningEngine:
    """根据配置构造当前 GPT 推理 Agent。"""
    client = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort,
    )
    return GPTReasoningAgent(client)


def build_committee_reviewer(settings: Settings) -> CommitteeReviewer:
    """根据配置构造当前 GPT 决策委员会 Reviewer。"""
    client = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort,
    )
    return GPTCommitteeReviewer(client)


__all__ = [
    "CommitteeReviewer",
    "GPTCommitteeReviewer",
    "GPTReasoningAgent",
    "OpenAIClient",
    "ReasoningEngine",
    "build_committee_reviewer",
    "build_reasoning_agent",
]
