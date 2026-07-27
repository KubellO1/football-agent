"""AI 提示词核心原则的不变量测试。"""

from __future__ import annotations

import pytest

from app.prompts.committee_review import (
    PROMPT_VERSION as COMMITTEE_PROMPT_VERSION,
)
from app.prompts.committee_review import (
    SYSTEM_PROMPT as COMMITTEE_SYSTEM_PROMPT,
)
from app.prompts.match_reasoning import (
    PROMPT_VERSION as REASONING_PROMPT_VERSION,
)
from app.prompts.match_reasoning import (
    SYSTEM_PROMPT as REASONING_SYSTEM_PROMPT,
)

SYSTEM_PROMPTS = [
    pytest.param(REASONING_SYSTEM_PROMPT, id="match-reasoning"),
    pytest.param(COMMITTEE_SYSTEM_PROMPT, id="committee-review"),
]


@pytest.mark.unit
@pytest.mark.parametrize("system_prompt", SYSTEM_PROMPTS)
def test_system_prompt_keeps_mathematical_source_of_truth(system_prompt: str) -> None:
    assert "数学模型" in system_prompt
    assert "唯一真相来源" in system_prompt


@pytest.mark.unit
@pytest.mark.parametrize("system_prompt", SYSTEM_PROMPTS)
def test_system_prompt_forbids_numeric_changes(system_prompt: str) -> None:
    assert "不得" in system_prompt
    assert "修改" in system_prompt
    assert "新增" in system_prompt
    assert "数值" in system_prompt


@pytest.mark.unit
@pytest.mark.parametrize("system_prompt", SYSTEM_PROMPTS)
def test_system_prompt_prioritizes_risk_control(system_prompt: str) -> None:
    assert "风险控制" in system_prompt
    assert "优先" in system_prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (REASONING_PROMPT_VERSION, "match-reasoning/zh-v1"),
        (COMMITTEE_PROMPT_VERSION, "committee-review/zh-v1"),
    ],
)
def test_prompt_version_is_stable(version: str, expected: str) -> None:
    assert version == expected
