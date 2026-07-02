"""AI reasoning agents (Claude)."""

from __future__ import annotations

from app.agents.claude_client import ClaudeClient
from app.agents.committee_reviewer import ClaudeCommitteeReviewer
from app.agents.interfaces import CommitteeReviewer, ReasoningEngine
from app.agents.reasoning_agent import ClaudeReasoningAgent
from app.config.settings import Settings


def build_reasoning_agent(settings: Settings) -> ReasoningEngine:
    """Composition helper: wire a Claude-backed reasoning engine from settings."""
    client = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
    return ClaudeReasoningAgent(client)


def build_committee_reviewer(settings: Settings) -> CommitteeReviewer:
    """Composition helper: wire a Claude-backed committee reviewer from settings."""
    client = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
    return ClaudeCommitteeReviewer(client)


__all__ = [
    "ClaudeClient",
    "ClaudeCommitteeReviewer",
    "ClaudeReasoningAgent",
    "CommitteeReviewer",
    "ReasoningEngine",
    "build_committee_reviewer",
    "build_reasoning_agent",
]
