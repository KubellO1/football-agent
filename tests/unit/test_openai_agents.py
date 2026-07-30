"""OpenAI 客户端与 GPT Agent 的纯单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import openai
import pytest
from pydantic import BaseModel

import app.agents.openai_client as openai_client_module
from app.agents.gpt_committee_reviewer import GPTCommitteeReviewer
from app.agents.gpt_reasoning_agent import GPTReasoningAgent
from app.agents.openai_client import OpenAIClient
from app.core.exceptions import ExternalServiceError
from app.schemas.committee_review import (
    CommitteeReview,
    CommitteeReviewContext,
    MarketMovementContext,
    TeamFormContext,
)
from app.schemas.reasoning import ReasoningContext, ReasoningOutput


class ExampleOutput(BaseModel):
    verdict: str


class FakeResponses:
    """模拟 SDK 的 responses 资源。"""

    def __init__(self, *, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, object] = {}

    async def parse(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class FakeSDK:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def _response(
    *,
    parsed: BaseModel | None = None,
    status: str = "completed",
    output: list[object] | None = None,
    incomplete_reason: str | None = None,
) -> SimpleNamespace:
    details = SimpleNamespace(reason=incomplete_reason) if incomplete_reason is not None else None
    return SimpleNamespace(
        status=status,
        incomplete_details=details,
        output=output or [],
        output_parsed=parsed,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    responses: FakeResponses,
) -> OpenAIClient:
    sdk = FakeSDK(responses)
    monkeypatch.setattr(
        openai_client_module,
        "AsyncOpenAI",
        lambda **_kwargs: sdk,
    )
    return OpenAIClient(
        api_key="test-key",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )


@pytest.mark.unit
async def test_openai_client_fails_closed_when_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_constructed(**_kwargs: object) -> None:
        raise AssertionError("SDK must not be constructed without an API key")

    monkeypatch.setattr(openai_client_module, "AsyncOpenAI", fail_if_constructed)
    client = OpenAIClient(api_key="", model="gpt-5.6-sol")

    with pytest.raises(ExternalServiceError, match="API key is not configured"):
        await client.parse_structured(system="system", user="user", schema=ExampleOutput)


@pytest.mark.unit
async def test_openai_client_returns_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = ExampleOutput(verdict="keep")
    responses = FakeResponses(response=_response(parsed=expected))
    client = _client(monkeypatch, responses)

    result = await client.parse_structured(
        system="system prompt",
        user="evidence packet",
        schema=ExampleOutput,
    )

    assert result == expected
    assert responses.kwargs["model"] == "gpt-5.6-sol"
    assert responses.kwargs["reasoning"] == {"effort": "high"}
    assert responses.kwargs["text_format"] is ExampleOutput
    assert responses.kwargs["store"] is False


@pytest.mark.unit
async def test_openai_client_translates_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    client = _client(monkeypatch, FakeResponses(error=error))

    with pytest.raises(ExternalServiceError, match="OpenAI reasoning request failed"):
        await client.parse_structured(system="system", user="user", schema=ExampleOutput)


@pytest.mark.unit
async def test_openai_client_rejects_incomplete_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(status="incomplete", incomplete_reason="max_output_tokens")
    client = _client(monkeypatch, FakeResponses(response=response))

    with pytest.raises(ExternalServiceError, match="incomplete"):
        await client.parse_structured(system="system", user="user", schema=ExampleOutput)


@pytest.mark.unit
async def test_openai_client_rejects_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = SimpleNamespace(type="refusal", refusal="request refused")
    message = SimpleNamespace(type="message", content=[refusal])
    client = _client(
        monkeypatch,
        FakeResponses(response=_response(output=[message])),
    )

    with pytest.raises(ExternalServiceError, match="refused"):
        await client.parse_structured(system="system", user="user", schema=ExampleOutput)


@pytest.mark.unit
async def test_openai_client_rejects_empty_parsed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakeResponses(response=_response()))

    with pytest.raises(ExternalServiceError, match="no parseable"):
        await client.parse_structured(system="system", user="user", schema=ExampleOutput)


class FakeStructuredClient:
    """记录 Agent 交给客户端的 prompt 和 schema。"""

    def __init__(self, response: BaseModel) -> None:
        self.response = response
        self.system = ""
        self.user = ""
        self.schema: type[BaseModel] | None = None

    async def parse_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
    ) -> Any:
        self.system = system
        self.user = user
        self.schema = schema
        return self.response


@pytest.mark.unit
async def test_gpt_reasoning_agent_uses_reasoning_schema() -> None:
    expected = ReasoningOutput(chief_summary="测试汇总", selection_assessments=[])
    fake = FakeStructuredClient(expected)
    agent = GPTReasoningAgent(cast("OpenAIClient", fake))
    context = ReasoningContext(
        fixture_summary="主队 vs 客队",
        kickoff_iso="2026-07-26T18:00:00Z",
        competition="测试联赛",
    )

    result = await agent.analyze(context)

    assert result is expected
    assert fake.schema is ReasoningOutput
    assert "主队 vs 客队" in fake.user
    assert fake.system


@pytest.mark.unit
async def test_gpt_committee_reviewer_uses_committee_schema() -> None:
    expected = CommitteeReview(
        executive_summary="测试汇总",
        why_market_may_be_wrong="市场定价可能滞后",
        why_model_recommends_or_rejects="模型检测到正期望值",
        confidence_explanation="证据质量可接受",
        betting_recommendation_explanation="保持保守仓位",
    )
    fake = FakeStructuredClient(expected)
    reviewer = GPTCommitteeReviewer(cast("OpenAIClient", fake))
    form = TeamFormContext(
        side="home",
        matches_played=5,
        wins=3,
        draws=1,
        losses=1,
        goals_for=8,
        goals_against=4,
    )
    context = CommitteeReviewContext(
        fixture_summary="主队 vs 客队",
        competition="测试联赛",
        kickoff_iso="2026-07-26T18:00:00Z",
        probabilities={"home": 0.5, "draw": 0.3, "away": 0.2},
        league_baseline_rate=1.3,
        league_baseline_metric="xg",
        home_form=form,
        away_form=form.model_copy(update={"side": "away"}),
        market_movement_opening_as_of="2026-07-25T18:00:00+00:00",
        market_movement_current_as_of="2026-07-26T18:00:00+00:00",
        market_movements=[
            MarketMovementContext(
                selection_label="1x2:home",
                opening_captured_at="2026-07-25T17:59:00+00:00",
                current_captured_at="2026-07-26T17:59:00+00:00",
                opening_snapshot_ids=[
                    UUID("00000000-0000-0000-0000-000000000001"),
                    UUID("00000000-0000-0000-0000-000000000002"),
                ],
                opening_bookmaker_ids=[
                    UUID("00000000-0000-0000-0000-000000000011"),
                    UUID("00000000-0000-0000-0000-000000000012"),
                ],
                current_snapshot_ids=[
                    UUID("00000000-0000-0000-0000-000000000003"),
                    UUID("00000000-0000-0000-0000-000000000004"),
                ],
                current_bookmaker_ids=[
                    UUID("00000000-0000-0000-0000-000000000011"),
                    UUID("00000000-0000-0000-0000-000000000012"),
                ],
                opening_consensus_odds=2.1,
                current_consensus_odds=1.9,
                decimal_delta=-0.2,
                implied_probability_shift=0.05,
                direction="shortening",
                opening_snapshot_count=2,
                opening_bookmaker_count=2,
                current_snapshot_count=2,
                current_bookmaker_count=2,
            )
        ],
    )

    result = await reviewer.review(context)

    assert result is expected
    assert fake.schema is CommitteeReview
    assert "主队 vs 客队" in fake.user
    assert "每队每场xG基准：1.300" in fake.user
    assert "已验证盘口变化" in fake.user
    assert "1x2:home" in fake.user
    assert "不得据此重算模型概率或 EV" in fake.user
    assert fake.system
