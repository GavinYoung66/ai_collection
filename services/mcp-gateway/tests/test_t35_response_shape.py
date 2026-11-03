"""T35：网关响应形状元数据单测。

验收口径：
- 工具出口响应带 ``shape`` 元数据（list/object），客户端据此还原，不再双处维护；
- 所有已注册工具都能声明结构化输出（FastMCP 不会把 dict 再包成 ``{"result": ...}``）；
- 形状与 payload 类型不一致时契约层直接报错。
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import app.mcp_server as mcp_mod
from common.mcp.contract import (
    PAYLOAD_KEY,
    SHAPE_KEY,
    SHAPE_LIST,
    SHAPE_OBJECT,
    typed_result,
    unwrap_typed_result,
)


def test_typed_result_marks_list():
    payload = [{"doc_id": "R1"}]
    assert typed_result(payload) == {SHAPE_KEY: SHAPE_LIST, PAYLOAD_KEY: payload}


def test_typed_result_marks_object():
    payload = {"case_id": "c1"}
    assert typed_result(payload) == {SHAPE_KEY: SHAPE_OBJECT, PAYLOAD_KEY: payload}


@pytest.mark.parametrize(
    "structured",
    [
        {"shape": "list", "payload": {"a": 1}},
        {"shape": "object", "payload": [1, 2]},
        {"shape": "unknown", "payload": []},
        {"payload": []},
    ],
)
def test_unwrap_rejects_mismatched_shape(structured):
    with pytest.raises(ValueError):
        unwrap_typed_result(structured)


@pytest.mark.asyncio
async def test_dispatch_typed_wraps_list_payload():
    with patch.object(mcp_mod, "dispatch", new=AsyncMock(return_value=[{"doc_id": "R1"}])):
        result = await mcp_mod._dispatch_typed("rag_kb", "search_regulations", {"query": "q"})
    assert result[SHAPE_KEY] == SHAPE_LIST
    assert result[PAYLOAD_KEY] == [{"doc_id": "R1"}]
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_dispatch_typed_wraps_object_payload():
    with patch.object(mcp_mod, "dispatch", new=AsyncMock(return_value={"case_id": "c1"})):
        result = await mcp_mod._dispatch_typed("business_ticket", "query_case_info", {"case_id": "c1"})
    assert result[SHAPE_KEY] == SHAPE_OBJECT
    assert result[PAYLOAD_KEY] == {"case_id": "c1"}


@pytest.mark.asyncio
async def test_all_registered_tools_have_structured_output():
    """所有工具必须走结构化输出，响应才可能携带 shape 元数据。"""
    tools = mcp_mod.mcp._tool_manager.list_tools()
    assert len(tools) == 10
    for tool in tools:
        meta = tool.fn_metadata
        assert meta.output_schema is not None, tool.name
        assert meta.output_schema.get("type") == "object", tool.name
        # dict 返回不得被再包成 {"result": ...}，否则客户端拿不到 shape
        assert meta.wrap_output is False, tool.name


@pytest.mark.asyncio
async def test_tool_response_carries_shape_metadata_end_to_end():
    """真实注册链路（含 PII 脱敏/兜底）返回的 structuredContent 带 shape 元数据。"""
    result: Any = await mcp_mod.mcp._tool_manager.call_tool(
        "business_ticket.query_case_info", {"case_id": "c1"}, context=None
    )
    assert result[SHAPE_KEY] == SHAPE_OBJECT
    assert "payload" in result
    assert unwrap_typed_result(result) == result["payload"]
