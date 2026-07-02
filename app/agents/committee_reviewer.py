"""Claude-backed implementation of the committee reviewer."""

from __future__ import annotations

from app.agents.claude_client import ClaudeClient
from app.agents.interfaces import CommitteeReviewer
from app.prompts.committee_review import SYSTEM_PROMPT, build_user_prompt
from app.schemas.committee_review import CommitteeReview, CommitteeReviewContext


class ClaudeCommitteeReviewer(CommitteeReviewer):
    """Reviews a fixture's deterministic analysis using Claude.

    Renders the evidence packet into a prompt and returns Claude's structured,
    schema-constrained review. It never mutates the quantitative numbers — the
    output carries only qualitative explanations, critiques, and recorded
    disagreements.
    """

    def __init__(self, client: ClaudeClient) -> None:
        self._client = client

    async def review(self, context: CommitteeReviewContext) -> CommitteeReview:
        return await self._client.parse_structured(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(context),
            schema=CommitteeReview,
        )
