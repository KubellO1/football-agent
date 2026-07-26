"""基于 GPT 的决策委员会 Reviewer。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.interfaces import CommitteeReviewer
from app.prompts.committee_review import SYSTEM_PROMPT, build_user_prompt
from app.schemas.committee_review import CommitteeReview, CommitteeReviewContext

if TYPE_CHECKING:
    from app.agents.openai_client import OpenAIClient


class GPTCommitteeReviewer(CommitteeReviewer):
    """使用 GPT 评审确定性分析结果，只输出定性意见和反方挑战。"""

    def __init__(self, client: OpenAIClient) -> None:
        self._client = client

    async def review(self, context: CommitteeReviewContext) -> CommitteeReview:
        return await self._client.parse_structured(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(context),
            schema=CommitteeReview,
        )
