"""统一响应工具。

成功：code 为 int 0；错误：code 为 str（如 "A5014"）。
trace_id 优先取 X-Trace-Id header，否则生成。
"""
from __future__ import annotations

import uuid
from typing import Any, Optional, Union

from fastapi import Request

from common.errors.codes import ErrorCode

CodeType = Union[int, str]


def get_trace_id(request: Optional[Request] = None) -> str:
    """从请求头取 trace_id，否则生成。"""
    if request is not None:
        tid = request.headers.get("x-trace-id")
        if tid:
            return tid
    return uuid.uuid4().hex


def success(data: Any = None, message: str = "ok", request: Optional[Request] = None) -> dict:
    return {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(request),
    }


def fail(code: CodeType, message: str, data: Any = None, request: Optional[Request] = None) -> dict:
    """code 为 str（错误码）或 ErrorCode。"""
    if isinstance(code, ErrorCode):
        code = code.value
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(request),
    }
