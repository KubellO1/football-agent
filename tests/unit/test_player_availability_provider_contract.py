"""球员可用性 Provider 契约与 DTO 测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import Parameter, iscoroutinefunction, signature

import pytest
from pydantic import ValidationError

from app.providers.interfaces.player_availability_provider import (
    PlayerAvailabilityProvider,
)
from app.providers.schemas.player_availability import (
    ProviderAvailabilityBatch,
    ProviderPlayerAvailability,
)


@pytest.mark.unit
def test_provider_contract_is_abstract_and_async() -> None:
    assert "get_fixture_availability" in PlayerAvailabilityProvider.__abstractmethods__
    assert iscoroutinefunction(PlayerAvailabilityProvider.get_fixture_availability)


@pytest.mark.unit
def test_provider_query_requires_keyword_only_fixture_external_id() -> None:
    parameters = signature(
        PlayerAvailabilityProvider.get_fixture_availability,
    ).parameters

    assert tuple(parameters) == ("self", "fixture_external_id")
    fixture_id = parameters["fixture_external_id"]
    assert fixture_id.kind is Parameter.KEYWORD_ONLY
    assert fixture_id.default is Parameter.empty


@pytest.mark.unit
def test_availability_dto_normalizes_external_identifiers() -> None:
    record = ProviderPlayerAvailability(
        team_external_id=" 33 ",
        player_external_id=" 99 ",
        player_name=" Test Player ",
        raw_status=" Questionable ",
        reason=" Muscle injury ",
        source_updated_at=datetime(2026, 7, 31, 10, 0, tzinfo=UTC),
    )

    assert record.team_external_id == "33"
    assert record.player_external_id == "99"
    assert record.player_name == "Test Player"
    assert record.raw_status == "Questionable"
    assert record.reason == "Muscle injury"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("team_external_id", " "),
        ("player_external_id", " "),
        ("raw_status", " "),
    ],
)
def test_availability_dto_rejects_blank_required_facts(
    field: str,
    value: str,
) -> None:
    values = {
        "team_external_id": "33",
        "player_external_id": "99",
        "raw_status": "Questionable",
        field: value,
    }

    with pytest.raises(ValidationError):
        ProviderPlayerAvailability.model_validate(values)


@pytest.mark.unit
def test_availability_dto_rejects_naive_source_timestamp() -> None:
    with pytest.raises(ValidationError, match="source_updated_at must be timezone-aware"):
        ProviderPlayerAvailability(
            team_external_id="33",
            player_external_id="99",
            raw_status="Questionable",
            source_updated_at=datetime(2026, 7, 31, 10, 0),
        )


@pytest.mark.unit
def test_batch_can_explicitly_represent_complete_empty_response() -> None:
    batch = ProviderAvailabilityBatch(
        source=" api-football ",
        fixture_external_id=" 1234 ",
        captured_at=datetime(2026, 7, 31, 10, 0, tzinfo=UTC),
        response_complete=True,
    )

    assert batch.source == "api-football"
    assert batch.fixture_external_id == "1234"
    assert batch.response_complete is True
    assert batch.records == []


@pytest.mark.unit
def test_batch_rejects_naive_capture_timestamp() -> None:
    with pytest.raises(ValidationError, match="captured_at must be timezone-aware"):
        ProviderAvailabilityBatch(
            source="api-football",
            fixture_external_id="1234",
            captured_at=datetime(2026, 7, 31, 10, 0),
            response_complete=True,
        )
