"""qc-service MCP endpoint（/mcp，streamable-http 最小子集，零新增依赖）。

复用 CallQcServer 的 list_tools / call 逻辑（FORBIDDEN_TOOLS 校验仍在 server 内），
对外暴露 MCP 协议端点供 mcp-gateway 的 MCP client（streamable-http）调用。

实现范围（与网关 mcp 1.x SDK client 对齐的有状态无关子集）：
- initialize：回显客户端 protocolVersion（无状态，无需协商存储）
- notifications/initialized：202 Accepted
- tools/list：TOOL_SPECS → MCP Tool schema
- tools/call：query_qc_violations / query_qc_alerts → CallQcServer.call；
  FORBIDDEN / 未知工具 → isError=true（不穿透为 HTTP 错误）
- GET（SSE 流）：405（stateless，不支持服务器主动推送）

依赖说明：qc-service 容器镜像当前不含 mcp SDK（requirements 变更属后续批次），
故本文件按 MCP 规范手工实现 JSON-RPC 2.0 帧处理，行为与 FastMCP stateless 等价。
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.mcp.server import CallQcServer
from app.mcp.tools import TOOL_SPECS

logger = structlog.get_logger()

router = APIRouter()

_SERVER_INFO = {"name": "qc-service-call-qc", "version": "1.0.0"}
# 本服务支持的 MCP 协议版本（对齐网关 mcp 1.30 SDK），供客户端 initialize 后判断兼容性
_SUPPORTED_PROTOCOL_VERSION = "2025-06-18"

# 入参 schema 与 qc-service/app/mcp/tools.py TOOL_SPECS params 对齐
_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string", "description": "案件 ID"},
        "session_id": {"type": "string", "description": "通话会话 ID"},
    },
    "required": ["case_id", "session_id"],
}


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": dict(_INPUT_SCHEMA),
        }
        for spec in TOOL_SPECS
    ]


def _result_envelope(payload: Any) -> dict[str, Any]:
    """MCP tools/call result：structuredContent 为主，text content 兜底。"""
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": {"result": payload},
        "isError": False,
    }


def _error_envelope(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _build_server() -> CallQcServer:
    """每次调用装配 repo（与 deps.get_quality_repo 同源：DB session + Redis job_store）。"""
    from app.deps import get_redis
    from app.repositories.alert_repo import AlertRepo
    from app.repositories.job_store import QcJobStore
    from app.repositories.quality_repo import QualityRepo
    from common.db.session import async_session_factory

    session = async_session_factory()
    redis = await get_redis()
    server = CallQcServer(
        quality_repo=QualityRepo(session, QcJobStore(redis)),
        alert_repo=AlertRepo(session),
    )
    # 挂在 server 上随用随关（调用方负责 aclose）
    server._qc_db_session = session  # noqa: SLF001
    return server


async def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    req_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return _jsonrpc_result(
            req_id,
            {
                # 无状态端点：返回本服务支持的协议版本，由客户端判断兼容性；
                # 不存储协商状态，也不回显客户端版本（回显会掩盖不兼容）
                "protocolVersion": _SUPPORTED_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": dict(_SERVER_INFO),
            },
        )

    if method.startswith("notifications/"):
        return None

    if method == "ping":
        return _jsonrpc_result(req_id, {})

    if method == "tools/list":
        return _jsonrpc_result(req_id, {"tools": _tool_schemas()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        server = await _build_server()
        try:
            payload = await server.call(name, arguments)
        except ValueError as e:
            # FORBIDDEN_TOOLS / 未知工具：isError=true，不穿透为 HTTP 错误
            logger.info("mcp_call_rejected", tool=name, error=str(e))
            return _jsonrpc_result(req_id, _error_envelope(str(e)))
        except Exception as e:  # noqa: BLE001 — DB 等故障按工具错误返回，连接保持可用
            logger.warning("mcp_call_failed", tool=name, error=str(e))
            return _jsonrpc_result(req_id, _error_envelope(f"call_qc backend error: {e}"))
        finally:
            session = getattr(server, "_qc_db_session", None)
            if session is not None:
                await session.close()
        return _jsonrpc_result(req_id, _result_envelope(payload))

    if is_notification:
        return None
    return _jsonrpc_error(req_id, -32601, f"method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — 非法 JSON 按 JSON-RPC parse error 返回
        return JSONResponse(_jsonrpc_error(None, -32700, "parse error"), status_code=400)

    messages = body if isinstance(body, list) else [body]
    responses: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            responses.append(_jsonrpc_error(None, -32600, "invalid request"))
            continue
        result = await _handle_request(message)
        if result is not None:
            responses.append(result)

    if not responses:
        # 纯 notification：202 无正文（MCP streamable-http 规范）
        return Response(status_code=202)
    payload: Any = responses if isinstance(body, list) else responses[0]
    return JSONResponse(payload)


@router.get("/mcp")
async def mcp_stream() -> Response:
    """stateless 端点不支持服务器主动 SSE 推送（规范允许 405）。"""
    return Response(status_code=405)
