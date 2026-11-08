"""ASGI 中间件共享小工具：读请求 body + 单次重放。

auth / rate_limit 两个中间件都需先读 body 做 JSON-RPC 解析、再放行给下游
（放行路径只重放一次 buffered body），此处抽公共实现避免两处重复。
"""
from __future__ import annotations

from typing import Any

# T103（spec mcp-gateway：请求体读取上限与断连处理）：累计读取上限显式常量，
# 超限停止累积（内存不随请求体无限增长），由调用方转 413。
MAX_BODY_BYTES = 1024 * 1024  # 1MB

# 413 的 JSON-RPC error code（T103）：服务端保留段（-32000~-32099）顺延——
# -32000 已被 429（rate_limit）占用、-32001 已被 401（auth）占用。
# 与 BodyTooLargeError 同源定义（auth/rate_limit 两个调用方共享，不重复定义）。
JSONRPC_PAYLOAD_TOO_LARGE_CODE = -32002


class BodyTooLargeError(Exception):
    """请求体累计超过 MAX_BODY_BYTES（调用方应返回 413）。"""


class ClientDisconnectedError(Exception):
    """客户端在请求体传输完成前断开连接（读取协程退出，不挂起）。"""


async def read_body(receive) -> bytes:
    """读取完整 http.request body（含 more_body 分片）。

    T103 约束：
    - 非 request 消息跳过；``http.disconnect`` 抛 ClientDisconnectedError
      （此前 ``continue`` 会永久等待后续分片，断连后协程挂起）；
    - 累计字节超 1MB 抛 BodyTooLargeError 并停止累积（MUST NOT 继续读入内存）。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        msg_type = message["type"]
        if msg_type == "http.disconnect":
            raise ClientDisconnectedError("client disconnected mid-body")
        if msg_type != "http.request":
            continue
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise BodyTooLargeError(
                f"request body exceeds {MAX_BODY_BYTES} bytes"
            )
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def make_replay_receive(receive, body: bytes):
    """返回「单次重放 buffered body，此后回退原 receive」的 receive 工厂。

    streamable-http 长连接 SSE 会持续调 receive 轮询 http.disconnect，若无限重放
    http.request 会导致空转挂起，故 buffered body 只重放一次。
    """
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
