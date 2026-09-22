"""common.resilience.timeout 单测。"""
import dataclasses

import pytest

from common.resilience.timeout import (
    COMPLAINT_GLOBAL_TIMEOUT,
    LLM_TOTAL_TIMEOUT,
    MCP_TOTAL_TIMEOUT,
    Timeout,
)


def test_defaults():
    t = Timeout()
    assert t.connect == 5.0
    assert t.read == 60.0
    assert t.total == 120.0


def test_frozen_config():
    t = Timeout()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.total = 3.0  # type: ignore[misc]


def test_custom_mcp_timeout():
    # MCP 场景：total=30 为硬上限，read 沿用默认 60（total 先到先截断，合法）
    t = Timeout(total=30.0)
    assert t.total == 30.0
    assert t.connect == 5.0
    assert t.read == 60.0


@pytest.mark.parametrize("kwargs", [
    {"connect": 0},
    {"connect": -1.0},
    {"read": 0},
    {"total": -5},
])
def test_invalid_timeout_rejected(kwargs):
    with pytest.raises(ValueError):
        Timeout(**kwargs)


def test_timeout_layering_mcp_before_llm_before_complaint():
    # MCP total=30s < LLM total=120s < 投诉全局 30min
    assert MCP_TOTAL_TIMEOUT < LLM_TOTAL_TIMEOUT < COMPLAINT_GLOBAL_TIMEOUT
    assert MCP_TOTAL_TIMEOUT == 30.0
    assert LLM_TOTAL_TIMEOUT == 120.0
    assert COMPLAINT_GLOBAL_TIMEOUT == 1800.0
