"""MCP 工具响应形状契约（网关产出、客户端消费，单一事实源）。

历史问题（T35）：FastMCP 对 ``list`` 返回值按「每元素一个 content block」编码，
客户端无法从线上形态区分 list/dict，只能在客户端静态镜像「返回 list 的工具」
集合，网关新增工具必须双处同步维护。

现行契约：网关在 ``dispatch`` 出口把工具原始返回值包成
``{"shape": "list"|"object", "payload": <原始值>}``，作为
``structuredContent`` 返回；客户端只按 ``shape`` 还原，不再维护工具名单。
"""
from __future__ import annotations

from typing import Any

SHAPE_KEY = "shape"
PAYLOAD_KEY = "payload"
SHAPE_LIST = "list"
SHAPE_OBJECT = "object"
VALID_SHAPES = frozenset({SHAPE_LIST, SHAPE_OBJECT})


def typed_result(value: Any) -> dict[str, Any]:
    """把工具返回值包成带形状元数据的响应体。"""
    shape = SHAPE_LIST if isinstance(value, list) else SHAPE_OBJECT
    return {SHAPE_KEY: shape, PAYLOAD_KEY: value}


def unwrap_typed_result(structured: Any) -> Any:
    """从 structuredContent 还原原始返回值；不合契约时抛 ValueError。

    校验形状与 payload 类型一致，避免客户端静默接受错位数据。
    """
    if not isinstance(structured, dict):
        raise ValueError("structuredContent is not an object")
    shape = structured.get(SHAPE_KEY)
    if shape not in VALID_SHAPES:
        raise ValueError(f"missing/invalid response shape metadata: {shape!r}")
    if PAYLOAD_KEY not in structured:
        raise ValueError("missing response payload")
    payload = structured[PAYLOAD_KEY]
    if shape == SHAPE_LIST and not isinstance(payload, list):
        raise ValueError(f"shape=list but payload is {type(payload).__name__}")
    if shape == SHAPE_OBJECT and not isinstance(payload, dict):
        raise ValueError(f"shape=object but payload is {type(payload).__name__}")
    return payload


def has_shape_metadata(structured: Any) -> bool:
    """判断 structuredContent 是否携带本契约的形状元数据。"""
    return isinstance(structured, dict) and structured.get(SHAPE_KEY) in VALID_SHAPES
